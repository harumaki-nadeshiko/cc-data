# v5-freeze company-side patches

These patches target the company simulator forked from the 2026-07-17 gem5
and cc-data baselines. They add the minimum compatibility required to run the
M1/M2 workloads with the original pure-TimingSimpleCPU configuration. No O3 or
hybrid CPU switching is introduced.

## Baselines

- gem5: `63bc49e9ce427e2db424c6feb553a043cb3db821`
- cc-data: `2d063fb`

Apply each patch in its own repository. Do not apply the cc-data patch inside
the gem5/Pem5 tree.

## Files

### `0001-gem5-0717-pem5-companion.patch`

Two small gem5 changes:

1. `src/sim/sync_wait.cc`: bit 31 is accepted as the portable-startup barrier
   tag and excluded from the physical-plane validity check.
2. `src/arch/arm/system.cc`: SE mode no longer queries a kernel workload entry
   point from `SEWorkload`; reset/architecture inference remains full-system
   only.

Patch size: 2 files, +18/-8.

### `0002-cc-data-0717-m1-m2.patch`

cc-data-only M1/M2 support:

- TC120-TC124 and TC142-TC147 workload sources and verification.
- Portable workload/timing helpers.
- 2N1S topology and run launcher support.
- 0719 spill/naive directory behavior and writeback serialization.
- Cross-node portable-startup BarrierReached/BarrierRelease lifecycle.
- Pure TimingSimpleCPU path. `--cpu-model` is accepted for launcher
  compatibility but v5-freeze does not add O3 or hybrid construction.

The patch deliberately excludes the gem5 submodule pointer and preserves the
0717 protocol/framework ABI.

## Apply

```bash
# Pem5/gem5 repository
git checkout <branch-based-on-63bc49e9ce>
git apply --check /path/to/0001-gem5-0717-pem5-companion.patch
git apply /path/to/0001-gem5-0717-pem5-companion.patch

# cc-data repository
git checkout <branch-based-on-2d063fb>
git apply --check /path/to/0002-cc-data-0717-m1-m2.patch
git apply /path/to/0002-cc-data-0717-m1-m2.patch
```

## Build and smoke test

All builds and simulations should use the project's Docker image with network
disabled.

```bash
docker run --rm --network none \
  -v "$PWD:/workspace" -w /workspace ubcc-dev:ubuntu20.04 \
  bash -lc 'bash scripts/build_framework.sh && bash scripts/build_all.sh'

docker run --rm --network none \
  -v "$PWD:/workspace" -w /workspace/gem5 ubcc-dev:ubuntu20.04 \
  bash -lc 'scons build/ARM/gem5.opt -j32 PROTOCOL=CHI'

docker run --rm --network none \
  -v "$PWD:/workspace" -w /workspace ubcc-dev:ubuntu20.04 \
  bash -lc 'EP_CPU_MODEL=timing EP_PERF_PROFILE=naive \
    UBCC_POLICY=naive PORTABLE_512K_DIR=0 \
    bash tests/e2e/run_multi.sh --2n1s 142'
```

Recommended three-test qualification:

- TC142: `naive`
- TC143: `spill-noopt`
- TC147: `optimized`

All three should use `EP_CPU_MODEL=timing`.

## Current verification status

- Docker builds passed for the patched 0717 gem5 and cc-data native modules.
- TC1 2N1S passed end-to-end on the frozen timing stack.
- Remote TC142/TC143/TC147 timing runs entered the workload and emitted
  `E2E_META`, `GUEST-TIMER`, and initial performance markers. They were still
  running when the patches were exported; no fatal/assert/deadlock was seen.

## SHA-256

```text
f5ae6afeb5e9f14b702b32045d5782a4a7238208d7607489d7e53b49f14a8f5f  0001-gem5-0717-pem5-companion.patch
f750f17c3a862039f702423ebfd648cdf7a28e51a5e25547bb1345d0af2af156  0002-cc-data-0717-m1-m2.patch
```
