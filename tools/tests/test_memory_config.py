"""Validate untrusted CDCB layouts and distinguish equal-capacity populations."""
# SPDX-License-Identifier: BSD-2-Clause-Patent

from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import memory_config as memory


def checksum(data, offset):
    data[offset] = 0
    data[offset] = (~sum(data)) & 0xFF


def make_block(guid, mask, payload):
    block = bytearray(memory.BLOCK_HEADER.pack(
        memory.SIGNATURES[guid], 0, 16 + len(payload), 0, 0, mask, 0) + payload)
    checksum(block, 10)
    return block


def make_config(blocks=None):
    if blocks is None:
        blocks = [
            (0x1007, 0xFFFF, b"\xff" * 29),
            (0x1001, 0xFFFF, struct.pack("<H6B", 2750, 15, 1, 255, 255, 255, 0)),
            (0x1001, 1 << 9, struct.pack("<H6B", 2500, 15, 1, 12, 8, 2, 0)),
            (0x1001, 1 << 3, struct.pack("<H6B", 3000, 15, 1, 12, 8, 2, 0)),
            (0x1005, 0xFFFF, b"\x00" * 16),
            (0x1005, 1 << 9, b"\x01" * 16),
        ]
    table_size = (len(blocks) * 4 + 15) & ~15
    content = bytearray(64 + table_size)
    for i, (guid, mask, payload) in enumerate(blocks):
        struct.pack_into("<HH", content, 64 + i * 4, guid, len(content))
        block = make_block(guid, mask, payload)
        content.extend(block)
        content.extend(bytes((-len(block)) % 16))
    struct.pack_into("<4sIHHHBBBBH", content, 0,
                     b"CDCB", 0, 2, 0, 64, len(blocks), 0, 64, 0, len(content))
    header = content[:64]
    checksum(header, 17)
    content[:64] = header
    return bytes(b"\xff" * 32 + content)


class MemoryConfigTests(unittest.TestCase):
    def test_capacity_does_not_select_a_memory_supplier(self):
        report = memory.inspect_memory(make_config(), capacity_gib=48)
        self.assertEqual(len(report["capacity_candidates"]), 2)
        self.assertEqual({p["configured_data_rate_mt_s"] for p in report["capacity_candidates"]},
                         {5000, 6000})
        self.assertNotIn("profile_for_logical_id_hint", report)
        self.assertFalse(report["hardware_readback"])
        self.assertIsNone(report["profiles"][0]["configured_capacity_mib"])
        self.assertIsNone(report["bios_frequency_requests"][0]["data_rate_mt_s"])

    def test_board_override_keeps_population_specific_bus_configuration(self):
        hs = memory.inspect_memory(make_config(), logical_id=9, capacity_gib=48)
        hynix = memory.inspect_memory(make_config(), logical_id=3, capacity_gib=48)
        def bus(report):
            return next(b for b in report["blocks_for_logical_id_hint"] if b["guid"] == 0x1005)
        self.assertEqual(bus(hs)["board_mask"], 1 << 9)
        self.assertEqual(bus(hynix)["board_mask"], 0xFFFF)
        self.assertNotEqual(bus(hs)["sha256"], bus(hynix)["sha256"])

    def test_mismatched_identity_hints_reject(self):
        with self.assertRaisesRegex(memory.ContractError, "hints disagree"):
            memory.inspect_memory(make_config(), logical_id=3, capacity_gib=32)

    def test_corrupt_header_or_payload_reject(self):
        for offset in (40, 100, len(make_config()) - 1):
            with self.subTest(offset=offset):
                data = bytearray(make_config())
                data[offset] ^= 1
                with self.assertRaises(memory.ContractError):
                    memory.inspect_memory(data)

    def test_overlapping_entries_reject(self):
        data = bytearray(make_config())
        # Two otherwise valid CONF entries point at the same complete block.
        data[110:112] = data[106:108]
        with self.assertRaisesRegex(memory.ContractError, "overlapping"):
            memory.inspect_memory(data)

    def test_manual_request_is_separate_from_population_default(self):
        data = bytearray(make_config())
        offset = 32 + struct.unpack_from("<H", data, 98)[0]
        block = data[offset:offset + 45]
        struct.pack_into("<H", block, 16, 2750)
        checksum(block, 10)
        data[offset:offset + 45] = block
        report = memory.inspect_memory(data, logical_id=9)
        self.assertEqual(report["bios_frequency_requests"][0]["data_rate_mt_s"], 5500)
        self.assertEqual(report["profile_for_logical_id_hint"]["configured_data_rate_mt_s"], 5000)

    def test_full_image_uses_directory_and_records_payload_identity(self):
        image = bytearray(memory.FLASH_SIZE)
        struct.pack_into("<4I", image, memory.FLASH_HEADER_OFFSET, 0x55AA55AA, 0, 2, 0)
        directory = memory.FLASH_HEADER_OFFSET + 16
        boot = bytearray(16384)
        boot[:8] = b"CIXBTFF!"
        struct.pack_into("<3I", boot, 8, 1, 0, 1)
        struct.pack_into("<I", boot, 2048, 1)
        struct.pack_into("<2I", boot, 2532, 0x5A, 3)
        for index in range(3):
            struct.pack_into("<6I", boot, 2540 + index * 24,
                             index + 1, 1, 0, (index + 1) * 4096, 4096, 0)
        config = make_config()
        struct.pack_into("<4I", image, directory, 1, 0x188000, len(boot), 0)
        struct.pack_into("<4I", image, directory + 16, 3, 0x402000, len(config), 0)
        image[0x188000:0x188000 + len(boot)] = boot
        image[0x402000:0x402000 + len(config)] = config
        report = memory.inspect_memory(image, capacity_gib=48)
        self.assertEqual(report["memory_config_flash_offset"], 0x402000)
        self.assertEqual(len(report["capacity_candidates"]), 2)
        self.assertEqual(report["boot_components"][0]["sha256"], memory.sha256(boot[4096:8192]))
        self.assertFalse(report["hardware_readback"])

    def test_truncated_tables_and_blocks_reject(self):
        data = make_config()
        for size in (0, 32, 95, 100, len(data) - 1):
            with self.subTest(size=size), self.assertRaises(memory.ContractError):
                memory.inspect_memory(data[:size])

    def test_ambiguous_population_masks_reject(self):
        blocks = [(0x1007, 0xFFFF, b"\xff" * 29),
                  (0x1001, 3, struct.pack("<H6B", 2500, 15, 1, 12, 8, 2, 0)),
                  (0x1001, 1, struct.pack("<H6B", 3000, 15, 1, 12, 8, 2, 0))]
        with self.assertRaisesRegex(memory.ContractError, "ambiguous"):
            memory.inspect_memory(make_config(blocks))

    def test_future_version_and_compression_reject(self):
        data = bytearray(make_config())
        struct.pack_into("<H", data, 40, 3)
        header = data[32:96]
        checksum(header, 17)
        data[32:96] = header
        with self.assertRaisesRegex(memory.ContractError, "version"):
            memory.inspect_memory(data)
        data = bytearray(make_config())
        offset = 32 + struct.unpack_from("<H", data, 98)[0]
        data[offset + 11] = 1
        with self.assertRaisesRegex(memory.ContractError, "compressed"):
            memory.inspect_memory(data)

    def test_directory_bounds_and_overlaps_reject(self):
        image = bytearray(memory.FLASH_SIZE)
        struct.pack_into("<4I", image, memory.FLASH_HEADER_OFFSET, 0x55AA55AA, 0, 2, 0)
        directory = memory.FLASH_HEADER_OFFSET + 16
        struct.pack_into("<4I", image, directory, 1, 0x188000, 4096, 0)
        struct.pack_into("<4I", image, directory + 16, 3, 0x400000, 4096, 0)
        self.assertEqual(memory.flash_entries(image)[3], (0x400000, 4096))
        for start, size in [(0x188000, 100), (memory.FLASH_SIZE - 1, 100),
                            (memory.FLASH_HEADER_OFFSET, 100), (0x400000, 0)]:
            with self.subTest(start=start, size=size):
                struct.pack_into("<4I", image, directory + 16, 3, start, size, 0)
                with self.assertRaises(memory.ContractError):
                    memory.flash_entries(image)


if __name__ == "__main__":
    unittest.main()
