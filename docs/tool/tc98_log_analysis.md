# TC98 Log Analysis

Use `scripts/analyze_tc98_logs.py` to summarize a remote TC98 run without
manually searching all 16 UBIO planes and 8 gem5 processes.

## Combined Log Directory

```bash
python3 scripts/analyze_tc98_logs.py /path/to/logs
```

## Separate Simout Directory

If guest `simout` files are stored outside the infrastructure log directory:

```bash
python3 scripts/analyze_tc98_logs.py /path/to/infra-logs \
  --simout-dir /path/to/simout-or-m5out
```

Both directories are scanned recursively. Supported guest filenames include
`simout_n*` and `simout_tc98_node*.log`.

## JSON And Strict Modes

```bash
python3 scripts/analyze_tc98_logs.py /path/to/infra-logs \
  --simout-dir /path/to/simout \
  --json

python3 scripts/analyze_tc98_logs.py /path/to/infra-logs \
  --simout-dir /path/to/simout \
  --strict
```

`--strict` returns zero only for a complete `PASS`. A protocol failure returns
exit code 2. An incomplete, progressing, timed-out, or stalled run returns exit
code 1 in strict mode.

## Status Meanings

| Status | Meaning |
|---|---|
| `PASS` | Verifier passed, 16 progress planes and done markers completed, all child exits are zero, and shutdown handshakes completed. |
| `HEALTHY_PROGRESS` | Run is incomplete but plane progress is balanced, the hot-line epoch is monotonic, and no critical protocol error was found. |
| `HEALTHY_TIMEOUT` | Timeout was observed, but progress remained balanced and epochs remained monotonic. |
| `STALLED` | Explicit stall evidence exists or progress differs substantially across planes. |
| `FAIL` | Panic, epoch decrease, UpgradeDone tuple mismatch, InvalidateAck reqId mismatch, read mismatch, verifier failure, or non-zero child exit. |
| `INCOMPLETE` | There is not enough evidence for another classification. |

## Important Fields

The default summary reports:

```text
TC98 STATUS
per-plane latest progress marker
done-marker MATCH/MISMATCH counts
automatically detected hot PA
hot-line commit count and epoch sequence
commit counts split by path (`Clear`, `UpgradeDone`, `CachedUpgradeDone`, `BatchRS`)
epoch rollback and tuple mismatch counts
PeerExit and NetworkExit completion counts
child exit counts
recommended next action
```

The hot PA is selected as the PA with the most `commitIntendedResult` records.
Override it when necessary:

```bash
python3 scripts/analyze_tc98_logs.py /path/to/logs \
  --hot-pa 0x10007800
```

For a normal TC98 run, all 16 planes should reach progress marker `r=12`.
TC98 has 16 rounds, and the workload prints markers every four rounds at
`r=0,4,8,12`.

`reservation_superseded` is a critical issue. It means a committed directory
epoch moved past a live transaction's reserved epoch. The corresponding
`[UBCC-RESERVATION-SUPERSEDED]` line reports the commit path, current entry
epoch, expected predecessor, requester base epoch, reserved epoch, requester
node/socket, reqId, op type, and stage.
