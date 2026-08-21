#!/usr/bin/env bash
# Native runner for an explicitly selected remote bare-metal framework backend.
# Deliberately does not use Docker; repository verification of this script does.
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE="$ROOT/tests/framework_stress/public_iface_stress.cc"
# Give all documented relative paths stable repository-root semantics even when
# the caller invokes this script from another directory.
cd "$ROOT" || {
    printf 'FWSTRESS FAIL stage=compile rc=2 diag=cannot enter repository root\n'
    exit 1
}
VERBOSE=0
MESSAGES=100000
PAYLOAD_BYTES=256
TIMEOUT_MS=120000
FORWARD_ARGS=()

usage() {
    cat <<'EOF'
usage: FRAMEWORK_BACKEND_LIB=path/to/libframework.{a,so} \
       [FRAMEWORK_INCLUDE_DIR=path] scripts/run_framework_stress_bare.sh [options]

Runner option: --verbose
Stress options: --messages N --payload-bytes N --timeout-ms N and the options
accepted by tests/framework_stress/public_iface_stress.cc.
EOF
}

fail_compile() {
    local rc="$1" diagnostic="$2"
    printf 'FWSTRESS FAIL stage=compile rc=%s diag=%s\n' "$rc" "$diagnostic"
    exit 1
}

last_diagnostic() {
    local file="$1" line="" fallback="" current
    while IFS= read -r current || [[ -n "$current" ]]; do
        if [[ -n "$current" ]]; then
            fallback="$current"
            [[ "$current" == '{"status":'* ]] || line="$current"
        fi
    done < "$file"
    [[ -n "$line" ]] || line="$fallback"
    line="${line//$'\r'/ }"
    line="${line//$'\n'/ }"
    [[ -n "$line" ]] || line="no diagnostic"
    printf '%.300s' "$line"
}

need_value() {
    if (($# < 2)); then
        printf 'FWSTRESS FAIL stage=run rc=2 diag=missing value for %s\n' "$1"
        exit 1
    fi
}

while (($#)); do
    case "$1" in
        --verbose)
            VERBOSE=1
            shift
            ;;
        --help|-h)
            usage
            exit 0
            ;;
        --role)
            printf 'FWSTRESS FAIL stage=run rc=2 diag=--role is managed by the runner\n'
            exit 1
            ;;
        --messages)
            need_value "$@"
            MESSAGES="$2"
            FORWARD_ARGS+=("$1" "$2")
            shift 2
            ;;
        --payload-bytes)
            need_value "$@"
            PAYLOAD_BYTES="$2"
            FORWARD_ARGS+=("$1" "$2")
            shift 2
            ;;
        --timeout-ms)
            need_value "$@"
            TIMEOUT_MS="$2"
            FORWARD_ARGS+=("$1" "$2")
            shift 2
            ;;
        *)
            FORWARD_ARGS+=("$1")
            shift
            ;;
    esac
done

if [[ ! "$MESSAGES" =~ ^[1-9][0-9]*$ || ! "$PAYLOAD_BYTES" =~ ^[1-9][0-9]*$ ||
      ! "$TIMEOUT_MS" =~ ^[1-9][0-9]*$ ]]; then
    printf 'FWSTRESS FAIL stage=run rc=2 diag=n, bytes and timeout must be positive integers\n'
    exit 1
fi

BACKEND_LIB="${FRAMEWORK_BACKEND_LIB:-}"
if [[ -z "$BACKEND_LIB" ]]; then
    fail_compile 2 "FRAMEWORK_BACKEND_LIB is required"
fi
[[ "$BACKEND_LIB" = /* ]] || BACKEND_LIB="$ROOT/$BACKEND_LIB"
[[ -f "$BACKEND_LIB" ]] || fail_compile 2 "backend library not found: $BACKEND_LIB"

if [[ -n "${FRAMEWORK_INCLUDE_DIR:-}" ]]; then
    INCLUDE_DIR="$FRAMEWORK_INCLUDE_DIR"
    [[ "$INCLUDE_DIR" = /* ]] || INCLUDE_DIR="$ROOT/$INCLUDE_DIR"
elif [[ -f "$ROOT/build/framework/include/framework/iface/Port.hh" ]]; then
    INCLUDE_DIR="$ROOT/build/framework/include"
elif [[ -f "$ROOT/framework/iface/Port.hh" ]]; then
    INCLUDE_DIR="$ROOT"
else
    fail_compile 2 "FRAMEWORK_INCLUDE_DIR is required (Port.hh default not found)"
fi
[[ -f "$INCLUDE_DIR/framework/iface/Port.hh" ]] || \
    fail_compile 2 "framework/iface/Port.hh not found under $INCLUDE_DIR"
[[ -f "$SOURCE" ]] || fail_compile 2 "stress source not found"

TMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/fwstress.XXXXXX")" || \
    fail_compile 2 "mktemp failed"
IPC_DIR="$TMP_ROOT/ipc"
mkdir "$IPC_DIR" || {
    rm -rf "$TMP_ROOT"
    fail_compile 2 "cannot create IPC directory"
}
BINARY="$TMP_ROOT/public_iface_stress"
COMPILE_LOG="$TMP_ROOT/compile.log"
GEM5_LOG="$TMP_ROOT/gem5.log"
UBIO_LOG="$TMP_ROOT/ubio.log"
GEM5_PID=""
UBIO_PID=""
WATCHDOG_PID=""
USE_PROCESS_GROUPS=0

terminate_pid() {
    local pid="$1"
    [[ -n "$pid" ]] || return 0
    if ((USE_PROCESS_GROUPS)); then
        kill -TERM -- "-$pid" 2>/dev/null || true
    else
        kill -TERM "$pid" 2>/dev/null || true
    fi
}

cleanup() {
    trap - EXIT INT TERM
    [[ -n "$WATCHDOG_PID" ]] && kill "$WATCHDOG_PID" 2>/dev/null || true
    terminate_pid "$GEM5_PID"
    terminate_pid "$UBIO_PID"
    [[ -n "$GEM5_PID" ]] && wait "$GEM5_PID" 2>/dev/null || true
    [[ -n "$UBIO_PID" ]] && wait "$UBIO_PID" 2>/dev/null || true
    rm -rf "$TMP_ROOT"
}
on_signal() {
    cleanup
    exit 130
}
trap cleanup EXIT
trap on_signal INT TERM

read -r -a CXX_CMD <<< "${CXX:-g++}"
((${#CXX_CMD[@]})) || fail_compile 2 "CXX is empty"
read -r -a EXTRA_CPPFLAGS <<< "${FRAMEWORK_BACKEND_CPPFLAGS:-}"
read -r -a EXTRA_LDFLAGS <<< "${FRAMEWORK_BACKEND_LDFLAGS:-}"

COMPILE_CMD=("${CXX_CMD[@]}" -std=c++17 -O2 -Wall -Wextra -Werror -pthread
             -I"$INCLUDE_DIR")
if [[ -n "${LIBZMQ_INCLUDE_DIR:-}" ]]; then
    ZMQ_INCLUDE="$LIBZMQ_INCLUDE_DIR"
    [[ "$ZMQ_INCLUDE" = /* ]] || ZMQ_INCLUDE="$ROOT/$ZMQ_INCLUDE"
    COMPILE_CMD+=(-I"$ZMQ_INCLUDE")
fi
COMPILE_CMD+=("${EXTRA_CPPFLAGS[@]}" "$SOURCE" "$BACKEND_LIB")

LINK_ZMQ="${FRAMEWORK_LINK_LIBZMQ:-auto}"
backend_needs_zmq() {
    local symbol
    command -v nm >/dev/null 2>&1 || return 1
    while IFS= read -r symbol; do
        [[ "$symbol" == *"zmq_"* ]] && return 0
    done < <(nm -u "$BACKEND_LIB" 2>/dev/null)
    return 1
}
case "$LINK_ZMQ" in
    0|no|false) LINK_ZMQ=0 ;;
    1|yes|true) LINK_ZMQ=1 ;;
    auto)
        LINK_ZMQ=0
        if [[ "$BACKEND_LIB" == *.a ]] && backend_needs_zmq; then
            LINK_ZMQ=1
        elif [[ "$(basename "$BACKEND_LIB")" == libframework_local.a ]]; then
            LINK_ZMQ=1
        fi
        ;;
    *) fail_compile 2 "FRAMEWORK_LINK_LIBZMQ must be auto, 0 or 1" ;;
esac

ZMQ_LIB_DIR="${LIBZMQ_LIB_DIR:-}"
if [[ -n "$ZMQ_LIB_DIR" ]]; then
    [[ "$ZMQ_LIB_DIR" = /* ]] || ZMQ_LIB_DIR="$ROOT/$ZMQ_LIB_DIR"
fi
if ((LINK_ZMQ)); then
    if [[ -n "$ZMQ_LIB_DIR" ]]; then
        [[ -d "$ZMQ_LIB_DIR" ]] || fail_compile 2 "LIBZMQ_LIB_DIR not found: $ZMQ_LIB_DIR"
        COMPILE_CMD+=(-L"$ZMQ_LIB_DIR")
    fi
    COMPILE_CMD+=(-lzmq)
fi
COMPILE_CMD+=("${EXTRA_LDFLAGS[@]}" -pthread -o "$BINARY")

if ((VERBOSE)); then
    printf 'FWSTRESS compile:'
    printf ' %q' "${COMPILE_CMD[@]}"
    printf '\n'
fi
"${COMPILE_CMD[@]}" >"$COMPILE_LOG" 2>&1
COMPILE_RC=$?
if ((VERBOSE)); then
    printf '%s\n' '--- compile log ---'
    while IFS= read -r line || [[ -n "$line" ]]; do printf '%s\n' "$line"; done < "$COMPILE_LOG"
fi
((COMPILE_RC == 0)) || fail_compile "$COMPILE_RC" "$(last_diagnostic "$COMPILE_LOG")"

RUNTIME_PATHS=("$(dirname "$BACKEND_LIB")")
[[ -n "$ZMQ_LIB_DIR" ]] && RUNTIME_PATHS+=("$ZMQ_LIB_DIR")
if [[ -n "${FRAMEWORK_RUNTIME_LIBRARY_PATH:-}" ]]; then
    RUNTIME_PATHS+=("$FRAMEWORK_RUNTIME_LIBRARY_PATH")
fi
RUNTIME_JOINED=""
for path in "${RUNTIME_PATHS[@]}"; do
    RUNTIME_JOINED+="${RUNTIME_JOINED:+:}$path"
done
export LD_LIBRARY_PATH="$RUNTIME_JOINED${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export UBCC_IPC_DIR="$IPC_DIR"

EXTERNAL_SECONDS=$(((TIMEOUT_MS + 999) / 1000 + 10))
PREFIX=()
if command -v setsid >/dev/null 2>&1; then
    PREFIX+=(setsid)
    USE_PROCESS_GROUPS=1
fi
if command -v timeout >/dev/null 2>&1; then
    PREFIX+=(timeout --foreground --signal=TERM "${EXTERNAL_SECONDS}s")
fi

run_role() {
    local role="$1" log="$2"
    if ((VERBOSE)); then
        printf 'FWSTRESS %s:' "$role"
        printf ' %q' "${PREFIX[@]}" "$BINARY" --role "$role" "${FORWARD_ARGS[@]}"
        printf '\n'
    fi
    "${PREFIX[@]}" "$BINARY" --role "$role" "${FORWARD_ARGS[@]}" >"$log" 2>&1 &
    ROLE_PID=$!
}

run_role gem5 "$GEM5_LOG"; GEM5_PID="$ROLE_PID"
run_role ubio "$UBIO_LOG"; UBIO_PID="$ROLE_PID"

# Even hosts without timeout(1) get an outer deadline in addition to the test's
# own --timeout-ms checks. The watchdog is always used to cover stuck teardown.
watchdog() {
    local sleeper=""
    trap '[[ -n "$sleeper" ]] && kill "$sleeper" 2>/dev/null || true; exit 0' TERM INT
    sleep "$EXTERNAL_SECONDS" &
    sleeper=$!
    wait "$sleeper" 2>/dev/null || return 0
    terminate_pid "$GEM5_PID"
    terminate_pid "$UBIO_PID"
}
watchdog &
WATCHDOG_PID=$!

wait "$GEM5_PID"; GEM5_RC=$?
GEM5_PID=""
wait "$UBIO_PID"; UBIO_RC=$?
UBIO_PID=""
kill "$WATCHDOG_PID" 2>/dev/null || true
wait "$WATCHDOG_PID" 2>/dev/null || true
WATCHDOG_PID=""

if ((VERBOSE)); then
    printf '%s\n' '--- gem5 log ---'
    while IFS= read -r line || [[ -n "$line" ]]; do printf '%s\n' "$line"; done < "$GEM5_LOG"
    printf '%s\n' '--- ubio log ---'
    while IFS= read -r line || [[ -n "$line" ]]; do printf '%s\n' "$line"; done < "$UBIO_LOG"
fi

if ((GEM5_RC != 0 || UBIO_RC != 0)); then
    printf 'FWSTRESS FAIL stage=run gem5_rc=%s ubio_rc=%s\n' "$GEM5_RC" "$UBIO_RC"
    printf 'gem5: %s\n' "$(last_diagnostic "$GEM5_LOG")"
    printf 'ubio: %s\n' "$(last_diagnostic "$UBIO_LOG")"
    exit 1
fi

GEM5_MS=""; UBIO_MS=""
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ \"elapsed_ms\":([0-9]+) ]] && GEM5_MS="${BASH_REMATCH[1]}"
done < "$GEM5_LOG"
while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ \"elapsed_ms\":([0-9]+) ]] && UBIO_MS="${BASH_REMATCH[1]}"
done < "$UBIO_LOG"
if [[ -z "$GEM5_MS" || -z "$UBIO_MS" ]]; then
    printf 'FWSTRESS FAIL stage=run gem5_rc=0 ubio_rc=0 diag=missing PASS timing\n'
    exit 1
fi

printf 'FWSTRESS PASS n=%s bytes=%s gem5_ms=%s ubio_ms=%s\n' \
    "$MESSAGES" "$PAYLOAD_BYTES" "$GEM5_MS" "$UBIO_MS"
