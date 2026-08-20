#!/usr/bin/env bash
# Docker-only: build and executable test run use ubcc-dev:ubuntu20.04.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
IMAGE="${UBCC_DOCKER_IMAGE:-ubcc-dev:ubuntu20.04}"

if [[ "${FRAMEWORK_STRESS_IN_DOCKER:-0}" != 1 && ! -f /.dockerenv ]]; then
    command -v docker >/dev/null || { echo "ERROR: Docker is required; no host execution fallback is permitted." >&2; exit 2; }
    extra_mount=()
    container_zmq_lib="${LIBZMQ_LIB_DIR:-}"
    if [[ -n "${LIBZMQ_HOST_LIB_DIR:-}" ]]; then
        [[ -d "$LIBZMQ_HOST_LIB_DIR" ]] || { echo "ERROR: LIBZMQ_HOST_LIB_DIR is not a directory: $LIBZMQ_HOST_LIB_DIR" >&2; exit 2; }
        extra_mount=(-v "$LIBZMQ_HOST_LIB_DIR:/workspace/.framework-stress-lib:ro")
        container_zmq_lib=/workspace/.framework-stress-lib
    fi
    exec docker run --rm \
        -v "$ROOT:/workspace" -w /workspace \
        "${extra_mount[@]}" \
        -e FRAMEWORK_STRESS_IN_DOCKER=1 \
        -e FRAMEWORK_BACKEND_LIB="${FRAMEWORK_BACKEND_LIB:-}" \
        -e FRAMEWORK_INCLUDE_DIR="${FRAMEWORK_INCLUDE_DIR:-}" \
        -e LIBZMQ_INCLUDE_DIR="${LIBZMQ_INCLUDE_DIR:-}" \
        -e LIBZMQ_LIB_DIR="$container_zmq_lib" \
        -e FRAMEWORK_BACKEND_CPPFLAGS="${FRAMEWORK_BACKEND_CPPFLAGS:-}" \
        -e FRAMEWORK_BACKEND_LDFLAGS="${FRAMEWORK_BACKEND_LDFLAGS:-}" \
        -e FRAMEWORK_LINK_LIBZMQ="${FRAMEWORK_LINK_LIBZMQ:-auto}" \
        -e FRAMEWORK_RUNTIME_LIBRARY_PATH="${FRAMEWORK_RUNTIME_LIBRARY_PATH:-}" \
        "$IMAGE" bash /workspace/scripts/run_framework_stress.sh "$@"
fi

[[ -f /.dockerenv ]] || { echo "ERROR: this test must run inside Docker." >&2; exit 2; }
FRAMEWORK_STRESS_IN_DOCKER=1 bash scripts/build_framework_stress.sh

ipc_dir="$(mktemp -d /tmp/cc-ep-framework-stress.XXXXXX)"
cleanup() {
    [[ -n "${gem5_pid:-}" ]] && kill "$gem5_pid" 2>/dev/null || true
    [[ -n "${ubio_pid:-}" ]] && kill "$ubio_pid" 2>/dev/null || true
    rm -rf "$ipc_dir"
}
trap cleanup EXIT INT TERM
export UBCC_IPC_DIR="$ipc_dir"
export LD_LIBRARY_PATH="${LIBZMQ_LIB_DIR:-thirdparty/zeromq/lib}:${FRAMEWORK_RUNTIME_LIBRARY_PATH:-}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"

binary=build/tests/framework_stress/public_iface_stress
gem5_log="$ipc_dir/gem5.log"
ubio_log="$ipc_dir/ubio.log"
timeout_ms=120000
args=("$@")
for ((i = 0; i < ${#args[@]}; ++i)); do
    if [[ "${args[$i]}" == --timeout-ms && $((i + 1)) -lt ${#args[@]} ]]; then
        timeout_ms="${args[$((i + 1))]}"
    fi
done
[[ "$timeout_ms" =~ ^[0-9]+$ && "$timeout_ms" -gt 0 ]] || { echo "ERROR: --timeout-ms must be a positive integer" >&2; exit 2; }
timeout_seconds=$(((timeout_ms + 999) / 1000 + 5))

timeout --foreground --signal=TERM "${timeout_seconds}s" \
    "$binary" --role gem5 "$@" >"$gem5_log" 2>&1 & gem5_pid=$!
timeout --foreground --signal=TERM "${timeout_seconds}s" \
    "$binary" --role ubio "$@" >"$ubio_log" 2>&1 & ubio_pid=$!

set +e
wait "$gem5_pid"; gem5_rc=$?
wait "$ubio_pid"; ubio_rc=$?
set -e
gem5_pid=""; ubio_pid=""

cat "$gem5_log"
cat "$ubio_log"
if [[ $gem5_rc -eq 0 && $ubio_rc -eq 0 ]]; then
    echo '{"status":"PASS","test":"framework_public_iface_stress"}'
    exit 0
fi
echo "{\"status\":\"FAIL\",\"test\":\"framework_public_iface_stress\",\"gem5_rc\":$gem5_rc,\"ubio_rc\":$ubio_rc}"
exit 1
