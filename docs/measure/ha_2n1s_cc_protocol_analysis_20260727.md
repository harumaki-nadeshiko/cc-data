# 2N1S CC Protocol Measurement Analysis

**Date:** 2026-07-27

## Scope

This report analyzes only completed CC `EP-PERF kind=outer` transactions. It
is a simulated protocol-path diagnostic, not a guest-visible measurement and
not a cross-platform HA comparison. All figures therefore retain:

```text
measurement_source = cc_outer_protocol
guest_visible = false
cross_platform_comparable = false
```

Guest `CNTVCT` records are still emitted by the portable workload. In the
current split SE setup the Arm `SystemCounter` / `GenericTimer` is not
instantiated, so the batch samples report zero advancement and the guest
summary correctly marks `timer_resolution_limited=true`. Those values are not
used below.

## Functional Matrix

The same 2N1S workload core and 8-entry pressure configuration completed both
profiles without timeout:

| Profile | Runs | Scenarios | Result |
|---|---:|---|---|
| `naive` | 2 | HA01-HA09 (TC210-216, TC218-219) | 9/9 each run |
| `optimized` | 2 | HA01-HA09 (TC210-216, TC218-219) | 9/9 each run |

The full-matrix logs are `logs/ha_full_naive_20260727`,
`logs/ha_full_naive_r2_20260727`, `logs/ha_full_opt_20260727`, and
`logs/ha_full_opt_r2_20260727`.

## Non-Capacity Scenarios

Across two runs, HA01, HA02, HA03, HA04, HA07, HA08, and HA09 have effectively
identical `EP-PERF outer` distributions in naive and optimized profiles. This
is expected: these small workloads primarily exercise baseline remote access,
ownership handoff, invalidation, stream synchronization, or mixed pressure;
they do not require an optimized spill-preserved reuse result.

This establishes correctness and path stability, not an optimization win. The
measured protocol throughput is stable across the two runs, but the sample
counts are small for HA01-HA04 and must not be used for confidence claims.

## Capacity Scenarios

HA05 and HA06 were rerun with 640 pressure lines. This is 125% of the
512-entry ResidentDir and is deliberately above the `directory-pressure`
threshold. Both profiles completed successfully:

```text
optimized: 305 [RESIDENT-SPILL-DONE], 0 [UBCC-NAIVE-EVICT]
naive:      0 [RESIDENT-SPILL-DONE], 130 [UBCC-NAIVE-EVICT]
```

| Scenario | Profile | Outer samples | Mean ns | P95 ns | Protocol throughput ops/s |
|---|---|---:|---:|---:|---:|
| HA05 | naive | 643 | 39.240 | 119.000 | 821,042.737 |
| HA05 | optimized | 642 | 83.641 | 239.500 | 1,055,091.930 |
| HA06 | naive | 642 | 39.206 | 119.000 | 1,082,825.143 |
| HA06 | optimized | 641 | 84.772 | 261.500 | 1,578,777.286 |

Relative optimized versus naive changes:

| Scenario | Mean outer latency | P95 outer latency | Protocol throughput |
|---|---:|---:|---:|
| HA05 | +113.15% | +101.26% | +28.51% |
| HA06 | +116.22% | +119.75% | +45.80% |

## Interpretation

The capacity objective is partly achieved and partly not achieved in this
measurement window:

- **Achieved:** optimized executes the intended spill path under actual
  capacity pressure and sustains higher aggregate protocol transaction
  throughput than naive.
- **Not achieved for per-request protocol latency:** optimized has higher mean
  and P95 `outer` latency. The current implementation charges persistence,
  metadata, and spill-related work to the transaction path instead of fully
  hiding it behind admission or later reuse.
- **Not yet measurable:** the plan's primary `first_revisit - local_reuse`
  preservation gain remains unmeasured. The current guest timer cannot provide
  a trustworthy demand-visible latency boundary in this split SE configuration.

Therefore it is incorrect to claim that optimized is generally faster. The
supported statement is narrower: under this 125%-capacity pressure workload,
optimized preserves data via spill and delivers higher CC protocol throughput,
while naive has lower individual `outer` transaction latency.

## Reproduction

```bash
EP_PERF_PROFILE=optimized bash tests/e2e/run_multi.sh --2n1s 215 216
EP_PERF_PROFILE=naive bash tests/e2e/run_multi.sh --2n1s 215 216

python3 scripts/analyze_2n1s_cc.py \
  --log-dir logs/<run> --profile optimized \
  --output /tmp/ha_protocol.jsonl
```

The analyzer intentionally omits scenarios with no completed outer transaction
sample rather than assigning them a zero latency or zero throughput.
