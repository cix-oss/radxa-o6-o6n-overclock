# Radxa O6/O6N CPU Overclocking Firmware

[![Community checks](https://github.com/cix-oss/radxa-o6-o6n-overclock/actions/workflows/community.yaml/badge.svg)](https://github.com/cix-oss/radxa-o6-o6n-overclock/actions/workflows/community.yaml)

Independent community UEFI firmware with CPU overclocking support for Radxa O6
and O6N, based on the public Radxa firmware sources.

It includes BIG/MID settings up to 3200 MHz, optional LITTLE settings up to
2400 MHz, direct numeric voltage entry, PM admission reporting, and consistent
CPPC performance units. These are software input limits, not guaranteed
operating points.

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

[1.3.1-cix-oc.2](https://github.com/cix-oss/radxa-o6-o6n-overclock/releases/tag/1.3.1-cix-oc.2)
provides separate full-flash images for O6 and O6N and the matching build
dependencies. Download the image for your board:

| Board | Firmware image |
| --- | --- |
| Radxa O6 | `o6-1.3.1-cix-oc.2.bin` |
| Radxa O6N | `o6n-1.3.1-cix-oc.2.bin` |

Firmware reports its upstream version as 1.3.1; use `release-manifest.json` and
`SHA256SUMS` to identify and verify each download.

This is experimental firmware. Stability is not guaranteed for any overclock
profile, and O6N hardware validation is pending. See
[CPU settings and limitations](docs/cpu-overclocking.md).

## Build from the public sources

```sh
git clone --branch cix-community --recurse-submodules https://github.com/cix-oss/radxa-o6-o6n-overclock.git
cd radxa-o6-o6n-overclock
```

For the release sources, check out tag `1.3.1-cix-oc.2` and run
`git submodule update --init --recursive`. Install the dependencies listed in
`debian/control`; the CPU firmware builder requires native ARM64 Linux.

Download `cix-sky1-cpu-oc-abi5-cppc.2.tar.xz` and `SHA256SUMS` from the release,
verify the archive against its checksum, and extract it outside `src/`.
The extracted directory contains the already signed BL1/BL2 pair and its
source and payload identifiers. The matching proprietary PM is inside BL1;
its source and compiler are not needed to build the public UEFI.

```sh
python3 tools/build_cpu_oc.py all \
  --boot-chain /path/to/cix-sky1-cpu-oc-abi5-cppc.2 --preflight-only
python3 tools/build_cpu_oc.py all \
  --boot-chain /path/to/cix-sky1-cpu-oc-abi5-cppc.2 --build --jobs 4
```

Use `O6` or `O6N` instead of `all` to build one board. Results are under
`.build/cpu-oc/artifacts/`. The helper verifies and freezes dependency identities,
generates the matching PM binding only in its build copy, and checks each image.
Ordinary `make deb` uses upstream dependencies and does not enable Custom.

## Checks

```sh
python3 -m unittest discover -s tools/tests -v
CIX_RUN_HOST_C_TESTS=1 python3 -m unittest discover \
  -s src/edk2-platforms/Platform/Radxa/Platforms/CIX/Sky1/Drivers/PmConfigUpdateDxe/Tests -v
```

Community CI runs these tests, including host C checks with UBSan.
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
to the wrapper's license. The proprietary PM and other non-OSI firmware do not
inherit the public UEFI license. See the dependency bundle's `NOTICES.md` for
the available provenance and licensing information.
