#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "$0")/.." && pwd)
DATA_ROOT=${DATA_ROOT:-/mnt/data2/$USER/docker-cc}
IMAGE_TAG=${IMAGE_TAG:-ubcc-dev:ubuntu20.04}
HTTP_PROXY_ARG=${http_proxy:-http://127.0.0.1:19973}
HTTPS_PROXY_ARG=${https_proxy:-http://127.0.0.1:19973}
NO_PROXY_ARG=${no_proxy:-localhost,127.0.0.1}

mkdir -p "$DATA_ROOT/ccache" "$DATA_ROOT/home"

docker build \
  --network=host \
  --build-arg HOST_UID="$(id -u)" \
  --build-arg HOST_GID="$(id -g)" \
  --build-arg http_proxy="$HTTP_PROXY_ARG" \
  --build-arg https_proxy="$HTTPS_PROXY_ARG" \
  --build-arg no_proxy="$NO_PROXY_ARG" \
  -t "$IMAGE_TAG" \
  -f "$REPO_ROOT/docker/ubcc-dev.Dockerfile" \
  "$REPO_ROOT/.mold-build-context"
