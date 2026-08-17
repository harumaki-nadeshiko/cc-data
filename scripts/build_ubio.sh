#!/bin/bash
# Build ubio against the selected opaque framework backend.
# Produces: build/bin/ubio
set -euo pipefail
parse_shell_words() {
    local value="$1"
    local -n destination="$2"
    local parsed
    parsed="$(mktemp)"
    if ! python3 -c 'import os, shlex, sys
for word in shlex.split(sys.argv[1], posix=True):
    sys.stdout.buffer.write(os.fsencode(word) + b"\0")' "$value" > "$parsed"; then
        rm -f "$parsed"
        return 1
    fi
    readarray -d '' -t destination < "$parsed"
    rm -f "$parsed"
}
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="${FRAMEWORK_BACKEND:-local}"
FW_LIB_VALUE="${FRAMEWORK_BACKEND_LIB:-build/framework/lib/libframework_${BACKEND}.a}"
if [[ "$FW_LIB_VALUE" = /* ]]; then
    FW_LIB="$FW_LIB_VALUE"
else
    FW_LIB="$ROOT/$FW_LIB_VALUE"
fi
FW_INC="$ROOT/build/framework/include"
MOD="$ROOT/modules/ubiomodule"
ZMQ_INC="$ROOT/thirdparty/zeromq/include"
ZMQ_LIB="$ROOT/thirdparty/zeromq/lib"
OUT="$ROOT/build/bin"

[ -f "$FW_LIB" ] || { echo "ERROR: framework backend '$BACKEND' archive missing: $FW_LIB" >&2; echo "Set FRAMEWORK_BACKEND_LIB to an existing absolute or workspace-relative archive." >&2; exit 1; }
mkdir -p "$OUT"

CXXFLAGS=(-std=c++17 -O2 -Wall -pthread "-I$MOD" "-I$MOD/mem/ruby" "-I$FW_INC" "-I$ROOT")
LDFLAGS=(-lpthread)
if [ "$BACKEND" = local ]; then
    CXXFLAGS+=("-I$ZMQ_INC")
    LDFLAGS=("-L$ZMQ_LIB" -lzmq "${LDFLAGS[@]}")
fi
parse_shell_words "${FRAMEWORK_BACKEND_CPPFLAGS:-}" BACKEND_CPPFLAGS
parse_shell_words "${FRAMEWORK_BACKEND_LDFLAGS:-}" BACKEND_LDFLAGS
CXXFLAGS+=("${BACKEND_CPPFLAGS[@]}")

SRCS="$MOD/UBCCController.cc $MOD/ResidentDir.cc $MOD/BackstoreSchemaA.cc $MOD/BackstoreSchemaC.cc $MOD/BackstoreSchemaH64.cc $MOD/BackstoreHostH64.cc $MOD/NodeAddressMap.cc $MOD/PeerExitCoordinator.cc $ROOT/modules/hamodule/FlatBitmapDirectory.cc $ROOT/modules/hamodule/HAController.cc"

g++ "${CXXFLAGS[@]}" "$MOD/ubio_main.cc" $SRCS "$FW_LIB" "${LDFLAGS[@]}" "${BACKEND_LDFLAGS[@]}" -o "$OUT/ubio"
echo "[build_ubio] $(ls -lh "$OUT/ubio" | awk '{print $5}') -> $OUT/ubio"
