"""Check memory payload binding and byte preservation during CDCB extension."""
# SPDX-License-Identifier: BSD-2-Clause-Patent
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_tuning as tuning
import memory_config as memory
import build_cpu_oc as builder
from test_memory_config import make_config
from test_cpu_oc_tools import bl1


def sealed(data):
    return bytes(data[:60]) + struct.pack("<I", zlib.crc32(data[:60]))


def custom_request():
    data = bytearray(tuning.vendor_request())
    struct.pack_into("<IBBHIH4B", data, 8, 1, 1, 2, 1 << 9, 49152,
                     5500, 15, 8, 2, 1)
    return sealed(data)


def dependency(with_marker=True, marker_offset=4096):
    data = bytearray(bl1())
    if with_marker:
        data[marker_offset:marker_offset + len(tuning.ABI_MARKER)] = tuning.ABI_MARKER
    parsed = tuning.inspect_bl1(bytes(data))
    se, pm = parsed["components"][:2]
    metadata = {"schema": 1, "memory_oc_abi": 1, "boards": ["O6", "O6N"],
                "bl1_sha256": parsed["sha256"], "se_sha256": se["sha256"],
                "pm_sha256": pm["sha256"]}
    return bytes(data), metadata


class MemoryTuningTests(unittest.TestCase):
    def test_vendor_record_is_off_and_auto(self):
        result = tuning.decode_request(tuning.vendor_request())
        self.assertEqual(result["profile"], "Vendor")
        self.assertIsNone(result["requested_rate_mt_s"])
        self.assertEqual(result["timing_ck"], [0] * 7)
        self.assertEqual(len(tuning.vendor_request()), 64)

    def test_custom_record_binds_population(self):
        data = custom_request()
        self.assertEqual(tuning.decode_request(data)["board_mask"], 512)
        for offset, value in ((16, 0), (14, 3), (22, 0), (23, 32), (24, 0)):
            bad = bytearray(data)
            if offset in (14, 16):
                struct.pack_into("<I" if offset == 16 else "<H", bad, offset, value)
            else:
                bad[offset] = value
            with self.subTest(offset=offset), self.assertRaises(memory.ContractError):
                tuning.decode_request(sealed(bad))

    def test_crc_and_future_records_rejected(self):
        original = custom_request()
        for offset in (0, 4, 12, 20, 30, 40, 63):
            bad = bytearray(original)
            bad[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(memory.ContractError):
                tuning.decode_request(bytes(bad))
        bad = bytearray(original)
        bad[4] = 2
        with self.assertRaises(memory.ContractError):
            tuning.decode_request(sealed(bad))
        bad = bytearray(original)
        bad[40] = 1
        with self.assertRaises(memory.ContractError):
            tuning.decode_request(sealed(bad))

    def test_contradictory_and_out_of_range_timings_rejected(self):
        for index, value in ((0, 1), (0, 64), (4, 128), (5, 512), (6, 7)):
            bad = bytearray(custom_request())
            struct.pack_into("<H", bad, 26 + 2 * index, value)
            with self.subTest(index=index, value=value), self.assertRaises(memory.ContractError):
                tuning.decode_request(sealed(bad))
        for timings in ((10, 10, 12, 10, 30, 180, 80),
                        (10, 10, 10, 12, 30, 80, 180)):
            bad = bytearray(custom_request())
            struct.pack_into("<7H", bad, 26, *timings)
            with self.assertRaises(memory.ContractError):
                tuning.decode_request(sealed(bad))

    def test_all_original_blocks_and_population_choices_survive(self):
        original = make_config()
        extended = tuning.add_vendor_request(original)
        before, after = memory.parse_cdcb(original), memory.parse_cdcb(extended)
        self.assertEqual(original[:32], extended[:32])
        self.assertEqual([b["sha256"] for b in before["blocks"]],
                         [b["sha256"] for b in after["blocks"][:-1]])
        for logical_id in (3, 9):
            a = memory.inspect_memory(original, logical_id, 48)
            b = memory.inspect_memory(extended, logical_id, 48)
            self.assertEqual(a["profile_for_logical_id_hint"],
                             b["profile_for_logical_id_hint"])
        block = after["blocks"][-1]
        record = extended[block["offset"] + 16:block["offset"] + block["size"]]
        self.assertEqual(tuning.decode_request(record)["profile"], "Vendor")

    def test_table_growth_moves_blocks_without_changing_them(self):
        raw = make_config()
        parsed = memory.parse_cdcb(raw)
        blocks = [(b["guid"], b["board_mask"], raw[b["offset"] + 16:b["offset"] + b["size"]])
                  for b in parsed["blocks"]]
        blocks += [(0x1008, 0xFFFF, b"routing"), (0x100A, 0xFFFF, b"trace")]
        original = make_config(blocks)
        before = memory.parse_cdcb(original)
        after = memory.parse_cdcb(tuning.add_vendor_request(original))
        for a, b in zip(before["blocks"], after["blocks"]):
            self.assertEqual(b["offset"], a["offset"] + 16)
            self.assertEqual(a["sha256"], b["sha256"])

    def test_rejects_duplicate_extension_padding_and_buffer_overflow(self):
        with self.assertRaises(memory.ContractError):
            tuning.add_vendor_request(tuning.add_vendor_request(make_config()))
        with self.assertRaises(memory.ContractError):
            tuning.add_vendor_request(make_config() + bytes(16))
        raw = make_config()
        parsed = memory.parse_cdcb(raw)
        blocks = [(b["guid"], b["board_mask"], raw[b["offset"] + 16:b["offset"] + b["size"]])
                  for b in parsed["blocks"]]
        original = make_config(blocks + [(0x1008, 0xFFFF, bytes(3680))])
        self.assertLessEqual(len(original), 4096)
        with self.assertRaisesRegex(memory.ContractError, "4 KiB"):
            tuning.add_vendor_request(original)

    def test_dependency_requires_marker_in_se_not_pm(self):
        for marker, offset in ((False, 4096), (True, 9000)):
            data, metadata = dependency(marker, offset)
            with self.assertRaises(memory.ContractError):
                tuning.memory_contract(data, metadata)
        data, metadata = dependency()
        result = tuning.memory_contract(data, metadata)
        self.assertEqual(result["memory_oc_abi"], 1)
        self.assertEqual(result["pm"]["cpu_oc_abi"], 6)
        self.assertIn("#define CIX_MEMORY_OC_ABI 1U", tuning.expected_header(result))

    def test_dependency_requires_matching_hashes_and_one_abi(self):
        data, metadata = dependency()
        for field in ("bl1_sha256", "se_sha256", "pm_sha256"):
            with self.subTest(field=field), self.assertRaises(memory.ContractError):
                tuning.memory_contract(data, {**metadata, field: "0" * 64})
        with self.assertRaises(memory.ContractError):
            tuning.memory_contract(data, [])
        mixed = bytearray(data)
        extra = b"RADXA-MEM-OC-ABI2\0\0"
        mixed[4200:4200 + len(extra)] = extra
        parsed = tuning.inspect_bl1(mixed)
        metadata.update(bl1_sha256=parsed["sha256"],
                        se_sha256=parsed["components"][0]["sha256"])
        with self.assertRaises(memory.ContractError):
            tuning.memory_contract(bytes(mixed), metadata)

    def test_build_mode_must_match_the_memory_dependency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data, metadata = dependency()
            (root / "bootloader1.img").write_bytes(data)
            (root / "memory-oc-source.json").write_text(json.dumps(metadata))
            header = root / "src" / builder.MEMORY_HEADER
            header.parent.mkdir(parents=True)
            header.write_text("/* Generated during the build. */\n")
            with self.assertRaises(builder.BuildError):
                builder.validate_memory_mode(root, root / "src", ["O6"], root, False)
            builder.validate_memory_mode(root, root / "src", ["O6"], root, True)
            with self.assertRaises(builder.BuildError):
                builder.validate_memory_mode(root, root / "src", ["O6"], None, True)


if __name__ == "__main__":
    unittest.main()
