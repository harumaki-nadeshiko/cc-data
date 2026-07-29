#!/bin/bash
# Build the shared framework static library (libframework.a) and install public headers.
# Produces:
#   build/framework/lib/libframework.a
#   build/framework/include/framework/{Port.hh, MemMessage.hh, Log.hh}
#   build/framework/manifest.txt
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FW="$ROOT/framework"
OUT="$ROOT/build/framework"
ZMQ_INC="$ROOT/thirdparty/zeromq/include"
ZMQ_LIB="$ROOT/thirdparty/zeromq/lib"

mkdir -p "$OUT/lib" "$OUT/include" "$OUT/obj"
rm -rf "$OUT/include/framework"
mkdir -p "$OUT/include/framework"

CXXFLAGS="-std=c++17 -O2 -Wall -pthread -I$ROOT -I$FW -I$ZMQ_INC"

# Compile framework sources
g++ $CXXFLAGS -c "$FW/Port.cc" -o "$OUT/obj/Port.o"
g++ $CXXFLAGS -c "$FW/Log.cc" -o "$OUT/obj/Log.o"

# (ZMQChannel.cc is legacy/unused at runtime but kept in the archive for compat;
#  compile only if present)
if [ -f "$FW/ZMQChannel.cc" ]; then
    g++ $CXXFLAGS -c "$FW/ZMQChannel.cc" -o "$OUT/obj/ZMQChannel.o" 2>/dev/null || true
fi

ar rcs "$OUT/lib/libframework.a" "$OUT/obj"/*.o

# Install public headers
cp "$FW/Port.hh" "$OUT/include/framework/Port.hh"
cp "$FW/MemMessage.hh" "$OUT/include/framework/MemMessage.hh"
cp "$FW/Log.hh" "$OUT/include/framework/Log.hh"

cat > "$OUT/manifest.txt" <<EOF
libframework.a
  Port.o
  $(ls "$OUT/obj"/*.o | xargs -n1 basename | grep -v Port.o | tr '\n' ' ')
headers
  framework/Port.hh
  framework/MemMessage.hh
EOF

echo "[build_framework] $(ls -lh "$OUT/lib/libframework.a" | awk '{print $5}') -> $OUT/lib/libframework.a"
