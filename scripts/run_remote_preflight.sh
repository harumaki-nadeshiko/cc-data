#!/usr/bin/env bash
# Remote preflight. Builds/tests are delegated to Docker-only helper scripts.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASELINE="${RUNTIME_BASELINE:-$ROOT/configs/runtime_fingerprint_local.json}"
REPORT="${RUNTIME_REPORT:-$ROOT/runtime_fingerprint_remote.json}"
LIBZMQ="${LIBZMQ_PATH:-}"
IMAGE_ID="${UBCC_DOCKER_IMAGE_ID:-}"
IMAGE="${UBCC_DOCKER_IMAGE:-ubcc-dev:ubuntu20.04}"
GEM5_BIN="${GEM5_BIN:-$ROOT/gem5/build/ARM/gem5.opt}"
if [ ! -f "$GEM5_BIN" ] && [ -f "$ROOT/gem5/gem5/build/ARM/gem5.opt" ]; then
    GEM5_BIN="$ROOT/gem5/gem5/build/ARM/gem5.opt"
fi
BACKEND_LIB="${FRAMEWORK_BACKEND_LIB:-build/framework/lib/libframework_local.a}"
case "$BACKEND_LIB" in
    /workspace/*)
        BACKEND_CONTAINER="$BACKEND_LIB"
        BACKEND_HOST="$ROOT/${BACKEND_LIB#/workspace/}"
        ;;
    "$ROOT"/*)
        BACKEND_HOST="$BACKEND_LIB"
        BACKEND_CONTAINER="/workspace/${BACKEND_LIB#"$ROOT"/}"
        ;;
    /*)
        echo "ERROR: FRAMEWORK_BACKEND_LIB must be inside the checkout" >&2
        exit 2
        ;;
    *)
        BACKEND_HOST="$ROOT/$BACKEND_LIB"
        BACKEND_CONTAINER="/workspace/$BACKEND_LIB"
        ;;
esac
[[ -f "$BACKEND_HOST" ]] || { echo "ERROR: backend archive missing: $BACKEND_HOST" >&2; exit 2; }

usage() {
    cat <<'EOF'
usage: bash scripts/run_remote_preflight.sh [--tc 98|134 --log-root DIR [--profile NAME]]

Environment:
  RUNTIME_BASELINE   local baseline JSON (default configs/runtime_fingerprint_local.json)
  RUNTIME_REPORT     remote report output
  LIBZMQ_PATH        explicit remote libzmq.so path
  LIBZMQ_HOST_LIB_DIR host directory mounted for framework stress when needed
  FRAMEWORK_BACKEND_LIB remote backend archive for stress test
EOF
}

tc=""
log_root=""
profile=""
while [ $# -gt 0 ]; do
    case "$1" in
        --tc) tc="$2"; shift 2 ;;
        --log-root) log_root="$2"; shift 2 ;;
        --profile) profile="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "ERROR: unknown argument: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ -z "${LIBZMQ_HOST_LIB_DIR:-}" ]; then
    echo "ERROR: set LIBZMQ_HOST_LIB_DIR to the directory containing the tested libzmq" >&2
    exit 2
fi
GEM5_SOURCE_DIR="$(cd "$(dirname "$GEM5_BIN")/../.." && pwd)" \
LIBZMQ_HOST_LIB_DIR="$LIBZMQ_HOST_LIB_DIR" \
bash "$ROOT/scripts/generate_runtime_fingerprint_baseline.sh" "$REPORT" \
    --label remote-docker --repo /workspace \
    --libzmq /workspace/.fingerprint-lib/$(basename "${LIBZMQ:-libzmq.so}") \
    --binary /workspace/build/bin/ubio \
    --binary /workspace/build/bin/networksim \
    --binary /workspace/gem5/build/ARM/gem5.opt \
    --artifact /workspace/tests/e2e/run_multi.sh \
    --artifact /workspace/tests/e2e/test_e2e.py \
    --artifact /workspace/configs/topo_8n2s.json \
    --artifact /workspace/scripts/audit_tc_launch.py \
    --artifact "$BACKEND_CONTAINER"
fingerprint_rc=0
if [ -f "$BASELINE" ]; then
    set +e
    python3 "$ROOT/scripts/collect_runtime_fingerprint.py" \
        --input "$REPORT" --compare "$BASELINE"
    fingerprint_rc=$?
    set -e
else
    echo "WARN runtime baseline missing: $BASELINE"
    fingerprint_rc=1
fi

FRAMEWORK_BACKEND_LIB="$BACKEND_CONTAINER" \
bash "$ROOT/scripts/run_framework_stress.sh" \
    --messages 100000 --payload-bytes 256 --timeout-ms 120000

docker run --rm --network none \
    -v "$ROOT:/workspace" -w /workspace "$IMAGE" \
    python3 scripts/audit_protocol_state_capacity.py

if [ -n "$tc" ]; then
    [ -n "$log_root" ] || { echo "ERROR: --tc requires --log-root" >&2; exit 2; }
    audit=(python3 "$ROOT/scripts/audit_tc_launch.py" "$log_root" --tc "$tc")
    if [ "$tc" = 98 ]; then
        audit+=(--formal)
    else
        [ -n "$profile" ] || { echo "ERROR: TC134 requires --profile" >&2; exit 2; }
        audit+=(--profile "$profile")
    fi
    "${audit[@]}"
fi

if [ "$fingerprint_rc" -ne 0 ]; then
    echo "PREFLIGHT FAIL: runtime fingerprint differs; stress/audit completed"
    exit 1
fi
echo "PREFLIGHT PASS"
