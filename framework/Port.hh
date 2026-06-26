#ifndef FRAMEWORK_PORT_HH
#define FRAMEWORK_PORT_HH

#include <deque>
#include <memory>
#include <string>
#include <zmq.hpp>

#include "framework/MemMessage.hh"

namespace framework {

enum class ReceiveStatus {
    kMessage,        // message ready, timestamp <= curT
    kEmpty,          // no message available
    kSync,           // sync control message (caller skips, _lastRxT updated)
    kPendingFuture,  // future message cached, timestamp > curT
};

enum class PortState {
    INIT,
    RX_BOUND,
    TX_CONNECTED,
    HANDSHAKING,
    READY,
    PEER_LOST,
};

class Port
{
  public:
    /**
     * Duplex Port — two separate ZMQ PAIR sockets (tx + rx).
     *
     * This port binds its _rxSock to local_rx_endpoint for RECEIVING,
     * and connects its _txSock to peer_rx_endpoint for SENDING.
     *
     * The peer's Port mirrors this: its local_rx_ep = our peer_rx_ep,
     * its peer_rx_ep = our local_rx_ep.
     *
     * After bind/connect, a PORT_HELLO / PORT_HELLO_ACK handshake
     * establishes the READY state before any data traffic.
     */
    Port(const std::string& name,
         uint32_t module_id, uint32_t port_id,
         const std::string& local_rx_endpoint,
         const std::string& peer_rx_endpoint,
         zmq::context_t& ctx,
         uint64_t syncWindow,
         uint64_t syncInterval = 0);

    // ---- Deprecated single-endpoint constructor (to be removed in Phase B) ----
    Port(const std::string& name,
         uint32_t module_id, uint32_t port_id,
         const std::string& endpoint,
         bool bind,
         zmq::context_t& ctx,
         uint64_t syncWindow,
         uint64_t syncInterval = 0);

    ~Port();

    // ---- Handshake / lifecycle ----

    /** Drive the internal handshake state machine. Call periodically until isReady(). */
    void pollHandshake();

    /** True once both PORT_HELLO and PORT_HELLO_ACK exchanged. */
    bool isReady() const { return _state == PortState::READY; }

    /** Force peer-lost state (used on timeout). */
    void failClosed(const char* reason);

    // ---- Data plane (allowed only in READY state) ----

    MemMessage* sendAllocateBuffer(uint64_t timestamp);
    bool send(MemMessage* msg);

    MemMessage* recv(uint64_t curT, ReceiveStatus* status = nullptr);

    uint64_t receiveTimestamp() const {
        return _pending ? _pendingT : _lastRxT;
    }

    uint64_t safeTs(uint64_t curT) const {
        uint64_t base = (_lastSyncTs > 0) ? _lastSyncTs : curT;
        uint64_t syncBound = base + _syncWindow;
        uint64_t rxt = receiveTimestamp();
        return (rxt < syncBound) ? rxt : syncBound;
    }

    bool emitSync(uint64_t curTick);

    uint32_t moduleId() const { return _moduleId; }
    uint32_t portId() const { return _portId; }

    // ---- deprecated wrappers ----
    uint64_t nextVisibleTick() const { return receiveTimestamp(); }
    void advanceVisibleTick(uint64_t t) {
        if (t > _lastRxT) _lastRxT = t;
    }

    /** Expose state for launcher health checks. */
    PortState state() const { return _state; }

  private:
    void tryHandshakeStep();

    void sendHello();       // send PORT_HELLO on tx
    void sendHelloAck();    // send PORT_HELLO_ACK on tx
    bool tryRecvHello();    // try recv PORT_HELLO on rx → reply ACK
    bool tryRecvHelloAck(); // try recv PORT_HELLO_ACK on rx → READY
    bool tryRecvControl();  // recv any control (hello/ack/terminate) on rx

    std::string _name;
    uint32_t _moduleId, _portId;
    zmq::context_t& _ctx;

    std::unique_ptr<zmq::socket_t> _txSock;  // send on this
    std::unique_ptr<zmq::socket_t> _rxSock;  // recv on this

    PortState _state;

    // Hello handshake
    bool _helloSent, _helloRecvd, _ackSent, _ackRecvd;

    uint64_t _syncWindow;
    uint64_t _syncInterval;
    uint64_t _lastSyncTs;

    // Single-slot future message cache
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
