#include "modules/ubiomodule/PeerExitCoordinator.hh"

#include <algorithm>
#include <limits>
#include <stdexcept>
#include <utility>

namespace ubiocc {

PeerExitCoordinator::PeerExitCoordinator(
    std::uint32_t numNodes, std::uint32_t numSockets, PeerId self,
    Config config, std::function<void()> closureCallback, ExitId exitId)
    : numNodes_(numNodes), numSockets_(numSockets), self_(self),
      config_(config), closureCallback_(std::move(closureCallback))
{
    if (numNodes_ == 0 || numSockets_ == 0 || !validPeer(self_))
        throw std::invalid_argument("invalid peer-exit topology or self ID");
    if (config_.retryIntervalMs == 0)
        throw std::invalid_argument("retry interval must be nonzero");
    if (config_.quiesceIntervalMs == 0)
        throw std::invalid_argument("quiesce interval must be nonzero");

    // The derived value is deterministic and useful for isolated unit tests.
    // Production integrations should supply a per-process nonzero nonce.
    exitId_ = exitId ? exitId
                     : static_cast<ExitId>(self_.node) * numSockets_ +
                           self_.socket + 1;
}

bool
PeerExitCoordinator::validPeer(PeerId peer) const
{
    return peer.node < numNodes_ && peer.socket < numSockets_;
}

PeerExitCoordinator::Milliseconds
PeerExitCoordinator::addSaturating(Milliseconds a, Milliseconds b) const
{
    const auto max = std::numeric_limits<Milliseconds>::max();
    return b > max - a ? max : a + b;
}

void
PeerExitCoordinator::updateState(Milliseconds nowMs)
{
    if (state_ == State::WaitingForAcks &&
        ackedPeers_.size() == requiredPeers_.size()) {
        state_ = State::Quiescing;
        quiesceDeadline_ = std::max(
            quiesceNotBefore_, addSaturating(nowMs, config_.quiesceIntervalMs));
    }
    closeReady_ = state_ == State::Quiescing && nowMs >= quiesceDeadline_;
}

std::vector<PeerExitCoordinator::Action>
PeerExitCoordinator::startLocalExit(Milliseconds nowMs)
{
    std::vector<Action> actions;
    if (state_ != State::Running)
        return actions;

    for (std::uint32_t node = 0; node < numNodes_; ++node) {
        for (std::uint32_t socket = 0; socket < numSockets_; ++socket) {
            PeerId peer{node, socket};
            if (peer == self_ || seenNotifyPeers_.count(peer))
                continue;
            requiredPeers_.insert(peer);
            actions.push_back({ActionKind::Notify, peer, exitId_});
            nextNotifyMs_[peer] = addSaturating(nowMs, config_.retryIntervalMs);
        }
    }
    state_ = State::WaitingForAcks;
    updateState(nowMs);
    return actions;
}

std::vector<PeerExitCoordinator::Action>
PeerExitCoordinator::receiveNotify(PeerId from, ExitId exitId,
                                   Milliseconds nowMs)
{
    std::vector<Action> actions;
    if (state_ == State::Closed || !validPeer(from) || from == self_ ||
        exitId == 0)
        return actions;

    seenNotifyPeers_.insert(from);
    // Both first and duplicate notifications cause exactly one immediate ACK.
    // A dropped ACK is recovered when the sender retries Notify; there is no
    // receiver-side ACK timer or unbounded ACK obligation.
    actions.push_back({ActionKind::Ack, from, exitId});

    quiesceNotBefore_ = std::max(
        quiesceNotBefore_, addSaturating(nowMs, config_.quiesceIntervalMs));
    if (state_ == State::Quiescing) {
        quiesceDeadline_ = std::max(quiesceDeadline_, quiesceNotBefore_);
        closeReady_ = false;
    }
    updateState(nowMs);
    return actions;
}

std::vector<PeerExitCoordinator::Action>
PeerExitCoordinator::receiveAck(PeerId from, ExitId exitId,
                                Milliseconds nowMs)
{
    if (state_ == State::WaitingForAcks && exitId == exitId_ &&
        validPeer(from) && requiredPeers_.count(from)) {
        ackedPeers_.insert(from); // set insertion makes duplicate ACKs harmless
        nextNotifyMs_.erase(from);
    }
    updateState(nowMs);
    return {};
}

std::vector<PeerExitCoordinator::Action>
PeerExitCoordinator::pump(Milliseconds nowMs)
{
    std::vector<Action> actions;
    if (state_ == State::WaitingForAcks) {
        for (const auto &peer : requiredPeers_) {
            if (ackedPeers_.count(peer))
                continue;
            auto &due = nextNotifyMs_[peer];
            if (nowMs >= due) {
                actions.push_back({ActionKind::Notify, peer, exitId_});
                due = addSaturating(nowMs, config_.retryIntervalMs);
            }
        }
    }
    updateState(nowMs);
    return actions;
}

bool
PeerExitCoordinator::finalizeClose()
{
    if (!closeReady_ || state_ != State::Quiescing)
        return false;
    state_ = State::Closed;
    closeReady_ = false;
    if (!callbackCalled_) {
        callbackCalled_ = true;
        if (closureCallback_)
            closureCallback_();
    }
    return true;
}

} // namespace ubiocc
