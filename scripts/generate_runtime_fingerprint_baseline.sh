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
tmp="$(mktemp /tmp/cc-ep-runtime-baseline.XXXXXX.json)"
trap 'rm -f "$tmp"' EXIT
docker run --rm --network none \
  -v "$root:/workspace" \
  -v "$host_lib:/workspace/.fingerprint-lib:ro" \
  -e LD_LIBRARY_PATH=/workspace/.fingerprint-lib \
  -w /workspace "$image" \
  python3 scripts/collect_runtime_fingerprint.py \
    --container-image-id "$image_id" "$@" >"$tmp"
mv "$tmp" "$output"
trap - EXIT
echo "wrote $output" >&2
