#!/bin/bash
# Remove stale CPU affinity from containers launched by an already-running coordinator.
set -uo pipefail

coordinator_pid="$1"
all_cpus="0-$(( $(nproc) - 1 ))"

while kill -0 "$coordinator_pid" 2>/dev/null; do
    while IFS= read -r container; do
        [ -n "$container" ] || continue
        cpuset=$(docker inspect --format '{{.HostConfig.CpusetCpus}}' "$container" 2>/dev/null) || continue
        [ "$cpuset" != "$all_cpus" ] || continue
        docker update --cpuset-cpus "$all_cpus" "$container" >/dev/null 2>&1 || true
    done < <(docker ps --filter 'name=^/p0-' --format '{{.Names}}')
    sleep 5
done
