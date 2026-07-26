# Portable 2N1S Workloads

This package delivers the workload source and fixed scenario contract. The CC
reference build runs it on exactly two nodes and one socket per node. Scenario
IDs use the same source, seed (`131`), validation schema, primary-thread rule,
and barrier algorithm:

| ID | Scenario |
|---:|---|
| 210 | HA01 local reuse |
| 211 | HA02 remote read |
| 212 | HA03 ownership handoff |
| 213 | HA04 shared read then writer |
| 214 | HA07 producer consumer |

Run CC profiles with identical inputs:

```bash
EP_PERF_PROFILE=naive bash tests/e2e/run_multi.sh --2n1s 210
EP_PERF_PROFILE=optimized bash tests/e2e/run_multi.sh --2n1s 210
```

Each node emits JSONL `manifest` and `validation` records. The verifier accepts
only a run with successful validation from both nodes.

## FPGA Target Adaptation

The customer receives the workload source, not an FPGA-specific binary or an
adapter request. To compile on the target, replace the CC-only primitives used
by `e2e_ha_2n1s_core.c` with the target SDK equivalents:

| CC reference primitive | Target-side replacement |
|---|---|
| `dsm_load(home, offset)` / `dsm_store(home, offset, value)` | Load/store to the allocated HA-visible shared range. |
| `sync_wait(0x3)` | Two-participant barrier supplied by the target runtime. |
| `printf` JSONL records | Target console, UART, file, or result transport. |
| `node` / `cpu` command arguments | Target runtime node/thread identity. |

The operation sequence, offsets, values, seed, barrier positions, and JSONL
validation schema must remain unchanged. No proprietary placement, affinity,
timer, cache-control, or FPGA SDK details need to be shared with this project.
