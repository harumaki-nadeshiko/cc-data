#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
DATA_ROOT=${DATA_ROOT:-/mnt/data2/$USER/docker-cc}
IMAGE_TAG=${IMAGE_TAG:-ubcc-dev:ubuntu20.04}

mkdir -p "$DATA_ROOT/ccache" "$DATA_ROOT/home"

if [ "$#" -eq 0 ]; then
  set -- bash
fi

docker run --rm -it \
  --network none \
  -e CCACHE_DIR=/ccache \
  -e HOME=/home/builder \
  -v "$REPO_ROOT:/workspace" \
  -v "$DATA_ROOT/ccache:/ccache" \
  -v "$DATA_ROOT/home:/home/builder" \
  -w /workspace \
  "$IMAGE_TAG" \
  "$@"
