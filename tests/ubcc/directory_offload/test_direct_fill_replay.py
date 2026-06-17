import os


def test_offload_api_exists():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
    ubcc = os.path.join(root, "gem5/src/mem/ruby/protocol/chi/ep/UBCCController.cc")
    with open(ubcc, "r", encoding="utf-8") as f:
        txt = f.read()
    assert "onBackstoreFillComplete" in txt
    assert "replayResidentWaiters" in txt
    assert "fillPending" in txt
