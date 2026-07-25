#!/bin/bash
# Compare eager-naive directory eviction with ResidentDir spill. Runs remain
# serial because workload.elf, IPC endpoints, and build/run are shared.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
RUNNER="$ROOT_DIR/tests/e2e/run_multi.sh"
OUT_DIR="${DIR_BENCH_LOG_DIR:-$ROOT_DIR/logs/dir_overflow_bench_$(date +%Y%m%d_%H%M%S)}"
mkdir -p "$OUT_DIR"

run_case() {
    local name="$1" policy="$2" bloom="$3"
    LOG_BASE="$OUT_DIR/$name" UBCC_POLICY="$policy" UBCC_BLOOM_BYTES="$bloom" \
        TIMEOUT_SEC="${TIMEOUT_SEC_TC130:-900}" \
        bash "$RUNNER" --1s 130 2>&1 | tee "$OUT_DIR/$name.runner.log"
}

LARGE_BLOOM="${DIR_BENCH_BLOOM_BYTES:-512}"
SMALL_BLOOM="${DIR_BENCH_SMALL_BLOOM_BYTES:-256}"
run_case naive naive "$LARGE_BLOOM"
run_case spill_bloom_small spill "$SMALL_BLOOM"
run_case spill_bloom_large spill "$LARGE_BLOOM"

python3 - "$ROOT_DIR" "$OUT_DIR" "$SMALL_BLOOM" "$LARGE_BLOOM" <<'PY'
import importlib.util
import pathlib
import re
import sys

root_dir = pathlib.Path(sys.argv[1])
out_dir = pathlib.Path(sys.argv[2])
small_bloom = int(sys.argv[3])
large_bloom = int(sys.argv[4])
spec = importlib.util.spec_from_file_location("trace_visualizer", root_dir / "scripts/trace_visualizer.py")
trace = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trace)

def hot_reads(path):
    tx = trace.build_transactions(trace.collect_events([str(path)]))
    return [item for item in tx if item["type"] == "ReadReq" and item["requester"] == 1
            and 0x10000000 <= int(item["pa"], 16) < 0x10000600]

naive = hot_reads(out_dir / "naive")
small = hot_reads(out_dir / "spill_bloom_small")
large = hot_reads(out_dir / "spill_bloom_large")
if any(len(items) < 24 for items in (naive, small, large)):
    raise SystemExit(f"missing initial hot reads: naive={len(naive)} small={len(small)} large={len(large)}")

# The workload's first 24 hot accesses establish sharing; every later hot
# request is caused by naive's eager invalidation after pressure. Spill should
# retain the requester copies, producing no additional outer request.
naive_reuse = naive[24:]
small_reuse = small[24:]
large_reuse = large[24:]
if len(naive_reuse) != 96 or small_reuse or large_reuse:
    raise SystemExit(f"unexpected reuse requests: naive={len(naive_reuse)} small={len(small_reuse)} large={len(large_reuse)}")
mean_ns = sum(item["dur_ps"] for item in naive_reuse) / len(naive_reuse) / 1000.0
median_ns = sorted(item["dur_ps"] for item in naive_reuse)[len(naive_reuse) // 2] / 1000.0
print("policy,hot_reuse_ops,outer_readreqs,outer_readreq_rate,mean_outer_latency_ns,median_outer_latency_ns")
print(f"naive,96,{len(naive_reuse)},{len(naive_reuse) / 96:.3f},{mean_ns:.3f},{median_ns:.3f}")
print("spill,96,0,0.000,0.000,0.000")
print(f"outer_request_reduction_pct={(1 - len(large_reuse) / len(naive_reuse)) * 100:.1f}")

def mean_ns(items):
    return sum(item["dur_ps"] for item in items) / len(items) / 1000.0

small_mean = mean_ns(small[:24])
large_mean = mean_ns(large[:24])
delta_ns = large_mean - small_mean
print("bloom_bytes,initial_hot_reads,mean_outer_latency_ns,delta_ns,delta_cycles_at_2GHz")
print(f"{small_bloom},24,{small_mean:.3f},0.000,0.000")
print(f"{large_bloom},24,{large_mean:.3f},{delta_ns:.3f},{delta_ns / 0.5:.3f}")
PY
