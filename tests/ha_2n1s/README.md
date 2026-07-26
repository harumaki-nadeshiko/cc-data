# Portable 2N1S Workloads

The CC adapter runs one portable workload core on exactly two nodes and one
socket per node. Scenario IDs use the same source, seed (`131`), validation
schema, primary-thread rule, and barrier algorithm:

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
only a run with successful validation from both nodes. `ha-native` is not
implemented until the customer supplies concrete placement, affinity, timer,
and barrier APIs; `tests/ha_native_adapter_contract.h` defines that contract.
