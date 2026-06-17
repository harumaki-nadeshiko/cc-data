import os


def test_offload_dirty_tombstone_hooks():
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
    resident = os.path.join(root, "gem5/src/mem/ruby/protocol/chi/ep/ResidentDir.cc")
    with open(resident, "r", encoding="utf-8") as f:
        txt = f.read()
    assert "canonicalOneHotRequired" in txt
    assert "panic_if(__builtin_popcountll" in txt
