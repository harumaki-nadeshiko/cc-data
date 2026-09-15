#include "ep_boundary_lifecycle.hh"

#include <algorithm>
#include <array>
#include <cstdlib>
#include <iostream>

using namespace ep_boundary;

static void require(bool ok) {
    if (!ok) {
        std::cerr << "lifecycle invariant failed\n";
        std::abort();
    }
}

int main() {
    using Bank = Table<4, 1, 2>;
    const Identity owner{23, 7};
    const Request parent{101, 0}, control{102, 1}, write{103, 1};
    Bank bank;
    Token token, other, reserve;
    require(bank.admit({0, 0, 1, 0}, owner, parent,
                       Role::Ordinary, true, token) == Result::Accepted);
    for (uint64_t i = 1; i < 3; ++i)
        require(bank.admit({i * 64, 0, 1, 0}, owner, parent,
                           Role::Ordinary, false, other) == Result::Accepted);
    require(bank.admit({192, 0, 1, 0}, owner, parent,
                       Role::Ordinary, false, other) == Result::Retry);
    require(bank.admit({192, 0, 1, 0}, owner, parent,
                       Role::Control, false, reserve) == Result::Accepted);
    require(bank.occupancy() == 4);
    require(bank.attachControl(token, owner, control) == Result::Accepted);
    require(bank.attachControl(token, owner, control) == Result::Duplicate);
    require(bank.attachControl(token, owner, {104, 0}) == Result::Accepted);
    require(bank.attachControl(token, owner, {105, 0}) == Result::Retry);
    require(!bank.snapshot(token)->access);
    require(bank.snapshot(token)->custody);
    require(bank.snapshot(token)->committed == owner);
    // Full table cannot block the WB on which its parent's recall depends.
    require(bank.attachWrite(token, owner, write) == Result::Accepted);
    require(bank.attachWrite(token, {24, 7}, write) == Result::Conflict);
    require(bank.attachWrite(token, owner, {104, 1}) == Result::Retry);
    require(bank.completeControl(token, true) == Result::Retry);
    require(bank.dataComplete(token, write, 0xff) == Result::Retry);
    require(bank.publish(token, owner, true) == Result::Retry);
    require(bank.dataComplete(token, write, ~uint64_t(0)) == Result::Accepted);
    require(bank.publish(token, owner, false) == Result::Conflict);
    require(bank.publish(token, owner, true) == Result::Accepted);
    require(bank.retire(token) == Result::Retry);
    require(bank.completeControl(token, true) == Result::Accepted);
    require(bank.deliverControls(token, false) == Result::Retry);
    require(bank.deliverControls(token, true) == Result::Accepted);
    require(bank.completeParent(token) == Result::Accepted);
    require(bank.completeWrite(token, {103, 0}) == Result::Conflict);
    require(bank.completeWrite(token, write) == Result::Accepted);
    require(bank.retire(token) == Result::Accepted);
    Token recycled;
    require(bank.admit({0, 0, 1, 0}, {24, 7}, parent,
                       Role::Ordinary, true, recycled) == Result::Accepted);
    require(recycled.slot == token.slot && recycled.generation != token.generation);
    require(bank.completeParent(token) == Result::Stale);
    require(bank.publish(token, owner, true) == Result::Stale);
    require(bank.snapshot(recycled)->committed.epoch == 24);

    // Equal offsets in different canonical namespaces must not alias.
    {
        Table<8, 1> b;
        Token t;
        for (const auto key : {LineKey{0, 0, 1, 0}, LineKey{0, 0, 1, 1},
                               LineKey{0, 0, 2, 0}, LineKey{0, 1, 1, 0}})
            require(b.admit(key, owner, parent, Role::Ordinary, false, t) ==
                    Result::Accepted);
        require(b.occupancy() == 4);
        require(b.admit({0, 0, 1, 0}, {24, 7}, parent,
                        Role::Ordinary, false, t) == Result::Conflict);
    }

    // No-data is valid without custody, but never retires before publication.
    {
        Bank b;
        Token t;
        require(b.admit({0, 0, 1, 0}, owner, parent,
                        Role::Control, false, t) == Result::Accepted);
        require(b.attachControl(t, owner, control) == Result::Accepted);
        require(b.completeControl(t, true) == Result::Accepted);
        require(b.completeParent(t) == Result::Accepted);
        require(b.retire(t) == Result::Retry);
        require(b.publish(t, owner, false) == Result::Accepted);
        require(b.deliverControls(t, true) == Result::Accepted);
        require(b.retire(t) == Result::Accepted);
    }

    // Exhaust all 6! operation orderings, retrying only blocked operations.
    // This is a finite transition-order test, not a proof of the CHI system.
    std::array<int, 6> order{{0, 1, 2, 3, 4, 5}};
    size_t explored = 0;
    do {
        Bank b;
        Token t;
        require(b.admit({0, 0, 1, 0}, owner, parent,
                        Role::Ordinary, true, t) == Result::Accepted);
        require(b.attachControl(t, owner, control) == Result::Accepted);
        require(b.attachWrite(t, owner, write) == Result::Accepted);
        std::array<bool, 6> done{};
        for (int round = 0; round < 6; ++round) {
            for (int op : order) {
                if (done[op]) continue;
                Result r = Result::Conflict;
                switch (op) {
                  case 0: r = b.dataComplete(t, write, ~uint64_t(0)); break;
                  case 1: r = b.publish(t, owner, true); break;
                  case 2: r = b.completeParent(t); break;
                  case 3: r = b.completeWrite(t, write); break;
                  case 4: r = b.completeControl(t, true); break;
                  case 5: r = b.deliverControls(t, true); break;
                }
                require(r == Result::Accepted || r == Result::Retry);
                done[op] = r == Result::Accepted;
                if (!std::all_of(done.begin(), done.end(), [](bool v) { return v; }))
                    require(b.retire(t) == Result::Retry);
            }
        }
        require(std::all_of(done.begin(), done.end(), [](bool v) { return v; }));
        require(b.retire(t) == Result::Accepted);
        require(b.retire(t) == Result::Stale);
        require(b.occupancy() == 0);
        ++explored;
    } while (std::next_permutation(order.begin(), order.end()));
    std::cout << "PASS orderings=" << explored
              << " prototype_entry_bytes=" << sizeof(Table<>::Entry)
              << " prototype_64_slot_bytes=" << sizeof(Table<>) << '\n';
}
