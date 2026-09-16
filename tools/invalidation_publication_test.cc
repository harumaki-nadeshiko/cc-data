// Execute only inside the project Docker image. Exercises the real controller.
#include "modules/ubiomodule/UBCCController.hh"
#include <cassert>
#include <cstdio>
using namespace cc::glob;

struct Outbound : UBCCOutboundIf {
    CoherenceMessage inv;
    bool sendRecallReq(const CoherenceMessage &) override { return true; }
    bool sendInvalidateReq(const CoherenceMessage &m) override { inv = m; return true; }
    bool sendUpgradeAckNotify(const CoherenceMessage &) override { return true; }
    bool sendUpgradeResp(const CoherenceMessage &) override { return true; }
    bool sendGrantPush(const CoherenceMessage &) override { return true; }
};

int main()
{
    UBCCController c(0, 0, nullptr, 64, 0, 0, 1, 3);
    Outbound out;
    c.setOutbound(&out);
    constexpr uint64_t pa = 0x10003600, epoch = 2, parent = 123, write = 456;
    assert(c.debugSeedResidentForTest(pa, static_cast<int>(MESIState::G_S),
                                     3, epoch, false));
    assert(c.processOuterUpgradeReq(pa, 1, epoch, parent, 1,
                                   UBCC_UpgradeCause::LocalStoreUpgrade));
    assert(out.inv.h.reqId == parent && out.inv.h.epoch == epoch);
    auto valid = [&](int node, uint64_t p, uint64_t e, uint64_t w = 456) {
        return c.validateWritebackPersistence(pa, -1, 0, false, 0, w, 0, p, e, node);
    };
    assert(!c.validateWritebackPersistence(pa, -1, 0, false, 0, write, 0));
    assert(valid(0, parent, epoch));
    assert(!valid(1, parent, epoch));
    assert(!valid(64, parent, epoch));
    assert(!valid(0, parent + 1, epoch));
    assert(!valid(0, parent, epoch + 1));
    assert(!c.validateWritebackPersistence(pa, -1, 0, false, 1, write, 0,
                                         parent, epoch, 0));
    assert(c.reserveWritebackPersistence(pa, -1, 0, false, 0, write, 0,
                                        parent, epoch, 0));
    assert(valid(0, parent, epoch));
    assert(!valid(0, parent, epoch, write + 1));
    assert(!valid(0, parent + 1, epoch));
    assert(!c.processInvalidationAck(pa, 0, epoch, parent));
    c.releaseWritebackPersistence(pa, -1, 0, false, 0, write, 0);
    assert(c.processInvalidationAck(pa, 0, epoch, parent));
    assert(!valid(0, parent, epoch));
    assert(c.processOuterUpgradeDone(pa, 1, epoch, parent));
    assert(!valid(0, parent, epoch));
    assert(!c.reserveWritebackPersistence(pa, -1, 0, false, 0, write, 0,
                                         parent, epoch, 0));
    assert(c.getEpochForLine(pa) != epoch);
    std::puts("invalidation_publication_test: PASS (exact parent, reservation, late replay)");
}
