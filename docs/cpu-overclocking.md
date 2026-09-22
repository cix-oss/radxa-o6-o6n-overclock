# O6/O6N CPU overclocking

The public O6 and O6N platform sources contain a shared CPU tuning menu,
configuration validator, and PM configuration updater. BIG and MID inputs
support up to 3200 MHz; optional LITTLE tuning supports up to 2400 MHz.
These software limits do not establish safe or stable operating points.
Read the [risk warning and disclaimer](../README.cix.md) before use.

CPU voltage behavior depends on the board's regulator configuration:

| Board reference | CPU regulator path | Custom status |
| --- | --- | --- |
| O6 / RS600 V1.2 | MP2845 | Custom requests are subject to regulator topology and voltage validation. Acceptance does not guarantee hardware stability. |
| O6N / RS602 V1.11 | AU4683, with fixed DSU and LITTLE supplies | AU46xx uses its native driver. Fixed supplies retain their configured voltage and receive no Custom voltage commands. Hardware validation is pending. |

These hardware identities come from the revision-specific schematics linked
on [Radxa's download page](https://docs.radxa.com/en/orion/download).
Other PCB revisions need their own configuration check. O6N is RS602; RS601
refers to O6T. ABI capability and payload-hash checks identify the implementation.

The stock PM firmware shipped with the pinned `edk2-non-osi` dependency does
not implement the required CPU OC ABI 5. The ordinary build keeps Custom
unavailable. A build with CPU overclocking enabled requires the matching signed
BL1/BL2 dependency from the community release, including its PM implementation.

## Settings and behavior

CPU settings use their own `RadxaCpuOcVar` variable. The existing Radxa board
settings remain unchanged. Vendor is the default and retains native operating
points. Custom supplies frequency and minimum nominal voltage requests for
GB0, GB1, GM0, and GM1. PM coordinates CPU and DSU voltages dynamically; lower
CPU requests may receive additional voltage when another CPU requires it.
Programmable LITTLE and DSU rails can participate; fixed supplies cannot be
raised. DSU frequencies remain native. LITTLE retains its native table unless its
separate Custom option is enabled. GPU, memory,
power-rail selection, EC and fan configuration remain native or board-selected.
Existing foreign full or partial OPP configurations are reported and left untouched.

### CPU performance units

The settings and ABI5 request tables use MHz and mV. The PM tables published
through SCMI and CPPC use the native performance scale shared by the different
CPU types. Accepted BIG/MID Custom tables must retain that normalization and
the corresponding reference performance, as LITTLE already does. Physical
PLL and DPM limits continue to use frequency units.

UEFI derives CPPC reference performance and frequency endpoints from the
active SCMI sustained-performance/frequency pair. This also handles Vendor
mode and rejected requests that fall back to native tables. Checked 64-bit
arithmetic avoids intermediate overflow and premature ratio truncation.

Use the matching UEFI and PM pair from the same release. The ABI marker or
base version 1.3.1 alone does not establish compatibility; verify the image
and PM hashes against the release manifest.

### Frequency and voltage requests

The interface accepts 10 MHz steps, up to 3200 MHz for GB0/GB1 and
GM0/GM1. Voltage entries are minimum nominal requests in 10 mV steps, with a
1250 mV ceiling. The native CPU margin is normally +30 mV: a 1250 entry requests
1280 mV before any additional coordination. A lower entry may be raised to
maintain the CPU/DSU relationship. These requests are not measured voltages or
qualified operating points. Accepted Custom CPU rails have a 1280 mV limit;
the associated DSU rail has a 1080 mV limit. LITTLE retains its 980 mV limit,
and Vendor, GPU and other voltage policies remain unchanged.

For the selected O6N native preset, fixed DSU is modeled at 815 mV and LITTLE
at 825 mV. Retaining the 200 mV CPU/DSU rule and the +30 mV CPU margin limits
the nominal CPU request to 985 mV, or **980 mV on the menu's 10 mV grid**.
For example, 980 requests 1010 mV and can pass this bound; 1000 requests
1030 mV and fails it. Other checks still apply to the complete profile.
These values follow the selected source policy and configured fixed supplies;
they are neither measured voltages nor a hardware rating. The shared 1250 mV
menu ceiling remains available for compatible programmable topologies such
as O6. An incompatible O6N request is rejected, not silently reduced.

PM validates regulator topology, margins and quantization before accepting the
profile. At runtime it chooses compatible CPU and DSU targets and moves the
rails in steps that preserve the 200 mV nominal and programmed-voltage
difference. Every physically available CPU supply participates, even if its
cores are offline. Fixed or otherwise incompatible rails can make a profile
unavailable on a particular SKU. Voltages can return toward the lower requests
as higher requirements disappear. The 1500 MHz / 790 mV startup entry remains
a protected request. Native periodic DPM and board-selected thermal/fan control
are retained. Package-power controller enablement remains a board policy, and
the inherited power models need validation for coupled voltage changes; static
OPP power figures do not describe every coupled voltage state.

ABI5 writes selector `0xC1`. BIG/MID tables retain their existing layout;
domain 2 optionally carries a one-entry LITTLE request descriptor. The PM
expands that descriptor against its actual native table. Older `0xC0` four-domain
profiles remain readable for migration with their original 2600 MHz MID limit.
The new UEFI requires ABI5 capability and the exact installed PM hash; older
ABI1..4 payloads cannot enable it. The settings variable grows from 273 to 278
bytes by appending LITTLE fields, preserving all old field offsets. Exact
revision-1 settings migrate with LITTLE set to Native. Old Vendor records may
retain inactive edits; old Custom records must satisfy their original limits.
Unknown, partial or future records remain untouched with an error.

### Optional LITTLE tuning

LITTLE defaults to Native. Its separate Custom option requests a highest
frequency of 1800..2400 MHz in 10 MHz steps and a minimum nominal voltage of
0 (native) or 550..950 mV in 10 mV steps. The 2400 MHz ceiling is an
experimental software limit, not a hardware rating or a verified stable rate.

PM preserves the complete native LITTLE OPP table, reference performance and
sustained boot point. A higher frequency appends one top OPP; its performance
level and power are estimates scaled from the native top point. A lower-than-native
maximum rejects. An equal maximum leaves the table unchanged and cannot raise
its native voltage floor. Disabled or harvested LITTLE cores are not enabled.

On programmable supplies such as the selected O6 topology, the added point
uses at least the native top voltage request and retains the 980 mV programmed
cap. With the normal +25 mV LITTLE margin, the menu's 950 mV ceiling requests
975 mV before coupling. On O6N's fixed LITTLE supply, the minimum request must
fit the native configured fixed voltage; there are no voltage reads or writes.
The supplied topology models it as 825 mV, so a 830 mV minimum would reject.
Keep 0/native to request frequency tuning without an additional voltage floor.
This retains the native voltage request; it does not calculate a suitable
overclocking voltage automatically. Boot and workload stability still depend
on the selected frequency/voltage combination and the individual board.
Only complete profile acceptance extends the LITTLE PLL and DPM software limits.

During Custom S3 resume, PM checks programmable regulator readback and
coordinates voltage restoration before restoring clocks. Fixed supplies use
their native configured voltage without I2C reads or writes. AU46xx readback
uses the existing LINEAR11 decoder; the native less-than-10 mV consistency
allowance does not relax the rail caps or the 200 mV gap. Coarse or inconsistent
readback can therefore prevent resume. A failed read, write or voltage check
prevents clock restoration. Actual fixed voltages and readback behavior still
need measurement on each board.

After settings are saved and the board restarts, the updater validates the
installed PM identity and the complete CPU table before changing the dedicated
PM configuration sector. It preserves unrelated board bytes, verifies the
write, and requests another cold reset so PM consumes the new configuration.
The menu separately reports the saved request, persistence error, PM result
for this boot, and PM rejection reason. The PM result can be Custom inactive,
Custom accepted, rejected, unconfirmed, or a different boot configuration.
A matching saved request that PM rejected is not automatically rewritten or
reset in a loop. A failed or malformed status query remains unconfirmed.
Neither a saved request nor acceptance establishes the clock or voltage
delivered by PM. Selecting Vendor can
disable this feature's own CPU table on a board that still reaches UEFI; there
is no automatic rollback for an unstable configuration that prevents boot.

### Boot result interface

The read-only SCMI vendor protocol `0x80`, message `3`, accepts an empty request.
Its response is seven little-endian 32-bit words: SCMI status, CPU OC ABI (`5`),
boot state, rejection reason, original PMCF length, CRC1 and CRC2. Protocol
discovery messages `0`, `1`, and `2` are supported, with protocol version 1.0.
UEFI uses the existing platform MTL transport and checks response length,
header, status, ABI, state/reason values, and the three original PMCF identity
fields against the validated Flash sector. Those fields correlate the boot
configuration; they do not provide cryptographic attestation.

PM states are `0` uninitialized, `1` Custom inactive, `2` accepted, and `3`
rejected. Rejection reasons are `1` invalid PMCF, `2` invalid CPU table,
`3` external PMIC override, `4` debug guardband, `5` native rail/table,
`6` requested voltage, `7` unavailable boot domains, and `8` coupled voltage
range or transition. `0` means no rejection. PM reports acceptance only after
publishing the selected CPU tables. UEFI's seven-byte, boot-only status
variable remains revision 2; the persistent tuning variable is revision 2
and 278 bytes.

## Community release

See [the community guide](community.md) for the public forks, pinned sources,
matching binary dependency bundle and versioned O6/O6N release assets.

## Check inputs without compiling

From the repository root:

```sh
python3 tools/build_cpu_oc.py all --preflight-only
python3 -m unittest discover -s tools/tests -v
```

The first command checks the public dependencies and both selected boot chains.
With the stock inputs it reports ABI 0. It creates no build copy and runs no
compiler. The tool also defaults to this check when `--build` is absent.
Adding `--require-cpu-oc` rejects stock or unidentified PM inputs.

## Supply a compatible PM dependency

A compatible boot-chain directory must contain:

- `bootloader1.img`: a signed BL1 containing a PM with the explicit
  `CIX_PM_CPU_OC_ABI_5` capability marker and the CPU table behavior described above.
- `bootloader2.img`: the matching signed BL2.
- `cpu-oc-source.json`: the exact source and payload identities below.

The dependency bundle supplies a source record with the following required
fields. These placeholders describe the format; use the supplied record when
building firmware:

```json
{
  "schema": 1,
  "build_status": "built",
  "cpu_oc_abi": 5,
  "pm_source_revision": "<full 40-character source commit>",
  "pm_patch_sha256": "<64-character SHA-256 identifying the PM patch>",
  "pm_sha256": "<64-character SHA-256 of the PM component inside BL1>",
  "bl1_sha256": "<64-character SHA-256 of bootloader1.img>",
  "bl2_sha256": "<64-character SHA-256 of bootloader2.img>"
}
```

Inspect the component and container identities with:

```sh
python3 tools/firmware_contract.py inspect \
  --bl1 /path/to/boot-chain/bootloader1.img \
  --bl2 /path/to/boot-chain/bootloader2.img --require-cpu-oc
python3 tools/build_cpu_oc.py all --boot-chain /path/to/boot-chain --preflight-only
```

These checks establish layout, declared provenance, and byte identity. They do
not independently authenticate signatures or prove that the PM source matches
the declared commit.

## Build

The optional builder supports native ARM64 Linux with the repository's build
dependencies and public packaging tools. It copies the current public sources,
including local changes, into a separate work directory. The generated PM hash
header is written only into that copy. The selected BL1/BL2 pair and source
record are frozen and rechecked there before compilation. Public source
checkouts and their checked-in ABI 0 header remain unchanged.

```sh
python3 tools/build_cpu_oc.py all --boot-chain /path/to/boot-chain \
  --build --jobs 4 --work-dir /path/to/new-cpu-build
```

Use `O6` or `O6N` instead of `all` for one board and `--build-type DEBUG` for
debug UEFI. This option does not change the selected PM build type. Each board
uses its own memory and PM configuration; O6 retains its EC image and O6N's EC
area remains erased. The builder records source revisions, local changes,
input hashes, generated headers and full-image checks. Images and reports are
available under the work directory's `artifacts` link after success.

Host C policy, driver-state and flash-fault harnesses are supplied under the
platform source's driver `Tests` directory. They require explicit compiler
opt-in and are skipped by default:

```sh
CIX_RUN_HOST_C_TESTS=1 python3 -m unittest discover \
  -s src/edk2-platforms/Platform/Radxa/Platforms/CIX/Sky1/Drivers/PmConfigUpdateDxe/Tests -v
```

The ABI5 host C harnesses cover ABI0..4 rejection, ABI5 reporting, migration,
LITTLE descriptor encoding, transport failures and stale boot configuration.
The LITTLE encoder covers all 2562 legal frequency/voltage pairs.

Compilation and host tests do not establish board stability. Board qualification
needs measured clocks and rails, startup, DVFS, thermal behavior, S3 and recovery for each
board/SKU. Keep Vendor selected for initial boot checks.
