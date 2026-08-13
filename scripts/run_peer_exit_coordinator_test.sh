#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
binary="${TMPDIR:-/tmp}/peer_exit_coordinator_test"

g++ -std=c++17 -Wall -Wextra -Werror -pedantic -I"${root}" \
    "${root}/modules/ubiomodule/PeerExitCoordinator.cc" \
    "${root}/tools/peer_exit_coordinator_test.cc" \
    -o "${binary}"
"${binary}"
