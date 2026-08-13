#ifndef UBIO_PEER_EXIT_COORDINATOR_HH
#define UBIO_PEER_EXIT_COORDINATOR_HH

#include <cstdint>
#include <functional>
#include <map>
#include <set>
#include <vector>

namespace ubiocc {

class PeerExitCoordinator
{
  public:
    using Milliseconds = std::uint64_t;
    using ExitId = std::uint64_t;

    struct PeerId
    {
        std::uint32_t node = 0;
        std::uint32_t socket = 0;

        friend bool operator==(const PeerId &a, const PeerId &b)
        { return a.node == b.node && a.socket == b.socket; }
        friend bool operator!=(const PeerId &a, const PeerId &b)
        { return !(a == b); }
        friend bool operator<(const PeerId &a, const PeerId &b)
        { return a.node < b.node || (a.node == b.node && a.socket < b.socket); }
    };

    enum class State { Running, WaitingForAcks, Quiescing, Closed };
    enum class ActionKind { Notify, Ack };

    struct Action
    {
        ActionKind kind;
        PeerId peer;
        ExitId exitId;
    };

    struct Config
    {
        Milliseconds retryIntervalMs = 100;
        Milliseconds quiesceIntervalMs = 2000;
    };

    PeerExitCoordinator(std::uint32_t numNodes, std::uint32_t numSockets,
                        PeerId self, Config config,
                        std::function<void()> closureCallback = {},
                        ExitId exitId = 0);

    State state() const { return state_; }
    ExitId exitId() const { return exitId_; }
    bool closeReady() const { return closeReady_; }
    const std::set<PeerId> &seenNotifyPeers() const
    { return seenNotifyPeers_; }
    const std::set<PeerId> &requiredPeers() const { return requiredPeers_; }
    const std::set<PeerId> &ackedPeers() const { return ackedPeers_; }

    // All time arguments are caller-supplied wall-clock milliseconds. They
    // deliberately have no relationship to a simulator tick.
    std::vector<Action> startLocalExit(Milliseconds nowMs);
    std::vector<Action> receiveNotify(PeerId from, ExitId exitId,
                                      Milliseconds nowMs);
    std::vector<Action> receiveAck(PeerId from, ExitId exitId,
                                   Milliseconds nowMs);
    std::vector<Action> pump(Milliseconds nowMs);
    // The caller must send all returned actions before closing the transport.
    bool finalizeClose();

  private:
    bool validPeer(PeerId peer) const;
    void updateState(Milliseconds nowMs);
    Milliseconds addSaturating(Milliseconds a, Milliseconds b) const;

    std::uint32_t numNodes_;
    std::uint32_t numSockets_;
    PeerId self_;
    Config config_;
    std::function<void()> closureCallback_;
    ExitId exitId_;
    State state_ = State::Running;
    bool callbackCalled_ = false;
    bool closeReady_ = false;
    Milliseconds quiesceNotBefore_ = 0;
    Milliseconds quiesceDeadline_ = 0;
    std::set<PeerId> seenNotifyPeers_;
    std::set<PeerId> requiredPeers_;
    std::set<PeerId> ackedPeers_;
    std::map<PeerId, Milliseconds> nextNotifyMs_;
};

} // namespace ubiocc

#endif
