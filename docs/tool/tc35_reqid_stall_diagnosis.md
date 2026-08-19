# TC35 reqId Stall Diagnosis

Run:

```bash
python3 scripts/diagnose_tc35_reqid_stall.py /path/to/tc35-logs
```

The default output is one bounded line per unique mismatch tuple. Repeated
markers are folded into `n=<occurrences>` so a large stalled log remains linear
to scan.

Example:

```text
TC35STALL[1] n=26000 ... old=41 new=42 ...
break=after_HG_before_HUSN.RR |
O{HRR=1;HG=1;HUSN.RR=0;...} |
N{HRR=0;HG=0;...}
```

The old chain (`O`) is the outstanding Home transaction. The new chain (`N`)
is the incoming retry. Important stages:

```text
HRR      Home read request
HG       Home grant generated
HUSN.RR  Home UBIO SEND_NET ReadResp
NR.RR    networksim RECV ReadResp
NF.RR    networksim FWD ReadResp
RURN.RR  requester UBIO RECV_NET ReadResp
RUSG.RR  requester UBIO SEND_GEM5 ReadResp
AR       requester adapter received ReadResp
PG       requester saved pending grant
CS       requester initiated Clear
RURG.CQ  requester UBIO RECV_GEM5 ClearReq
RUSN.CQ  requester UBIO SEND_NET ClearReq
NR.CQ    networksim RECV ClearReq
NF.CQ    networksim FWD ClearReq
HURN.CQ  Home UBIO RECV_NET ClearReq
HC       Home Clear commit
HUSN.CR  Home UBIO SEND_NET ClearResp
NR.CR    networksim RECV ClearResp
NF.CR    networksim FWD ClearResp
RURN.CR  requester UBIO RECV_NET ClearResp
RUSG.CR  requester UBIO SEND_GEM5 ClearResp
CR       requester adapter received ClearResp
CH       requester consumed cached ClearResp
```

Each nonzero stage is formatted as:

```text
count@first..last
```

The script scans each log once, indexes events by parsed reqId fields, filters
PA-aware stages by the mismatch PA, and bounds file count, decoded bytes, line
length, mismatch tuples, and evidence samples.

Additional modes:

```bash
# Structured evidence
python3 scripts/diagnose_tc35_reqid_stall.py LOGS --json report.json

# Include bounded source-line samples
python3 scripts/diagnose_tc35_reqid_stall.py LOGS --verbose

# CI mode: mismatch makes the command fail with exit 1
python3 scripts/diagnose_tc35_reqid_stall.py LOGS --fail-on-mismatch
```

Exit status:

```text
0  scan completed; with default mode this includes no mismatch
1  --fail-on-mismatch was set and at least one mismatch was found
2  invalid arguments, I/O error, or configured scan limit exceeded
```
