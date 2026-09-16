#include <cassert>
#include <iostream>
#include "gem5/src/mem/ruby/protocol/chi/ep/BoundaryAuthorityTable.hh"
#include "gem5/src/mem/ruby/protocol/chi/ep/BoundaryTransactions.hh"
#include "gem5/src/mem/ruby/protocol/chi/ep/BoundedResponseCredits.hh"
using namespace gem5::ruby;
using A = BoundaryAuthorityTable;
using R = BoundaryResult;
using O = BoundaryOperation;
using P = BoundaryPool;
using T = BoundaryTransactions;

static void keys() {
    uint32_t k = 99;
    assert(A::packKey(63, 3, A::SegmentBytes - 64, 3, k));
    assert(k == 0x7fffffff);
    assert(!A::packKey(64, 0, 0, 0, k));
    assert(!A::packKey(0, 4, 0, 0, k));
    assert(!A::packKey(0, 0, A::SegmentBytes, 0, k));
    assert(!A::packKey(0, 0, 1, 0, k));
    assert(!A::packKey(0, 0, 0, 4, k));
    cc::glob::NodeAddressMap map(8, 4);
    auto addr = map.buildDsmPA(7, 5, 128, 3);
    assert(A::keyFromAddress(map, 7, addr, 0, k));
    uint32_t expected;
    assert(A::packKey(5, 3, 128, 0, expected) && expected == k);
    assert(!A::keyFromAddress(map, 6, addr, 0, k));
    assert(!A::keyFromAddress(map, 7, addr + 1, 0, k));
    assert(!A::keyFromAddress(map, -1, addr, 0, k));
    cc::glob::NodeAddressMap wrong(2, 1, 64 * 1024 * 1024);
    assert(!A::keyFromAddress(wrong, 0, wrong.buildDsmPA(0, 1, 0), 0, k));
}

static void saturation() {
    A a;
    assert(a.tryReserve(0x80000000U, 0).result == A::Admission::Invalid);
    for (uint32_t k = 0; k < 4096; ++k)
        assert(a.tryReserve(k, UINT64_MAX, A::Custody).result == A::Admission::Reserved);
    assert(a.occupancy() == 4096);
    auto r = a.tryReserve(4096, 19);
    assert(r.result == A::Admission::ReleaseCandidate);
    const auto oldKey = a.get(r.token)->key;
    const auto old = r.token;
    A::Borrow b;
    assert(a.borrow(old, b) == R::Applied);
    A::Token release;
    assert(a.beginRelease(old, release) == R::Busy);
    assert(a.tryReserve(oldKey, 20).result == A::Admission::Busy);
    assert(a.get(old)->epoch == UINT64_MAX);
    assert(a.dropBorrow(b) == R::Applied);
    assert(a.dropBorrow(b) == R::Duplicate);
    assert(a.setFlags(old, A::Custody | A::Stage) == R::Applied);
    assert(a.beginRelease(old, release) == R::Busy);
    assert(a.setFlags(old, A::Custody) == R::Applied);
    unsigned requests = 0;
    assert(a.requestRelease(old, release, [&](A::Token t, const A::Entry &e) {
        ++requests; assert(t.valid() && e.epoch == UINT64_MAX);
        assert(e.flags & A::Custody); return true;
    }) == R::Applied);
    assert(requests == 1 && !a.get(old));
    assert(a.completeRelease(old, true) == R::Stale);
    assert(a.completeRelease(release, false) == R::NotReady);
    assert(a.tryReserve(oldKey, 20).result == A::Admission::Busy);
    assert(a.borrow(release, b) == R::Busy);
    assert(a.occupancy() == 4096);
    assert(a.completeRelease(release, true) == R::Applied);
    assert(a.completeRelease(release, true) == R::Duplicate);
    assert(a.tryReserve(4096, 19).result == A::Admission::Reserved);
    assert(a.completeRelease(release, true) == R::Stale);
    assert(a.occupancy() == 4096);
}

static void borrowingAndCancel() {
    A a;
    auto t = a.tryReserve(0, 1).token;
    std::array<A::Borrow, 73> refs;
    for (auto &b : refs) assert(a.borrow(t, b) == R::Applied);
    A::Borrow extra;
    assert(a.borrow(t, extra) == R::Full && !extra.valid());
    auto bad = refs[0]; ++bad.generation;
    assert(a.dropBorrow(bad) == R::Stale);
    assert(a.get(t)->refs == 73);
    for (auto b : refs) assert(a.dropBorrow(b) == R::Applied);
    A::Token release;
    assert(a.requestRelease(t, release, [](A::Token, const A::Entry &) {
        return false;
    }) == R::NotReady);
    assert(!release.valid());
    t = a.find(0);
    assert(a.beginRelease(t, release) == R::Applied);
    assert(a.cancelRelease(release) == R::Applied);
    assert(a.completeRelease(release, true) == R::NotReady);
    A::Token next;
    assert(a.beginRelease(a.find(0), next) == R::Applied);
    assert(a.completeRelease(release, true) == R::Stale);
    assert(a.completeRelease(next, true) == R::Applied);
}

static void generationFence() {
    A a(3);
    // Pin seven ways; recycle only the remaining way until generation exhaustion.
    for (uint32_t k = 1; k < 4096; ++k)
        if (A::setOf(k) == 0)
            assert(a.tryReserve(k, 1, A::Stage).result == A::Admission::Reserved);
    A::Token first;
    for (unsigned n = 0; n < 3; ++n) {
        auto r = a.tryReserve(0, 1);
        assert(r.result == A::Admission::Reserved);
        if (!n) first = r.token;
        A::Token release;
        assert(a.beginRelease(r.token, release) == R::Applied);
        assert(a.completeRelease(release, true) == R::Applied);
    }
    auto last = a.tryReserve(0, 1);
    assert(last.result == A::Admission::Reserved && last.token.generation == 7);
    A::Token release;
    assert(a.beginRelease(last.token, release) == R::Exhausted);
    assert(a.completeRelease(first, true) == R::Stale);
    uint32_t colliding = 4096;
    while (A::setOf(colliding) != 0) ++colliding;
    assert(a.tryReserve(colliding, 1).result == A::Admission::Busy);
}

static void transactions() {
    T t;
    A a;
    auto candidate = a.tryReserve(0, 3).token;
    A::Token releasing;
    assert(a.beginRelease(candidate, releasing) == R::Applied);
    assert(!t.write(1, 1, 0, 1, 0).valid());
    assert(!t.recall(0, 1, 0, true, 0, 0).valid());
    assert(!t.write(0, 1, -1, 1, 0).valid());
    assert(t.occupancy() == 0);
    std::array<T::Token, 64> writes;
    for (unsigned i = 0; i < 64; ++i) {
        writes[i] = t.write(i * 64, 3, 0, i + 1, 0);
        assert(writes[i].valid());
    }
    for (unsigned i = 64; i < 72; ++i)
        assert(t.recall(i * 64, 3, 0, true, i + 1, 0).valid());
    assert(!t.write(72 * 64, 3, 0, 100, 0).valid());
    assert(!t.recall(72 * 64, 3, 0, true, 100, 0).valid());
    T::Token escape;
    assert(t.tryOperation(72 * 64, 3, 0, O::Read, 100, 0,
        P::Escape, releasing, {1, 0}, escape, a) == R::Invalid);
    assert(t.occupancy() == 72);
    assert(t.tryOperation(72 * 64, 3, 0, O::Release, 100, 0,
        P::Escape, releasing, {}, escape, a) == R::Applied);
    assert(escape.slot == 72 && t.occupancy() == 73);
    T::Token other;
    auto candidate2 = a.tryReserve(1, 3).token;
    A::Token releasing2;
    assert(a.beginRelease(candidate2, releasing2) == R::Applied);
    assert(t.tryOperation(73 * 64, 3, 0, O::Release, 101, 0,
        P::Escape, releasing2, {}, other, a) == R::Full);
    assert(t.finishOperation(escape, O::Read, 100, 0) == R::Invalid);
    auto bad = escape; ++bad.generation;
    assert(t.finishOperation(bad, O::Release, 100, 0) == R::Stale);
    assert(t.finishOperation(escape, O::Release, 100, 0) == R::Applied);
    assert(t.finishOperation(escape, O::Release, 100, 0) == R::Duplicate);
    assert(t.occupancy() == 72);
    auto w = writes[0];
    assert(t.finishWriteResult(w, 1, 0) == R::NotReady);
    assert(t.publication(w, 1, 0, 900));
    assert(!t.publication(w, 1, 0, 901));
    assert(t.finishWriteResult(w, 1, 0) == R::Applied);
    assert(t.get(w)); // publication before Recall must keep credit
    assert(t.finishWriteResult(w, 1, 0) == R::Duplicate);
    // All eight execution-control credits are occupied. A persisted late
    // receipt retains its ordinary credit, but cannot bypass control admission.
    assert(!t.recall(0, 3, 0, true, 900, 0).valid());
    const auto control = t.find(64 * 64);
    const auto *controlEntry = t.get(control);
    assert(controlEntry);
    assert(t.finishRecallResult(control, controlEntry->recallId,
        controlEntry->recallSocket) == R::Applied);
    assert(t.recall(0, 3, 0, true, 900, 0).valid());
    assert(t.finishRecallResult(w, 900, 0) == R::Applied);
    assert(!t.get(w));
    assert(t.finishRecallResult(w, 900, 0) == R::Duplicate);
    assert(t.write(0, 4, 0, 1000, 0).valid());
    assert(t.finishRecallResult(w, 900, 0) == R::Stale);
}

static void genericAndCredits() {
    A a; T t; BoundedResponseCredits<> q;
    auto stable = a.tryReserve(0, 42, A::Custody).token;
    A::Borrow borrow;
    assert(a.borrow(stable, borrow) == R::Applied);
    T::Token ticket;
    assert(t.tryOperation(0, 42, 0, O::Read, 88, 0, P::Ordinary,
                          stable, borrow, ticket, a) == R::Applied);
    T::Token dup;
    assert(t.tryOperation(0, 42, 0, O::Read, 88, 0, P::Ordinary,
                          stable, borrow, dup, a) == R::Duplicate);
    auto corrupt = borrow; ++corrupt.generation;
    assert(t.tryOperation(64, 42, 0, O::Grant, 89, 0, P::Ordinary,
                          stable, corrupt, dup, a) == R::Stale);
    assert(t.occupancy() == 1);
    BoundedResponseCredits<>::Index index{ticket.generation, uint16_t(ticket.slot)};
    assert(q.reserve(index) == R::Applied);
    assert(q.publish(index) == R::Applied);
    assert(q.publish(index) == R::Duplicate);
    assert(q.retire(index) == R::Applied);
    assert(q.retire(index) == R::Duplicate);
    assert(t.recall(0, 42, 0, true, 99, 0).valid());
    assert(t.finishOperation(ticket, O::Read, 88, 0) == R::Applied);
    assert(t.get(ticket));
    A::Token release;
    assert(a.beginRelease(stable, release) == R::Busy);
    assert(t.finishRecallResult(ticket, 99, 0) == R::Applied);
    assert(!t.get(ticket));
    assert(a.dropBorrow(borrow) == R::Applied);
    assert(a.beginRelease(stable, release) == R::Applied);
    assert(a.completeRelease(release, true) == R::Applied);
    for (unsigned i = 0; i < 73; ++i) {
        assert(q.reserve({2, uint16_t(i)}) == R::Applied);
        assert(q.publish({2, uint16_t(i)}) == R::Applied);
    }
    assert(q.size() == 73);
    assert(q.retire({2, 72}) == R::NotReady);
    for (unsigned i = 0; i < 73; ++i)
        assert(q.retire({2, uint16_t(i)}) == R::Applied);
    assert(q.publish(index) == R::Stale);
}
// Partial lifecycle only: stage represents a native partial-data / unpublished
// Doom obligation. No CHI model or data synthesis is substituted for runtime.
static void partialPressure() {
    A a; T t;
    std::array<A::Token, 8> pinned;
    unsigned n = 0;
    for (uint32_t k = 0; k < 4096; ++k) {
        auto r = a.tryReserve(k, 9, A::Custody | A::Stage);
        assert(r.result == A::Admission::Reserved);
        if (A::setOf(k) == A::setOf(4096)) pinned[n++] = r.token;
    }
    assert(n == 8);
    assert(a.tryReserve(4096, 10).result == A::Admission::Busy);
    A::Token release;
    assert(a.beginRelease(pinned[0], release) == R::Busy);
    assert(a.setFlags(pinned[0], A::Custody) == R::Applied);
    assert(a.tryReserve(4096, 10).result == A::Admission::ReleaseCandidate);
    for (O op : {O::Read, O::Grant, O::Upgrade, O::Invalidate}) {
        auto stable = pinned[0];
        A::Borrow b;
        assert(a.borrow(stable, b) == R::Applied);
        T::Token ticket;
        assert(t.tryOperation(0, 9, 0, op, 7, 0, P::Control,
                              stable, b, ticket, a) == R::Applied);
        assert(a.beginRelease(stable, release) == R::Busy);
        assert(t.finishOperation(ticket, op, 7, 0) == R::Applied);
        assert(t.finishOperation(ticket, op, 7, 0) == R::Duplicate);
        assert(a.dropBorrow(b) == R::Applied);
    }
    assert(a.beginRelease(pinned[0], release) == R::Applied);
    assert(a.completeRelease(release, false) == R::NotReady);
    assert(a.get(release)->flags & A::Custody);
    assert(a.completeRelease(release, true) == R::Applied);
    assert(a.tryReserve(4096, 10).result == A::Admission::Reserved);
}
static void configuredCapacity() {
    // Every supported geometry uses the actual header, including a partial
    // final set. No separate replacement model or altered key hash.
    for (unsigned limit = 1; limit <= A::Capacity; ++limit) {
        A a(64, limit);
        assert(a.capacity() == limit);
        unsigned admitted = 0;
        for (uint32_t k = 0; admitted < limit; ++k) {
            assert(k < A::Capacity * A::Ways);
            auto r = a.tryReserve(k, 7, A::Custody | A::Stage);
            assert(r.result == A::Admission::Reserved ||
                   r.result == A::Admission::Busy);
            if (r.result == A::Admission::Reserved) {
                assert(r.token.slot < limit);
                ++admitted;
            }
        }
        assert(a.occupancy() == limit);
        assert(a.tryReserve(0x7fffffff, 8).result == A::Admission::Busy);
    }
    for (unsigned limit : {0U, A::Capacity + 1, UINT32_MAX}) {
        A a(64, limit);
        assert(a.capacity() == 0 && a.occupancy() == 0);
        assert(a.tryReserve(0, 1).result == A::Admission::Invalid);
        assert(!a.find(0).valid());
        assert(!a.get({1, 0}));
        assert(a.completeRelease({1, 0}, true) == R::Invalid);
    }
    A a(64, 8);
    std::array<A::Borrow, 8> borrows;
    for (unsigned k = 0; k < 8; ++k) {
        auto r = a.tryReserve(k, 7, A::Custody);
        assert(r.result == A::Admission::Reserved);
        assert(a.borrow(r.token, borrows[k]) == R::Applied);
    }
    assert(a.tryReserve(8, 8).result == A::Admission::Busy);
    assert(a.dropBorrow(borrows[3]) == R::Applied);
    auto victim = a.tryReserve(8, 8);
    assert(victim.result == A::Admission::ReleaseCandidate);
    assert(a.get(victim.token)->key == 3);
    A::Token release;
    assert(a.beginRelease(victim.token, release) == R::Applied);
    assert(a.completeRelease(release, false) == R::NotReady);
    assert(a.tryReserve(8, 8).result == A::Admission::Busy);
    assert(a.completeRelease(release, true) == R::Applied);
    assert(a.tryReserve(8, 8).result == A::Admission::Reserved);
    assert(!a.get(victim.token) && a.occupancy() == 8);
}
int main() {
    keys(); saturation(); borrowingAndCancel(); generationFence();
    transactions(); genericAndCredits(); partialPressure(); configuredCapacity();
    std::cout << "PASS production authority geometry: limits1..4096, "
                 "invalid limits fail closed, cap8 borrowed victims blocked, "
                 "release confirmation before reuse\n";
    std::cout << "authority_entry=" << sizeof(A::Entry)
              << " authority_bank=" << sizeof(A)
              << " borrow_record=" << sizeof(A::BorrowRecord)
              << " transaction_entry=" << sizeof(T::Entry)
              << " transaction_bank=" << sizeof(T)
              << " response_credits=" << sizeof(BoundedResponseCredits<>) << '\n';
    std::cout << "PASS production headers: saturation4097, custody, borrowers73, "
                 "release ACK gating, stale/duplicate, 3bit fence, escape73, "
                 "publication-before-recall, merged retirement, response credits\n";
}
