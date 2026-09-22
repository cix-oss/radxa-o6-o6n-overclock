"""Exercise public CPPC initialization with mock SCMI and host UBSan."""
# SPDX-License-Identifier: BSD-2-Clause-Patent

import argparse
import hashlib
import json
from pathlib import Path
import resource
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / 'src/edk2-platforms/Platform/CIX/Sky1/Drivers/ConfigurationManagerDxe/ConfigurationManager.c'
OUTPUT_DIRECTORY = None

prelude = r'''
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t UINT8;
typedef uint32_t UINT32;
typedef uint64_t UINT64;
typedef unsigned long UINTN;
typedef int EFI_STATUS;
typedef void VOID;
#define STATIC static
#define IN
#define OUT
#define CONST const
#define EFIAPI
#define EFI_SUCCESS 0
#define EFI_INVALID_PARAMETER 2
#define EFI_COMPROMISED_DATA 33
#define EFI_NOT_FOUND 14
#define EFI_ERROR(s) ((s) != EFI_SUCCESS)
#define MAX_UINT32 UINT32_MAX
#define DEBUG(x) do {} while (0)
#define BIT0 1U
#define PLAT_CPU_COUNT 12
#define PLAT_PSD_INFO {{0},{1},{2},{3},{4},{5},{6},{7},{8},{9},{10},{11}}
typedef struct { UINT32 Domain; } AML_PSD_INFO;
typedef struct { UINT32 Level, PowerCost, Latency; } SCMI_PERFORMANCE_LEVEL;
typedef struct { UINT32 Attributes, RateLimit, SustainedFreq, SustainedPerfLevel; UINT8 Name[16]; } SCMI_PERFORMANCE_DOMAIN_ATTRIBUTES;
typedef struct Scmi SCMI_PERFORMANCE_PROTOCOL;
struct Scmi { EFI_STATUS (*GetDomainAttributes)(SCMI_PERFORMANCE_PROTOCOL *, UINT32, SCMI_PERFORMANCE_DOMAIN_ATTRIBUTES *); };
typedef struct { EFI_STATUS (*LocateProtocol)(void *, void *, void **); } BOOT_SERVICES;
typedef struct {
  UINT32 HighestPerformanceInteger, NominalPerformanceInteger;
  UINT32 LowestNonlinearPerformanceInteger, LowestPerformanceInteger;
  UINT32 ReferencePerformanceInteger, LowestFrequencyInteger, NominalFrequencyInteger;
} CM_ARM_CPC_INFO;
typedef struct { CM_ARM_CPC_INFO CpuCpcInfo[PLAT_CPU_COUNT]; } REPO;
typedef struct { REPO *PlatRepoInfo; } EDKII_CONFIGURATION_MANAGER_PROTOCOL;
static SCMI_PERFORMANCE_DOMAIN_ATTRIBUTES attributes[PLAT_CPU_COUNT];
static SCMI_PERFORMANCE_LEVEL levels[PLAT_CPU_COUNT][2];
static UINT32 counts[PLAT_CPU_COUNT], core_mask;
static EFI_STATUS attr_status, level_status, locate_status;
static int null_levels, gArmScmiPerformanceProtocolGuid;
static unsigned checks;
static UINT64 DivU64x64Remainder(UINT64 a, UINT64 b, UINT64 *r) { assert(b); *r = a % b; return a / b; }
static UINT64 DivU64x32(UINT64 a, UINT32 b) { assert(b); return a / b; }
static EFI_STATUS get_attrs(SCMI_PERFORMANCE_PROTOCOL *p, UINT32 id, SCMI_PERFORMANCE_DOMAIN_ATTRIBUTES *a) {
  (void)p; assert(id < PLAT_CPU_COUNT); if (!attr_status) *a = attributes[id]; return attr_status;
}
static SCMI_PERFORMANCE_PROTOCOL scmi = {get_attrs};
static EFI_STATUS locate(void *guid, void *registration, void **p) {
  (void)guid; (void)registration; if (!locate_status) *p = &scmi; return locate_status;
}
static BOOT_SERVICES boot = {locate};
static BOOT_SERVICES *gBS = &boot;
static EFI_STATUS GetPerfDomainLevelArra(UINT32 id, SCMI_PERFORMANCE_LEVEL **p, UINT32 *n) {
  assert(id < PLAT_CPU_COUNT); if (!level_status) { *p = null_levels ? NULL : levels[id]; *n = counts[id]; } return level_status;
}
static void GetCpuCoreMask(UINT32 *mask, UINT32 *n) { *mask = core_mask; *n = PLAT_CPU_COUNT; }
'''

tests = r'''
static void reset(UINT32 sf, UINT32 sp, UINT32 low, UINT32 high) {
  memset(attributes, 0, sizeof attributes); memset(levels, 0, sizeof levels);
  attr_status = level_status = locate_status = EFI_SUCCESS; null_levels = 0; core_mask = 0;
  for (unsigned i = 0; i < PLAT_CPU_COUNT; i++) {
    attributes[i].SustainedFreq = sf; attributes[i].SustainedPerfLevel = sp;
    levels[i][0].Level = low; levels[i][1].Level = high; counts[i] = 2;
  }
}
static CM_ARM_CPC_INFO run_one(void) {
  REPO repo; memset(&repo, 0xa5, sizeof repo);
  EDKII_CONFIGURATION_MANAGER_PROTOCOL cm = {&repo};
  assert(InitializeCmArmCpcInfo(&cm) == EFI_SUCCESS);
  for (unsigned i = 1; i < PLAT_CPU_COUNT; i++) assert(!memcmp(&repo.CpuCpcInfo[0], &repo.CpuCpcInfo[i], sizeof(CM_ARM_CPC_INFO)));
  checks++; return repo.CpuCpcInfo[0];
}
static void endpoints(UINT32 ref, UINT32 low_mhz, UINT32 nominal_mhz) {
  CM_ARM_CPC_INFO c = run_one();
  assert(c.HighestPerformanceInteger == levels[0][1].Level);
  assert(c.NominalPerformanceInteger == levels[0][1].Level);
  assert(c.LowestPerformanceInteger == levels[0][0].Level);
  assert(c.LowestNonlinearPerformanceInteger == levels[0][0].Level);
  assert(c.ReferencePerformanceInteger == ref);
  assert(c.LowestFrequencyInteger == low_mhz);
  assert(c.NominalFrequencyInteger == nominal_mhz);
}
static void unchanged(void) {
  CM_ARM_CPC_INFO c = run_one(), expected; memset(&expected, 0xa5, sizeof expected);
  assert(!memcmp(&c, &expected, sizeof c));
}
static UINT32 distance(UINT32 a, UINT32 b) { return a > b ? a-b : b-a; }
int main(void) {
  // Current O6 native/fallback and Custom tables, including the user's settings.
  reset(1500000, 4726, 2520, 8192); endpoints(3150, 800, 2600);
  reset(1500000, 4726, 2520, 9452); endpoints(3150, 800, 3000);
  reset(1500000, 4726, 2520, 8507); endpoints(3150, 800, 2700);
  reset(1500000, 4726, 2520, 10082); endpoints(3150, 800, 3200);
  // ABI5 MHz-level PM remains readable and must no longer retain ref=3150.
  reset(1500000, 1500, 800, 3000); endpoints(1000, 800, 3000);
  reset(1500000, 1500, 800, 3200); endpoints(1000, 800, 3200);
  // LITTLE native OPPs and appended Custom top retain their own scale.
  reset(1400000, 1736, 992, 2232); endpoints(1240, 800, 1800);
  reset(1400000, 1736, 992, 2728); endpoints(1240, 800, 2200);
  reset(1400000, 1736, 992, 2976); endpoints(1240, 800, 2400);
  // Native source standardization uses float division and truncation. Exercise
  // every supported native maximum, and all BIG/MID top selectors. This also
  // catches hard-coded ref=3150 and separately rounded Hz/performance ratios.
  unsigned normalized_cases = 0;
  for (unsigned native_max = 2200; native_max <= 3200; native_max += 100) {
    float granularity = (float)native_max / (1024.0f * 8.0f);
    UINT32 reference = (UINT32)(1000.0f / granularity);
    UINT32 sustained = (UINT32)(1500.0f / granularity);
    UINT32 low = (UINT32)(800.0f / granularity);
    for (unsigned mhz = 800; mhz <= 3200; mhz += 100) {
      UINT32 high = (UINT32)((float)mhz / granularity);
      reset(1500000, sustained, low, high);
      CM_ARM_CPC_INFO c = run_one();
      assert(distance(c.ReferencePerformanceInteger, reference) <= 1);
      assert(distance(c.LowestFrequencyInteger, 800) <= 1);
      assert(distance(c.NominalFrequencyInteger, mhz) <= 1);
      normalized_cases++;
    }
  }
  for (unsigned mhz = 1800; mhz <= 2400; mhz += 10) {
    UINT32 high = (mhz * 2232U + 1799U) / 1800U;
    reset(1400000, 1736, 992, high);
    CM_ARM_CPC_INFO c = run_one();
    assert(c.ReferencePerformanceInteger == 1240);
    assert(distance(c.NominalFrequencyInteger, mhz) <= 1);
    normalized_cases++;
  }
  // Wide products: valid input beyond the old sustained_freq*1000 overflow.
  reset(5000000, 65535, 10486, 65535); endpoints(13107, 800, 5000);
  reset(UINT32_MAX, UINT32_MAX, UINT32_MAX, UINT32_MAX);
  endpoints(1000000, 4294967, 4294967);
  // Failure leaves all fields together at the existing repository defaults.
  reset(0, 1500, 800, 3000); unchanged();
  reset(1500000, 0, 800, 3000); unchanged();
  reset(1500000, 1500, 0, 3000); unchanged();
  reset(1500000, 1500, 3001, 3000); unchanged();
  reset(1, UINT32_MAX, 800, 3000); unchanged(); // Ref overflows UINT32.
  reset(UINT32_MAX, 1, 800, 3000); unchanged(); // Ref rounds down to zero.
  reset(1000000, 1, UINT32_MAX, UINT32_MAX); unchanged(); // MHz overflow.
  reset(1, 1, 1, 1); unchanged(); // MHz below representable nonzero value.
  reset(1500000, 1500, 800, 3000); locate_status = EFI_NOT_FOUND; unchanged();
  reset(1500000, 1500, 800, 3000); attr_status = EFI_NOT_FOUND; unchanged();
  reset(1500000, 1500, 800, 3000); level_status = EFI_NOT_FOUND; unchanged();
  reset(1500000, 1500, 800, 3000); null_levels = 1; unchanged();
  reset(1500000, 1500, 800, 3000); for(unsigned i=0;i<PLAT_CPU_COUNT;i++) counts[i]=0; unchanged();
  reset(1500000, 1500, 800, 3000); core_mask = (1U<<PLAT_CPU_COUNT)-1; unchanged();
  UINT32 a=7,b=8,c=9;
  assert(GetCpcFrequencyData(PLAT_CPU_COUNT, 800, 3000, &a, &b, &c) == EFI_INVALID_PARAMETER);
  assert(a==7 && b==8 && c==9);
  assert(RoundCpcRatio(UINT64_MAX, UINT64_MAX, &a) == EFI_SUCCESS && a==1);
  assert(RoundCpcRatio(3, 2, &a) == EFI_SUCCESS && a==2);
  assert(RoundCpcRatio(0, 0, &a) == EFI_COMPROMISED_DATA && a==2);
  printf("{\"integration_cases\":%u,\"normalized_cases\":%u,\"cpus_per_case\":12,\"status\":\"passed\"}\n", checks, normalized_cases);
  return 0;
}
'''

def run_checks(output):
    raw = SOURCE.read_bytes()
    assert raw.count(b'\n') == raw.count(b'\r\n'), 'CRLF must be preserved'
    source = raw.decode().replace('\r\n', '\n')
    start = source.index('STATIC\nEFI_STATUS\nRoundCpcRatio (')
    end = source.index('/** Initialize the CPU topology information', start)
    production = source[start:end]
    output.mkdir(parents=True, exist_ok=True)
    harness = output / 'harness.c'
    harness.write_text(prelude + production + tests)
    binary = output / 'harness'
    command = ['cc', '-std=c11', '-O2', '-Wall', '-Wextra', '-Werror',
               '-fsanitize=undefined', '-fno-sanitize-recover=all',
               str(harness), '-o', str(binary)]
    subprocess.run(command, check=True)
    result = json.loads(subprocess.check_output([str(binary)], text=True))
    result.update(source=str(SOURCE), sha256=hashlib.sha256(raw).hexdigest(),
                  compiler=subprocess.check_output(['cc', '--version'], text=True).splitlines()[0],
                  command=command, crlf_preserved=True,
                  tested_functions=['RoundCpcRatio', 'GetCpcFrequencyData',
                                    'GetCpuPerfData', 'InitializeCmArmCpcInfo'])
    # Demonstrate that the suite detects the original stale-reference defect.
    mutation = '    CpuCpcInfo[i].ReferencePerformanceInteger       = ReferencePerf;'
    assert production.count(mutation) == 1
    negative = output / 'stale_reference.c'
    negative.write_text(prelude + production.replace(mutation, mutation.replace('= ReferencePerf;', '= 3150;')) + tests)
    negative_binary = output / 'stale_reference'
    negative_command = command[:-3] + [str(negative), '-o', str(negative_binary)]
    subprocess.run(negative_command, check=True)
    failure = subprocess.run([str(negative_binary)], text=True, capture_output=True,
                             preexec_fn=lambda: resource.setrlimit(resource.RLIMIT_CORE, (0, 0)))
    assert failure.returncode != 0 and 'c.ReferencePerformanceInteger == ref' in failure.stderr
    result['stale_reference_negative_control'] = 'failed as expected'
    (output / 'stale-reference-negative-control.log').write_text(failure.stderr)
    (output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


class CppcInitializationTests(unittest.TestCase):
    def test_actual_initializer_and_stale_reference_regression(self):
        if OUTPUT_DIRECTORY is None:
            with tempfile.TemporaryDirectory(prefix='cppc-host-') as temporary:
                result = run_checks(Path(temporary))
        else:
            result = run_checks(OUTPUT_DIRECTORY)
        self.assertEqual(result['status'], 'passed')
        self.assertEqual(result['integration_cases'], 361)
        self.assertEqual(result['normalized_cases'], 336)
        self.assertEqual(result['stale_reference_negative_control'], 'failed as expected')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--output-dir', type=Path, help='Keep host C, executables and JSON evidence here')
    options, remaining = parser.parse_known_args()
    OUTPUT_DIRECTORY = options.output_dir.resolve() if options.output_dir else None
    unittest.main(argv=[__file__] + remaining)
