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

class Port
{
  public:
    /**
     * @param name         Human-readable name
     * @param module_id    Launcher-assigned module ID
     * @param port_id      Port ID within this module
     * @param endpoint     IPC endpoint. bind=true → bind; bind=false → connect
     * @param bind         True if this side binds, false if connects
     * @param ctx          ZMQ context
     * @param syncWindow   Safety window: safeTs upper bound = _lastSyncTs + syncWindow
     * @param syncInterval Sync throttle interval; 0 = default to syncWindow
     */
    Port(const std::string& name,
         uint32_t module_id, uint32_t port_id,
         const std::string& endpoint,
         bool bind,
         zmq::context_t& ctx,
         uint64_t syncWindow,
         uint64_t syncInterval = 0);

    ~Port();

    MemMessage* sendAllocateBuffer(uint64_t timestamp);
    bool send(MemMessage* msg);

    /**
     * Three-state receive. Pulls from ZMQ or returns cached pending message.
     *
     * @param  curT   Current local virtual time
     * @param  status [out] ReceiveStatus classification
     * @return        Message pointer (owned by Port), or nullptr if none ready
     *
     * If a pending future message's timestamp <= curT, it is returned as kMessage.
     * If pending exists but timestamp > curT, returns kPendingFuture.
     * If a new ZMQ message has timestamp > curT, it is cached as pending (single slot)
     *   and kPendingFuture is returned.
     *
     * Invariant: while _pending is true, no new ZMQ message is pulled.
     * Invariant: _lastRxT is updated on every message pulled from ZMQ (including sync).
     */
    MemMessage* recv(uint64_t curT, ReceiveStatus* status = nullptr);

    /**
     * Earliest inbound time boundary visible to this Port.
     * If pending future message: returns its timestamp.
     * Otherwise: returns _lastRxT (timestamp of last ZMQ-received message).
     * Initial value: UINT64_MAX (no bound; comment fallback=0 for debug).
     */
    uint64_t receiveTimestamp() const {
        return _pending ? _pendingT : _lastRxT;
    }

    /**
     * Conservative upper bound on how far local time may advance
     * without risking loss of inbound messages.
     *
     * safeTs(curT) = min(receiveTimestamp(), syncBound)
     *   where syncBound = (hasLastSync ? _lastSyncTs : curT) + _syncWindow
     */
    uint64_t safeTs(uint64_t curT) const {
        uint64_t base = (_lastSyncTs > 0) ? _lastSyncTs : curT;
        uint64_t syncBound = base + _syncWindow;
        uint64_t rxt = receiveTimestamp();
        return (rxt < syncBound) ? rxt : syncBound;
    }

    /**
     * Emit a CONTROL_SYNC message, throttled by _syncInterval.
     * Also serves as heartbeat for silent ports — must be called periodically
     * even on ports with no data traffic.
     */
    bool emitSync(uint64_t curTick);

    uint32_t moduleId() const { return _moduleId; }
    uint32_t portId() const { return _portId; }

    // ---- deprecated wrappers, kept for transition ----
    uint64_t nextVisibleTick() const { return receiveTimestamp(); }
    void advanceVisibleTick(uint64_t t) {
        if (t > _lastRxT) _lastRxT = t;
    }

  private:
    std::string _name;
    uint32_t _moduleId, _portId;
    zmq::context_t& _ctx;
    std::unique_ptr<zmq::socket_t> _socket;

    uint64_t _syncWindow;    // safeTs safety window
    uint64_t _syncInterval;  // emitSync throttle interval
    uint64_t _lastSyncTs;    // last sync/heartbeat send time

    // Single-slot future message cache (replaces multi-slot deferred deque)
    // Invariant: while _pending==true, recv() MUST NOT pull from ZMQ socket.
    bool _pending;
    uint64_t _pendingT;
    MemMessage _pendingMsg;

    // receiveTimestamp() initial value: UINT64_MAX ("no inbound bound yet").
    // debug fallback: change to 0 if seeing over-advancement bugs.
    uint64_t _lastRxT;

    MemMessage _sendBuf;
    bool _sendBufInUse;
};

/**
 * Multi-port time-sync helper.
 * For each port: emitSync, drain ready messages, collect min safeTs.
 * Returns the minimum safe tick across all ports.
 */
uint64_t synced_receive_lower_bound(Port** ports, int n, uint64_t tick);

} // namespace framework
#endif
