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

fingerprint_args=(--label remote --repo "$ROOT"
    --binary "$ROOT/build/bin/ubio"
    --binary "$ROOT/build/bin/networksim"
    --binary "$GEM5_BIN"
    --artifact "$ROOT/tests/e2e/run_multi.sh"
    --artifact "$ROOT/tests/e2e/test_e2e.py"
    --artifact "$ROOT/configs/topo_8n2s.json"
    --artifact "$ROOT/scripts/audit_tc_launch.py")
[ -n "$LIBZMQ" ] && fingerprint_args+=(--libzmq "$LIBZMQ")
if [ -z "$IMAGE_ID" ] && command -v docker >/dev/null 2>&1; then
    IMAGE_ID="$(docker image inspect "$IMAGE" --format '{{.Id}}' 2>/dev/null || true)"
fi
[ -n "$IMAGE_ID" ] && fingerprint_args+=(--container-image-id "$IMAGE_ID")
python3 "$ROOT/scripts/collect_runtime_fingerprint.py" \
    "${fingerprint_args[@]}" >"$REPORT"
fingerprint_rc=0
if [ -f "$BASELINE" ]; then
    set +e
    python3 "$ROOT/scripts/collect_runtime_fingerprint.py" \
        "${fingerprint_args[@]}" --compare "$BASELINE" \
        --ignore-field label --ignore-field host --ignore-field environment
    fingerprint_rc=$?
    set -e
else
    echo "WARN runtime baseline missing: $BASELINE"
    fingerprint_rc=1
fi

bash "$ROOT/scripts/run_framework_stress.sh" \
    --messages 100000 --payload-bytes 256 --timeout-ms 120000

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
