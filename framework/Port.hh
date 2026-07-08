#ifndef FRAMEWORK_PORT_HH
#define FRAMEWORK_PORT_HH

#include <cstdint>
#include <memory>
#include <string>

#include "framework/MemMessage.hh"

// Forward-declare ZMQ types so Port's public header does not expose zmq.hpp to
// consumers (gem5 / ubio / nsim / barrier). The full zmq.hpp is only included in
// Port.cc.
namespace zmq {
class context_t;
class socket_t;
}

namespace framework {

// Default time values in ps. 10000 ps = 10 ns.
// - linkLatency  = physical ZMQ-hop delay + heartbeat send interval.
// - syncInterval = clock lookahead window.
// Invariant: syncInterval >= linkLatency (violating this prevents the local
// clock from advancing when the peer sends syncs at linkLatency cadence).
static constexpr uint64_t kDefaultSyncInterval = 100000;
static constexpr uint64_t kDefaultLinkLatency  = 100000;

enum class ReceiveStatus {
    kMessage,        // a message is visible (timestamp <= curT); may be a
                     // CONTROL_SYNC — the caller filters those by hdr.type.
    kEmpty,          // nothing to receive right now
    kPendingFuture,  // head message has timestamp > curT; buffered, not visible
};

// Static identity of a port (loaded by PortEnvLoader).
struct PortParams {
    std::string name;
    uint32_t moduleId = 0;
    uint32_t portId   = 0;
    std::string localRxEndpoint;   // complete ipc:// URL
    std::string peerRxEndpoint;    // complete ipc:// URL
};

// Runtime tunables (not part of static identity).
struct PortRuntime {
    uint64_t syncInterval = kDefaultSyncInterval;
    uint64_t linkLatency  = kDefaultLinkLatency;
};

class Port
{
  public:
    Port();
    ~Port();

    Port(const Port&) = delete;
    Port& operator=(const Port&) = delete;

    // One-shot init. Returns false on bind/connect failure. Not reusable.
    bool init(const PortParams& params, const PortRuntime& runtime = PortRuntime());

    // Best-effort TERMINATE to peer, then release sockets.
    void terminate();

    // ---- Data plane ----
    // Allocate a NEW transport packet stamped at `timestamp` (hdr.timestamp =
    // ts + linkLatency, sourceId, size preset). Returns a heap MemMessage* the
    // caller fills. Ownership passes to send(); if the caller decides NOT to
    // send, it must delete the returned pointer itself. Returns nullptr only on
    // allocation failure.
    MemMessage* allocateSendBuffer(uint64_t timestamp);
    // Send `msg` (memcpy into a zmq message) and DELETE msg (takes ownership,
    // deletes on both success and failure). Returns false on transport failure.
    bool send(MemMessage* msg);
    MemMessage* recv(uint64_t curT, ReceiveStatus* status = nullptr);

    uint64_t receiveTimestamp() const;
    uint64_t safeTs(uint64_t curT) const;
    bool emitSync(uint64_t curTick);

    uint32_t moduleId() const { return _moduleId; }
    uint32_t portId() const { return _portId; }
    uint64_t syncInterval() const { return _syncInterval; }
    const std::string& name() const { return _name; }

  private:
    void _releaseSockets();

    std::string _name;
    uint32_t _moduleId = 0, _portId = 0;
    bool _open = false;

    std::unique_ptr<zmq::context_t> _ctx;
    std::unique_ptr<zmq::socket_t>  _txSock;
    std::unique_ptr<zmq::socket_t>  _rxSock;

    uint64_t _syncInterval = kDefaultSyncInterval;
    uint64_t _linkLatency  = kDefaultLinkLatency;
    uint64_t _lastSyncTs   = 0;

    bool _pending = false;
    uint64_t _pendingT = 0;
    MemMessage _pendingMsg;
    // Init 0 (not a sentinel): before the first message from the peer,
    // receiveTimestamp()==0 makes safeTs()==0, which is the absorbing element
    // of the min()-based clock bound — the local clock cannot advance past 0
    // until the peer's first sync raises _lastRxT. This matches the reference
    // framework and needs no special-case branch in safeTs().
    uint64_t _lastRxT = 0;
};

// Per-port environment/config loader. Encapsulates the endpoint naming so each
// process does not re-implement the ipc:// URL assembly.
struct PortEnvLoader {
    // ubio <-> gem5 pair (per node n).
    //   ubio's gem5 port:   rx = ..._gem5_n_to_ubio_n,  tx = ..._ubio_n_to_gem5_n
    //   gem5's ubio port:   rx = ..._ubio_n_to_gem5_n,  tx = ..._gem5_n_to_ubio_n
    static PortParams ubioGem5Port(int nid, bool isUbio);
    static PortParams gem5UbioPort(int nid);
    // ubio <-> nsim pair (per module m).
    static PortParams ubioNetPort(int nid);
    static PortParams nsimUbioPort(int mod);
    // barrier (single endpoint, bind side).
    static PortParams barrierPort(int n);
};

} // namespace framework
#endif // FRAMEWORK_PORT_HH
