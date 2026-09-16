#include <cassert>
#include <iostream>
#include "BoundaryTransactions.hh"
using namespace gem5::ruby;
int main()
{
    for (bool nativeFirst : {false, true}) {
        BoundaryTransactions bank;
        auto parent = bank.foreground(0x1000, 1, 15, 100, 0);
        bool joined = false;
        assert(!bank.upgrade(0x1000, 2, 15, 200, 0, joined).valid());
        assert(bank.finishForeground(parent, 100, 0,
            BoundaryTransactions::HomeCommit) == BoundaryResult::Applied);
        auto child = bank.upgrade(0x1000, 2, 15, 200, 0, joined);
        assert(joined && child.valid());
        assert(bank.get(parent)->foregroundId == 100);
        assert(bank.creditUsage(BoundaryPool::Ordinary) == 1);
        assert(!bank.upgrade(0x1000, 3, 15, 201, 0, joined).valid());
        assert(bank.finishOperation(child, BoundaryOperation::Upgrade, 201, 0)
               == BoundaryResult::Invalid);
        if (nativeFirst)
            bank.finishForeground(parent, 100, 0, BoundaryTransactions::NativeClose);
        assert(bank.get(parent));
        assert(bank.finishOperation(child, BoundaryOperation::Upgrade, 200, 0)
               == BoundaryResult::Applied);
        if (!nativeFirst) {
            assert(bank.get(parent));
            bank.finishForeground(parent, 100, 0, BoundaryTransactions::NativeClose);
        }
        assert(!bank.get(parent));
        assert(bank.creditUsage(BoundaryPool::Ordinary) == 0);
    }
    // A completed child must not pin the old permission epoch while the
    // parent's independent native observer is still being closed.
    BoundaryTransactions bank;
    auto parent = bank.foreground(0x2000, 1, 0, 10, 0);
    bank.finishForeground(parent, 10, 0, BoundaryTransactions::HomeCommit);
    bool joined;
    auto child = bank.upgrade(0x2000, 2, 0, 11, 0, joined);
    bank.finishOperation(child, BoundaryOperation::Upgrade, 11, 0);
    auto recall = bank.recall(0x2000, 3, 0, true, 12, 0);
    assert(recall.valid());
    assert(bank.get(parent)->foregroundId == 10);
    bank.finishForeground(parent, 10, 0, BoundaryTransactions::NativeClose);
    assert(bank.get(recall));
    assert(bank.finishRecall(recall, 12, 0));
    assert(bank.occupancy() == 0);
    std::cout << "PASS joined upgrade preserves parent NativeClose, both completion orders\n";
}
