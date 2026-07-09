#!/bin/bash
# Compile an E2E workload by testcase id into a FIXED output path
# tests/e2e/workloads/workload.elf so the gem5 command line can reference a
# constant path regardless of TC number.
#
# usage:
#   bash scripts/compile_workload.sh <tc_id> [workload_dir]
#
#  produces: <workload_dir>/workload.elf   (overwritten each call)
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
TC_NAME="$(python3 - "$ROOT_DIR/tests/e2e/test_e2e.py" "$TC_ID" <<'PY'
import sys, re
src_path, tc_id_s = sys.argv[1], sys.argv[2]
tc_id = int(tc_id_s)
text = open(src_path).read()
# Match TESTCASES = { ... } block. The closing brace sits alone on its line
# at column 0. Use re.MULTILINE so ^ matches line starts.
start = re.search(r"^TESTCASES\s*=\s*\{", text, re.M)
if not start:
    sys.exit(2)
end = text.find("\n}", start.end())
if end < 0:
    sys.exit(2)
block = text[start.start():end+2]   # include 'TESTCASES = {' .. '\n}'
ns = {}
exec(block, ns)
tc_name = ns["TESTCASES"].get(tc_id)
print(tc_name if tc_name else "")
PY
)"
if [ -z "$TC_NAME" ]; then
    echo "ERROR: tc_id=$TC_ID not found in TESTCASES" >&2
    exit 2
fi

SRC="$WL_DIR/${TC_NAME}.c"
OUT="$WL_DIR/workload.elf"
if [ ! -f "$SRC" ]; then
    echo "ERROR: workload source not found: $SRC" >&2
    exit 3
fi

# NUM_SOCKETS from env (run_multi.sh passes it from topo JSON). If not set,
# fall back to per-TC defaults.
if [ -z "${NUM_SOCKETS:-}" ]; then
    case "$TC_ID" in
        32|33|34|35|39) NUM_SOCKETS=2 ;;
        *)              NUM_SOCKETS=1 ;;
    esac
fi

cc="aarch64-linux-gnu-gcc"
cflags="-static -O0 -g -DNUM_NODES=${NUM_NODES:-3} -DNUM_SOCKETS=${NUM_SOCKETS:-1} -I${WL_DIR}"
echo "[compile_workload] tc=$TC_ID name=$TC_NAME sockets=$NUM_SOCKETS nodes=${NUM_NODES:-3}"
echo "[compile_workload] $cc $cflags -o $OUT $SRC"
$cc $cflags -o "$OUT" "$SRC"
echo "[compile_workload] -> $OUT"