#!/usr/bin/env python3
"""Check CIX image boundaries and bind CPU tuning to an exact PM payload.

These checks establish layout and payload identity. Signature authentication is
performed by the platform boot chain, not by this offline packaging tool.
"""
# SPDX-License-Identifier: BSD-2-Clause-Patent

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import sys


CBFF_MAGIC = b"CIXBTFF!"
CBFF_HEADER_SIZE = 4096
CBFF_COUNT_OFFSET = 2536
CBFF_ENTRIES_OFFSET = 2540
CBFF_ENTRY = struct.Struct("<6I")
BL1_MAX_SIZE = 0x100000
BL2_MAX_SIZE = 0x100000
CPU_OC_ABI = 5
CPU_OC_MARKERS = {version: f"CIX_PM_CPU_OC_ABI_{version}\x00".encode()
                  for version in (1, 2, 3, 4, 5)}
CPU_OC_MARKER = CPU_OC_MARKERS[CPU_OC_ABI]
FIP_MAGIC = 0xAA640001
FLASH_SIZE = 0x800000
KNOWN_PM = {
    "59d7445c0f85ba3040f3be2c3507956a34d4a8945548fea550aa13f88830e942": "79f0fdb030f9",
    "f6f248f2cdda9c60dc4942f09bf0a9060bd651732a21a5607f8e16b4c65ec24d": "a2327331813f",
    "1f30e5c65858f591e8171b43d05e8712659a261e40111b26a04897d8c85a2947": "a2327331813f",
}


class ContractError(ValueError):
    """An image does not satisfy the declared firmware contract."""


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def checked_slice(data: bytes, offset: int, size: int, label: str) -> bytes:
    if offset < 0 or size <= 0 or offset > len(data) or size > len(data) - offset:
        raise ContractError(f"{label}: range exceeds image")
    return data[offset:offset + size]


def reject_overlaps(ranges: list[tuple[int, int, str]]) -> None:
    ordered = sorted(ranges)
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1]:
            raise ContractError(f"overlapping {previous[2]} and {current[2]}")


def inspect_bl1(data: bytes) -> dict:
    if not CBFF_HEADER_SIZE <= len(data) <= BL1_MAX_SIZE:
        raise ContractError("BL1 size is outside its flash allocation")
    if data[:8] != CBFF_MAGIC:
        raise ContractError("BL1 CBFF signature is missing")
    version, encryption, key_count = struct.unpack_from("<3I", data, 8)
    if encryption != 0:
        raise ContractError("encrypted BL1 cannot be inspected for PM capabilities")
    if not 1 <= key_count <= 2:
        raise ContractError("invalid BL1 signing key count")
    qspi_count = struct.unpack_from("<I", data, 2048)[0]
    if not 1 <= qspi_count <= 30:
        raise ContractError("invalid BL1 QSPI configuration count")
    sign_type, count = struct.unpack_from("<2I", data, CBFF_COUNT_OFFSET - 4)
    if sign_type not in (0x5A, 0x5C):
        raise ContractError("unsupported BL1 signature format")
    if count != 3:
        raise ContractError("invalid BL1 component count")
    components = []
    ids = set()
    spans = [(0, CBFF_HEADER_SIZE, "BL1 header")]
    for index in range(count):
        ident, major, minor, offset, size, address = CBFF_ENTRY.unpack_from(
            data, CBFF_ENTRIES_OFFSET + index * CBFF_ENTRY.size)
        if ident in ids or ident not in (1, 2, 3):
            raise ContractError("duplicate or unsupported BL1 component (SE, PM, PBL required)")
        ids.add(ident)
        if offset % 4096:
            raise ContractError("BL1 component is not page aligned")
        payload = checked_slice(data, offset, size, f"BL1 component {ident}")
        spans.append((offset, offset + size, f"BL1 component {ident}"))
        entry = {"id": ident, "version": [major, minor], "offset": offset,
                 "size": size, "run_address": address, "sha256": sha256(payload)}
        if ident == 2:
            # Match only the complete, NUL-terminated capability string inside PM.
            versions = [version for version, marker in CPU_OC_MARKERS.items()
                        if marker in payload]
            if len(versions) > 1:
                raise ContractError("PM declares conflicting CPU OC capabilities")
            entry["cpu_oc_abi"] = versions[0] if versions else 0
            match = re.search(rb"MINI_PORT_[\x20-\x7e]{0,160}?([0-9a-f]{12})\.", payload)
            entry["source_revision"] = KNOWN_PM.get(entry["sha256"],
                                                    match[1].decode() if match else None)
        components.append(entry)
    if ids != {1, 2, 3}:
        raise ContractError("BL1 must contain exactly SE, PM, and PBL")
    reject_overlaps(spans)
    return {"size": len(data), "sha256": sha256(data), "version": version,
            "sign_type": sign_type, "components": components}


def inspect_bl2(data: bytes) -> dict:
    if not 56 <= len(data) <= BL2_MAX_SIZE:
        raise ContractError("BL2 size is outside its flash allocation")
    if struct.unpack_from("<I", data)[0] != FIP_MAGIC:
        raise ContractError("BL2 is not a TF-A FIP image")
    offset = 16
    entries = []
    spans = []
    ids = set()
    for _ in range(64):
        raw = checked_slice(data, offset, 40, "BL2 table entry")
        uuid, start, size, flags = struct.unpack("<16s3Q", raw)
        offset += 40
        if uuid == b"\x00" * 16:
            break
        if uuid in ids:
            raise ContractError("duplicate BL2 component UUID")
        ids.add(uuid)
        payload = checked_slice(data, start, size, "BL2 component")
        entries.append({"uuid": uuid.hex(), "offset": start, "size": size,
                        "flags": flags, "sha256": sha256(payload)})
        spans.append((start, start + size, f"BL2 component {uuid.hex()}"))
    else:
        raise ContractError("BL2 table has no terminator")
    if not entries:
        raise ContractError("BL2 has no components")
    reject_overlaps([(0, offset, "BL2 table"), *spans])
    return {"size": len(data), "sha256": sha256(data), "components": entries}


def boot_chain(bl1: bytes, bl2: bytes, require_cpu_oc: bool = False) -> dict:
    first = inspect_bl1(bl1)
    second = inspect_bl2(bl2)
    pm = next(entry for entry in first["components"] if entry["id"] == 2)
    if require_cpu_oc and pm["cpu_oc_abi"] != CPU_OC_ABI:
        raise ContractError("PM lacks CPU OC ABI 5 for MID 3200 MHz and optional LITTLE tuning; "
                            "a compatible signed boot chain is required")
    return {"schema": 1, "cpu_oc_abi": pm["cpu_oc_abi"], "bl1": first,
            "bl2": second, "pm": pm}


def expected_header(contract: dict) -> str:
    pm = contract["pm"]
    digest = ", ".join(f"0x{byte:02x}" for byte in bytes.fromhex(pm["sha256"]))
    return (
        "/** Generated for this build's exact PM payload. Do not edit. */\n"
        "#ifndef CIX_CPU_OC_EXPECTED_H_\n#define CIX_CPU_OC_EXPECTED_H_\n"
        f"#define CIX_CPU_OC_PM_ABI {contract['cpu_oc_abi']}U\n"
        f"#define CIX_CPU_OC_BL1_SIZE {contract['bl1']['size']}U\n"
        f"#define CIX_CPU_OC_PM_OFFSET {pm['offset']}U\n"
        f"#define CIX_CPU_OC_PM_SIZE {pm['size']}U\n"
        f"#define CIX_CPU_OC_PM_SHA256 {{ {digest} }}\n#endif\n"
    )


def number(value) -> int:
    return int(value, 0) if isinstance(value, str) else int(value)


def verify_vendor_pm_config(data: bytes) -> None:
    """A newly packaged image must start with native CPU and PMIC policy."""
    if len(data) != 4096:
        raise ContractError("PM configuration must occupy exactly one 4 KiB block")
    major, minor, _, length, signature, crc1, crc2 = struct.unpack_from("<HH5I", data)
    if (major, minor) != (3, 0) or signature != int.from_bytes(b"PMCF", "little"):
        raise ContractError("unsupported PM configuration header")
    if not 3339 <= length <= len(data) or length % 4:
        raise ContractError("invalid PM configuration checksum length")
    checked = bytearray(data[:length])
    checked[16:24] = b"\0" * 8
    first = second = 0
    for (word,) in struct.iter_unpack("<I", checked):
        first = (first + word) & 0xFFFFFFFF
        second = (second + first) & 0xFFFFFFFF
    if (first, second) != (crc1, crc2):
        raise ContractError("PM configuration checksum mismatch")
    if not struct.unpack_from("<I", data, 24)[0] & 1:
        raise ContractError("initial PM configuration overrides the native PMIC scheme")
    if data[152] not in (1, 0xFF):
        raise ContractError("initial PM configuration must use Vendor CPU operating points")


def verify_image(image: bytes, layout: dict, contract: dict, board: str,
                 layout_dir: Path) -> dict:
    if contract.get("schema") != 1 or contract.get("cpu_oc_abi") not in (0, *CPU_OC_MARKERS):
        raise ContractError("unsupported boot-chain manifest")
    if board not in ("O6", "O6N"):
        raise ContractError("unsupported board")
    if len(image) != FLASH_SIZE or number(layout["flash_size"]) != FLASH_SIZE:
        raise ContractError("full-flash image must be exactly 8 MiB")
    groups = layout["image_header_groups"]
    if len(groups) != number(layout["image_count"]):
        raise ContractError("flash layout entry count mismatch")
    entries = {}
    spans = []
    for group in groups:
        ident = number(group["image_type"])
        if ident in entries:
            raise ContractError("duplicate flash layout entry type")
        start, capacity = number(group["address"]), number(group["size"])
        checked_slice(image, start, capacity, f"flash entry {ident}")
        spans.append((start, start + capacity, f"flash entry {ident}"))
        entries[ident] = group
    reject_overlaps(spans)
    for ident in (1, 2, 3, 4, 6, 7):
        if ident not in entries:
            raise ContractError(f"flash layout is missing entry {ident}")
    verified = []
    for ident, group in entries.items():
        source = Path(group["file"])
        if not source.is_absolute():
            source = layout_dir / source
        expected = source.read_bytes()
        if len(expected) > number(group["size"]):
            raise ContractError(f"source for entry {ident} exceeds its allocation")
        actual = checked_slice(image, number(group["address"]), len(expected), f"entry {ident}")
        if actual != expected:
            raise ContractError(f"full image differs from packaged source for entry {ident}")
        if ident in (1, 2):
            pinned = contract[f"bl{ident}"]
            if len(expected) != pinned["size"] or sha256(expected) != pinned["sha256"]:
                raise ContractError(f"BL{ident} differs from the prepared boot chain")
        if ident == 1:
            parsed = inspect_bl1(expected)
            pm = next(entry for entry in parsed["components"] if entry["id"] == 2)
            if parsed != contract["bl1"] or pm != contract["pm"] or pm["cpu_oc_abi"] != contract["cpu_oc_abi"]:
                raise ContractError("manifest PM capability differs from the actual payload")
        if ident == 4:
            verify_vendor_pm_config(expected)
        verified.append({"type": ident, "size": len(expected), "sha256": sha256(expected)})
    header_base = number(layout["firmware_header_addr"])
    signature, _, count, _ = struct.unpack("<4I", checked_slice(image, header_base, 16, "flash header"))
    if signature != 0x55AA55AA or count != len(entries):
        raise ContractError("packaged flash header does not match its layout")
    found = {}
    for index in range(count):
        ident, start, size, _ = struct.unpack("<4I", checked_slice(
            image, header_base + 16 + 16 * index, 16, "flash header entry"))
        if ident in found or ident not in entries:
            raise ContractError("invalid packaged flash entry type")
        group = entries[ident]
        # Some packagers record occupied bytes, others the full allocation.
        if start != number(group["address"]) or not 0 < size <= number(group["size"]):
            raise ContractError("packaged flash entry bounds differ from layout")
        source_size = next(item["size"] for item in verified if item["type"] == ident)
        if size < source_size:
            raise ContractError("packaged entry omits source bytes")
        if ident in (1, 4) and size != source_size:
            raise ContractError("BL1 and PM directory lengths must match the runtime identity contract")
        found[ident] = (start, size)
    ec_start = number(layout["ec_addr"])
    ec_end = number(layout.get("ec_end", "0xF0000"))
    if board == "O6N" and any(byte != 0xFF for byte in checked_slice(image, ec_start, ec_end - ec_start, "EC area")):
        raise ContractError("O6N EC area must remain erased")
    checked_slice(image, ec_start, ec_end - ec_start, "EC area")
    spans.extend([(header_base, header_base + 4096, "flash header"),
                  (ec_start, ec_end, "EC allocation")])
    checked_slice(image, header_base, 4096, "flash header block")
    auxiliary = []
    for label in ("ec", "sec_debug", "xip"):
        filename = layout.get(f"{label}_file")
        if not filename:
            if label == "ec":
                raise ContractError("flash layout is missing the board EC file")
            continue
        source = Path(filename)
        if not source.is_absolute():
            source = layout_dir / source
        expected = source.read_bytes()
        start = number(layout[f"{label}_addr"])
        if label == "ec":
            if len(expected) > ec_end - ec_start:
                raise ContractError("EC source exceeds its allocation")
            if board == "O6" and (not expected or all(byte == 0xFF for byte in expected)):
                raise ContractError("O6 requires its populated board EC firmware")
        else:
            spans.append((start, start + len(expected), label))
        if checked_slice(image, start, len(expected), label) != expected:
            raise ContractError(f"full image differs from packaged {label} source")
        auxiliary.append({"name": label, "size": len(expected), "sha256": sha256(expected)})
    reject_overlaps(spans)
    return {"schema": 1, "board": board, "size": len(image), "sha256": sha256(image),
            "cpu_oc_abi": contract["cpu_oc_abi"], "verified_entries": verified,
            "verified_auxiliary": auxiliary}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inspect", "prepare"):
        command = commands.add_parser(name)
        command.add_argument("--bl1", required=True, type=Path)
        command.add_argument("--bl2", required=True, type=Path)
        command.add_argument("--require-cpu-oc", action="store_true")
        if name == "prepare":
            command.add_argument("--header", required=True, type=Path)
            command.add_argument("--manifest", required=True, type=Path)
    verify = commands.add_parser("verify-image")
    verify.add_argument("--image", required=True, type=Path)
    verify.add_argument("--layout", required=True, type=Path)
    verify.add_argument("--manifest", required=True, type=Path)
    verify.add_argument("--board", choices=("O6", "O6N"), required=True)
    verify.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command in ("inspect", "prepare"):
            result = boot_chain(args.bl1.read_bytes(), args.bl2.read_bytes(), args.require_cpu_oc)
            if args.command == "prepare":
                args.header.parent.mkdir(parents=True, exist_ok=True)
                args.manifest.parent.mkdir(parents=True, exist_ok=True)
                args.header.write_text(expected_header(result))
                args.manifest.write_text(json.dumps(result, indent=2) + "\n")
        else:
            result = verify_image(args.image.read_bytes(), json.loads(args.layout.read_text()),
                                  json.loads(args.manifest.read_text()), args.board,
                                  args.layout.resolve().parent)
            if args.output:
                args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))
    except (OSError, ValueError, KeyError, TypeError, struct.error) as exc:
        print(f"firmware contract: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
