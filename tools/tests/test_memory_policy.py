"""Run the actual memory policy C against both public board configurations."""
# SPDX-License-Identifier: BSD-2-Clause-Patent
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
from memory_tuning import add_vendor_request

PLATFORMS = ROOT / "src/edk2-platforms/Platform/Radxa"
DRIVER = PLATFORMS / "Platforms/CIX/Sky1/Drivers/MemoryTuningDxe"


@unittest.skipUnless(os.environ.get("CIX_RUN_HOST_C_TESTS") == "1",
                     "set CIX_RUN_HOST_C_TESTS=1 to compile the native checks")
class MemoryPolicyTests(unittest.TestCase):
    def test_actual_policy_preserves_both_board_configurations(self):
        with tempfile.TemporaryDirectory(prefix="cix-memory-policy-") as temporary:
            work = Path(temporary)
            policy = work / "memory-policy"
            subprocess.run([
                "cc", "-std=c11", "-Wall", "-Wextra", "-Werror",
                "-fsanitize=undefined", "-fno-sanitize-recover=all",
                "-DMEMORY_TUNING_PORTABLE=1", "-I" + str(DRIVER),
                str(ROOT / "tools/tests/memory_tuning_policy.c"),
                str(DRIVER / "MemoryTuningPolicy.c"), "-o", str(policy),
            ], check=True, capture_output=True)
            for board in ("O6", "O6N"):
                with self.subTest(board=board):
                    source = PLATFORMS / "Orion" / board / "mem_config"
                    output = work / board
                    output.mkdir()
                    generator = output / "config-generator"
                    sources = sorted(source.glob("*.c")) + sorted(source.glob("*/*.c"))
                    self.assertTrue(sources)
                    subprocess.run([
                        "cc", "-I" + str(source / "include"), "-I" + str(source),
                        *map(str, sources), "-o", str(generator),
                    ], check=True, capture_output=True)
                    subprocess.run([str(generator)], cwd=output,
                                   check=True, capture_output=True)
                    config = output / "memory_config.bin"
                    config.write_bytes(add_vendor_request(config.read_bytes()))
                    result = subprocess.run([str(policy), str(config)], check=True,
                                            capture_output=True, text=True)
                    self.assertIn("byte preservation passed", result.stdout)


if __name__ == "__main__":
    unittest.main()
