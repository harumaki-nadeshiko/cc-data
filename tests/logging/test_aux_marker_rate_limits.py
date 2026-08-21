#!/usr/bin/env python3
"""Static compatibility checks for high-frequency auxiliary markers."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

def source(path):
    return (ROOT / path).read_text(errors="replace")

ubcc = source("modules/ubiomodule/UBCCController.cc")
backend = source("gem5/gem5/src/mem/ruby/protocol/chi/ep/EPBackend.cc")

assert "[UBCC-OUTER-REQ] home={}" in ubcc
assert "pa=0x{:x} req={} write={} requester={}" in ubcc
assert "_lastOuterReqLogTuple" not in source("modules/ubiomodule/UBCCController.hh")
assert "processOuterRequest PA=" not in ubcc
assert "[PENDING-READ-HIT]" in backend
assert "reqId=%lu retry=%lu" in backend
assert "txn.retryCount >= 1024" in backend
assert "[CLEAR-SEND]" in backend
assert "clearSendLogged" in source(
    "gem5/gem5/src/mem/ruby/protocol/chi/ep/EPBackend.hh")
assert "[CLR-CACHE-HIT]" in source(
    "gem5/gem5/src/mem/ruby/protocol/chi/ep/UBAdapter.cc")
assert "[TRACE-PERF-MANIFEST]" in source("protocol/TracePerfPolicy.hh")

print("aux marker compatibility: PASS")
