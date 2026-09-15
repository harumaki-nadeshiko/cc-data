#include "../gem5/src/mem/ruby/protocol/chi/ep/BoundaryTransactions.hh"
#include "../gem5/src/mem/ruby/protocol/chi/ep/BoundaryQueue.hh"
#include "../gem5/src/mem/ruby/protocol/chi/ep/BoundarySlots.hh"
#include <cstdlib>
#include <iostream>

using gem5::ruby::BoundaryTransactions;
static void check(bool value) { if (!value) std::abort(); }
int main()
{
    gem5::ruby::BoundaryQueue<unsigned, 64> queue;
    for (unsigned i = 0; i < 64; ++i) check(queue.push_back(i));
    check(!queue.push_back(100));
    check(queue.size() == 64);
    auto second = queue.erase(queue.begin());
    check(*second == 1 && queue.size() == 63);
    check(queue.push_back(64));
    unsigned expected = 1;
    for (auto value : queue) check(value == expected++);
    while (!queue.empty()) queue.erase(queue.begin());
    check(queue.begin() == queue.end());
    gem5::ruby::BoundarySlots<unsigned, unsigned, 64> slots;
    for (unsigned i = 0; i < 64; ++i) check(slots.emplace(i, i + 1).second);
    check(!slots.emplace(64, 65).second);
    check(!slots.emplace(0, 100).second);
    auto survivor = slots.find(63);
    auto *survivorAddress = &survivor->second;
    for (auto it = slots.begin(); it != slots.end(); ) {
        auto current = it++;
        if (current->first != 63) slots.erase(current);
    }
    check(slots.size() == 1 && survivor->second == 64);
    check(slots.emplace(0, 101).second);
    check(&slots.find(63)->second == survivorAddress);
    check(!slots.count(62));
    BoundaryTransactions bank;
    check(!bank.write(0, 9, 2, 0, 1).valid());
    check(!bank.recall(0, 9, 2, true, 0, 1).valid());
    check(!bank.write(0, 9, 2, 1, -1).valid());
    check(!bank.recall(0, 9, 2, true, 1, -1).valid());
    check(!bank.canRecall(0, 0, 1));
    check(bank.occupancy() == 0);
    BoundaryTransactions::Token first;
    for (unsigned i = 0; i < BoundaryTransactions::OrdinaryCapacity; ++i) {
        auto t = bank.write(i * 64, 9, 2, i + 1, 1);
        check(t.valid());
        if (!i) first = t;
    }
    check(!bank.write(64 * 64, 9, 2, 100, 1).valid());
    for (unsigned i = 64; i < 72; ++i)
        check(bank.recall(i * 64, 9, 2, true, i + 100, 0).valid());
    check(bank.occupancy() == 72);
    check(!bank.canRecall(72 * 64, 500, 0));
    check(!bank.recall(72 * 64, 9, 2, true, 500, 0).valid());
    // A merged control still needs a control credit; sharing a physical slot
    // must not bypass the eight-control budget.
    check(!bank.recall(0, 9, 2, true, 201, 0).valid());
    check(bank.finishRecall(bank.find(64 * 64), 164, 0));
    // Recall-first and WB-first both share a slot at full occupancy.
    auto t = bank.recall(0, 9, 2, true, 201, 0);
    check(t.slot == first.slot && t.generation == first.generation);
    check(!bank.canRecall(0, 202, 0));
    check(bank.canRecall(0, 201, 0));
    check(!bank.publication(t, 1, 0, 201));
    check(!bank.publication(t, 1, 1, 202));
    check(bank.publication(t, 1, 1, 201));
    check(bank.finishWrite(t, 1, 1));
    check(bank.get(t));
    check(bank.finishRecall(t, 201, 0));
    check(!bank.get(t));
    auto next = bank.write(0, 10, 2, 301, 1);
    check(next.valid() && next.generation != t.generation);
    check(!bank.finishRecall(t, 201, 0));
    check(!bank.recall(0, 9, 2, true, 201, 0).valid());
    // Home merged before Recall reaches the endpoint: preserve identity even
    // after CHI WB Comp, then settle the later no-data native cleanup once.
    check(bank.publication(next, 301, 1, 401));
    check(bank.finishWrite(next, 301, 1));
    check(bank.get(next));
    check(!bank.canRecall(0, 402, 0));
    check(bank.canRecall(0, 401, 0));
    auto late = bank.recall(0, 10, 2, false, 401, 0);
    check(late.valid());
    check(bank.get(late)->mergedRecallId == 401);
    check(bank.finishRecall(late, 401, 0));
    check(!bank.finishRecall(late, 401, 0));
    BoundaryTransactions foreground;
    BoundaryTransactions::Token roots[64];
    for (unsigned i = 0; i < 64; ++i) {
        roots[i] = foreground.foreground(i * 64, 0, 0, i + 1, 0);
        check(roots[i].valid());
    }
    check(!foreground.foreground(4096, 0, 0, 65, 0).valid());
    // Prior reservation completes while ordinary is full; Home completion
    // alone cannot admit another operation before exact native close.
    check(foreground.finishForeground(roots[0], 1, 0,
        BoundaryTransactions::HomeCommit) == gem5::ruby::BoundaryResult::Applied);
    check(foreground.creditUsage(gem5::ruby::BoundaryPool::Ordinary) == 64);
    for (unsigned i = 0; i < 8; ++i)
        check(foreground.recall(i * 64, 4, 0, false, 100 + i, 0).valid());
    check(foreground.occupancy() == 64);
    check(foreground.creditUsage(gem5::ruby::BoundaryPool::Control) == 8);
    check(!foreground.recall(8 * 64, 4, 0, false, 108, 0).valid());
    check(foreground.finishRecall(roots[0], 100, 0));
    check(foreground.creditUsage(gem5::ruby::BoundaryPool::Control) == 7);
    check(foreground.creditUsage(gem5::ruby::BoundaryPool::Ordinary) == 64);
    check(foreground.finishForeground(roots[0], 1, 0,
        BoundaryTransactions::NativeClose) == gem5::ruby::BoundaryResult::Applied);
    auto replacement = foreground.foreground(4096, 0, 0, 65, 0);
    check(replacement.valid());
    check(foreground.finishForeground(roots[0], 1, 0,
        BoundaryTransactions::NativeClose) == gem5::ruby::BoundaryResult::Stale);
    BoundaryTransactions invalidations;
    auto fg = invalidations.foreground(0, 0, 0, 1, 0);
    check(fg.valid());
    for (unsigned i = 0; i < 8; ++i)
        check(invalidations.invalidate(i * 64, 4, 0, 200 + i, 0).valid());
    check(invalidations.creditUsage(gem5::ruby::BoundaryPool::Control) == 8);
    check(!invalidations.invalidate(512, 4, 0, 208, 0).valid());
    check(!invalidations.recall(0, 4, 0, false, 300, 0).valid());
    check(invalidations.finishInvalidate(fg, 200, 0) == gem5::ruby::BoundaryResult::Applied);
    check(invalidations.creditUsage(gem5::ruby::BoundaryPool::Ordinary) == 1);
    check(invalidations.creditUsage(gem5::ruby::BoundaryPool::Control) == 7);
    // Home merged a write while all eight local execution control slots are
    // occupied. The receipt fits in the pre-reserved write descriptor, and
    // holds ordinary until the later exact native cleanup can be admitted.
    BoundaryTransactions lateBank;
    auto write = lateBank.write(0, 9, 0, 1, 0);
    for (unsigned i = 1; i <= 8; ++i)
        check(lateBank.recall(i * 64, 9, 0, false, 100 + i, 0).valid());
    check(lateBank.publication(write, 1, 0, 50));
    check(lateBank.finishWrite(write, 1, 0));
    check(lateBank.creditUsage(gem5::ruby::BoundaryPool::Ordinary) == 1);
    check(!lateBank.canRecall(0, 50, 0));
    check(lateBank.finishRecall(lateBank.find(64), 101, 0));
    check(lateBank.recall(0, 9, 0, false, 50, 0).valid());
    check(lateBank.finishRecall(write, 50, 0));
    check(lateBank.creditUsage(gem5::ruby::BoundaryPool::Ordinary) == 0);
    std::cout << "PASS production boundary ordinary=64 control=8 escape=1 entry="
              << sizeof(BoundaryTransactions::Entry)
              << " bank=" << sizeof(BoundaryTransactions) << '\n';
}
