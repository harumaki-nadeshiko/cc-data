#!/bin/bash
# Build all native binaries (ubio, networksim, barrier_manager).
# Auto-build only the default local framework backend if it is missing.
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$DIR/.." && pwd)"

BACKEND="${FRAMEWORK_BACKEND:-local}"
FW_LIB_VALUE="${FRAMEWORK_BACKEND_LIB:-build/framework/lib/libframework_${BACKEND}.a}"
if [[ "$FW_LIB_VALUE" = /* ]]; then
    FW_LIB="$FW_LIB_VALUE"
else
    FW_LIB="$ROOT/$FW_LIB_VALUE"
fi
if [ ! -f "$FW_LIB" ]; then
    if [ "$BACKEND" = local ] && [ -z "${FRAMEWORK_BACKEND_LIB:-}" ]; then
        bash "$DIR/build_framework.sh"
    else
        echo "ERROR: framework backend '$BACKEND' archive missing: $FW_LIB" >&2
        echo "Build/provide that backend, then set FRAMEWORK_BACKEND_LIB to its absolute or workspace-relative archive." >&2
        exit 1
    fi
fi
bash "$DIR/build_ubio.sh"
bash "$DIR/build_networksim.sh"
bash "$DIR/build_barrier.sh"
echo "[build_all] done -> build/bin/{ubio,networksim,barrier_manager}"
