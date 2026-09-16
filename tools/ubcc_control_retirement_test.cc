#include <cassert>
#include <iostream>
#include "BoundaryAuthorityTable.hh"
using namespace gem5::ruby;

int main()
{
    BoundaryAuthorityTable bank(64, 8);
    auto first = bank.tryReserve(0, 7).token;
    assert(first.valid());
    bank.metadata(first)->access = RequesterLineState::R_M;
    BoundaryBorrowToken borrow;
    assert(bank.borrow(first, borrow) == BoundaryResult::Applied);
    // An unrelated epoch cannot discharge data custody or an existing borrower.
    assert(bank.retireByControl(first, 8) == BoundaryResult::Stale);
    assert(bank.get(first)->epoch == 7);
    assert(bank.retireByControl(first, 7) == BoundaryResult::Applied);
    assert(bank.get(first)->epoch == 0);
    assert(bank.get(first)->access == RequesterLineState::R_I);
    assert(bank.ownsBorrow(borrow, first));
    BoundaryStableToken release;
    assert(bank.beginRelease(first, release) == BoundaryResult::Busy);
    assert(bank.dropBorrow(borrow) == BoundaryResult::Applied);
    assert(bank.beginRelease(first, release) == BoundaryResult::Applied);
    assert(bank.completeRelease(release, true) == BoundaryResult::Applied);
    auto next = bank.tryReserve(0, 9).token;
    assert(next.valid());
    assert(bank.retireByControl(first, 7) == BoundaryResult::Stale);
    assert(bank.get(next)->epoch == 9);
    // The same key/epoch after slot reuse is still a different incarnation.
    bank.metadata(next)->epoch = 7;
    assert(bank.retireByControl(first, 7) == BoundaryResult::Stale);
    assert(bank.get(next)->epoch == 7);
    std::cout << "PASS exact control retirement, borrower fence, successor and ABA rejection\n";
}
