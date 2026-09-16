#include <cassert>
#include <iostream>
#include "protocol/CoherenceMessage.hh"
#include "protocol/ResponseFrame.hh"
#include "gem5/src/mem/ruby/protocol/chi/ep/BoundaryAuthorityTable.hh"
int main()
{
    cc::glob::CoherenceMessage wire;
    wire.h.type = cc::glob::CoherenceMessageType::UpgradeDoneResp;
    wire.h.reqId = 99;
    wire.h.epoch = 888; // requester tuple, never the new permission epoch
    wire.b.upgradeDoneResp.accepted = true;
    wire.b.upgradeDoneResp.committedEpoch = 0x123456789abcdefULL;
    cc::glob::ResponseFrame frame(wire);
    cc::glob::CoherenceMessage copy = frame;
    assert(copy.h.epoch == 888 && copy.h.reqId == 99);
    assert(copy.b.upgradeDoneResp.committedEpoch == 0x123456789abcdefULL);
    gem5::ruby::BoundaryAuthorityTable bank(64, 8);
    auto token = bank.tryReserve(0, 1).token;
    gem5::ruby::BoundaryBorrowToken borrow;
    assert(bank.borrow(token, borrow) == gem5::ruby::BoundaryResult::Applied);
    bank.metadata(token)->epoch = copy.b.upgradeDoneResp.committedEpoch;
    assert(bank.retireByControl(token, 1) == gem5::ruby::BoundaryResult::Stale);
    assert(bank.ownsBorrow(borrow, token));
    assert(bank.dropBorrow(borrow) == gem5::ruby::BoundaryResult::Applied);
    std::cout << "PASS full-width committed epoch response and old-retirement rejection\n";
}
