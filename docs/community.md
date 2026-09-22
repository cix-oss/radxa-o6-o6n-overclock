# Community firmware

Community CPU tuning is maintained on `cix-community` in
[cix-oss/radxa-o6-o6n-overclock](https://github.com/cix-oss/radxa-o6-o6n-overclock).
Start with the [project README](https://github.com/cix-oss/radxa-o6-o6n-overclock/blob/cix-community/README.md)
and [CPU behavior](cpu-overclocking.md).

An OC build needs the matching signed firmware dependency bundle from the
community release and `tools/build_cpu_oc.py`. The upstream build instructions
and ordinary Debian package path use stock dependencies, which leave Custom
unavailable. The community guide identifies the separate O6 and O6N images,
source commits, checksums and validation limits.
