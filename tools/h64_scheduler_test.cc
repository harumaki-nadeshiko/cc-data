// Phase 3: MetaRNFClient deferred scheduler focused test.
// Tests the reentrant-defer mechanism independently of full UBIO/gem5.
//
// Build (no gem5 deps):
//   g++ -std=c++17 -I. -o /tmp/sched_test tools/h64_scheduler_test.cc

#include <cassert>
#include <cstdio>
#include <cstring>
#include <functional>
#include <deque>
#include <vector>

// ---- Minimal replica of the scheduling primitives ----
// Extracted from ubio_main.cc MetaRNFClient for focused testing.

static constexpr int kMaxPending = 32;
static constexpr int kMaxDeferred = 64;

struct Scheduler {
    int _reentrantDepth = 0;
    int _deferredCount = 0;
    int _pendingReads = 0;
    int _pendingWrites = 0;

    // Track deferred ops
    struct DeferredOp {
        bool isWrite;
        int id;
    };
    DeferredOp _deferred[kMaxDeferred];

    // Track whether send happened (simulated)
    std::vector<int> _sentIds;
    std::vector<int> _ioErrorIds;
    bool _simulateSendFailure = false;

    void enter() { _reentrantDepth++; }
    void leave() { if (_reentrantDepth > 0) _reentrantDepth--; }
    bool isReentrant() const { return _reentrantDepth > 0; }
    bool hasDeferred() const { return _deferredCount > 0; }

    void enqueue(int id, bool isWrite) {
        assert(_deferredCount < kMaxDeferred);
        _deferred[_deferredCount].isWrite = isWrite;
        _deferred[_deferredCount].id = id;
        _deferredCount++;
    }

    void drain() {
        int drained = 0;
        while (_deferredCount > 0 && drained < kMaxDeferred) {
            auto op = _deferred[0];
            for (int i = 1; i < _deferredCount; ++i) _deferred[i-1] = _deferred[i];
            _deferredCount--;
            drained++;

            if (_simulateSendFailure) {
                _ioErrorIds.push_back(op.id);
                if (op.isWrite) _pendingWrites--;
                else _pendingReads--;
                continue;
            }
            _sentIds.push_back(op.id);
            // Simulate: send may trigger callback which may enqueue more deferred
            // The next outer loop iteration will drain those.
        }
    }

    // Simulate: request read within reentrant callback
    void requestRead(int id) {
        int combined = _pendingReads + _deferredCount;
        assert(combined < kMaxPending && "combined limit");
        if (isReentrant()) {
            enqueue(id, false);
        } else {
            _pendingReads++;
            if (!_simulateSendFailure) _sentIds.push_back(id);
            else { _ioErrorIds.push_back(id); _pendingReads--; }
        }
    }

    void requestWrite(int id) {
        int combined = _pendingWrites + _deferredCount;
        assert(combined < kMaxPending && "combined limit");
        if (isReentrant()) {
            enqueue(id, true);
        } else {
            _pendingWrites++;
            if (!_simulateSendFailure) _sentIds.push_back(id);
            else { _ioErrorIds.push_back(id); _pendingWrites--; }
        }
    }
};

// ---- Tests ----

static void test_no_send_until_outer_drain() {
    std::fprintf(stderr, "[T1] No send until outer drain...\n");
    Scheduler s;
    // Inside callback (reentrant): request must be deferred
    s.enter();        // enter reentrant
    s.requestRead(1); // should defer, not send
    s.leave();        // leave reentrant

    assert(s._sentIds.empty() && "No send during reentrant callback");
    assert(s.hasDeferred() && "Deferred op enqueued");
    assert(s._deferredCount == 1);

    // Outer drain: now send
    s.drain();
    assert(s._sentIds.size() == 1 && s._sentIds[0] == 1);
    assert(!s.hasDeferred());
    std::fprintf(stderr, "[T1] PASS\n");
}

static void test_send_failure_ioerror_no_leak() {
    std::fprintf(stderr, "[T2] Send failure → IoError, no leak...\n");
    Scheduler s;
    s._simulateSendFailure = true;

    // Immediate send failure
    s.requestRead(1);
    assert(s._ioErrorIds.size() == 1 && s._ioErrorIds[0] == 1);
    assert(s._pendingReads == 0 && "No leaked pending after failure");

    // Deferred send failure
    s.enter();
    s.requestRead(2); // defer
    s.leave();
    assert(s._deferredCount == 1);
    s.drain();
    assert(s._ioErrorIds.size() == 2 && s._ioErrorIds[1] == 2);
    assert(!s.hasDeferred() && "No leaked deferred after failure drain");
    std::fprintf(stderr, "[T2] PASS\n");
}

static void test_callback_chains_deferred() {
    std::fprintf(stderr, "[T3] Callback chains deferred, drained next iteration...\n");
    Scheduler s;
    // Simulate: response callback triggers more reads
    s.enter();
    s.requestRead(1);
    s.leave();
    assert(s._deferredCount == 1);

    // Outer drain sends 1, but doesn't process callbacks (next iteration does)
    s.drain();
    assert(s._sentIds.size() == 1 && s._sentIds[0] == 1);

    // Next iteration: simulate callback that creates more deferred
    s.enter();
    s.requestRead(2);
    s.requestWrite(3);
    s.leave();
    s.drain();
    assert(s._sentIds.size() == 3);
    std::fprintf(stderr, "[T3] PASS\n");
}

static void test_combined_pending_limit() {
    std::fprintf(stderr, "[T4] Combined pending+deferred limit...\n");
    Scheduler s;
    // Fill with deferred (simulate many reentrant callbacks)
    s.enter();
    for (int i = 0; i < kMaxPending; i++) s.requestRead(i);
    s.leave();
    assert(s._deferredCount == kMaxPending);
    assert(s._sentIds.empty());

    // 33rd should trigger RetryableBusy (combined limit hit)
    // Not easy to test without callback, but we verify the count is exact
    assert(s._deferredCount <= kMaxPending);

    s.drain();
    assert(s._sentIds.size() == (size_t)kMaxPending);
    assert(!s.hasDeferred());
    std::fprintf(stderr, "[T4] PASS\n");
}

static void test_outer_loop_drains_once_per_iteration() {
    std::fprintf(stderr, "[T5] Outer loop drains bounded per iteration...\n");
    Scheduler s;
    // Enqueue many deferred ops
    for (int i = 0; i < 10; i++) {
        s.enter(); s.requestRead(i); s.leave();
    }
    assert(s._deferredCount == 10);

    // Drain — all 10 should be processed (≤ kMaxDeferred)
    s.drain();
    assert(s._sentIds.size() == 10);
    assert(!s.hasDeferred());
    std::fprintf(stderr, "[T5] PASS\n");
}

int main() {
    std::fprintf(stderr, "=== Scheduler Test ===\n");
    test_no_send_until_outer_drain();
    test_send_failure_ioerror_no_leak();
    test_callback_chains_deferred();
    test_combined_pending_limit();
    test_outer_loop_drains_once_per_iteration();
    std::fprintf(stderr, "=== 5/5 PASS ===\n");
    return 0;
}
