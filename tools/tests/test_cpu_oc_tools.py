"""Exercise firmware identity checks and the default no-build path."""
# SPDX-License-Identifier: BSD-2-Clause-Patent

import importlib.util
import itertools
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock

TOOLS = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


contract = load("firmware_contract", TOOLS / "firmware_contract.py")
builder = load("build_cpu_oc", TOOLS / "build_cpu_oc.py")


def bl1(oc=True):
    data = bytearray(16384)
    data[:8] = b"CIXBTFF!"
    struct.pack_into("<3I", data, 8, 1, 0, 1)
    struct.pack_into("<I", data, 2048, 1)
    struct.pack_into("<2I", data, 2532, 0x5A, 3)
    for index in range(3):
        struct.pack_into("<6I", data, 2540 + index * 24,
                         index + 1, 1, 0, (index + 1) * 4096, 4096, 0)
    if oc:
        data[8192:8192 + len(contract.CPU_OC_MARKER)] = contract.CPU_OC_MARKER
    return bytes(data)


def bl2():
    data = bytearray(100)
    struct.pack_into("<I", data, 0, 0xAA640001)
    struct.pack_into("<16s3Q", data, 16, b"a" * 16, 96, 4, 0)
    data[96:] = b"test"
    return bytes(data)


def pm_config():
    data = bytearray(b"\xff" * 4096)
    struct.pack_into("<HH5I", data, 0, 3, 0, 0, 3340, 0x46434D50, 0, 0)
    update_checksum(data)
    return data


def update_checksum(data):
    data[16:24] = bytes(8)
    length, = struct.unpack_from("<I", data, 8)
    a = b = 0
    for word, in struct.iter_unpack("<I", data[:length]):
        a = (a + word) & 0xFFFFFFFF
        b = (b + a) & 0xFFFFFFFF
    struct.pack_into("<2I", data, 16, a, b)


class IdentityTests(unittest.TestCase):
    def test_pm_hash_is_of_component_not_container(self):
        parsed = contract.boot_chain(bl1(), bl2(), True)
        self.assertEqual(parsed["pm"]["offset"], 8192)
        self.assertEqual(parsed["pm"]["sha256"], contract.sha256(bl1()[8192:12288]))
        header = contract.expected_header(parsed)
        self.assertIn("#define CIX_CPU_OC_PM_ABI 5U", header)
        self.assertIn("#define CIX_CPU_OC_BL1_SIZE 16384U", header)

    def test_stock_is_inspectable_but_not_oc_capable(self):
        self.assertEqual(contract.boot_chain(bl1(False), bl2())["cpu_oc_abi"], 0)
        with self.assertRaisesRegex(contract.ContractError, "lacks CPU OC"):
            contract.boot_chain(bl1(False), bl2(), True)

    def test_legacy_pm_cannot_enable_mid_3200_or_little_tuning(self):
        for version in (1, 2, 3, 4):
            with self.subTest(version=version):
                data = bytearray(bl1(False))
                marker = contract.CPU_OC_MARKERS[version]
                data[8192:8192 + len(marker)] = marker
                parsed = contract.boot_chain(data, bl2())
                self.assertEqual(parsed["cpu_oc_abi"], version)
                self.assertIn(f"CIX_CPU_OC_PM_ABI {version}U", contract.expected_header(parsed))
                with self.assertRaisesRegex(contract.ContractError, "ABI 5"):
                    contract.boot_chain(data, bl2(), True)

    def test_conflicting_capabilities_are_rejected(self):
        for versions in itertools.combinations(contract.CPU_OC_MARKERS, 2):
            with self.subTest(versions=versions):
                data = bytearray(bl1(False))
                for offset, version in zip((8192, 8256), versions):
                    marker = contract.CPU_OC_MARKERS[version]
                    data[offset:offset + len(marker)] = marker
                with self.assertRaisesRegex(contract.ContractError, "conflicting"):
                    contract.boot_chain(data, bl2())

    def test_marker_in_se_does_not_enable_pm(self):
        data = bytearray(bl1(False))
        data[4096:4096 + len(contract.CPU_OC_MARKER)] = contract.CPU_OC_MARKER
        self.assertEqual(contract.boot_chain(data, bl2())["cpu_oc_abi"], 0)

    def test_incomplete_marker_is_rejected(self):
        data = bytearray(bl1())
        data[8192 + len(contract.CPU_OC_MARKER) - 1] = ord("x")
        with self.assertRaises(contract.ContractError):
            contract.boot_chain(data, bl2(), True)

    def test_bl1_malformed_directories_fail(self):
        mutations = ((12, 1), (16, 0), (2048, 31), (2532, 0), (2536, 4),
                     (2564, 1), (2552, 0), (2576, 4096), (2556, 20000))
        for offset, value in mutations:
            with self.subTest(offset=offset, value=value):
                data = bytearray(bl1())
                struct.pack_into("<I", data, offset, value)
                with self.assertRaises(contract.ContractError):
                    contract.inspect_bl1(data)

    def test_bl2_rejects_payload_overlapping_directory(self):
        data = bytearray(bl2())
        struct.pack_into("<Q", data, 32, 16)
        with self.assertRaisesRegex(contract.ContractError, "overlapping"):
            contract.inspect_bl2(data)

    def test_bl2_rejects_truncated_payload(self):
        with self.assertRaises(contract.ContractError):
            contract.inspect_bl2(bl2()[:-1])

    def test_pm_initial_configuration_and_checksum(self):
        data = pm_config()
        contract.verify_vendor_pm_config(data)
        data[400] ^= 1
        with self.assertRaisesRegex(contract.ContractError, "checksum"):
            contract.verify_vendor_pm_config(data)

    def test_initial_image_cannot_override_pmic_or_cpu(self):
        for offset, value in ((24, 0), (152, 0xC0), (152, 0xC1)):
            data = pm_config()
            data[offset] = value
            update_checksum(data)
            with self.subTest(offset=offset), self.assertRaises(contract.ContractError):
                contract.verify_vendor_pm_config(data)


class BuilderTests(unittest.TestCase):
    def test_builder_and_contract_require_the_same_current_abi(self):
        self.assertEqual(builder.CPU_OC_ABI, 5)
        self.assertEqual(builder.CPU_OC_ABI, contract.CPU_OC_ABI)

    def test_default_preflight_cannot_reach_host_or_compiler(self):
        with mock.patch.object(builder, "check_sources"), \
             mock.patch.object(builder, "validate_contract"), \
             mock.patch.object(builder, "check_host", side_effect=AssertionError("build reached")), \
             mock.patch.object(builder, "snapshot_sources", side_effect=AssertionError("copy reached")):
            self.assertEqual(builder.main([]), 0)

    def test_conflicting_build_options_fail(self):
        with self.assertRaisesRegex(builder.BuildError, "mutually exclusive"):
            builder.main(["--build", "--preflight-only"])

    def test_future_public_marker_cannot_implicitly_enable_custom(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = root / "bl1", root / "bl2"
            first.write_bytes(bl1())
            second.write_bytes(bl2())
            parsed = contract.boot_chain(bl1(), bl2(), True)
            with mock.patch.object(builder, "select_boot_chain", return_value=(first, second)), \
                 mock.patch.object(builder, "run", return_value=json.dumps(parsed)):
                with self.assertRaisesRegex(builder.BuildError, "explicit boot-chain"):
                    builder.validate_contract(root, root, ("O6",), None, False)

    def test_stock_changed_during_snapshot_is_rejected_before_compiling(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = root / "bl1", root / "bl2"
            first.write_bytes(bl1())
            second.write_bytes(bl2())
            oc = contract.boot_chain(bl1(), bl2(), True)
            stock = dict(oc, cpu_oc_abi=0)
            with mock.patch.object(builder, "check_sources"), \
                 mock.patch.object(builder, "check_host"), \
                 mock.patch.object(builder, "snapshot_sources", return_value={}) as snapshot, \
                 mock.patch.object(builder, "snapshot_build_tools", return_value={}), \
                 mock.patch.object(builder, "select_boot_chain", return_value=(first, second)), \
                 mock.patch.object(builder, "run", side_effect=(json.dumps(stock), json.dumps(oc))) as command:
                with self.assertRaisesRegex(builder.BuildError, "explicit boot-chain"):
                    builder.main(["O6", "--build", "--work-dir", str(root / "work")])
                snapshot.assert_called_once()
                self.assertEqual(command.call_count, 2)
                for call in command.call_args_list:
                    self.assertEqual(call.args[0][2], "inspect")

    def test_override_provenance_matches_each_payload(self):
        parsed = contract.boot_chain(bl1(), bl2(), True)
        record = {
            "schema": 1, "build_status": "built", "cpu_oc_abi": 5,
            "pm_source_revision": "a" * 40,
            "pm_patch_sha256": "b" * 64, "pm_sha256": parsed["pm"]["sha256"],
            "bl1_sha256": parsed["bl1"]["sha256"], "bl2_sha256": parsed["bl2"]["sha256"],
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            manifest = path / "cpu-oc-source.json"
            manifest.write_text(json.dumps(record))
            self.assertEqual(builder.check_cpu_oc_provenance(path, parsed), record)
            for key in ("pm_sha256", "bl1_sha256", "bl2_sha256", "pm_source_revision", "pm_patch_sha256", "cpu_oc_abi"):
                changed = dict(record, **{key: "bad"})
                manifest.write_text(json.dumps(changed))
                with self.subTest(key=key), self.assertRaises(builder.BuildError):
                    builder.check_cpu_oc_provenance(path, parsed)
            for changed in (dict(record, build_status="not_run"),
                            dict(record, build_status="source-prepared-not-built"),
                            dict(record, cpu_oc_abi=4),
                            {key: value for key, value in record.items() if key != "cpu_oc_abi"}):
                manifest.write_text(json.dumps(changed))
                with self.subTest(record=changed), self.assertRaises(builder.BuildError):
                    builder.check_cpu_oc_provenance(path, parsed)
            for version in (1, 2, 3, 4):
                manifest.write_text(json.dumps(dict(record, cpu_oc_abi=version)))
                with self.subTest(legacy_abi=version), self.assertRaises(builder.BuildError):
                    builder.check_cpu_oc_provenance(path, dict(parsed, cpu_oc_abi=version))

    def test_o6n_keeps_its_firmware_and_erases_ec_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "src"
            defaults = source / builder.PACKAGE / "Firmwares"
            defaults.mkdir(parents=True)
            (defaults / "ec_fw.bin").write_bytes(b"stock ec")
            board = source / builder.PLATFORMS / "O6N/Firmwares"
            board.mkdir(parents=True)
            (board / "board.bin").write_bytes(b"O6N data")
            (base / "bl1").write_bytes(bl1())
            (base / "bl2").write_bytes(bl2())
            output = base / "out"
            output.mkdir()
            firmware = builder.stage_firmware(source, "O6N", output,
                                               base / "bl1", base / "bl2", ec_size=4096)
            self.assertEqual((firmware / "ec_fw.bin").read_bytes(), b"\xff" * 4096)
            self.assertEqual((firmware / "board.bin").read_bytes(), b"O6N data")
            self.assertEqual((firmware / "bootloader1.img").read_bytes(), bl1())

    def test_isolated_copy_rejects_external_source_symlink(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            source = base / "src"
            source.mkdir()
            outside = base / "outside"
            outside.write_text("external")
            (source / "link").symlink_to(outside)
            with self.assertRaises(builder.BuildError):
                builder.copy_entry(source / "link", base / "dst/link", source)


class ImageTests(unittest.TestCase):
    def fixture(self, directory):
        image = bytearray(b"\xff" * 0x800000)
        layout = {
            "flash_size": "0x800000", "firmware_header_addr": "0x100000",
            "ec_addr": "0x10000", "ec_end": "0xF0000", "ec_file": "ec.bin",
            "image_count": 6, "image_header_groups": [],
        }
        payloads = ((1, 0x188000, 0x100000, bl1()), (2, 0x288000, 0x100000, bl2()),
                    (3, 0x400000, 0x4000, b"memory" * 248),
                    (4, 0x404000, 0x1000, pm_config()),
                    (6, 0x405000, 0x1000, b"se"), (7, 0x406000, 0x1F9000, b"uefi"))
        (directory / "ec.bin").write_bytes(b"\xff" * 4096)
        struct.pack_into("<4I", image, 0x100000, 0x55AA55AA, 1, 6, 0)
        for index, (ident, start, capacity, payload) in enumerate(payloads):
            filename = f"{ident}.bin"
            (directory / filename).write_bytes(payload)
            image[start:start + len(payload)] = payload
            layout["image_header_groups"].append({
                "image_type": ident, "address": start, "size": capacity, "file": filename,
            })
            struct.pack_into("<4I", image, 0x100010 + index * 16,
                             ident, start, len(payload), 0)
        parsed = contract.boot_chain(bl1(), bl2(), True)
        return image, layout, parsed

    def test_full_image_and_short_memory_directory(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            image, layout, parsed = self.fixture(path)
            result = contract.verify_image(image, layout, parsed, "O6N", path)
            self.assertEqual(result["board"], "O6N")
            self.assertEqual(result["cpu_oc_abi"], 5)

    def test_bl1_directory_padding_cannot_defeat_runtime_binding(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            image, layout, parsed = self.fixture(path)
            struct.pack_into("<I", image, 0x100018, len(bl1()) + 4096)
            with self.assertRaisesRegex(contract.ContractError, "runtime identity"):
                contract.verify_image(image, layout, parsed, "O6N", path)

    def test_board_configuration_and_ec_changes_are_detected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)
            image, layout, parsed = self.fixture(path)
            image[0x400000] ^= 1
            with self.assertRaisesRegex(contract.ContractError, "entry 3"):
                contract.verify_image(image, layout, parsed, "O6N", path)
            image[0x400000] ^= 1
            image[0x20000] = 0
            with self.assertRaisesRegex(contract.ContractError, "EC area"):
                contract.verify_image(image, layout, parsed, "O6N", path)


if __name__ == "__main__":
    unittest.main()
