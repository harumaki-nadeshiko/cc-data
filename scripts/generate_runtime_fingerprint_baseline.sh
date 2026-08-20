#!/usr/bin/env bash
# Generate a local baseline inside the project Docker image.
set -euo pipefail

if [ "$#" -lt 1 ]; then
    echo "usage: $0 OUTPUT.json [collect_runtime_fingerprint.py options ...]" >&2
    echo "example: $0 local.json --label local --binary build/program" >&2
    exit 2
fi

output=$1
shift
root="$(cd "$(dirname "$0")/.." && pwd)"
image="${UBCC_DOCKER_IMAGE:-ubcc-dev:ubuntu20.04}"
host_lib="${LIBZMQ_HOST_LIB_DIR:-$root/thirdparty/zeromq/lib}"
command -v docker >/dev/null || { echo "ERROR: Docker is required" >&2; exit 2; }
[[ -d "$host_lib" ]] || { echo "ERROR: libzmq host directory missing: $host_lib" >&2; exit 2; }
image_id="$(docker image inspect "$image" --format '{{.Id}}')"
main_head="$(git -C "$root" rev-parse HEAD)"
gem5_root="${GEM5_SOURCE_DIR:-$root/gem5/gem5}"
[[ -d "$gem5_root" ]] || { echo "ERROR: gem5 source directory missing: $gem5_root" >&2; exit 2; }
gem5_head="$(git -C "$gem5_root" rev-parse HEAD)"
if [[ -n "$(git -C "$root" status --porcelain=v1 --untracked-files=no)" ]]; then
  tracked_dirty=true
else
  tracked_dirty=false
fi
tmp="$(mktemp /tmp/cc-ep-runtime-baseline.XXXXXX.json)"
trap 'rm -f "$tmp"' EXIT
docker run --rm --network none \
  -v "$root:/workspace" \
  -v "$gem5_root:/workspace/gem5" \
  -v "$host_lib:/workspace/.fingerprint-lib:ro" \
  -e LD_LIBRARY_PATH=/workspace/.fingerprint-lib \
  -w /workspace "$image" \
  python3 scripts/collect_runtime_fingerprint.py \
    --container-image-id "$image_id" \
    --git-head "$main_head" --git-dirty "$tracked_dirty" \
    --submodule "gem5=$gem5_head" "$@" >"$tmp"
mv "$tmp" "$output"
trap - EXIT
echo "wrote $output" >&2
