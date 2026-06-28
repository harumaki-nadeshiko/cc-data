#ifndef FRAMEWORK_PORT_HH
#define FRAMEWORK_PORT_HH

#include <deque>
#include <memory>
#include <string>
#include <zmq.hpp>

#include "framework/MemMessage.hh"

namespace framework {

static constexpr uint64_t kDefaultSyncInterval = 100000;
// linkLatency is BOTH the per-hop message delay AND the lockstep "leapfrog"
// step: two peers advancing toward each other move in increments of
// linkLatency (each CONTROL_SYNC promises ts = curTick + linkLatency). The
// heartbeat rate-limit is also linkLatency. Keeping linkLatency == syncInterval
// means (a) the rate-limit never blocks a leapfrog step (no lockstep stall),
// (b) the sync traffic stays at the syncInterval cadence (no 10x flood that
// previously overran the ZMQ buffers and stalled the idle node), and (c) steps
// are large enough that the clock advances quickly. Round-trip stays well under
// the EPSNF retry window (~8 hops * 1e5 = 8e5 << 1.6e6).
static constexpr uint64_t kDefaultLinkLatency  = 100000;

enum class ReceiveStatus {
    kMessage,
    kEmpty,
    kSync,
    kPendingFuture,
};

enum class PortState { INIT, RX_BOUND, TX_CONNECTED, HANDSHAKING, READY, PEER_LOST };

class Port
{
  public:
    Port(const std::string& name,
         uint32_t module_id, uint32_t port_id,
         const std::string& local_rx_endpoint,
         const std::string& peer_rx_endpoint,
         zmq::context_t& ctx,
         uint64_t syncInterval = kDefaultSyncInterval,
         uint64_t linkLatency  = kDefaultLinkLatency);

    // ---- Deprecated single-endpoint constructor ----
    Port(const std::string& name,
         uint32_t module_id, uint32_t port_id,
         const std::string& endpoint,
         bool bind,
         zmq::context_t& ctx,
         uint64_t syncInterval = kDefaultSyncInterval,
         uint64_t linkLatency  = kDefaultLinkLatency);

    ~Port();

    void pollHandshake();
    bool isReady() const { return _state == PortState::READY; }
    void failClosed(const char* reason);

    MemMessage* sendAllocateBuffer(uint64_t timestamp);
    bool send(MemMessage* msg);
    MemMessage* recv(uint64_t curT, ReceiveStatus* status = nullptr);

    uint64_t receiveTimestamp() const { return _pending ? _pendingT : _lastRxT; }

    uint64_t safeTs(uint64_t curT) const {
        uint64_t rxt = receiveTimestamp();
        // PLAN 1: before we have ever heard from the peer (rxt == sentinel
        // UINT64_MAX), do NOT free-run — park at curT until the first sync
        // arrives. gem5 emits its first heartbeat at tick 0, breaking symmetry.
        // After that, advance to min(peer's latest ts, ownLastSync + window).
        if (rxt == ~static_cast<uint64_t>(0))
            return curT;
        uint64_t base = (_lastSyncTs > 0) ? _lastSyncTs : curT;
        uint64_t syncBound = base + _syncInterval;
        return (rxt < syncBound) ? rxt : syncBound;
    }

    bool emitSync(uint64_t curTick);

    uint32_t moduleId() const { return _moduleId; }
    uint32_t portId() const { return _portId; }
    uint64_t syncInterval() const { return _syncInterval; }

    // ---- deprecated wrappers ----
    uint64_t nextVisibleTick() const { return receiveTimestamp(); }
    void advanceVisibleTick(uint64_t t) { if (t > _lastRxT) _lastRxT = t; }

    PortState state() const { return _state; }

  private:
    void tryHandshakeStep();
    void sendHello();
    void sendHelloAck();
    bool tryRecvHello();
    bool tryRecvHelloAck();
    bool tryRecvControl();

    std::string _name;
    uint32_t _moduleId, _portId;
    zmq::context_t& _ctx;

    std::unique_ptr<zmq::socket_t> _txSock;
    std::unique_ptr<zmq::socket_t> _rxSock;

    PortState _state;

    // Hello handshake
    bool _helloSent, _helloRecvd, _ackSent, _ackRecvd;

    uint64_t _syncInterval;
    uint64_t _linkLatency;
    uint64_t _lastSyncTs;

    bool _pending;
    uint64_t _pendingT;
    MemMessage _pendingMsg;
    uint64_t _lastRxT;

    MemMessage _sendBuf;
    bool _sendBufInUse;
};

uint64_t synced_receive_lower_bound(Port** ports, int n, uint64_t tick);

} // namespace framework
#endif
