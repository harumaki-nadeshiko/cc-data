#!/usr/bin/env bash
# Docker-only: all compilation/linking is performed in ubcc-dev:ubuntu20.04.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${UBCC_DOCKER_IMAGE:-ubcc-dev:ubuntu20.04}"

if [[ "${FRAMEWORK_STRESS_IN_DOCKER:-0}" != 1 && ! -f /.dockerenv ]]; then
    command -v docker >/dev/null || { echo "ERROR: Docker is required; no host build fallback is permitted." >&2; exit 2; }
    exec docker run --rm \
        -v "$ROOT:/workspace" -w /workspace \
        -e FRAMEWORK_STRESS_IN_DOCKER=1 \
        -e FRAMEWORK_BACKEND_LIB="${FRAMEWORK_BACKEND_LIB:-}" \
        -e FRAMEWORK_INCLUDE_DIR="${FRAMEWORK_INCLUDE_DIR:-}" \
        -e LIBZMQ_INCLUDE_DIR="${LIBZMQ_INCLUDE_DIR:-}" \
        -e LIBZMQ_LIB_DIR="${LIBZMQ_LIB_DIR:-}" \
        -e FRAMEWORK_BACKEND_CPPFLAGS="${FRAMEWORK_BACKEND_CPPFLAGS:-}" \
        -e FRAMEWORK_BACKEND_LDFLAGS="${FRAMEWORK_BACKEND_LDFLAGS:-}" \
        -e FRAMEWORK_LINK_LIBZMQ="${FRAMEWORK_LINK_LIBZMQ:-auto}" \
        "$IMAGE" bash /workspace/scripts/build_framework_stress.sh "$@"
fi

[[ -f /.dockerenv ]] || { echo "ERROR: this build must run inside Docker." >&2; exit 2; }

backend="${FRAMEWORK_BACKEND_LIB:-build/framework/lib/libframework_local.a}"
includes="${FRAMEWORK_INCLUDE_DIR:-build/framework/include}"
zmq_include="${LIBZMQ_INCLUDE_DIR:-thirdparty/zeromq/include}"
zmq_lib="${LIBZMQ_LIB_DIR:-thirdparty/zeromq/lib}"
out="build/tests/framework_stress/public_iface_stress"

if [[ ! -f "$backend" && -z "${FRAMEWORK_BACKEND_LIB:-}" ]]; then
    if [[ ! -f "$zmq_lib/libzmq.a" && ! -f "$zmq_lib/libzmq.so" ]]; then
        echo "ERROR: local backend dependency libzmq is missing from $zmq_lib; set LIBZMQ_LIB_DIR or provide FRAMEWORK_BACKEND_LIB." >&2
        exit 2
    fi
    bash scripts/build_framework.sh
fi
[[ -f "$backend" ]] || { echo "ERROR: FRAMEWORK_BACKEND_LIB not found in container: $backend" >&2; exit 2; }
[[ -d "$includes" ]] || { echo "ERROR: FRAMEWORK_INCLUDE_DIR not found in container: $includes" >&2; exit 2; }

mkdir -p "$(dirname "$out")"
read -r -a extra_cppflags <<< "${FRAMEWORK_BACKEND_CPPFLAGS:-}"
read -r -a extra_ldflags <<< "${FRAMEWORK_BACKEND_LDFLAGS:-}"
link_zmq="${FRAMEWORK_LINK_LIBZMQ:-auto}"
if [[ "$link_zmq" == auto ]]; then
    [[ "$(basename "$backend")" == libframework_local.a ]] && link_zmq=1 || link_zmq=0
fi
link_args=()
if [[ "$link_zmq" == 1 ]]; then
    link_args+=("-L$zmq_lib" -lzmq)
fi

g++ -std=c++17 -O2 -Wall -Wextra -Werror -pthread \
    -I"$includes" -I"$zmq_include" "${extra_cppflags[@]}" \
    tests/framework_stress/public_iface_stress.cc "$backend" \
    "${link_args[@]}" "${extra_ldflags[@]}" -pthread -o "$out"
echo "BUILD PASS: $out"
