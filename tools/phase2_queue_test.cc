// Phase 2: runtime validation of typed status, response body layout,
// and bounded scanning queue scheduling (per-PA FIFO, ready-scan).
//
// This test has ZERO gem5/SimObject dependencies. It exercises:
//   1. MetaRNFLineStatus enum value correctness
//   2. Response body sizeof/offsetof
//   3. CoherenceMessage construction/layout
//   4. A bounded scanning queue scheduler matching MetaRNFController's
//      drainPendingLineOps algorithm — same PA FIFO, unrelated PA proceed,
//      bounded slot capacity, blocked items re-queued.
//
// Compile (in workspace root):
//   g++ -std=c++17 -I. -o /tmp/phase2_test tools/phase2_queue_test.cc
// Run:
//   /tmp/phase2_test
//
// The production MetaRNFController does NOT call this helper; this is an
// independently testable algorithmic twin used for verification only.

#include "protocol/CoherenceMessage.hh"

#include <cassert>
#include <cstdio>
#include <cstring>
#include <deque>
#include <functional>
#include <set>
#include <string>
#include <vector>

// ---- Section 1: typed status round-trip ----
static void test_status_values()
{
    using S = cc::glob::MetaRNFLineStatus;
    std::fprintf(stderr, "[TEST] status values...\n");

    assert(static_cast<uint8_t>(S::Ok) == 0);
    assert(static_cast<uint8_t>(S::RetryableBusy) == 1);
    assert(static_cast<uint8_t>(S::IoError) == 2);
    assert(static_cast<uint8_t>(S::Corrupt) == 3);
    assert(static_cast<uint8_t>(S::RangeError) == 4);
    assert(static_cast<uint8_t>(S::InvalidArgument) == 5);

    assert(std::string(cc::glob::metaRNFLineStatusName(S::Ok)) == "Ok");
    assert(std::string(cc::glob::metaRNFLineStatusName(S::RetryableBusy)) == "RetryableBusy");
    assert(std::string(cc::glob::metaRNFLineStatusName(S::IoError)) == "IoError");
    assert(std::string(cc::glob::metaRNFLineStatusName(S::RangeError)) == "RangeError");
    std::fprintf(stderr, "[TEST] status values: PASS\n");
}

// ---- Section 2: response body layout ----
static void test_body_layout()
{
    std::fprintf(stderr, "[TEST] body layout...\n");

    cc::glob::UBMetaRNFLineReadRespBody rrb;
    assert(offsetof(cc::glob::UBMetaRNFLineReadRespBody, status) == 0);
    // Phase3: bucketOffset (8B) inserted after status, before data
    // data offset = status(1B) + bucketOffset(8B) = 9
    // But the actual offset depends on struct layout; just verify order.
    assert(offsetof(cc::glob::UBMetaRNFLineReadRespBody, bucketOffset) > 0);
    assert(offsetof(cc::glob::UBMetaRNFLineReadRespBody, data) > offsetof(cc::glob::UBMetaRNFLineReadRespBody, bucketOffset));
    // A non-Ok status must not carry meaningful data.
    rrb.status = cc::glob::MetaRNFLineStatus::IoError;
    memset(rrb.data, 0xCD, 64);
    // The caller must only read data when status == Ok.

    cc::glob::UBMetaRNFLineWriteRespBody wrb;
    assert(offsetof(cc::glob::UBMetaRNFLineWriteRespBody, status) == 0);
    wrb.status = cc::glob::MetaRNFLineStatus::Ok;

    // sizeof checks (wire-format paranoia) — updated for Phase3 bucketOffset
    assert(sizeof(cc::glob::UBMetaRNFLineReadRespBody) >= 1 + 8 + 64);
    assert(sizeof(cc::glob::UBMetaRNFLineWriteRespBody) >= 1 + 8);
    assert(sizeof(cc::glob::UBMetaRNFLineWriteReqBody) >= 8 + 64);

    std::fprintf(stderr, "[TEST] body layout: PASS\n");
}

// ---- Section 3: CoherenceMessage construction / envelope ----
static void test_message_envelope()
{
    std::fprintf(stderr, "[TEST] message envelope...\n");

    cc::glob::CoherenceMessage msg;
    msg.h.type = cc::glob::CoherenceMessageType::MetaRNFLineReadResp;
    msg.h.reqId = 0xDEADBEEF;
    msg.h.homeLinePa = 0x1000;
    msg.b.metaRNFLineReadResp.status = cc::glob::MetaRNFLineStatus::Ok;
    memset(msg.b.metaRNFLineReadResp.data, 0xAB, 64);

    assert(msg.h.type == cc::glob::CoherenceMessageType::MetaRNFLineReadResp);
    assert(msg.h.reqId == 0xDEADBEEF);
    assert(msg.b.metaRNFLineReadResp.status == cc::glob::MetaRNFLineStatus::Ok);
    assert(msg.b.metaRNFLineReadResp.data[0] == 0xAB);

    // Union aliasing: write resp body at same offset, read back status.
    cc::glob::CoherenceMessage msg2;
    msg2.h.type = cc::glob::CoherenceMessageType::MetaRNFLineWriteResp;
    msg2.b.metaRNFLineWriteResp.status = cc::glob::MetaRNFLineStatus::RangeError;
    assert(msg2.b.metaRNFLineWriteResp.status == cc::glob::MetaRNFLineStatus::RangeError);

    std::fprintf(stderr, "[TEST] message envelope: PASS\n");
}

// ---- Section 4: bounded scanning queue scheduler ----
//
// This is an algorithmic twin of MetaRNFController::drainPendingLineOps().
// It models:
//   - N flight slots (configurable)
//   - A scoreboard tracking in-flight PAs
//   - A pending queue of {PA, callback}
//   - drainPending: scan once, issue ready items, re-queue blocked items
//
// Assertions:
//   1. Same-PA items are issued in FIFO order (observed via callbacks).
//   2. Unrelated PA B can proceed while PA A is blocked.
//   3. If no slots and all items blocked by scoreboard, none issued.
//   4. Capacity exhaust: no issue when slots=0, everything re-queued.

struct QueueOp {
    int pa;
    int seq;  // global sequence number for FIFO tracking
};

struct QueueTest {
    static constexpr int kMaxSlots = 8;
    int _numSlots;
    std::set<int> _scoreboard;         // in-flight PAs
    std::deque<QueueOp> _pendingQueue;
    std::vector<int> _issuedSeq;       // seq numbers issued, in order
    int _nextSeq = 0;

    explicit QueueTest(int slots) : _numSlots(slots) {}

    int activeFlights() const { return (int)_scoreboard.size(); }
    bool hasFreeSlot() const { return activeFlights() < _numSlots; }

    void enqueue(int pa) {
        _pendingQueue.push_back({pa, _nextSeq++});
    }

    // Complete a flight for `pa`, then drain.
    void completeAndDrain(int pa) {
        _scoreboard.erase(pa);
        drainPending();
    }

    void drainPending() {
        if (_pendingQueue.empty()) return;

        std::deque<QueueOp> remaining;
        while (!_pendingQueue.empty()) {
            if (!hasFreeSlot()) {
                remaining.insert(remaining.end(),
                                 std::make_move_iterator(_pendingQueue.begin()),
                                 std::make_move_iterator(_pendingQueue.end()));
                break;
            }
            QueueOp op = std::move(_pendingQueue.front());
            _pendingQueue.pop_front();

            if (_scoreboard.count(op.pa)) {
                remaining.push_back(std::move(op));
                continue;
            }

            // Issue: mark PA in-flight, record seq.
            _scoreboard.insert(op.pa);
            _issuedSeq.push_back(op.seq);
        }
        _pendingQueue = std::move(remaining);
    }
};

static void test_queue_scheduling()
{
    std::fprintf(stderr, "[TEST] queue scheduling...\n");

    // Scenario A: single PA, FIFO order.
    {
        QueueTest qt(2);
        qt.enqueue(1); // A1 seq=0
        qt.enqueue(1); // A2 seq=1
        qt.enqueue(2); // B1 seq=2 (unrelated, can proceed independently)
        // drain: A1 issues (PA 1 → scoreboard). A2 blocked (same PA).
        //        B1 issues (PA 2 → scoreboard, slot free, unrelated PA).
        qt.drainPending();
        assert(qt._issuedSeq.size() == 2);
        assert(qt._issuedSeq[0] == 0); // A1
        assert(qt._issuedSeq[1] == 2); // B1 (unrelated PA proceeds)
        assert(qt._pendingQueue.size() == 1); // A2 still queued
        assert(qt._pendingQueue[0].seq == 1); // A2
        // Complete A1 → slot free, A2 can issue.
        qt.completeAndDrain(1);
        assert(qt._issuedSeq.size() == 3);
        assert(qt._issuedSeq[2] == 1); // A2 issued after A1
        assert(qt._pendingQueue.empty());
    }

    // Scenario B: unrelated PAs proceed when head PA blocked.
    {
        QueueTest qt(2);
        // Queue: A1, B1, A2 — but A is already in scoreboard (in flight).
        qt._scoreboard.insert(1); // PA 1 busy
        qt.enqueue(1); // A1
        qt.enqueue(2); // B1 (unrelated)
        qt.enqueue(1); // A2
        qt.drainPending();
        // A1 blocked (PA 1 busy), B1 issues (PA 2 free), A2 blocked.
        assert(qt._issuedSeq.size() == 1);
        assert(qt._issuedSeq[0] == 1); // B1 (seq 1) issued
        assert(qt._pendingQueue.size() == 2); // A1, A2 remain
        // Verify A1 (seq 0) is before A2 (seq 2) in the remaining queue.
        assert(qt._pendingQueue[0].seq < qt._pendingQueue[1].seq);
        // Complete A's flight, drain again.
        qt.completeAndDrain(1);
        // Now A1 and A2 can issue (2 slots, only B is busy now).
        // A1 should issue, A2 too (slots available).
        assert(qt._issuedSeq.size() >= 2); // at least B1 + A1
        // Verify A1 issued before A2.
        auto a1it = std::find(qt._issuedSeq.begin(), qt._issuedSeq.end(), 0);
        auto a2it = std::find(qt._issuedSeq.begin(), qt._issuedSeq.end(), 2);
        assert(a1it < a2it); // FIFO per PA preserved
    }

    // Scenario C: zero slots → none issued, all re-queued.
    {
        QueueTest qt(2);
        qt._scoreboard.insert(3); // already using slot 0
        qt._scoreboard.insert(4); // already using slot 1
        qt.enqueue(5);
        qt.enqueue(6);
        qt.drainPending();
        assert(qt._issuedSeq.empty());
        assert(qt._pendingQueue.size() == 2); // both re-queued
        // Complete 3, drain: should issue 5.
        qt.completeAndDrain(3);
        assert(qt._issuedSeq.size() == 1);
    }

    // Scenario D: 8 active + 9th queued → busy semantics.
    {
        QueueTest qt(8);
        // Fill all 8 slots with distinct PAs.
        for (int i = 0; i < 8; ++i) {
            qt._scoreboard.insert(100 + i);
        }
        // Enqueue 9th item (PA 200).
        qt.enqueue(200);
        qt.drainPending();
        assert(qt._issuedSeq.empty()); // no free slot
        assert(qt._pendingQueue.size() == 1); // re-queued
        // Complete one flight → 9th should issue.
        qt.completeAndDrain(100);
        assert(qt._issuedSeq.size() == 1);
        assert(qt._pendingQueue.empty());
    }

    // Scenario E: same PA write/read FIFO interleaved.
    {
        QueueTest qt(3);
        qt.enqueue(1); // W1 seq=0
        qt.enqueue(1); // R1 seq=1
        qt.enqueue(2); // W2 seq=2 (unrelated)
        qt.enqueue(1); // W3 seq=3
        qt.drainPending();
        // W1, R1, W2 should issue (3 slots, 3 distinct-ish PAs for first 2,
        // but W1 occupies PA 1 so R1 is blocked, then W2 proceeds).
        // Actually: W1 issues (PA 1 → scoreboard), R1 blocked, W2 issues (PA 2 → scoreboard),
        // W3 blocked.
        assert(qt._issuedSeq[0] == 0); // W1
        assert(qt._issuedSeq[1] == 2); // W2 (skipped R1)
        assert(qt._pendingQueue.size() == 2); // R1 (seq 1), W3 (seq 3)
        assert(qt._pendingQueue[0].seq == 1); // R1 before W3 (same PA)
        assert(qt._pendingQueue[1].seq == 3);
        // Complete W1, drain: R1 issues (PA 1 now free).
        qt.completeAndDrain(1);
        assert(qt._issuedSeq[2] == 1); // R1
        // W3 still queued (PA 1 busy again by R1)
        assert(qt._pendingQueue.size() == 1);
        assert(qt._pendingQueue[0].seq == 3); // W3
        // Complete R1, drain: W3 issues.
        qt.completeAndDrain(1);
        assert(qt._issuedSeq[3] == 3); // W3
        assert(qt._pendingQueue.empty());
    }

    std::fprintf(stderr, "[TEST] queue scheduling: PASS\n");
}

int main()
{
    test_status_values();
    test_body_layout();
    test_message_envelope();
    test_queue_scheduling();
    std::fprintf(stderr, "\n[PHASE2-TEST] ALL PASSED\n");
    return 0;
}
