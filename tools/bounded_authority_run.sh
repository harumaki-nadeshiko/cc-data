#!/bin/bash
set -euo pipefail
# Invoke only inside docker --network none ubcc-dev:ubuntu20.04, workspace mounted
# at /work. All executables and logs remain in SAMEHOME, never /tmp.
test -f /.dockerenv
ulimit -c 0
cd /work
out=boundary-evidence/bounded-authority-B
mkdir -p "$out"
g++ -std=c++17 -Wall -Wextra -Werror -pedantic -g -O1 \
    -fsanitize=address,undefined -fno-omit-frame-pointer -fno-pie -no-pie \
    -I. tools/bounded_authority_test.cc protocol/NodeAddressMap.cc \
    -o "$out/test" 2>&1 | tee "$out/build.log"
ASAN_OPTIONS=detect_leaks=1 UBSAN_OPTIONS=halt_on_error=1 \
    "$out/test" 2>&1 | tee "$out/test.log"
g++ -std=c++17 -Wall -Wextra -Werror -g -O1 \
    -fsanitize=address,undefined -fno-omit-frame-pointer -fno-pie -no-pie \
    tools/boundary_transactions_test.cc -o "$out/legacy-test" \
    2>&1 | tee "$out/legacy-build.log"
ASAN_OPTIONS=detect_leaks=1 UBSAN_OPTIONS=halt_on_error=1 \
    "$out/legacy-test" 2>&1 | tee "$out/legacy-test.log"
