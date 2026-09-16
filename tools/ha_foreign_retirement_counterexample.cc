#include "modules/hamodule/HAController.hh"
#include <cassert>
#include <iostream>

// Production Home state-machine schedule, not a replacement protocol model.
// Exit 1 means that the old escape cannot obtain a retirement receipt.
int main()
{
    using C = cc::ha::HAController;
    using R = cc::ha::HolderLeases::Result;
    C::Config cfg;
    cfg.directory.base = 0;
    cfg.directory.bytes = 64 * 1024;
    cfg.directory.nodeCount = 3;
    C home(cfg);
    constexpr uint64_t k = 64;
    auto drain = [&] { while (home.hasAction()) home.popAction(); };
    assert(home.reserveHolder(k, 0));
    home.directoryForTest().set(k, 0, true);
    assert(home.commitHolder(k, 0, 10));
    assert(home.reserveHolder(k, 1));
    assert(home.submit({k, 1, C::RequestKind::Write, 11,
                        C::Payload::fromU64(0x12345678)}));
    drain();
    assert(home.releaseHolder(k, 0, 10, 20, 0) == R::Busy);
    home.accept({C::EventKind::InvalidateAck, k, 0, 11});
    drain();
    home.accept({C::EventKind::InstallAck, k, 1, 11});
    drain();
    assert(home.commitHolder(k, 1, 11));
    assert(!home.busy(k));
    assert(home.directory().sharers(k) == 2);
    const auto retired = home.releaseHolder(k, 0, 10, 20, 0);
    assert(home.directory().sharers(k) == 2);
    // A new same-K installation must never be removed by the old escape.
    assert(home.reserveHolder(k, 0));
    home.directoryForTest().set(k, 0, true);
    assert(home.commitHolder(k, 0, 12));
    const auto replay = home.releaseHolder(k, 0, 10, 20, 0);
    assert(home.directory().sharers(k) == 3);
    assert(retired == R::AlreadyRetiredMatch);
    assert(replay == R::AlreadyRetiredMatch);
    assert(home.releaseHolder(k, 0, 10, 20, 1) == R::Invalid);
    assert(home.releaseHolder(k + 64, 0, 10, 20, 0) == R::Invalid);
    assert(home.releaseHolder(k, 0, 12, 20, 0) == R::Invalid);
    assert(home.releaseHolder(k, 2, 10, 20, 0) == R::Stale);
    assert(home.releaseHolder(k, 0, 12, 21, 0) == R::Busy);
    assert(home.directory().sharers(k) == 3);
    assert(!home.acknowledgeRelease(k, 0, 10, 20, 1));
    assert(home.releaseHolder(k, 0, 10, 20, 0) == R::AlreadyRetiredMatch);
    assert(home.acknowledgeRelease(k, 0, 10, 20, 0));
    assert(home.releaseHolder(k, 0, 10, 20, 0) == R::Stale);
    assert(home.directory().sharers(k) == 3);
    std::cout << "PASS: exact registered foreign retirement, bounded credit, lease12 preserved\n";
    // First arrival at each foreign-control boundary. An unregistered late
    // request must fail closed, including before a successor is installed.
    for (unsigned arrival = 0; arrival != 4; ++arrival) {
        C h(cfg);
        auto flush = [&] { while (h.hasAction()) h.popAction(); };
        assert(h.reserveHolder(k, 0));
        h.directoryForTest().set(k, 0, true);
        assert(h.commitHolder(k, 0, 10));
        assert(h.reserveHolder(k, 1));
        assert(h.submit({k, 1, C::RequestKind::Write, 11,
                         C::Payload::fromU64(0xabcdef01)}));
        flush();
        if (arrival == 0) assert(h.releaseHolder(k, 0, 10, 20, 0) == R::Busy);
        h.accept({C::EventKind::InvalidateAck, k, 0, 11});
        flush();
        if (arrival == 1) assert(h.releaseHolder(k, 0, 10, 20, 0) == R::Busy);
        h.accept({C::EventKind::InstallAck, k, 1, 11});
        flush();
        assert(h.commitHolder(k, 1, 11));
        if (arrival == 3) {
            assert(h.reserveHolder(k, 0));
            h.directoryForTest().set(k, 0, true);
            assert(h.commitHolder(k, 0, 12));
        }
        const auto expected = arrival < 2 ? R::AlreadyRetiredMatch : R::Stale;
        for (unsigned retry = 0; retry != 3; ++retry)
            assert(h.releaseHolder(k, 0, 10, 20, 0) == expected);
        assert(h.directory().sharers(k) == (arrival == 3 ? 3 : 2));
    }
    std::cout << "PASS: Busy before/after InvAck, unknown after retirement, successor and repeated arrival\n";
    return 0;
}
