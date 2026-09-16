#!/bin/sh
# Host Docker orchestration only. Does not background/start without explicit flag.
set -eu
root=${1:?usage: sh tools/park_validation_launch.sh /mnt/data-xfs/cgc/park-validation-NEW [--launch-final72|--check-publication]}
shift
case "$root" in /mnt/data-xfs/cgc/park-validation-*) ;; *) exit 2 ;; esac
exec docker run --rm --network none \
  -v "$root:$root" -v /var/run/docker.sock:/var/run/docker.sock \
  -v /sys/devices/system/node:/sys/devices/system/node:ro \
  -v "$PWD/tools:/controller:ro" -w /controller \
  ubcc-dev:ubuntu20.04 python3 park_validation_driver.py "$root" "$@"
