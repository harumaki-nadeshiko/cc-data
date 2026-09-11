#include "modules/hamodule/HAController.hh"

#include <iostream>

// Integration blocker reproducer: an old node release must not erase a
// subsequent install by that node. Nonzero exit keeps the performance gate shut.
int main()
{
    using HA = cc::ha::HAController;
    constexpr std::uint64_t pa = 0x100000;
    HA ha({{pa, 64, 64, 2}, 4});
    ha.directoryForTest().setSharers(pa, 1);
    const auto data = HA::Payload::fromU64(0xb100);
    ha.accept({HA::EventKind::Writeback, pa, 0, 10, data, false, true});
    while (ha.hasAction()) ha.popAction();
    const bool accepted = ha.submit({pa, 0, HA::RequestKind::Write, 11, data});
    bool granted = false;
    bool retry = false;
    while (ha.hasAction()) {
        const auto action = ha.popAction();
        granted |= action.kind == HA::ActionKind::GrantWrite;
        retry |= action.kind == HA::ActionKind::Reject && !action.permanentReject;
    }
    if (!accepted && retry && !granted) {
        ha.accept({HA::EventKind::PersistenceComplete, pa, 0, 10});
        if (!ha.submit({pa, 0, HA::RequestKind::Write, 12, data})) return 2;
        while (ha.hasAction()) {
            const auto action = ha.popAction();
            granted |= action.kind == HA::ActionKind::GrantWrite;
        }
        if (!granted) return 2;
        ha.accept({HA::EventKind::InstallAck, pa, 0, 12});
    } else {
        ha.accept({HA::EventKind::InstallAck, pa, 0, 11});
    }
    ha.accept({HA::EventKind::PersistenceComplete, pa, 0, 10});
    if (ha.directory().sharers(pa) != 1) {
        std::cerr << "FAIL: old release cleared newly installed node holder\n";
        return 1;
    }
    std::cout << "PASS: new holder survived old release\n";
}
