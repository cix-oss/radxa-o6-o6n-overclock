#!/usr/bin/env python3
"""Inspect the public O6/O6N CDCB memory configuration without changing it.

Input may be memory_config.bin, its padded allocation, or an 8 MiB board image.
Reported rates and geometry are configuration values, not hardware readback.
"""
# SPDX-License-Identifier: BSD-2-Clause-Patent

from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import sys

from firmware_contract import (
    ContractError, FLASH_SIZE, checked_slice, inspect_bl1, reject_overlaps, sha256,
)


QUICK_SIZE = 32
HEADER_SIZE = 64
MAX_CDCB_SIZE = 4096
FLASH_HEADER_OFFSET = 0x100000
BLOCK_HEADER = struct.Struct("<4sIHBBHH")
SIGNATURES = {
    0x1000: b"BDGM", 0x1001: b"CONF", 0x1002: b"FEAT", 0x1003: b"MSPD",
    0x1004: b"BCL4", 0x1005: b"BCL5", 0x1006: b"PADC", 0x1007: b"BSET",
    0x1008: b"DQMP", 0x1009: b"BDMP", 0x100A: b"TLDF", 0x100B: b"TNOP",
    0x100C: b"MTUN",
}


def flash_entries(image: bytes) -> dict[int, tuple[int, int]]:
    """Read the current public O6/O6N full-image directory, not a guessed offset."""
    if len(image) != FLASH_SIZE:
        raise ContractError("full-flash image must be exactly 8 MiB")
    magic, _, count, _ = struct.unpack_from("<4I", image, FLASH_HEADER_OFFSET)
    if magic != 0x55AA55AA or not 1 <= count <= 255:
        raise ContractError("unsupported public O6/O6N flash directory")
    entries = {}
    spans = [(FLASH_HEADER_OFFSET, FLASH_HEADER_OFFSET + 4096, "flash directory")]
    for index in range(count):
        ident, start, size, _ = struct.unpack_from(
            "<4I", image, FLASH_HEADER_OFFSET + 16 + 16 * index)
        if ident in entries:
            raise ContractError("duplicate flash entry type")
        checked_slice(image, start, size, f"flash entry {ident}")
        entries[ident] = (start, size)
        spans.append((start, start + size, f"flash entry {ident}"))
    reject_overlaps(spans)
    if 1 not in entries or 3 not in entries:
        raise ContractError("flash directory is missing BL1 or memory configuration")
    return entries


def parse_cdcb(data: bytes) -> dict:
    """Validate version 2.0 headers, checksums, block bounds and overlaps."""
    header = checked_slice(data, QUICK_SIZE, HEADER_SIZE, "CDCB header")
    magic, _, major, minor, table, count, _, hsize, _, total = struct.unpack_from(
        "<4sIHHHBBBBH", header)
    if magic != b"CDCB" or (major, minor) != (2, 0):
        raise ContractError("unsupported CDCB signature or version (2.0 required)")
    if hsize != HEADER_SIZE or table != HEADER_SIZE or count == 0:
        raise ContractError("unsupported CDCB header or block table layout")
    if sum(header) & 0xFF != 0xFF:
        raise ContractError("CDCB header checksum mismatch")
    if total + QUICK_SIZE > MAX_CDCB_SIZE:
        raise ContractError("CDCB exceeds the 4 KiB consumer buffer")
    content = checked_slice(data, QUICK_SIZE, total, "CDCB content")
    table_end = table + ((count * 4 + 15) & ~15)
    checked_slice(content, table, table_end - table, "CDCB block table")
    blocks = []
    spans = [(0, table_end, "CDCB header and block table")]
    for index in range(count):
        guid, offset = struct.unpack_from("<HH", content, table + index * 4)
        raw_header = checked_slice(content, offset, BLOCK_HEADER.size, "block header")
        signature, _, size, _, compression, mask, _ = BLOCK_HEADER.unpack(raw_header)
        if offset % 16 or size < BLOCK_HEADER.size or mask == 0:
            raise ContractError("invalid CDCB block alignment, size or board mask")
        if compression:
            raise ContractError("compressed CDCB blocks are not supported")
        block = checked_slice(content, offset, size, "CDCB block")
        padded_size = (size + 15) & ~15
        checked_slice(content, offset, padded_size, "aligned CDCB block")
        if sum(block) & 0xFF != 0xFF:
            raise ContractError(f"CDCB block 0x{guid:04x} checksum mismatch")
        if guid in SIGNATURES and signature != SIGNATURES[guid]:
            raise ContractError(f"CDCB block 0x{guid:04x} signature mismatch")
        if guid == 0x1001 and size != 24:
            raise ContractError("unsupported CONF block size")
        if guid == 0x1007 and size != 45:
            raise ContractError("unsupported BSET block size")
        if guid == 0x100C and (size != 80 or mask != 0xFFFF):
            raise ContractError("unsupported MTUN block size or mask")
        spans.append((offset, offset + padded_size, f"CDCB block {index}"))
        blocks.append({
            "guid": guid, "signature": signature.decode("ascii", errors="replace"),
            "offset": QUICK_SIZE + offset, "size": size,
            "board_mask": mask, "sha256": sha256(block),
        })
    reject_overlaps(spans)
    for guid in (0x1001, 0x1007):
        if not any(block["guid"] == guid for block in blocks):
            raise ContractError(f"required block 0x{guid:04x} is missing")
    # Ambiguous masks must not silently select a different population.
    for guid in {block["guid"] for block in blocks}:
        matching = [block for block in blocks if block["guid"] == guid]
        if sum(block["board_mask"] == 0xFFFF for block in matching) > 1:
            raise ContractError(f"duplicate default block 0x{guid:04x}")
        seen = 0
        for block in matching:
            mask = block["board_mask"]
            if mask != 0xFFFF:
                if seen & mask:
                    raise ContractError(f"ambiguous board masks for block 0x{guid:04x}")
                seen |= mask
    return {
        "version": [major, minor], "occupied_bytes": QUICK_SIZE + total,
        "sha256": sha256(data[:QUICK_SIZE + total]), "blocks": blocks,
        "quick_config": {
            "board_id_method": data[0], "recorded_board_id": data[1],
            "board_id_changed": data[2], "recorded_board_revision": data[17],
            "board_revision_changed": data[18],
        },
    }


def choose_block(blocks: list[dict], guid: int, logical_id: int) -> dict | None:
    default = None
    for block in blocks:
        if block["guid"] != guid:
            continue
        if block["board_mask"] == 0xFFFF:
            default = block
        elif block["board_mask"] & (1 << logical_id):
            return block
    return default


def decode_geometry(data: bytes, block: dict) -> dict:
    freq, channels, kind, density, width, ranks, _ = struct.unpack_from(
        "<H6B", data, block["offset"] + BLOCK_HEADER.size)
    capacity = None
    if (0 < channels <= 0xF and density not in (0, 0xFF)
            and width in (8, 16, 32) and 1 <= ranks <= 4):
        capacity = density * 128 * (32 // width) * ranks * channels.bit_count()
    return {
        "config_offset": block["offset"], "board_mask": block["board_mask"],
        "logical_board_ids": ([] if block["board_mask"] == 0xFFFF else
                              [i for i in range(16) if block["board_mask"] & (1 << i)]),
        "configured_memfreq_value": freq, "configured_data_rate_mt_s": freq * 2,
        "ddr_type_code": kind, "channel_mask": channels,
        "device_density_gbit": density, "device_width_bits": width,
        "ranks_per_channel": ranks, "configured_capacity_mib": capacity,
    }


def inspect_memory(data: bytes, logical_id: int | None = None,
                   capacity_gib: int | None = None) -> dict:
    if logical_id is not None and not 0 <= logical_id <= 15:
        raise ContractError("logical board ID must be in 0..15")
    if capacity_gib is not None and capacity_gib <= 0:
        raise ContractError("capacity hint must be positive")
    report = {"schema": 1, "input_sha256": sha256(data), "input_size": len(data),
              "hardware_readback": False, "logical_board_id_hint": logical_id,
              "capacity_gib_hint": capacity_gib}
    if len(data) == FLASH_SIZE:
        entries = flash_entries(data)
        start, size = entries[1]
        bl1 = inspect_bl1(checked_slice(data, start, size, "BL1"))
        report["boot_components"] = bl1["components"]
        start, size = entries[3]
        report["memory_config_flash_offset"] = start
        data = checked_slice(data, start, size, "memory configuration")
    elif not QUICK_SIZE + HEADER_SIZE <= len(data) <= 0x4000:
        raise ContractError("expected a CDCB input (at most 16 KiB) or an 8 MiB image")
    cdcb = parse_cdcb(data)
    report["cdcb"] = cdcb
    profiles = [decode_geometry(data, block) for block in cdcb["blocks"]
                if block["guid"] == 0x1001]
    report["profiles"] = profiles
    report["capacity_candidates"] = [profile for profile in profiles
        if profile["logical_board_ids"] and capacity_gib is not None
        and profile["configured_capacity_mib"] == capacity_gib * 1024]
    report["bios_frequency_requests"] = []
    for block in cdcb["blocks"]:
        if block["guid"] == 0x1007:
            value, = struct.unpack_from("<H", data, block["offset"] + BLOCK_HEADER.size)
            report["bios_frequency_requests"].append({
                "board_mask": block["board_mask"], "memfreq_value": value,
                "data_rate_mt_s": None if value == 0xFFFF else value * 2,
                "mode": "Auto" if value == 0xFFFF else "Manual",
            })
    if logical_id is not None:
        chosen = [choose_block(cdcb["blocks"], guid, logical_id)
                  for guid in sorted({b["guid"] for b in cdcb["blocks"]})]
        report["blocks_for_logical_id_hint"] = [block for block in chosen if block]
        config = choose_block(cdcb["blocks"], 0x1001, logical_id)
        if config is None:
            raise ContractError("no configuration for the supplied logical board ID")
        report["profile_for_logical_id_hint"] = decode_geometry(data, config)
        if (capacity_gib is not None and report["profile_for_logical_id_hint"]
                ["configured_capacity_mib"] != capacity_gib * 1024):
            raise ContractError("logical board ID and capacity hints disagree")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--logical-board-id", type=int,
                        help="post-mapping CDCB ID, not a raw GPIO/PCB ID; never inferred")
    parser.add_argument("--capacity-gib", type=int,
                        help="list matching configured populations; does not select one")
    args = parser.parse_args()
    try:
        report = inspect_memory(args.input.read_bytes(), args.logical_board_id, args.capacity_gib)
    except (OSError, ContractError) as error:
        print(f"memory configuration: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
