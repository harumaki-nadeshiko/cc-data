# Log Tracing Manual — TRACE-PERF + Chain Tools

> Produced: 2026-07-16 | Phase 4.3 P2

## Overview

The TRACE-PERF pipeline captures per-event timing data across gem5, ubio,
and networksim processes, then builds interactive latency timelines.

Three tools form the pipeline:

| Tool | Input | Output | Purpose |
|------|-------|--------|---------|
| `scripts/trace2chain.py` | Raw `stderr.log` files (TRACE-PERF lines) | JSON chain file | Parse & group events by reqId |
| `scripts/chain2html.py` | JSON chain file | Interactive HTML | Visual timeline with segment coloring |
| `scripts/latency_compare.py` | Multiple JSON chain files | Text table | Cross-run latency comparison |

## 1. Collecting TRACE-PERF Logs

TRACE-PERF lines are emitted to ubio + gem5 stderr:
```
[TRACE-PERF] <tick>|<node>|<comp>|<reqId>|<pa>|<event>|<extra>
```

A full run's logs live under `logs/<timestamp>_<topo>/`:
```
logs/20260716_120000_1s/
├── gem5_tc5_node0/stderr.log
├── gem5_tc5_node1/stderr.log
├── gem5_tc5_node2/stderr.log
├── ubio_n0_s0/stderr.log
├── ubio_n1_s0/stderr.log
├── ubio_n2_s0/stderr.log
└── nsim_tc5.log
```

## 2. trace2chain.py — Parse & Build Chains

```bash
# From a log directory:
python3 scripts/trace2chain.py logs/20260716_120000_1s > /tmp/tc_chains.json

# Or pipe grep output:
grep -h 'TRACE-PERF' logs/*/gem5*stderr.log logs/*/ubio*/stderr.log \
  | sort -t'|' -k1 -n | python3 scripts/trace2chain.py > chains.json
```

Output JSON structure:
```json
{
  "meta": {"total_events": N, "total_reqIds": M, "tick_range": [min, max]},
  "chains": {
    "<reqId>:<pa>": {
      "reqId": 123,
      "pa": "0x10018000000",
      "duration_ps": 2401000,
      "critical_path_ps": 405000,
      "events": [...],
      "summary": "gem5_0 → ubio_0 → nsim → ubio_1 → gem5_1"
    }
  }
}
```

### Chain Fields
- `duration_ps`: wall-clock from first SEND to last RECV (includes PDES sync tails)
- `critical_path_ps`: sum of `nsim_link` segments only — real configured link latency
  (excludes `nsim_sync` PDES alignment artifacts)

## 3. chain2html.py — Interactive Visualization

```bash
python3 scripts/chain2html.py /tmp/tc_chains.json > /tmp/tc_chains.html
python3 scripts/chain2html.py --target-ns 415 /tmp/tc_chains.json > tc_chains.html
```

Features:
- **Time ruler** with ns grid lines
- **Segment colors**: blue (gem5→ubio), green (ubio→gem5), violet (nsim link latency),
  pale (PDES sync alignment), yellow (ubio processing)
- **crit field**: shows critical_path_ns in swimlane label
- **Filter**: by PA prefix, reqId, min hops, min events
- **Zoom**: slider 1x–50x
- **CSV export** for further analysis

## 4. latency_compare.py — Cross-Run Comparison

```bash
python3 scripts/latency_compare.py /tmp/run1.json /tmp/run2.json
```

Outputs a side-by-side table of per-type median latencies, useful for:
- Comparing `EP_SILENT_UPGRADE=0` vs `=1`
- Comparing UBCC vs HA-C
- Tracking regression across commits

## 5. Limitations

- TRACE-PERF lines are emitted to stderr; large runs (>100K reqIds) may produce
  multi-GB logs. Filter by reqId or PA prefix before processing.
- `nsim_link` segments capture only RECV→FWD gap; actual wire latency includes
  the configured link parameter from `gen_topo.py`.
- `critical_path_ps` excludes ubio processing time (yellow segments) — these are
  host-side overhead, not network latency.
