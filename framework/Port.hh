#ifndef FRAMEWORK_PORT_HH
#define FRAMEWORK_PORT_HH

#include <memory>
#include <string>
#include <zmq.hpp>

#include "framework/MemMessage.hh"

namespace framework {

enum class ReceiveStatus {
    kMessage,
    kEmpty,
    kSync,
    kPendingFuture,
};

class Port
{
  public:
    /**
     * Full-duplex Port using TWO ZMQ PAIR sockets.
     *
     * @param name        Human-readable name
     * @param module_id   Launcher-assigned module ID
     * @param port_id     Port ID within this module
     * @param endpoint    Base IPC endpoint (suffix _tx and _rx appended automatically)
     * @param bind_tx     If true, we bind _tx (send) socket; peer connects
     * @param ctx         ZMQ context
     * @param syncWindow  Safety window for safeTs
     * @param syncInterval Sync throttle; 0 = default to syncWindow
     *
     * TX channel: this side binds _tx, peer connects _tx (we send, peer recvs)
     * RX channel: peer binds _rx, we connect _rx (peer sends, we recv)
     */
    Port(const std::string& name,
         uint32_t module_id, uint32_t port_id,
         const std::string& endpoint,
         bool bind_tx,
         zmq::context_t& ctx,
         uint64_t syncWindow,
         uint64_t syncInterval = 0);

    ~Port();

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

    uint64_t nextVisibleTick() const { return receiveTimestamp(); }
    void advanceVisibleTick(uint64_t t) {
        if (t > _lastRxT) _lastRxT = t;
    }

  private:
    std::string _name;
    uint32_t _moduleId, _portId;
    zmq::context_t& _ctx;

    // TX socket: we bind (_bindTx=true) → used for send
    std::unique_ptr<zmq::socket_t> _txSock;
    // RX socket: we connect (_bindTx=false → peer binds) → used for recv
    std::unique_ptr<zmq::socket_t> _rxSock;

    uint64_t _syncWindow, _syncInterval, _lastSyncTs;
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
