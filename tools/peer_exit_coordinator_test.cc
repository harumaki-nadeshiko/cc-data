#include "modules/ubiomodule/PeerExitCoordinator.hh"

#include <cassert>
#include <cstdio>
#include <limits>

using C = ubiocc::PeerExitCoordinator;
using P = C::PeerId;
using A = C::Action;

static C make(P self, unsigned nodes = 3, unsigned sockets = 1,
              int *closed = nullptr, C::ExitId exitId = 0)
{
    return C(nodes, sockets, self, {10, 20},
             closed ? [closed] { ++*closed; } : std::function<void()>{},
             exitId);
}

static void deliver(C &to, P from, const A &a, std::uint64_t now)
{
    if (a.kind == C::ActionKind::Notify)
        to.receiveNotify(from, a.exitId, now);
    else
        to.receiveAck(from, a.exitId, now);
}

static void normal_3x1()
{
    int closed = 0;
    auto a = make({0, 0}, 3, 1, &closed);
    auto b = make({1, 0});
    auto c = make({2, 0});
    auto out = a.startLocalExit(0);
    assert(out.size() == 2 && a.state() == C::State::WaitingForAcks);
    for (const auto &n : out) {
        C &peer = n.peer.node == 1 ? b : c;
        auto ack = peer.receiveNotify({0, 0}, n.exitId, 1);
        assert(ack.size() == 1);
        deliver(a, n.peer, ack[0], 2);
    }
    assert(a.state() == C::State::Quiescing);
    a.pump(22);
    assert(a.state() == C::State::Quiescing && a.closeReady());
    assert(a.finalizeClose());
    assert(a.state() == C::State::Closed && closed == 1);
    a.pump(100);
    assert(closed == 1);
}

static void sequential_early_exit()
{
    auto a = make({0, 0});
    auto b = make({1, 0});
    auto notify = a.startLocalExit(0);
    auto ack = b.receiveNotify({0, 0}, notify[0].exitId, 1);
    assert(ack.size() == 1);
    assert(b.seenNotifyPeers().count({0, 0}));
    auto out = b.startLocalExit(2);
    const P remaining{2, 0};
    assert(out.size() == 1 && out[0].peer == remaining);
    assert(!b.requiredPeers().count({0, 0}));
}

static void simultaneous_exit()
{
    auto a = make({0, 0}, 2);
    auto b = make({1, 0}, 2);
    auto an = a.startLocalExit(0);
    auto bn = b.startLocalExit(0);
    auto ba = b.receiveNotify({0, 0}, an[0].exitId, 1);
    auto aa = a.receiveNotify({1, 0}, bn[0].exitId, 1);
    deliver(a, {1, 0}, ba[0], 2);
    deliver(b, {0, 0}, aa[0], 2);
    assert(a.state() == C::State::Quiescing);
    assert(b.state() == C::State::Quiescing);
}

static void first_notify_drop_frozen_sim_tick()
{
    auto a = make({0, 0}, 2);
    auto b = make({1, 0}, 2);
    (void)a.startLocalExit(100); // dropped; simulated tick is notionally frozen
    assert(a.pump(109).empty());
    auto retry = a.pump(110); // wall clock alone drives retry
    assert(retry.size() == 1);
    auto ack = b.receiveNotify({0, 0}, retry[0].exitId, 110);
    deliver(a, {1, 0}, ack[0], 111);
    assert(a.state() == C::State::Quiescing);
}

static void first_ack_drop()
{
    auto a = make({0, 0}, 2);
    auto b = make({1, 0}, 2);
    auto n = a.startLocalExit(0);
    (void)b.receiveNotify({0, 0}, n[0].exitId, 1); // ACK dropped
    assert(b.pump(11).empty()); // no unsolicited receiver ACK retry
    auto retryNotify = a.pump(10);
    assert(retryNotify.size() == 1 &&
           retryNotify[0].kind == C::ActionKind::Notify);
    auto retryAck = b.receiveNotify({0, 0}, retryNotify[0].exitId, 11);
    assert(retryAck.size() == 1 && retryAck[0].kind == C::ActionKind::Ack);
    deliver(a, {1, 0}, retryAck[0], 12);
    assert(a.state() == C::State::Quiescing);
}

static void duplicates_and_wrong_ack()
{
    auto a = make({0, 0}, 2);
    auto b = make({1, 0}, 2);
    auto n = a.startLocalExit(0);
    a.receiveAck({1, 0}, a.exitId() + 99, 1);
    a.receiveAck({9, 9}, a.exitId(), 1);
    assert(a.ackedPeers().empty());
    auto ack1 = b.receiveNotify({0, 0}, n[0].exitId, 2);
    auto ack2 = b.receiveNotify({0, 0}, n[0].exitId, 8);
    assert(ack1.size() == 1 && ack2.size() == 1); // duplicate => immediate ACK
    deliver(a, {1, 0}, ack1[0], 3);
    deliver(a, {1, 0}, ack2[0], 9);
    assert(a.ackedPeers().size() == 1);

    auto x = make({0, 0}, 2);
    x.receiveNotify({1, 0}, 7, 0);
    x.startLocalExit(1); // no required peers; quiesce initially to 21
    assert(x.state() == C::State::Quiescing);
    auto immediate = x.receiveNotify({1, 0}, 7, 15);
    assert(immediate.size() == 1);
    x.pump(21);
    assert(x.state() == C::State::Quiescing); // duplicate extended to 35
    x.pump(35);
    assert(x.closeReady());
    assert(x.finalizeClose());
    assert(x.state() == C::State::Closed);
}

static void multi_socket_snapshot_2x2()
{
    auto x = make({0, 1}, 2, 2);
    x.receiveNotify({1, 0}, 77, 0);
    auto out = x.startLocalExit(1);
    assert(out.size() == 2);
    assert(x.requiredPeers().count({0, 0}));
    assert(x.requiredPeers().count({1, 1}));
    assert(!x.requiredPeers().count({0, 1}));
    assert(!x.requiredPeers().count({1, 0}));
    for (const auto &a : out)
        assert(a.exitId == x.exitId() && a.exitId != 0);
}

static void retry_is_per_peer()
{
    auto a = make({0, 0});
    auto out = a.startLocalExit(0);
    assert(out.size() == 2);
    a.receiveAck({1, 0}, a.exitId(), 1);
    auto retry = a.pump(10);
    assert(retry.size() == 1);
    assert((retry[0].peer == P{2, 0}));
    assert(retry[0].exitId == a.exitId());
}

static void exact_close_boundary_and_one_shot_callback()
{
    int closed = 0;
    auto a = make({0, 0}, 1, 1, &closed, 0x1234);
    assert(a.exitId() == 0x1234);
    assert(a.startLocalExit(100).empty());
    assert(a.state() == C::State::Quiescing);
    a.pump(119);
    assert(!a.closeReady() && !a.finalizeClose());
    a.pump(120);
    assert(a.closeReady() && a.finalizeClose());
    assert(!a.finalizeClose());
    assert(closed == 1);
    assert(a.receiveNotify({1, 0}, 0x9999, 121).empty());
}

static void saturating_deadlines_do_not_wrap()
{
    const auto max = std::numeric_limits<std::uint64_t>::max();
    auto a = make({0, 0}, 2, 1, nullptr, 0x5678);
    auto initial = a.startLocalExit(max - 5);
    assert(initial.size() == 1);
    assert(a.pump(max - 1).empty());
    auto retry = a.pump(max);
    assert(retry.size() == 1 && retry[0].exitId == 0x5678);

    auto b = make({0, 0}, 1, 1);
    b.startLocalExit(max - 5);
    assert(b.state() == C::State::Quiescing);
    b.pump(max - 1);
    assert(!b.closeReady());
    b.pump(max);
    assert(b.closeReady());
}

int main()
{
    normal_3x1();
    sequential_early_exit();
    simultaneous_exit();
    first_notify_drop_frozen_sim_tick();
    first_ack_drop();
    duplicates_and_wrong_ack();
    multi_socket_snapshot_2x2();
    retry_is_per_peer();
    exact_close_boundary_and_one_shot_callback();
    saturating_deadlines_do_not_wrap();
    std::puts("peer_exit_coordinator_test: all 10 scenarios PASS");
    return 0;
}
