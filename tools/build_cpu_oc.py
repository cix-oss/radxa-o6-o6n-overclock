#!/usr/bin/env python3
"""Check or build O6/O6N CPU firmware from an isolated public source copy.

The default operation checks inputs. Compilation requires --build.
"""
# SPDX-License-Identifier: BSD-2-Clause-Patent

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
import re

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = Path("edk2-non-osi/Platform/CIX/Sky1/PackageTool")
PLATFORMS = Path("edk2-platforms/Platform/Radxa/Orion")
HEADER = Path("edk2-platforms/Platform/Radxa/Platforms/CIX/Sky1/Drivers/PmConfigUpdateDxe/CixCpuOcExpected.h")
BOARDS = ("O6", "O6N")
BUILD_TYPES = ("RELEASE", "DEBUG")
CPU_OC_ABI = 5
BUILD_INPUTS = ("tools/build_cpu_oc.py", "tools/firmware_contract.py")
MARKER = "cix-public-cpu-oc-work-v1\n"
EXPORTED_SKIP = {".git", "Build", "Conf", ".build", "__pycache__", ".pytest_cache"}
OPENSSL_TEST_LINKS = {"boringssl", "pyca-cryptography", "krb5"}
# Upstream's macOS emulator include alias is unused by either ARM64 board.
UNUSED_EXTERNAL_LINKS = {
    Path("edk2/EmulatorPkg/Unix/Host/X11IncludeHack"): "/opt/X11/include",
}


class BuildError(RuntimeError):
    pass


def optional_gitlink(repo, name):
    return ((repo.name == "openssl" and name in OPENSSL_TEST_LINKS) or
            (repo.name == "edk2-platforms" and
             name == "Silicon/RISC-V/ProcessorPkg/Library/RiscVOpensbiLib/opensbi"))


def run(command, *, cwd=None, env=None, capture=False):
    command = [str(arg) for arg in command]
    if not capture:
        print("+ " + shlex.join(command), flush=True)
    return subprocess.run(command, cwd=cwd, env=env, check=True,
                          stdout=subprocess.PIPE if capture else None,
                          text=capture).stdout


def git_root(path):
    result = subprocess.run(["git", "-C", str(path), "rev-parse", "--show-toplevel"],
                            capture_output=True, text=True)
    return result.returncode == 0 and Path(result.stdout.strip()).resolve() == path.resolve()


def source_files(repo):
    """Include working files, local edits, and initialized nested gitlinks."""
    if not git_root(repo):
        raise BuildError(f"Not a Git worktree: {repo}")
    staged = subprocess.check_output(["git", "-C", str(repo), "ls-files", "--stage", "-z"])
    links = {}
    for record in staged.split(b"\0"):
        if not record:
            continue
        info, name = record.split(b"\t", 1)
        mode, commit, stage = info.split()
        if stage != b"0":
            raise BuildError(f"Resolve unmerged source paths in {repo} before building")
        if mode == b"160000":
            links[os.fsdecode(name)] = commit.decode()
    listed = subprocess.check_output([
        "git", "-C", str(repo), "ls-files", "--cached", "--others", "--exclude-standard", "-z"])
    return sorted({os.fsdecode(name) for name in listed.split(b"\0") if name}), links


def check_gitlinks(repo):
    _, links = source_files(repo)
    for name in links:
        child = repo / name
        if optional_gitlink(repo, name) and not git_root(child):
            continue
        if not git_root(child):
            raise BuildError(f"Missing submodule {child}; run git submodule update --init --recursive")
        check_gitlinks(child)


def copy_entry(source, target, source_root):
    if source.is_symlink():
        if UNUSED_EXTERNAL_LINKS.get(source.relative_to(source_root)) == os.readlink(source):
            return
        if not source.resolve().is_relative_to(source_root.resolve()):
            raise BuildError(f"Source symlink leaves the project: {source}")
        target.parent.mkdir(parents=True, exist_ok=True)
        # Absolute links into the original checkout would defeat build isolation.
        target.symlink_to(os.path.relpath(source.resolve(), source.parent.resolve()))
    elif source.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def copy_worktree(repo, target, source_root, revisions, label):
    files, links = source_files(repo)
    revisions[label] = {
        "commit": run(["git", "-C", repo, "rev-parse", "HEAD"], capture=True).strip(),
        "dirty": bool(run(["git", "-C", repo, "status", "--porcelain"], capture=True)),
        "diff_sha256": hashlib.sha256(subprocess.check_output([
            "git", "-C", str(repo), "diff", "--binary", "HEAD"])).hexdigest(),
    }
    for name in files:
        if name in links:
            child = repo / name
            if not git_root(child):
                if optional_gitlink(repo, name):
                    revisions[f"{label}/{name}"] = {
                        "commit": links[name], "included": False,
                        "reason": "Unused upstream test or RISC-V dependency"}
                    continue
                raise BuildError(f"Missing submodule: {child}")
            copy_worktree(child, target / name, source_root, revisions, f"{label}/{name}")
        else:
            copy_entry(repo / name, target / name, source_root)


def snapshot_digest(directory):
    digest = hashlib.sha256()
    for source in sorted(directory.rglob("*")):
        if source.is_dir() and not source.is_symlink():
            continue
        digest.update(os.fsencode(source.relative_to(directory).as_posix()) + b"\0")
        if source.is_symlink():
            digest.update(b"symlink\0" + os.fsencode(os.readlink(source)) + b"\0")
        else:
            digest.update(f"file:{source.stat().st_mode & 0o777:o}\0".encode())
            with source.open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(block)
            digest.update(b"\0")
    return digest.hexdigest()


def archive_ignore(directory, names):
    ignored = EXPORTED_SKIP.intersection(names)
    if Path(directory).name == "openssl":
        ignored = ignored.union(OPENSSL_TEST_LINKS.intersection(names))
    return ignored


def copy_archive(source, target, source_root):
    """Apply the same symlink isolation to exported and Git-managed sources."""
    target.mkdir()
    entries = sorted(source.iterdir())
    ignored = archive_ignore(source, {entry.name for entry in entries})
    for entry in entries:
        if entry.name in ignored:
            continue
        if entry.is_dir() and not entry.is_symlink():
            copy_archive(entry, target / entry.name, source_root)
        else:
            copy_entry(entry, target / entry.name, source_root)


def snapshot_sources(project, destination):
    """Copy only src, avoiding output recursion even when work-dir is in project."""
    destination.mkdir()
    revisions = {}
    source_root = project / "src"
    for child in sorted(source_root.iterdir()):
        if child.name in EXPORTED_SKIP or child.name == "tools":
            continue
        if child.is_symlink():
            copy_entry(child, destination / child.name, source_root)
        elif child.is_dir() and git_root(child):
            copy_worktree(child, destination / child.name, source_root, revisions, child.name)
        elif child.is_dir():
            # Debian source archives have no .git metadata; copy their complete source.
            copy_archive(child, destination / child.name, source_root)
        else:
            copy_entry(child, destination / child.name, source_root)
    revisions["snapshot"] = {"sha256": snapshot_digest(destination),
                             "contents": "Source copy before generated contract header and compilation"}
    return revisions


def snapshot_build_tools(project, destination):
    """Retain the exact build wrapper and verifier used for this invocation."""
    records = {}
    for relative in BUILD_INPUTS:
        source = project / relative
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        records[relative] = {"sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
                             "size": target.stat().st_size}
    return records


def select_boot_chain(src, board, override):
    if override is not None:
        return override / "bootloader1.img", override / "bootloader2.img"
    board_files = src / PLATFORMS / board / "Firmwares"
    default = src / PACKAGE / "Firmwares"
    selected = []
    for name in ("bootloader1.img", "bootloader2.img"):
        selected.append(board_files / name if (board_files / name).is_file() else default / name)
    return tuple(selected)


def check_sources(project, boards):
    src = project / "src"
    for name in ("edk2", "edk2-platforms", "edk2-non-osi"):
        tree = src / name
        if not tree.is_dir():
            raise BuildError(f"Missing source directory: {tree}")
        if git_root(project) and not git_root(tree):
            raise BuildError(f"Missing submodule {tree}; run git submodule update --init --recursive")
        if git_root(tree):
            check_gitlinks(tree)
    for board in boards:
        for relative in (f"{board}.dsc", "mem_config/Makefile", "pm_config/Makefile"):
            if not (src / PLATFORMS / board / relative).is_file():
                raise BuildError(f"Missing {board} source: {relative}")
        if board == "O6" and not (src / PLATFORMS / board / "Firmwares/ec_fw.bin").is_file():
            raise BuildError("O6 requires its board-specific EC image")
    if not (src / HEADER).is_file():
        raise BuildError("Missing public CPU configuration driver and expected-PM header")
    for name in ("AARCH64/cix_package_tool", "AARCH64/cert_uefi_create_rsa", "AARCH64/fiptool"):
        program = src / PACKAGE / name
        if not os.access(program, os.X_OK):
            raise BuildError(f"Missing executable package tool: {program}")
    for name in ("Keys/oem_privatekey.pem", "certs/trusted_key_no.crt", "spi_flash_config_all.json"):
        if not (src / PACKAGE / name).is_file():
            raise BuildError(f"Missing public packaging asset: {name}")
    for relative in ("BaseTools/Source/C/BrotliCompress/brotli/c/include/brotli/decode.h",
                     "CryptoPkg/Library/OpensslLib/openssl/Configure"):
        if not (src / "edk2" / relative).is_file():
            raise BuildError(f"Missing EDK2 dependency: {relative}; initialize recursive submodules")


def check_host():
    if platform.system() != "Linux" or platform.machine() not in ("aarch64", "arm64"):
        raise BuildError("This optional CPU firmware builder requires native ARM64 Linux")
    required = ("bash", "git", "make", "gcc", "g++", "ar", "ld", "objcopy", "iasl",
                "python", "python3", "openssl", "pkg-config", "dpkg-parsechangelog")
    missing = [name for name in required if shutil.which(name) is None]
    if missing:
        raise BuildError("Missing build tools: " + ", ".join(missing) + "; see debian/control")
    run(["pkg-config", "--exists", "uuid", "zlib"], capture=True)
    if "aarch64" not in run(["gcc", "-dumpmachine"], capture=True):
        raise BuildError("gcc must target native AArch64")


def check_cpu_oc_provenance(override, contract):
    """Bind the separately built private payload to its recorded source inputs."""
    path = override / "cpu-oc-source.json"
    record = json.loads(path.read_text())
    if record.get("schema") != 1 or record.get("build_status") != "built":
        raise BuildError("CPU OC input must record a completed PM build in cpu-oc-source.json")
    if record.get("cpu_oc_abi") != CPU_OC_ABI or contract["cpu_oc_abi"] != CPU_OC_ABI:
        raise BuildError("CPU OC source record differs from the selected payload: cpu_oc_abi")
    for key, length in (("pm_source_revision", 40), ("pm_patch_sha256", 64)):
        if not re.fullmatch(r"[0-9a-f]{%d}" % length, str(record.get(key, ""))):
            raise BuildError(f"Missing exact CPU OC source identity: {key}")
    for key, expected in (("pm_sha256", contract["pm"]["sha256"]),
                          ("bl1_sha256", contract["bl1"]["sha256"]),
                          ("bl2_sha256", contract["bl2"]["sha256"])):
        if record.get(key) != expected:
            raise BuildError(f"CPU OC source record differs from the selected payload: {key}")
    return record


def validate_contract(project, src, boards, override, require_cpu_oc):
    for board in boards:
        bl1, bl2 = select_boot_chain(src, board, override)
        for blob in (bl1, bl2):
            if not blob.is_file():
                raise BuildError(f"Missing boot-chain image: {blob}")
        command = [sys.executable, project / "tools/firmware_contract.py", "inspect",
                   "--bl1", bl1, "--bl2", bl2]
        if require_cpu_oc:
            command.append("--require-cpu-oc")
        contract = json.loads(run(command, capture=True))
        pm_source = contract['pm']['source_revision'] or contract['pm']['sha256'][:12]
        if not require_cpu_oc and contract["cpu_oc_abi"] != 0:
            raise BuildError("An OC-capable PM requires an explicit boot-chain directory and source record")
        if require_cpu_oc:
            if override is None:
                raise BuildError("CPU OC requires an explicit boot-chain directory and source record")
            source_record = check_cpu_oc_provenance(override, contract)
            pm_source = source_record['pm_source_revision'][:12]
        print(f"{board}: PM {pm_source}, CPU OC ABI {contract['cpu_oc_abi']}, "
              f"BL1 {contract['bl1']['sha256'][:12]}, BL2 {contract['bl2']['sha256'][:12]}")


def stage_firmware(src, board, output, bl1, bl2, *, ec_size=0xE0000, fip_serial=None):
    firmware = output / "Firmwares"
    shutil.copytree(src / PACKAGE / "Firmwares", firmware)
    board_firmware = src / PLATFORMS / board / "Firmwares"
    if board_firmware.is_dir():
        shutil.copytree(board_firmware, firmware, dirs_exist_ok=True)
    # Copy the exact pair validated by the contract, after board overrides.
    shutil.copy2(bl1, firmware / "bootloader1.img")
    shutil.copy2(bl2, firmware / "bootloader2.img")
    if fip_serial is not None:
        # CIX uses this unsigned FIP header field for update ordering. Keep the
        # board's version policy without modifying any certificate or payload.
        data = bytearray((firmware / "bootloader2.img").read_bytes())
        data[4:8] = fip_serial.to_bytes(4, "little")
        (firmware / "bootloader2.img").write_bytes(data)
    (firmware / "dummy.bin").write_bytes(b"\xff" * 8192)
    if board == "O6N":
        (firmware / "ec_fw.bin").write_bytes(b"\xff" * ec_size)
    elif not (board_firmware / "ec_fw.bin").is_file():
        raise BuildError("O6 requires its board-specific EC image")
    return firmware


def checked_full_image(raw, destination, flash_size):
    if not raw.is_file() or raw.stat().st_size > flash_size:
        raise BuildError("Package tool output is absent or exceeds the flash size")
    data = raw.read_bytes()
    destination.write_bytes(data + b"\xff" * (flash_size - len(data)))


def build_environment():
    env = os.environ.copy()
    for name in ("MAKEFLAGS", "MFLAGS", "GCC5_AARCH64_PREFIX", "IASL_PREFIX", "CFLAG",
                 "CFLAGS", "CXXFLAGS", "CPPFLAGS", "LDFLAGS", "MEM_CFG_MEMFREQ",
                 "EDK_TOOLS_PATH", "EDK_TOOLS_BIN", "CONF_PATH", "WORKSPACE", "PACKAGES_PATH",
                 "CC", "CXX", "AS", "AR", "LD", "NM", "OBJCOPY", "OBJDUMP"):
        env.pop(name, None)
    return env


def build_board(project, src, board, jobs, override, require_cpu_oc, run_dir, revisions,
                *, build_type="RELEASE", contract_tool=None):
    if build_type not in BUILD_TYPES:
        raise BuildError(f"Unsupported EDK2 build type: {build_type}")
    contract_tool = contract_tool or project / "tools/firmware_contract.py"
    package = src / PACKAGE
    board_dir = src / PLATFORMS / board
    output = src / "Build" / board / f"{build_type}_GCC5"
    output.mkdir(parents=True)
    bl1, bl2 = select_boot_chain(src, board, override)
    layout = board_dir / "spi_flash_config_all.json"
    if not layout.is_file():
        layout = package / "spi_flash_config_all.json"
    config = json.loads(layout.read_text())
    ec_size = int(str(config.get("ec_end", "0xF0000")), 0) - int(str(config.get("ec_addr", "0x10000")), 0)
    firmware = stage_firmware(src, board, output, bl1, bl2, ec_size=ec_size,
                              fip_serial=int(str(config["fip_version"]), 0))
    manifest = output / "boot-chain.json"
    prepare = [sys.executable, contract_tool, "prepare",
               "--bl1", firmware / "bootloader1.img", "--bl2", firmware / "bootloader2.img",
               "--header", src / HEADER, "--manifest", manifest]
    if require_cpu_oc:
        prepare.append("--require-cpu-oc")
    run(prepare)
    prepared = json.loads(manifest.read_text())
    expected_header_bytes = (src / HEADER).read_bytes()
    prepared["expected_pm_header_sha256"] = hashlib.sha256(expected_header_bytes).hexdigest()
    prepared["input_bl2"] = {"sha256": hashlib.sha256(bl2.read_bytes()).hexdigest(),
                              "fip_serial": int.from_bytes(bl2.read_bytes()[4:8], "little")}
    prepared["packaged_fip_serial"] = int(str(config["fip_version"]), 0)
    manifest.write_text(json.dumps(prepared, indent=2) + "\n")
    for name in ("Keys", "certs"):
        shutil.copytree(package / name, output / name)

    env = build_environment()
    env["WORKSPACE"] = str(src)
    env["PACKAGES_PATH"] = ":".join(str(src / name) for name in (
        "edk2", "edk2-platforms", "edk2-non-osi", "edk2-platforms/Silicon/Intel"))
    env["GCC5_AARCH64_PREFIX"] = ""
    env["PYTHON_COMMAND"] = sys.executable
    epoch = int(env.get("SOURCE_DATE_EPOCH", "0"))
    date = datetime.fromtimestamp(epoch, timezone.utc) if epoch else datetime.now(timezone.utc)
    version = run(["dpkg-parsechangelog", "-S", "Version"], cwd=project, capture=True).strip()
    defines = {"BOARD_NAME": board, "BUILD_DATE": date.isoformat(), "SMP_ENABLE": "1",
               "ACPI_BOOT_ENABLE": "1", "FASTBOOT_LOAD": "", "VARIABLE_TYPE": "SPI",
               "STANDARD_MM": "TRUE", "DEB_VERSION": version,
               "COMMIT_HASH": project_revision(project),
               "EDK2_COMMIT_HASH": revisions.get("edk2", {}).get("commit", "source-archive")[:12],
               "EDK2_NON_OSI_COMMIT_HASH": revisions.get("edk2-non-osi", {}).get("commit", "source-archive")[:12],
               "EDK2_PLATFORMS_COMMIT_HASH": revisions.get("edk2-platforms", {}).get("commit", "source-archive")[:12]}
    command = ["build", "-a", "AARCH64", "-t", "GCC5", "-b", build_type, "-n", str(jobs),
               "-p", f"Platform/Radxa/Orion/{board}/{board}.dsc"]
    for name, value in defines.items():
        command.extend(["-D", f"{name}={value}"])
    run(["bash", "-ec", 'source edk2/edksetup.sh --reconfig; exec "$@"', "firmware-build", *command],
        cwd=src, env=env)
    for config_name, result in (("mem_config", "memory_config.bin"), ("pm_config", "csu_pm_config.bin")):
        run(["make", "-C", board_dir / config_name, "clean"], env=env)
        run(["make", "-C", board_dir / config_name, f"-j{jobs}"], env=env)
        shutil.copy2(board_dir / config_name / result, firmware / result)

    # Retain the public OEM key/certificate pair used by the upstream signed chain.
    native = package / "AARCH64"
    fd = output / "FV/SKY1_BL33_UEFI.fd"
    if not fd.is_file() or fd.stat().st_size == 0:
        raise BuildError(f"EDK2 did not produce the requested {build_type} firmware: {fd}")
    run([native / "cert_uefi_create_rsa", "--key-alg", "rsa", "--key-size", "3072",
         "--hash-alg", "sha256", "-p", "--ntfw-nvctr", "223",
         "--nt-fw-cert", output / "certs/nt_fw_cert.crt",
         "--nt-fw-key-cert", output / "certs/nt_fw_key.crt",
         "--nt-fw-key", output / "Keys/oem_privatekey.pem",
         "--non-trusted-world-key", output / "Keys/oem_privatekey.pem", "--nt-fw", fd])
    run([native / "fiptool", "create", "--trusted-key-cert", output / "certs/trusted_key_no.crt",
         "--nt-fw-key-cert", output / "certs/nt_fw_key.crt", "--nt-fw-cert", output / "certs/nt_fw_cert.crt",
         "--nt-fw", fd, firmware / "bootloader3.img"])
    layout_copy = output / "spi_flash_config_all.json"
    shutil.copy2(layout, layout_copy)
    raw, image = output / "cix_flash_all.raw", output / "cix_flash_all.bin"
    run([native / "cix_package_tool", "-c", layout_copy.name, "-o", raw.name], cwd=output)
    flash_size = int(str(config["flash_size"]), 0)
    checked_full_image(raw, image, flash_size)
    validation = output / "image-verification.json"
    run([sys.executable, contract_tool, "verify-image", "--image", image,
         "--layout", layout_copy, "--manifest", manifest, "--board", board, "--output", validation])
    artifacts = run_dir / "artifacts" / board
    artifacts.mkdir(parents=True)
    if (src / HEADER).read_bytes() != expected_header_bytes:
        raise BuildError("The expected PM header changed during the board build")
    (artifacts / "CixCpuOcExpected.h").write_bytes(expected_header_bytes)
    for source in (image, manifest, layout_copy, validation):
        shutil.copy2(source, artifacts / source.name)
    for filename in ("Shell.efi", "VariableInfo.efi"):
        source = output / "AARCH64" / filename
        if source.is_file():
            shutil.copy2(source, artifacts / filename)
    for filename in ("BurnImage.efi", "FlashUpdate.efi"):
        shutil.copy2(src / "edk2-non-osi/Platform/CIX/Sky1/FlashTool" / filename, artifacts / filename)
    (artifacts / "sources.json").write_text(json.dumps(revisions, indent=2) + "\n")
    metadata = {"board": board, "build_type": build_type, "edk2_command": command,
                "uefi_fd_sha256": hashlib.sha256(fd.read_bytes()).hexdigest(),
                "uefi_fd_size": fd.stat().st_size,
                "expected_pm_header_sha256": prepared["expected_pm_header_sha256"],
                "build_tools": revisions.get("build_tools", {}),
                "pm_profile": "Defined by the selected boot-chain input; unchanged by --build-type"}
    (artifacts / "build.json").write_text(json.dumps(metadata, indent=2) + "\n")
    checksum = hashlib.sha256(image.read_bytes()).hexdigest()
    (artifacts / "SHA256SUMS").write_text(f"{checksum}  cix_flash_all.bin\n")


def project_revision(project):
    if git_root(project):
        return run(["git", "-C", project, "rev-parse", "--short=12", "HEAD"], capture=True).strip()
    return "source-archive"


def positive(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be a positive integer")
    return number


def arguments(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("board", choices=("all", *BOARDS), nargs="?", default="all")
    parser.add_argument("--boot-chain", type=Path, help="matched signed BL1/BL2 directory; requires CPU OC ABI")
    parser.add_argument("--require-cpu-oc", action="store_true", help="reject a chain without the CPU OC ABI")
    parser.add_argument("--work-dir", type=Path, default=ROOT / ".build/cpu-oc")
    parser.add_argument("--jobs", type=positive, default=os.cpu_count() or 1)
    parser.add_argument("--build-type", choices=BUILD_TYPES, default="RELEASE",
                        help="EDK2 build type; the selected PM firmware is unchanged (default: RELEASE)")
    parser.add_argument("--preflight-only", action="store_true", help="check inputs without compiling or creating build copies")
    parser.add_argument("--build", action="store_true", help="compile and package the selected boards")
    return parser.parse_args(argv)


def main(argv=None):
    args = arguments(argv)
    if args.build and args.preflight_only:
        raise BuildError("--build and --preflight-only are mutually exclusive")
    boards = BOARDS if args.board == "all" else (args.board,)
    override = args.boot_chain.resolve() if args.boot_chain else None
    require_cpu_oc = args.require_cpu_oc or override is not None
    check_sources(ROOT, boards)
    validate_contract(ROOT, ROOT / "src", boards, override, require_cpu_oc)
    if not args.build:
        print("Preflight passed for " + ", ".join(boards) +
              f" ({args.build_type}). No build files created.")
        return 0
    check_host()
    work = args.work_dir.resolve()
    if ROOT.is_relative_to(work) or work.is_relative_to(ROOT / "src"):
        raise BuildError("work-dir must not be the project root or inside src")
    work.mkdir(parents=True, exist_ok=True)
    marker = work / ".cix-firmware-work"
    if marker.exists() and marker.read_text() != MARKER:
        raise BuildError("Invalid firmware work directory marker")
    if not marker.exists():
        if any(work.iterdir()):
            raise BuildError("Choose an empty work-dir or an existing marked firmware work-dir")
        marker.write_text(MARKER)
    with (work / ".lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BuildError("Another firmware build is using this work-dir") from exc
        published = work / "artifacts"
        if published.exists() and not published.is_symlink():
            raise BuildError("artifacts must be a builder-managed symlink")
        run_dir = Path(tempfile.mkdtemp(prefix="run-", dir=work))
        print(f"Build directory: {run_dir}", flush=True)
        src = run_dir / "src"
        revisions = snapshot_sources(ROOT, src)
        revisions["build_tools"] = snapshot_build_tools(ROOT, run_dir / "build-tools")
        revisions["build_type"] = args.build_type
        if override is not None:
            frozen_chain = run_dir / "boot-chain-input"
            frozen_chain.mkdir()
            for name in ("bootloader1.img", "bootloader2.img", "cpu-oc-source.json"):
                shutil.copy2(override / name, frozen_chain / name)
            override = frozen_chain
            revisions["cpu_oc_source"] = json.loads((override / "cpu-oc-source.json").read_text())
        # Recheck every frozen input, including stock dependencies that may
        # have changed in the working checkout after the initial preflight.
        validate_contract(run_dir / "build-tools", src, boards, override, require_cpu_oc)
        contract_tool = run_dir / "build-tools/tools/firmware_contract.py"
        # A fresh copy excludes ignored objects; clean tracked BaseTools outputs too.
        clean_env = build_environment()
        run(["make", "-C", src / "edk2/BaseTools", "clean"], env=clean_env)
        run(["make", "-C", src / "edk2/BaseTools", f"-j{args.jobs}", "Source/C"], env=clean_env)
        for board in boards:
            build_board(ROOT, src, board, args.jobs, override, require_cpu_oc, run_dir, revisions,
                        build_type=args.build_type, contract_tool=contract_tool)
        next_link = work / (run_dir.name + "-artifacts")
        next_link.symlink_to((run_dir / "artifacts").relative_to(work), target_is_directory=True)
        next_link.replace(published)
        print(f"Verified firmware artifacts: {published}")
        print(f"Build sources and intermediate files: {run_dir}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (BuildError, OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"firmware build: {exc}", file=sys.stderr)
        raise SystemExit(1)
