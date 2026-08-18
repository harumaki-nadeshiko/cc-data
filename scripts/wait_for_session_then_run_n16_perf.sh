#!/bin/bash
# Wait for another OpenCode session and sustained CPU idle, then start N16 perf.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SESSION_ID="${SESSION_ID:-ses_fff5776abffeaC453EqnmoAaPu}"
DB_PATH="${OPENCODE_DB:-$HOME/.local/share/opencode/opencode.db}"
POLL_SEC="${POLL_SEC:-60}"
IDLE_SAMPLE_SEC="${IDLE_SAMPLE_SEC:-20}"
IDLE_REQUIRED="${IDLE_REQUIRED:-3}"
IDLE_THRESHOLD_PCT="${IDLE_THRESHOLD_PCT:-90}"
RUN_TAG="${RUN_TAG:-n16_formal_$(date +%Y%m%d_%H%M%S)}"
LOG_ROOT="${LOG_ROOT:-/mnt/data2/cgc/cc-ep-v5-o3-n16-formal/$RUN_TAG}"
STATE_ROOT="${STATE_ROOT:-/mnt/data2/cgc/cc-ep-v5-o3-n16-formal/launchers}"
mkdir -p "$STATE_ROOT" "$LOG_ROOT"
exec 9>"$STATE_ROOT/$SESSION_ID.lock"
flock -n 9 || { echo "another waiter already holds $STATE_ROOT/$SESSION_ID.lock"; exit 1; }

timestamp() { date --iso-8601=seconds; }

session_done() {
    local running latest_finish
    running=$(sqlite3 "$DB_PATH" "SELECT count(*) FROM part WHERE session_id='$SESSION_ID' AND json_extract(data,'$.type')='tool' AND json_extract(data,'$.state.status')='running';")
    latest_finish=$(sqlite3 "$DB_PATH" "SELECT coalesce(json_extract(data,'$.finish'),'') FROM message WHERE session_id='$SESSION_ID' AND json_extract(data,'$.role')='assistant' ORDER BY time_created DESC LIMIT 1;")
    [ "$running" = "0" ] && [ "$latest_finish" = "stop" ]
}

cpu_idle_pct() {
    local cpu user nice system idle iowait irq softirq steal guest guest_nice
    local total_a idle_a total_b idle_b delta_total delta_idle
    read -r cpu user nice system idle iowait irq softirq steal guest guest_nice < /proc/stat
    total_a=$((user + nice + system + idle + iowait + irq + softirq + steal))
    idle_a=$((idle + iowait))
    sleep "$IDLE_SAMPLE_SEC"
    read -r cpu user nice system idle iowait irq softirq steal guest guest_nice < /proc/stat
    total_b=$((user + nice + system + idle + iowait + irq + softirq + steal))
    idle_b=$((idle + iowait))
    delta_total=$((total_b - total_a))
    delta_idle=$((idle_b - idle_a))
    awk -v idle="$delta_idle" -v total="$delta_total" 'BEGIN { printf "%.2f", total ? idle * 100.0 / total : 0 }'
}

echo "$(timestamp) WAIT session=$SESSION_ID run_tag=$RUN_TAG log_root=$LOG_ROOT"
idle_count=0
while true; do
    if ! session_done; then
        idle_count=0
        echo "$(timestamp) WAIT session_active"
        sleep "$POLL_SEC"
        continue
    fi
    idle=$(cpu_idle_pct)
    if awk -v idle="$idle" -v threshold="$IDLE_THRESHOLD_PCT" 'BEGIN { exit !(idle >= threshold) }'; then
        idle_count=$((idle_count + 1))
        echo "$(timestamp) IDLE sample=$idle count=$idle_count/$IDLE_REQUIRED"
    else
        idle_count=0
        echo "$(timestamp) BUSY idle=$idle threshold=$IDLE_THRESHOLD_PCT"
    fi
    if [ "$idle_count" -ge "$IDLE_REQUIRED" ]; then
        break
    fi
    sleep "$POLL_SEC"
done

echo "$(timestamp) START run_tag=$RUN_TAG"
cd "$ROOT_DIR"
export RUN_TAG LOG_ROOT
set +e
python3 scripts/run_n16_formal_perf_matrix.py \
    >"$LOG_ROOT/coordinator.log" 2>&1
status=$?
set -e
echo "$(timestamp) DONE status=$status log_root=$LOG_ROOT"
exit "$status"
