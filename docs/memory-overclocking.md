# O6/O6N memory tuning

Release 1.3.3 adds experimental memory tuning above the public 1.3.2 CPU
firmware baseline and retains its CPU tuning support.
The initial test systems are O6 with 32 GiB and O6N with 48 GiB. The boards
share one test SSD and will be tested separately. O6N's UEFI `Memory Chip`
field identifies the Hive 48 GiB population (raw value 9). O6's NOR backup
and new boot result identify logical ID 4, `RS600_32G`, with x16 devices.
Exact chip part numbers and both physical PCB revisions remain unidentified.

**This is experimental firmware; memory stability remains unverified.**
The 1.3.3 images include the frequency/timing menu, configuration writer and
matching early-boot consumer in each complete board image. The build option
defaults to off; release builds explicitly enable `--memory-tuning`.
The exact rebuilt 1.3.3 images have not been hardware-tested.

On 2026-09-28, the preceding `1.3.2+memoc1` prototype images on both boards
passed complete flash readback and Vendor boots
after user-confirmed physical power cycles: O6 at 5500 MT/s and O6N at
5000 MT/s. On O6N, the user also selected Custom 5400 MT/s with Auto timings;
one reboot completed with a matching CRC-valid training report. O6 later
passed one user-configured Custom 6400 MT/s boot with Auto timings. Memory
stability on both boards remains untested.
The existing
[overclocking risk warning](../README.md) applies.

## Existing release behavior and populations

The existing Memory Configuration menu uses `PlatformSetupVar.MemFreq`.
It includes Auto and rates through 6400 MT/s. The value written to the memory
configuration is half the displayed transfer rate: 3000 means 6000 MT/s.
These are configuration choices, not established stable operating points.
LPDDR5 command-clock cycles and these stored frequency values are different
units; the experimental timing menu below uses CK cycles explicitly.

`MemConfigUpdateDxe` is currently supplied as a binary in `edk2-non-osi`.
The separate Radxa `FirmwareConfigUpdateDxe` source is not a complete memory
settings writer. Adding manual timing fields only to the setup screen would
not make the boot firmware consume them.

The memory configuration contains a default geometry plus population-specific
geometry, bus settings, PHY pads, training settings, and routing information.
Capacity alone does not select a population. The public 1.3.2 configurations
include these candidates for the initial test systems:

| Board | Logical CDCB ID | Source population | Configured rate |
| --- | ---: | --- | ---: |
| O6, 32 GiB | 4 | `RS600_32G` | 5500 MT/s |
| O6, 32 GiB | 7 | `RS600_32G_LO` | 4800 MT/s |
| O6, 32 GiB | 9 | `RS600_32G_x8_HS` | 5500 MT/s |
| O6, 32 GiB | 12 | `RS600_32G_HYNIX` | 6000 MT/s |
| O6N, 48 GiB | 9 | `RS602_48G_HS` | 5000 MT/s |
| O6N, 48 GiB | 3 | `RS602_48G_HYNIX` | 6000 MT/s |

O6 uses a board-ID mapping table; logical CDCB IDs must not be confused with
raw GPIO or EC IDs. O6N's HS 48 GiB population also has its own LPDDR5 bus
configuration. Preserve that block when changing a frequency request.

The initial O6's read-only NOR backup recorded fixed raw board ID `0x9c`.
Its actual `BDMP` table maps that ID to logical ID 4, `RS600_32G`, with
16 Gbit x16 devices, two ranks per channel, four channels and 32 GiB total.
The experimental boot report independently confirmed logical mask `0x0010`
and the 5500 MT/s Vendor default. This identifies the selected configuration,
not an exact chip manufacturer or part number.

Find the population in UEFI **System Information -> Memory Chip**, including
the `Raw Value`. For the initial O6N, the user reported `12 GiB (Hive)` with
raw value 9. That selects `RS602_48G_HS`: x8 devices, two ranks per channel,
four channels and 48 GiB total. A separate read-only boot-result capture
reports 48 GiB and a trained maximum of 5000 MT/s. This identifies the
firmware-selected population; it does not establish an exact chip part number
or the hashes of the currently installed firmware.

Preserve this population's dedicated bus (`BCL5`), PHY (`PADC`) and trace
compensation (`TLDF`) blocks together with its geometry (`CONF`). Its x8 PHY
table selects different CA-drive and FFE settings above 5000 MT/s. A higher
rate therefore also changes the selected PHY entry; a table extending to
6400 MT/s is not proof that the populated board is stable at that rate.

## Voltage control depends on the board circuit

The reviewed O6 schematic (file V1.20, sheet revision V1.2) has an NB705GQ-Z
at U119 for the memory supplies and an MPQ8626GD-Z at U75 for the DDR PHY
supply. The O6N V1.11 schematic has an SC6301DFHR at U119 and SY83088ARHC
at U75. These sheets show enable, feedback, mode and power-good connections;
they do not show a software programming interface for those supplies.

Consequently, arbitrary VDD1/VDD2/VDDQ or DDR PHY voltage entry cannot be
implemented by removing a software check on these reviewed circuits. Vref
training values are reference levels, not programmable supply voltages.
The SoC power rail is also not interchangeable with the DRAM supply rails.
Other PCB revisions or substituted components require their own review.

Sources: [Radxa hardware downloads](https://docs.radxa.com/en/orion/download),
[O6 schematic, sheets 43–44](https://dl.radxa.com/orion/o6/hw/radxa_orion_o6_v1.20_schematic.pdf#page=43),
[O6N schematic, sheets 40–41](https://dl.radxa.com/orion/o6n/docs/hw/radxa_orion_o6n_schematic_v1.11.pdf#page=40).

## Inspect the actual configuration inputs

Run from the repository root, using an existing release image or flash dump:

```sh
python3 tools/memory_config.py /path/to/o6-1.3.2.bin --capacity-gib 32
python3 tools/memory_config.py /path/to/o6n-1.3.2.bin --capacity-gib 48
```

The inspector also accepts `memory_config.bin` or its padded allocation.
It checks the CDCB 2.0 header, table bounds, alignment, checksums, overlapping
blocks and ambiguous population masks. Full images must use the current
public 8 MiB layout and the directory at `0x100000`; alternate layouts are
rejected. It records SE/PM/PBL identities from the actual BL1 directory.
These structural checks do not authenticate firmware signatures.

The capacity option lists candidates. It never assumes a manufacturer or
detects the board from the image filename. Once a logical ID is independently
known, inspect its selected blocks explicitly:

```sh
# Example only: logical ID 9 is the O6N HS 48 GiB population.
python3 tools/memory_config.py /path/to/o6n-1.3.2.bin \
  --capacity-gib 48 --logical-board-id 9
```

The report distinguishes the per-population default rate from the BIOS
frequency request. Neither is a measurement of the running memory clock.
The firmware's memory handoff reports the trained maximum, while runtime
frequency switching can select lower states. BIOS/DMI version strings do not
identify every flashed payload, and DMI board version is not a PCB inspection.

The tool only reads the supplied file and prints JSON. It does not open device
files, change firmware variables, patch images, compile code, or flash a board.

```sh
python3 -m unittest discover -s tools/tests -p test_memory_config.py -v
```

## Experimental controls

The separate **Memory tuning** form has Vendor and Custom profiles. Factory
images contain a Vendor request. Custom offers Auto, 4800, 5000, 5200, 5400,
5500, 5600, 5800, 6000, 6200 and 6400 MT/s. The consumer rejects requests
above the controller fuse limit. These menu choices are software bounds, not
verified operating points or chip ratings.

Seven optional primary timings apply at FSP2, the highest frequency state:

| Timing | Manual range, CK cycles |
| --- | ---: |
| tRCD read / write | 2–63 each |
| tRP per-bank / all-bank | 2–63 each |
| tRAS | 3–127 |
| tRFC all-bank | 16–511 |
| tRFC per-bank | 8–511 |

Zero means Auto. The writer validates ranges and timing relationships; the
consumer checks them again after resolving Auto values. Overrides precede
dependent PI/PHY timing generation. The affected tRC, tMRRI, tDAL and
self-refresh-exit values are recomputed. RL/WL and lower-state timings retain
the vendor derivation so their mode-register encodings remain coherent.
These bounds are implementation limits, not guaranteed stable values.

The voltage row reports **unavailable**, consistent with the reviewed circuits.
There are no editable supply-voltage or substitute Vref controls.

## Request ownership and boot reporting

`RadxaMemoryTuning` is an independent HII variable. The public
`MemoryTuningDxe` replaces the binary memory writer only when the experimental
build option is enabled. Its versioned request occupies one global `MTUN`
CDCB block (ID `0x100C`, 80 bytes including the block header). The 64-byte
request includes a sequence, CRC32, profile, rate, timings and population
binding: logical board mask, capacity, channel mask, device width, ranks and
DDR type. The shared layout is in `RadxaMemTuning.h`.

The build tool appends an initial Vendor block, preserves every original
block byte and the quick header, and respects the consumer's 4 KiB buffer.
The runtime writer only replaces an existing compatible request and its block
checksum. It reads back and compares the complete 4 KiB configuration sector
after a write, using the public firmware-update protocol v3 access size. A
failed or interrupted flash write is not reported as a successful
save and cannot be assumed to have rolled back.

The matching experimental consumer owns memory settings and ignores legacy
BSET overrides, including earlier advanced memory-menu settings. Vendor uses
the original per-population configuration blocks. Do not pair that consumer
with the old menu or use it as a drop-in SE replacement for a stock build.

The new consumer publishes a separate 128-byte CRC-protected result at
`0x83C00040`, preserving the existing CDOB handoff at `0x83C00000`. It reports
the request identity, population, boot state, trained maximum rate and primary
timings. The UI distinguishes a saved request from a completed boot. A trained
result does not establish stress-test stability or the current runtime clock.

The UEFI writer requires the exact build-pinned BL1 hash, memory ABI1 and a
valid boot result before enabling Custom. CPU PM ABI6 alone is insufficient.
Build preflight rejects a memory-capable SE with the old configuration mode,
and rejects enabling the new mode with a stock SE.

## Recovery implementation and remaining validation

The experimental SE code starts a hardware watchdog only for an accepted Custom
request, before training parameters are programmed. Its 30-second load uses
the existing two-stage watchdog implementation; the reset interval and reset
reason must be measured on hardware. Returned training failures also trigger
a reset after three unsuccessful attempts. The guard spans DDR and CI700
initialization, then yields to the existing runtime watchdog handling.

On a watchdog or external-reset boot, the consumer uses Vendor parameters.
Once UEFI is reached, it clears the saved Custom request only if its sequence
and CRC still match the rejected/recovery boot result. This is **unverified
recovery code**, not a guaranteed recovery mechanism. In particular, power
loss followed by a cold boot is not a persistent one-shot rollback, failures
outside the guarded path remain possible, and an interrupted CDCB write can
prevent the base configuration from loading.

Both initial Vendor boots and actual rate/timing reports passed on 2026-09-28.
Repeated request persistence, rejection paths, watchdog recovery, CPU ABI6 hardware
behavior and suspend/resume remain to be validated. Capture installed payload
identities and CPU settings for each test. Each board has passed one Custom
frequency boot; test further changes separately.

The flashed O6N image and its entire 8 MiB readback have SHA-256
`21aca49326586ca89165350239169b75febc84125a35301bd0cd75aeb4e65089`.
After a physical power cycle, Debian reported `1.3.2+memoc1`. The CRC-valid
ABI1 result reported Vendor, no rejection reason, logical board mask `0x0200`,
49152 MiB, four channels, x8 devices, two ranks, and 5000 MT/s. Requested rate
and all seven requested timings remained Auto. FSP2 applied timings were
12/12/12/14/27/175/88 CK in the order tRCD read/write, tRP per-bank/all-bank,
tRAS, tRFC all-bank/per-bank. The legacy CDOB and SMBIOS reports agreed on
capacity and trained maximum rate. This is a default boot check.

Later that day, the user selected Custom 5400 MT/s and rebooted. The CRC-valid
ABI1 report accepted sequence 2 with no rejection or fallback, retained the
full population geometry and capacity, and reported both requested and trained
maximum rates of 5400 MT/s. CDOB and all four SMBIOS memory records agreed.
Requested timings remained Auto; applied timings were 13/13/13/15/29/189/95 CK
in the same order as above. This establishes one successful Custom boot and
an 8% increase in trained maximum rate, not an 8% application performance gain.
Memory stability, repeated cold boots and manual timing overrides remain
untested.

The O6 image also passed a complete 8 MiB readback, with SHA-256
`b19bc53b1854cf18f3aae75d403b9c0ed7266f0e5cb17c0685f5ffa3a5af3bac`.
After a user-confirmed physical power cycle, Debian reported `1.3.2+memoc1`.
Its CRC-valid ABI1 Vendor result reported no rejection or fallback, logical
mask `0x0010`, 32768 MiB, four channels, x16 devices, two ranks and 5500 MT/s.
Rate and timing requests remained Auto; applied FSP2 timings were
13/13/13/15/29/193/97 CK in the order above. CDOB and all four SMBIOS records
agreed on capacity and rate. The original boot order was preserved, the
temporary flash entry was absent, and the selected kernel error filter found
no matching lines. No memory stress workload was run.

The user then configured O6 Custom 6400 MT/s and rebooted. A new CRC-valid
ABI1 result accepted sequence 2 without rejection or fallback, reported
requested and trained rates of 6400 MT/s, and retained the full 32 GiB
population. CDOB and all four SMBIOS records agreed. Timing requests remained
Auto, with applied FSP2 timings 15/15/15/17/34/224/112 CK in the order above.
The trained maximum is approximately 16.36% above the 5500 MT/s default;
application performance and memory stability were not measured. The selected
kernel error filter found no matching lines during this boot check.

## Build interface

Normal CPU builds keep memory tuning disabled. The combined release requires
the matching signed dependency directory containing BL1, BL2,
`cpu-oc-source.json` and `memory-oc-source.json`. Version 1.3.3 provides
`cix-sky1-cpu-abi6-memory-abi1-1.3.3.tar.xz` alongside both board images;
see the [downloads and build instructions](../README.md).

```sh
# Read-only preflight once the matching dependency exists.
python3 tools/build_cpu_oc.py all --memory-tuning \
  --boot-chain /path/to/memory-dependency --preflight-only

# Compile/package only after the local build authorization is satisfied.
python3 tools/build_cpu_oc.py all --memory-tuning \
  --boot-chain /path/to/memory-dependency \
  --work-dir /path/to/local-memory-build --build
```

The builder snapshots inputs, generates the exact expected BL1 identity,
enables `RADXA_MEM_OC_SUPPORT`, and appends Vendor requests to each board's
own configuration. Firmware uses the version from `debian/changelog`,
which is `1.3.3` for this release. The historical prototype used `+memoc1`.
Private
SE/DDR implementation, patches, signing inputs and
build records remain local. The retained CPU PM, PBL and BL2 must be verified
byte-for-byte when assembling the new BL1 dependency.

The public Python/tools suite covers format corruption,
dependency mismatches and preservation of existing CDCB blocks. Offline
extension of the actual 1.3.2 inputs preserves O6's 24 and O6N's 21 original
blocks and all logical-population selections. Their occupied configuration
sizes become 1584 and 1568 bytes respectively. The new native C policy test is
in `tools/tests/memory_tuning_policy.c`; it passed against both real CDCB
fixtures with AddressSanitizer and UndefinedBehaviorSanitizer enabled.
Community CI also compiles that policy and generates both public board
configurations for a UBSan run when `CIX_RUN_HOST_C_TESTS=1` is set.
Sixteen local cases compiled from the actual early-boot consumer also passed
with mocked hardware services, covering population/rate rejection, timing
derivation, reset fallback and retry handling. Those mocks do not validate
physical watchdog timing, memory training or recovery.

Each full 8 MiB image passed independent BL1 RSA-PSS and BL3 certificate/digest
checks. Artifact inspection bound the delivered UEFI modules to the actual
compressed firmware volumes, decoded the memory and retained CPU ABI6 menus,
checked the runtime CPU capability query, verified the exact BL1 digest in the
memory writer, and confirmed removal of the legacy memory writer. Factory
requests select Vendor with Auto timings. All original memory blocks and
population selections, CPU PM/PBL bytes and board payloads are preserved;
the regenerated PM configuration differs only in its timestamp and valid
checksums. These checks establish artifact consistency, not board stability.

## Baseline

Public source identities used for this investigation:

- Firmware wrapper: `b6ed51f0fda12d2a60242823e9597a404524b697` (1.3.2).
- `edk2-platforms`: `b5665f47cf2d8e620233c884aeb2eea01bef5590`.
- `edk2`: `39c4bb1d67e842b64cfab75a674b9620c2c45acd`.
- `edk2-non-osi`: `f567460e07814038c6ff1aef79cc5c71ba237b83`.

Internal reference identities and source analysis are recorded locally. A
reference checkout is not proof of the source used to build an existing binary.
The historical prototype completed both boards' flashes and Vendor boots,
plus one O6 Custom 6400 MT/s and one O6N Custom 5400 MT/s boot. These results
belong to `1.3.2+memoc1`, not the rebuilt 1.3.3 image hashes.
Further training/recovery validation, manual timings,
suspend/resume and stability testing remain outstanding.
