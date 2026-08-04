#!/bin/bash
# Build the stable opaque framework local backend and install iface headers.
# This script always builds the local backend; backend selection is performed
# by consumers through FRAMEWORK_BACKEND and FRAMEWORK_BACKEND_LIB.
# Produces:
#   build/framework/lib/libframework_local.a
#   build/framework/include/framework/iface/{Message.hh, Port.hh, Log.hh}
#   build/framework/manifest.txt
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FW="$ROOT/framework"
OUT="$ROOT/build/framework"
ZMQ_INC="$ROOT/thirdparty/zeromq/include"
ZMQ_LIB="$ROOT/thirdparty/zeromq/lib"

mkdir -p "$OUT/lib" "$OUT/include" "$OUT/obj"
rm -rf "$OUT/include/framework"
mkdir -p "$OUT/include/framework/iface"
rm -f "$OUT/obj"/*.o "$OUT/lib/libframework_local.a"

CXXFLAGS="-std=c++17 -O2 -Wall -pthread -I$ROOT -I$FW -I$ZMQ_INC"

# Compile the local ZMQ backend.  Its object layout is private to Port.cc.
g++ $CXXFLAGS -c "$FW/Port.cc" -o "$OUT/obj/Port.o"
g++ $CXXFLAGS -c "$FW/Log.cc" -o "$OUT/obj/Log.o"

ar rcs "$OUT/lib/libframework_local.a" "$OUT/obj/Port.o" "$OUT/obj/Log.o"

# Install only the stable opaque interface.  Legacy concrete Port/MemMessage
# headers intentionally are not public artifacts.
cp "$FW/iface/Message.hh" "$OUT/include/framework/iface/Message.hh"
cp "$FW/iface/Port.hh" "$OUT/include/framework/iface/Port.hh"
cp "$FW/iface/Log.hh" "$OUT/include/framework/iface/Log.hh"

cat > "$OUT/manifest.txt" <<EOF
libframework_local.a
  Port.o
  Log.o
headers
  framework/iface/Message.hh
  framework/iface/Port.hh
  framework/iface/Log.hh
EOF

echo "[build_framework] $(ls -lh "$OUT/lib/libframework_local.a" | awk '{print $5}') -> $OUT/lib/libframework_local.a"
