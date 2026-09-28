# Radxa O6/O6N CPU and Memory Overclocking Firmware

[![Community checks](https://github.com/cix-oss/radxa-o6-o6n-overclock/actions/workflows/community.yaml/badge.svg)](https://github.com/cix-oss/radxa-o6-o6n-overclock/actions/workflows/community.yaml)

Independent community UEFI firmware with CPU and memory overclocking support
for Radxa O6 and O6N, based on the public Radxa firmware sources.

It includes BIG/MID settings up to 3200 MHz, optional LITTLE settings up to
2400 MHz, direct numeric voltage entry, PM admission reporting, and consistent
CPPC performance units. These are software input limits, not guaranteed
operating points.

Version 1.3.3 adds experimental memory frequency and seven primary timing
controls, a report of the completed memory-training result, and matching
early-boot firmware. Each board uses one complete BIOS image containing both
CPU and memory support. DRAM supply-voltage adjustment is unavailable on the
reviewed board circuits. See [memory tuning](docs/memory-overclocking.md).

O6N includes a default-off experimental option to relax the fixed-DSU voltage
gap for higher BIG/MID voltage requests. This exceeds the original policy and
may damage hardware; see [CPU settings and limitations](docs/cpu-overclocking.md).

> [!WARNING]
> **This project is not affiliated with, sponsored by, endorsed by, or supported
> by CIX or Radxa.** References to those companies identify the hardware and
> upstream sources only. This is not an official CIX or Radxa firmware release.
>
> **Overclocking and changing voltages can immediately and permanently damage
> hardware.** Prolonged overclocking may accelerate hardware degradation and
> shorten its service life. Flashing or using this firmware may also cause
> crashes, data loss, or leave the device unable to boot.
>
> **Use entirely at your own risk.** The firmware is provided "AS IS", without
> warranty of any kind. To the maximum extent permitted by applicable law,
> this project, its maintainers, and its contributors accept no responsibility
> or liability for hardware damage, data loss, loss of use, or any other loss
> arising from flashing, configuring, or using this firmware. You are solely
> responsible for your decision to use it and for your chosen settings.

## Firmware downloads

[1.3.3](https://github.com/cix-oss/radxa-o6-o6n-overclock/releases/tag/1.3.3)
provides separate full-flash images for O6 and O6N and the matching build
dependencies. Download the image for your board:

| Board | Firmware image |
| --- | --- |
| Radxa O6 | `o6-1.3.3.bin` |
| Radxa O6N | `o6n-1.3.3.bin` |

Firmware reports version 1.3.3; use `release-manifest.json` and
`SHA256SUMS` to identify and verify each download.

This is experimental firmware. The preceding `1.3.2+memoc1` prototype passed
Vendor boots on O6 32 GiB and O6N 48 GiB and one Custom boot per board at
6400 and 5400 MT/s respectively. The rebuilt 1.3.3 images have not been flashed
on hardware. Stress stability, manual timings and physical recovery remain
unverified. These observations are not recommended settings for other boards.

After updating the complete firmware and verifying the write, fully disconnect
and reconnect board power. Memory controls are under **Device Manager ->
Platform Configuration -> Memory tuning**. Factory CPU and memory profiles
are Vendor; Custom memory rates and timings take effect after saving and
rebooting. Subsequent parameter changes do not require reflashing the BIOS.
Use the new Memory tuning form for this release; its consumer ignores legacy
BSET memory overrides. Read the
[CPU settings and limitations](docs/cpu-overclocking.md) and
[memory recovery limits](docs/memory-overclocking.md#recovery-implementation-and-remaining-validation).

## Build from the public sources

```sh
git clone --branch cix-community --recurse-submodules https://github.com/cix-oss/radxa-o6-o6n-overclock.git
cd radxa-o6-o6n-overclock
```

For the release sources, check out tag `1.3.3` and run
`git submodule update --init --recursive`. Install the dependencies listed in
`debian/control`; the CPU firmware builder requires native ARM64 Linux.

Download `cix-sky1-cpu-abi6-memory-abi1-1.3.3.tar.xz` and `SHA256SUMS` from the release,
verify the archive against its checksum, and extract it outside `src/`.
The extracted directory contains the already signed BL1/BL2 pair and its
source and payload identifiers. The matching SE/DDR memory consumer and CPU
PM are inside BL1. Their private sources and compilers are not needed to build
the public UEFI. Use this release's matching dependency and memory build flag.

```sh
python3 tools/build_cpu_oc.py all --memory-tuning \
  --boot-chain /path/to/cix-sky1-cpu-abi6-memory-abi1-1.3.3 --preflight-only
python3 tools/build_cpu_oc.py all --memory-tuning \
  --boot-chain /path/to/cix-sky1-cpu-abi6-memory-abi1-1.3.3 --build --jobs 4
```

Use `O6` or `O6N` instead of `all` to build one board. Results are under
`.build/cpu-oc/artifacts/`. The helper verifies and freezes dependency identities,
generates matching PM and memory bindings only in its build copy, and checks each image.
Ordinary `make deb` uses upstream dependencies and does not enable Custom.

## Checks

```sh
CIX_RUN_HOST_C_TESTS=1 python3 -m unittest discover -s tools/tests -v
CIX_RUN_HOST_C_TESTS=1 python3 -m unittest discover \
  -s src/edk2-platforms/Platform/Radxa/Platforms/CIX/Sky1/Drivers/PmConfigUpdateDxe/Tests -v
```

Community CI runs these tests, including host CPU checks and memory-policy
checks against both public board configurations with UBSan.
Passing these checks does not establish hardware stability.

## Upstream sources

This repository is a fork of
[radxa-pkg/edk2-cix](https://github.com/radxa-pkg/edk2-cix).
The public UEFI changes live in
[cix-oss/edk2-platforms](https://github.com/cix-oss/edk2-platforms/tree/cix-community).
This repository pins that fork and the unchanged public `edk2` and
`edk2-non-osi` dependencies. Both community branches retain their upstream
history.

## Licensing

Preserve each upstream component's notices. New community tools and UEFI files
identify their license with SPDX headers; `debian/copyright` records exceptions
to the wrapper's license. The proprietary SE/DDR, PM and other non-OSI firmware do not
inherit the public UEFI license. See the dependency bundle's `NOTICES.md` for
the available provenance and licensing information.
