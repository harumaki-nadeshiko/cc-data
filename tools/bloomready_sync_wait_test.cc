// Compile with the fixture ThreadContext and the real sync_wait.cc in Docker.
#include <cassert>
#include <vector>
#include "cpu/thread_context.hh"
#include "sim/sync_wait.hh"

int main()
{
    gem5::SyncWaitManager manager;
    constexpr unsigned startup = 0x8000000f;
    std::vector<unsigned> sent;
    for (int socket = 0; socket < 2; ++socket) {
        manager.registerSocket(socket, socket);
        manager.registerSocketFn(socket,
            [&](unsigned mask, unsigned, unsigned seq) {
                assert(mask == startup && seq == 0);
                sent.push_back(mask);
            });
    }
    gem5::ThreadContext threads[4];
    for (int i = 0; i < 4; ++i) {
        assert(manager.barrierArrive(&threads[i], startup, 4) == 0);
        assert(threads[i].suspended);
        assert(sent.size() == (i == 3 ? 2 : 0));
    }
    // An ordinary barrier release cannot free any startup participant.
    manager.releaseBarrier(0xf, 0);
    for (const auto &thread : threads) assert(thread.suspended);
    manager.releaseBarrier(startup, 0);
    for (const auto &thread : threads) assert(!thread.suspended);
    assert(manager.barrierArrive(&threads[0], 0x80000000, 4) < 0);
    assert(manager.barrierArrive(&threads[0], 0x80010000, 4) < 0);
}
