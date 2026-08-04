#!/usr/bin/env python3
"""Summarize CC-only protocol latency and throughput for 2N1S runs."""
import argparse
import json
import re
import statistics
import sys
from collections import defaultdict
from pathlib import Path

OUTER_RE = re.compile(
    r"\[EP-PERF\] kind=outer node=(\d+) pa=0x[0-9a-f]+ reqId=(\d+) "
    r"start=(\d+) end=(\d+) latency_ps=(\d+)")
TC_RE = re.compile(r"gem5_tc(\d+)_node\d+$")
SCENARIOS = {
    210: "HA01", 211: "HA02", 212: "HA03", 213: "HA04",
    214: "HA07", 215: "HA05", 216: "HA06", 218: "HA08", 219: "HA09",
    217: "HA10",
}


def percentile(values, q):
    values = sorted(values)
    return values[min(len(values) - 1, int((len(values) - 1) * q))]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--log-dir", nargs="+", required=True)
    parser.add_argument("--profile", required=True,
                        choices=("naive", "spill-noopt", "optimized"))
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    samples = defaultdict(list)
    for run, log_dir in enumerate(args.log_dir):
        for stream in ("stdout.log", "stderr.log"):
            for path in Path(log_dir).glob(f"gem5_tc*_node*/{stream}"):
                match = TC_RE.match(path.parent.name)
                if not match or int(match.group(1)) not in SCENARIOS:
                    continue
                tc = int(match.group(1))
                for line in path.read_text(errors="replace").splitlines():
                    outer = OUTER_RE.search(line)
                    if outer:
                        samples[tc].append((run, int(outer.group(1)), int(outer.group(3)),
                                            int(outer.group(4)), int(outer.group(5))))

    with open(args.output, "w") as output:
        for tc, scenario in sorted(SCENARIOS.items()):
            records = samples[tc]
            base = {"scenario": scenario, "mode": args.profile,
                    "measurement_source": "cc_outer_protocol",
                    "guest_visible": False, "cross_platform_comparable": False,
                    "tc": tc}
            output.write(json.dumps({"kind": "manifest", **base}) + "\n")
            for index, (run, node, _, _, latency_ps) in enumerate(records):
                output.write(json.dumps({"kind": "sample", **base, "node": node,
                    "run": run, "iteration": index, "latency_ns": latency_ps / 1000.0}) + "\n")
            if not records:
                continue
            latencies = [record[4] / 1000.0 for record in records]
            throughputs = []
            for run in range(len(args.log_dir)):
                run_records = [record for record in records if record[0] == run]
                if len(run_records) > 1:
                    elapsed_ps = max(record[3] for record in run_records) - min(record[2] for record in run_records)
                    if elapsed_ps:
                        throughputs.append((len(run_records) - 1) * 1.0e12 / elapsed_ps)
            output.write(json.dumps({"kind": "summary", **base,
                "phase": "outer_protocol", "runs": len(args.log_dir), "samples": len(latencies),
                "mean_ns": statistics.mean(latencies), "p50_ns": percentile(latencies, .50),
                "p95_ns": percentile(latencies, .95), "p99_ns": percentile(latencies, .99),
                "throughput_ops_s": statistics.mean(throughputs) if throughputs else None,
                "throughput_stdev_ops_s": statistics.stdev(throughputs) if len(throughputs) > 1 else 0.0}) + "\n")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        sys.exit(2)
