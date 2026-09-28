#!/usr/bin/env python3
"""Prepare the experimental memory dependency and a Vendor-only CDCB request.

These functions operate on supplied bytes and build files, never device files.
They do not establish hardware stability or authenticate firmware signatures.
"""
# SPDX-License-Identifier: BSD-2-Clause-Patent
from __future__ import annotations

import argparse
import json
from pathlib import Path
import struct
import sys
import zlib

from firmware_contract import ContractError, checked_slice, inspect_bl1, sha256
from memory_config import BLOCK_HEADER, QUICK_SIZE, HEADER_SIZE, parse_cdcb

REQUEST = struct.Struct("<4sHHIBBHIH4B7H20sI")
BLOCK_ID = 0x100C
ABI_MARKER = b"RADXA-MEM-OC-ABI1\n\0"
RATES = (0, 4800, 5000, 5200, 5400, 5500, 5600, 5800, 6000, 6200, 6400)
TIMING_LIMITS = ((2, 63), (2, 63), (2, 63), (2, 63), (3, 127), (16, 511), (8, 511))


def vendor_request() -> bytes:
    data = REQUEST.pack(b"MOC1", 1, REQUEST.size, 0, 0, 2, 0, 0, 0,
                        0, 0, 0, 0, *([0] * 7), bytes(20), 0)
    return data[:-4] + struct.pack("<I", zlib.crc32(data[:-4]))


def decode_request(data: bytes) -> dict:
    if len(data) != REQUEST.size:
        raise ContractError("memory request must be exactly 64 bytes")
    (sig, rev, size, seq, profile, fsp, mask, capacity, rate,
     channels, width, ranks, kind, *tail) = REQUEST.unpack(data)
    timing, reserved, crc = tail[:7], tail[7], tail[8]
    if (sig, rev, size, fsp) != (b"MOC1", 1, 64, 2):
        raise ContractError("unsupported memory request format")
    if any(reserved) or crc != zlib.crc32(data[:-4]):
        raise ContractError("memory request CRC or reserved bytes are invalid")
    if profile not in (0, 1) or rate not in RATES:
        raise ContractError("invalid memory profile or data rate")
    for value, (low, high) in zip(timing, TIMING_LIMITS):
        if value and not low <= value <= high:
            raise ContractError("memory timing is outside the supported field range")
    if timing[2] and timing[3] and timing[3] < timing[2]:
        raise ContractError("tRP all-bank must not be shorter than tRP per-bank")
    if timing[5] and timing[6] and timing[5] < timing[6]:
        raise ContractError("tRFC all-bank must not be shorter than tRFC per-bank")
    if profile and (not seq or not mask or mask & (mask - 1) or not capacity
                    or not 0 < channels < 16 or width not in (8, 16)
                    or ranks not in (1, 2) or kind != 1):
        raise ContractError("custom memory request has no valid population binding")
    return {"abi": rev, "sequence": seq, "profile": "Custom" if profile else "Vendor",
            "target_fsp": fsp, "board_mask": mask, "capacity_mib": capacity,
            "requested_rate_mt_s": rate or None, "channel_mask": channels,
            "device_width_bits": width, "ranks_per_channel": ranks,
            "ddr_type_code": kind, "timing_ck": timing, "crc32": crc}


def add_vendor_request(data: bytes) -> bytes:
    """Add one MTUN block, preserving every original block and the quick header."""
    original = parse_cdcb(data)
    blocks = original["blocks"]
    if any(block["guid"] == BLOCK_ID for block in blocks):
        raise ContractError("memory configuration already contains MTUN")
    if len(blocks) >= 255:
        raise ContractError("memory configuration block table is full")
    # Build inputs must be occupied records, not an allocation containing
    # unrelated data that could be silently dropped.
    if len(data) != original["occupied_bytes"]:
        raise ContractError("expected an unpadded build memory configuration")
    count = len(blocks)
    old_table_end = HEADER_SIZE + ((count * 4 + 15) & ~15)
    new_table_end = HEADER_SIZE + (((count + 1) * 4 + 15) & ~15)
    shift = new_table_end - old_table_end
    old_total = len(data) - QUICK_SIZE
    new_total = old_total + shift + 80
    if QUICK_SIZE + new_total > 4096:
        raise ContractError("MTUN would exceed the DDR consumer's 4 KiB buffer")
    result = bytearray(QUICK_SIZE + new_total)
    result[:QUICK_SIZE + HEADER_SIZE] = data[:QUICK_SIZE + HEADER_SIZE]
    # Preserve gaps and padding too, not just the decoded payloads.
    result[QUICK_SIZE + new_table_end:QUICK_SIZE + old_total + shift] = \
        data[QUICK_SIZE + old_table_end:]
    for index, block in enumerate(blocks):
        offset = block["offset"] - QUICK_SIZE + shift
        struct.pack_into("<HH", result, QUICK_SIZE + HEADER_SIZE + index * 4,
                         block["guid"], offset)
    new_offset = old_total + shift
    struct.pack_into("<HH", result, QUICK_SIZE + HEADER_SIZE + count * 4,
                     BLOCK_ID, new_offset)
    block = bytearray(BLOCK_HEADER.pack(b"MTUN", 0, 80, 0, 0, 0xFFFF, 0) + vendor_request())
    block[10] = (~sum(block)) & 255
    result[QUICK_SIZE + new_offset:] = block
    result[QUICK_SIZE + 14] = count + 1
    struct.pack_into("<H", result, QUICK_SIZE + 18, new_total)
    result[QUICK_SIZE + 17] = 0
    result[QUICK_SIZE + 17] = (~sum(result[QUICK_SIZE:QUICK_SIZE + HEADER_SIZE])) & 255
    prepared = parse_cdcb(bytes(result))
    if [entry["sha256"] for entry in prepared["blocks"][:-1]] != \
            [entry["sha256"] for entry in blocks]:
        raise ContractError("adding MTUN changed an existing memory block")
    return bytes(result)


def memory_contract(bl1: bytes, provenance: dict) -> dict:
    """Pin the entire BL1 and require a separately identified SE ABI1 payload."""
    if not isinstance(provenance, dict):
        raise ContractError("memory provenance must be an object")
    parsed = inspect_bl1(bl1)
    se = next(entry for entry in parsed["components"] if entry["id"] == 1)
    pm = next(entry for entry in parsed["components"] if entry["id"] == 2)
    payload = checked_slice(bl1, se["offset"], se["size"], "SE")
    if (provenance.get("schema") != 1 or provenance.get("memory_oc_abi") != 1
            or provenance.get("bl1_sha256") != parsed["sha256"]
            or provenance.get("se_sha256") != se["sha256"]
            or provenance.get("pm_sha256") != pm["sha256"]
            or provenance.get("boards") != ["O6", "O6N"]
            or pm.get("cpu_oc_abi") != 6 or payload.count(ABI_MARKER) != 1
            or payload.count(b"RADXA-MEM-OC-ABI") != 1):
        raise ContractError("memory dependency identity, SE ABI1, or CPU ABI6 mismatch")
    return {"schema": 1, "memory_oc_abi": 1, "bl1_sha256": parsed["sha256"],
            "bl1_size": len(bl1), "se": se, "pm": pm,
            "provenance_sha256": sha256(json.dumps(
                provenance, sort_keys=True, separators=(",", ":")).encode())}


def expected_header(contract: dict) -> str:
    digest = ", ".join(f"0x{value:02x}" for value in bytes.fromhex(contract["bl1_sha256"]))
    return (
        "/* Generated memory dependency identity. SPDX-License-Identifier: BSD-2-Clause-Patent */\n"
        "#ifndef CIX_MEMORY_EXPECTED_H_\n#define CIX_MEMORY_EXPECTED_H_\n"
        "#define CIX_MEMORY_OC_ABI 1U\n"
        f"#define CIX_MEMORY_OC_BL1_SIZE {contract['bl1_size']}U\n"
        f"#define CIX_MEMORY_OC_BL1_SHA256 {{ {digest} }}\n#endif\n"
    )


def regular_input(path: Path) -> bytes:
    if not path.is_file():
        raise ContractError(f"input is not a regular file: {path}")
    return path.read_bytes()


def write_output(path: Path, data: bytes) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ContractError(f"output is not an ordinary build file: {path}")
    path.write_bytes(data)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("check", "prepare"):
        command = commands.add_parser(name)
        command.add_argument("--bl1", type=Path, required=True)
        command.add_argument("--metadata", type=Path, required=True)
        if name == "prepare":
            command.add_argument("--header", type=Path, required=True)
            command.add_argument("--manifest", type=Path, required=True)
    command = commands.add_parser("config")
    command.add_argument("--input", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "config":
            result = add_vendor_request(regular_input(args.input))
            write_output(args.output, result)
            print(f"Prepared Vendor memory request: {len(result)} bytes")
        else:
            result = memory_contract(regular_input(args.bl1),
                                     json.loads(regular_input(args.metadata)))
            if args.command == "prepare":
                write_output(args.header, expected_header(result).encode())
                write_output(args.manifest, (json.dumps(result, indent=2) + "\n").encode())
            print(f"Memory ABI1 dependency: SE {result['se']['sha256'][:12]}, "
                  f"PM ABI6 {result['pm']['sha256'][:12]}")
    except (OSError, ValueError, ContractError) as error:
        print(f"memory tuning: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
