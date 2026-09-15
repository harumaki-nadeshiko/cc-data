#include "modules/hamodule/HAController.hh"
#include <cassert>
#include <iostream>
using C = cc::ha::HAController;
using R = cc::ha::HolderLeases::Result;

static void drain(C &c) { while (c.hasAction()) c.popAction(); }
int main() {
    C::Config cfg;
    cfg.directory.base = 0;
    cfg.directory.bytes = 64 * 1024;
    cfg.directory.nodeCount = 3;
    C c(cfg);
    constexpr uint64_t k = 64;
    assert(c.reserveHolder(k, 0));
    c.directoryForTest().set(k, 0, true);
    assert(c.commitHolder(k, 0, 1));
    assert(c.reserveHolder(k, 1));
    assert(c.submit({k, 1, C::RequestKind::Read, 2}));
    drain(c);
    c.accept({C::EventKind::OwnerData, k, 0, 2, C::Payload::fromU64(42)});
    drain(c);
    c.accept({C::EventKind::PersistenceComplete, k, 1, 2});
    drain(c);
    // Reproduce the foreign NeedInstall window. A release cannot clear A
    // before B's captured oldSharers is committed.
    assert(c.busy(k));
    assert(c.releaseHolder(k, 0, 1, 17, 0) == R::Busy);
    assert(c.directory().sharers(k) == 1);
    c.accept({C::EventKind::InstallAck, k, 1, 2});
    assert(c.commitHolder(k, 1, 2));
    drain(c);
    assert(c.releaseHolder(k, 0, 1, 17, 0) == R::Applied);
    assert(c.directory().sharers(k) == 2);
    assert(c.reserveHolder(k, 0));
    c.directoryForTest().set(k, 0, true);
    assert(c.commitHolder(k, 0, 3));
    assert(c.releaseHolder(k, 0, 1, 17, 0) == R::Duplicate);
    assert(c.directory().sharers(k) == 3);
    assert(c.releaseHolder(k, 0, 1, 18, 0) == R::Stale);
    assert(c.releaseHolder(k, 0, 3, 17, 0) == R::Invalid);
    assert(c.releaseHolder(k, 0, 3, 18, 4) == R::Invalid);
    assert(c.directory().sharers(k) == 3);
    assert(c.acknowledgeRelease(k, 0, 1, 17, 0));
    assert(c.releaseHolder(k, 0, 3, 18, 0) == R::Applied);
    assert(c.directory().sharers(k) == 2);
    cc::ha::HolderLeases bank(3);
    assert(c.releaseHolder(k + 1, 0, 3, 19, 0) == R::Invalid);
    assert(c.releaseHolder(k, 64, 3, 19, 0) == R::Invalid);
    assert(c.releaseHolder(k, 0, 3, 0, 0) == R::Invalid);
    assert(c.directory().sharers(k) == 2);
    for (unsigned i = 0; i < bank.PerNode; ++i) assert(bank.reserve(i, 0));
    assert(!bank.reserve(bank.PerNode, 0));
    assert(bank.reserve(bank.PerNode, 1));
    cc::ha::HolderLeases pending(3);
    assert(pending.reserve(1, 0));
    assert(pending.commit(1, 0, 10, 1));
    assert(pending.reserve(1, 0)); // queued same-node successor
    assert(pending.reserve(1, 1));
    assert(pending.commit(1, 1, 11, 2)); // Write invalidates old A
    assert(pending.current(1, 0) == 0);
    assert(pending.commit(1, 0, 12, 1)); // reservation must survive invalidation
    assert(pending.release(1, 0, 10, 20, 0, false) == R::Stale);
    assert(pending.current(1, 0) == 12);
    assert(pending.release(1, 0, 12, 21, 0, false) == R::Applied);
    assert(pending.reserve(2, 0));
    assert(pending.commit(2, 0, 13, 1));
    assert(pending.acknowledge(1, 0, 12, 21, 0));
    assert(pending.release(2, 0, 13, 22, 0, false) == R::Applied);
    assert(pending.reserve(1, 0));
    assert(pending.commit(1, 0, 14, 1));
    // Older than the serial escape replay receipt: fail closed, never rerun.
    assert(pending.release(1, 0, 12, 21, 0, false) == R::Stale);
    assert(pending.current(1, 0) == 14);
    std::cout << "HA conditional release PASS Home_receipt_bytes=" << bank.bytes() << '\n';
}
