#!/bin/bash
# Compile an E2E workload by testcase id. The default output is
# tests/e2e/workloads/workload.elf; WORKLOAD_OUT selects a run-private path
# for concurrent E2E runs.
#
# usage:
#   bash scripts/compile_workload.sh <tc_id> [workload_dir]
#
#  produces: WORKLOAD_OUT or <workload_dir>/workload.elf
#
# The tc_name -> source mapping is queried from tests/e2e/test_e2e.py
# (TESTCASES dict) to keep a single source of truth. Dual-socket TCs
# (32-35,39) compile with -DNUM_SOCKETS=2; others with -DNUM_SOCKETS=1.
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "usage: $0 <tc_id> [workload_dir]" >&2
    exit 2
fi
TC_ID=$1
ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
WL_DIR="${2:-$ROOT_DIR/tests/e2e/workloads}"

# Resolve tc_name via the canonical TESTCASES map in test_e2e.py.
TC_NAME="$(python3 - "$ROOT_DIR" "$TC_ID" <<'PY'
import sys
root, tc_id_s = sys.argv[1], sys.argv[2]
sys.path.insert(0, root + "/tests/e2e")
from test_e2e import TESTCASES
tc_name = TESTCASES.get(int(tc_id_s))
print(tc_name if tc_name else "")
PY
)"
if [ -z "$TC_NAME" ]; then
    echo "ERROR: tc_id=$TC_ID not found in TESTCASES" >&2
    exit 2
fi

SRC="$WL_DIR/${TC_NAME}.c"
OUT="${WORKLOAD_OUT:-$WL_DIR/workload.elf}"
mkdir -p "$(dirname "$OUT")"
if [ ! -f "$SRC" ]; then
    echo "ERROR: workload source not found: $SRC" >&2
    exit 3
fi

# NUM_SOCKETS from env (run_multi.sh passes it from topo JSON). If not set,
# fall back to per-TC defaults.
if [ -z "${NUM_SOCKETS:-}" ]; then
    case "$TC_ID" in
        32|33|34|35|39|81|95|96|97|98|99|134) NUM_SOCKETS=2 ;;
        *)              NUM_SOCKETS=1 ;;
    esac
fi

cc="aarch64-linux-gnu-gcc"
cflags="-static -O0 -g -DNUM_NODES=${NUM_NODES:-3} -DNUM_SOCKETS=${NUM_SOCKETS:-1} ${WORKLOAD_CFLAGS:-} -I${WL_DIR}"
case "$TC_ID" in
    210) cflags="$cflags -DHA_SCENARIO=1" ;;
    211) cflags="$cflags -DHA_SCENARIO=2" ;;
    212) cflags="$cflags -DHA_SCENARIO=3" ;;
    213) cflags="$cflags -DHA_SCENARIO=4" ;;
    214) cflags="$cflags -DHA_SCENARIO=7" ;;
    215) cflags="$cflags -DHA_SCENARIO=5" ;;
    216) cflags="$cflags -DHA_SCENARIO=6" ;;
    217) cflags="$cflags -DHA_SCENARIO=10" ;;
    218) cflags="$cflags -DHA_SCENARIO=8" ;;
    219) cflags="$cflags -DHA_SCENARIO=9" ;;
    220) cflags="$cflags -DHA_SCENARIO=11" ;;
    221) cflags="$cflags -DHA_SCENARIO=12" ;;
    222) cflags="$cflags -DHA_CGROUP_SCENARIO=1" ;;
    223) cflags="$cflags -DHA_CGROUP_SCENARIO=2" ;;
    224) cflags="$cflags -DHA_CGROUP_SCENARIO=3" ;;
    225) cflags="$cflags -DHA_CGROUP_SCENARIO=4" ;;
    226) cflags="$cflags -DHA_CGROUP_SCENARIO=5" ;;
    227) cflags="$cflags -DHA_CGROUP_SCENARIO=6" ;;
    228) cflags="$cflags -DHA_TOPOLOGY_SCENARIO=1" ;;
    229) cflags="$cflags -DHA_TOPOLOGY_SCENARIO=2" ;;
    230) cflags="$cflags -DHA_TOPOLOGY_SCENARIO=3" ;;
    231) cflags="$cflags -DHA_EXT_SCENARIO=1" ;;
    232) cflags="$cflags -DHA_EXT_SCENARIO=2" ;;
    233) cflags="$cflags -DHA_EXT_SCENARIO=3" ;;
    234) cflags="$cflags -DHA_EXT_SCENARIO=4" ;;
    235) cflags="$cflags -DHA_EXT_SCENARIO=5" ;;
esac
echo "[compile_workload] tc=$TC_ID name=$TC_NAME sockets=$NUM_SOCKETS nodes=${NUM_NODES:-3}"
echo "[compile_workload] $cc $cflags -o $OUT $SRC"
$cc $cflags -o "$OUT" "$SRC"
echo "[compile_workload] -> $OUT"
