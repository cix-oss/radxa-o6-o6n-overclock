# edk2-cix

[![Community checks](https://github.com/cix-oss/edk2-cix/actions/workflows/community.yaml/badge.svg)](https://github.com/cix-oss/edk2-cix/actions/workflows/community.yaml)

Independent community CPU tuning firmware for Radxa O6 and O6N.

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

## Community firmware

- [Build instructions, dependencies, and validation status](README.cix.md)
- [CPU settings and limitations](docs/cpu-overclocking.md)
- [Firmware downloads and checksums](https://github.com/cix-oss/edk2-cix/releases)

## Upstream build

The original upstream workflow below uses stock dependencies and does not
enable Custom CPU tuning. Use the community guide above for an OC build.

1. `git clone --recurse-submodules https://github.com/radxa-pkg/edk2-cix.git`
2. Open in [`devcontainer`](https://code.visualstudio.com/docs/devcontainers/containers)
3. `make deb`
