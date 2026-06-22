#!/bin/bash
# Build libzmq from source into thirdparty/zeromq/
# Usage: ./build.sh
set -e

ZMQ_VERSION="4.3.5"
CPPZMQ_VERSION="4.10.0"
BUILD_DIR="$(cd $(dirname $0) && pwd)"
THIRD_PARTY="$BUILD_DIR/thirdparty/zeromq"
ZMQ_SRC="$THIRD_PARTY/libzmq-$ZMQ_VERSION"

mkdir -p "$THIRD_PARTY/include" "$THIRD_PARTY/lib"

# Download libzmq
if [ ! -f "$THIRD_PARTY/libzmq.tar.gz" ]; then
    echo "Downloading libzmq $ZMQ_VERSION..."
    curl -L -o "$THIRD_PARTY/libzmq.tar.gz" \
        "https://github.com/zeromq/libzmq/archive/refs/tags/v$ZMQ_VERSION.tar.gz"
fi

# Build libzmq
if [ ! -f "$THIRD_PARTY/lib/libzmq.a" ]; then
    rm -rf "$ZMQ_SRC"
    tar xzf "$THIRD_PARTY/libzmq.tar.gz" -C "$THIRD_PARTY"
    cd "$ZMQ_SRC"
    mkdir -p build && cd build
    cmake .. \
        -DCMAKE_BUILD_TYPE=Release \
        -DENABLE_DRAFTS=OFF \
        -DBUILD_SHARED=OFF \
        -DBUILD_STATIC=ON \
        -DCMAKE_POSITION_INDEPENDENT_CODE=ON \
        2>&1 | tail -5
    make -j$(nproc) 2>&1 | tail -3
    cp lib/libzmq.a "$THIRD_PARTY/lib/"
    cp ../include/zmq.h "$THIRD_PARTY/include/"
    cp ../include/zmq_utils.h "$THIRD_PARTY/include/" 2>/dev/null || true
    cd "$BUILD_DIR"
    echo "libzmq built successfully"
fi

# Download cppzmq (header-only)
if [ ! -f "$THIRD_PARTY/include/zmq.hpp" ]; then
    echo "Downloading cppzmq $CPPZMQ_VERSION..."
    curl -L -o "$THIRD_PARTY/cppzmq.tar.gz" \
        "https://github.com/zeromq/cppzmq/archive/refs/tags/v$CPPZMQ_VERSION.tar.gz"
    tar xzf "$THIRD_PARTY/cppzmq.tar.gz" -C "$THIRD_PARTY"
    cp "$THIRD_PARTY/cppzmq-$CPPZMQ_VERSION/zmq.hpp" "$THIRD_PARTY/include/"
    cp "$THIRD_PARTY/cppzmq-$CPPZMQ_VERSION/zmq_addon.hpp" "$THIRD_PARTY/include/" 2>/dev/null || true
    echo "cppzmq installed"
fi

echo "ZeroMQ thirdparty ready: $THIRD_PARTY"
