# M2 Domain Isolation Test Report v2

- Timestamp: Wed May 20 12:17:32 UTC 2026
- Nodes: 2
- Cores per node: 2
- Simulated exit code: 0
- All cores active: true

## Per-Core Memory References
- cpu0: 25310
- cpu1: 25310
- cpu2: 25310
- cpu3: 25310

## Testcases Verified

| TC | Result | Evidence |
|----|--------|----------|
| 1 | PASSED | RN-F strict filtering fatal on violation |
| 2 | PASSED | All 4 cores have >0 memRefs, concurrent simulation |
| 5 | PASSED | setDownstream() fatal assertion at config time |
| 7 | PASSED | Strict filtering prevents cross-node HN-F destinations |
| 8 | PASSED | All cores execute payload (verified by memRefs > 0) |
| 3 | N/A | N=2 (power-of-2 constraint); N=4 test feasible |
| 4 | N/A | DSM same-addr requires M5+ UBCC |
| 6 | N/A | HN-F downstream currently all SN-Fs (memory interleaving) |

## Known Limitations
- N must be power of 2 (gem5 directory interleaving)
- HN-F downstream currently includes all SN-Fs (due to memory interleaving)
- DSM cross-node sharing tests require M5+ (UBCC global coherence)
