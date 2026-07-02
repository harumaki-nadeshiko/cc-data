#include "framework/Port.hh"
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <zmq.hpp>

namespace framework {

// Per-message PORT-SEND/PORT-RECV traces fire on every sync (every ~linkLatency
// ticks) and generate multi-GB logs that exhaust the disk. Gate them behind
// EP_DEBUG_PORT=1 so they are OFF by default. Read once.
static inline bool portDebugEnabled() {
    static int v = -1;
    if (v < 0) {
        const char* e = std::getenv("EP_DEBUG_PORT");
        v = (e && e[0] == '1') ? 1 : 0;
    }
    return v != 0;
}

// ── TxHandle ────────────────────────────────────────────────────────
MemMessage* TxHandle::buffer() {
    if (!_port) return nullptr;
    return &_port->_sendBuf;
}
bool TxHandle::send() {
    if (!_port) return false;
    bool ok = _port->doSend();
    _port->releaseSendSlot();
    _port = nullptr;
    return ok;
}
void TxHandle::cancel() {
    if (!_port) { return; }
    _port->releaseSendSlot();
    _port = nullptr;
}

// ── Port ────────────────────────────────────────────────────────────
Port::Port() {}

Port::~Port() { closeLocal(); }

bool
Port::init(const PortParams& params, const PortRuntime& runtime)
{
    if (_state != PortState::INIT) return false;
    _name = params.name;
    _moduleId = params.moduleId;
    _portId = params.portId;
    _syncInterval = runtime.syncInterval;
    _linkLatency  = runtime.linkLatency;

    _ctx = std::make_unique<zmq::context_t>(1);
    _txSock = std::make_unique<zmq::socket_t>(*_ctx, zmq::socket_type::pair);
    _rxSock = std::make_unique<zmq::socket_t>(*_ctx, zmq::socket_type::pair);

    int sndtimeo = 10;
    _txSock->set(zmq::sockopt::sndtimeo, sndtimeo);
    int hwm = 0;  // unbounded — see clock-sync fix rationale
    _txSock->set(zmq::sockopt::sndhwm, hwm);
    _txSock->set(zmq::sockopt::rcvhwm, hwm);
    _rxSock->set(zmq::sockopt::sndhwm, hwm);
    _rxSock->set(zmq::sockopt::rcvhwm, hwm);

    try {
        _rxSock->bind(params.localRxEndpoint);
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[Port %s] rx bind(%s) failed: %s\n",
                     _name.c_str(), params.localRxEndpoint.c_str(), e.what());
        closeLocal();
        return false;
    }
    // If peer endpoint equals local (bind-only server mode, e.g. barrier),
    // don't connect — send/recv share the bound _rxSock.
    if (params.peerRxEndpoint != params.localRxEndpoint) {
        try {
            _txSock->connect(params.peerRxEndpoint);
        } catch (const zmq::error_t& e) {
            std::fprintf(stderr, "[Port %s] tx connect(%s) failed: %s\n",
                         _name.c_str(), params.peerRxEndpoint.c_str(), e.what());
            closeLocal();
            return false;
        }
    } else {
        _txSock.reset();  // bind-only; send via _rxSock
    }
    _state = PortState::READY;
    std::fprintf(stderr, "[Port %s] rx=%s tx->%s\n",
                 _name.c_str(), params.localRxEndpoint.c_str(),
                 params.peerRxEndpoint.c_str());
    return true;
}

void Port::failClosed(const char* reason) {
    _state = PortState::PEER_LOST;
    std::fprintf(stderr, "[Port %s] failClosed: %s\n", _name.c_str(), reason);
}

void Port::closeLocal() {
    if (_state == PortState::CLOSED) return;
    _state = PortState::CLOSED;
    if (_rxSock) _rxSock.reset();
    if (_txSock) _txSock.reset();
    if (_ctx)    _ctx.reset();
}

void Port::terminate() {
    if (_state != PortState::READY) { closeLocal(); return; }
    _state = PortState::TERMINATING;
    // best-effort TERMINATE notice
    if (_txSock && _sendBufInUse) {
        // a send is mid-fill; cancel it first
        releaseSendSlot();
    }
    if (_txSock) {
        try {
            MemMessage m;
            m.hdr.type = static_cast<uint32_t>(MemMessageType::TERMINATE);
            m.hdr.size = sizeof(MemMessageHeader);
            m.hdr.sourceId = _moduleId;
            zmq::message_t z(m.hdr.size);
            std::memcpy(z.data(), &m, m.hdr.size);
            _txSock->send(z, zmq::send_flags::dontwait);
        } catch (...) {}
    }
    closeLocal();
}

void Port::releaseSendSlot() { _sendBufInUse = false; }

uint64_t Port::receiveTimestamp() const { return _pending ? _pendingT : _lastRxT; }

uint64_t Port::safeTs(uint64_t curT) const {
    // Multi-process split: once the peer has terminated (sent TERMINATE) or the
    // link has otherwise closed, the peer's virtual clock is no longer a
    // constraint — it is "done", i.e. infinitely far ahead. Returning UINT64_MAX
    // removes this port from any min()-based clock bound, so a node that
    // finishes its workload early (e.g. an idle node) does NOT freeze the
    // distributed clock of the still-running nodes. Without this, a finished
    // peer's last sync timestamp would cap min(safeTs) forever -> global stall.
    if (_state == PortState::PEER_LOST || _state == PortState::CLOSED ||
        _state == PortState::TERMINATING)
        return ~static_cast<uint64_t>(0);

    // safeTs = min(peer's latest timestamp, own lookahead window). Before the
    // first message from the peer, receiveTimestamp()==0 (init value, not a
    // sentinel), so this returns 0 — the min() absorbing element — parking the
    // local clock at 0 until the peer's first sync raises _lastRxT. No special
    // case needed (matches the reference TimeSync::safeTs).
    uint64_t rxt = receiveTimestamp();
    uint64_t base = (_lastSyncTs > 0) ? _lastSyncTs : curT;
    uint64_t syncBound = base + _syncInterval;
    return (rxt < syncBound) ? rxt : syncBound;
}

// ── Data plane ──────────────────────────────────────────────────────

TxHandle*
Port::allocateSendBuffer(uint64_t timestamp)
{
    if (_state != PortState::READY || _sendBufInUse) return nullptr;
    _sendBuf.clear();
    _sendBuf.hdr.timestamp = timestamp + _linkLatency;
    _sendBuf.hdr.sourceId = _moduleId;
    _sendBuf.hdr.size = sizeof(MemMessageHeader);
    _sendBufInUse = true;
    _txHandle = TxHandle(this);
    return &_txHandle;
}

bool
Port::doSend()  // private helper invoked by TxHandle::send
{
    if (_state == PortState::PEER_LOST) return false;
    if (_state != PortState::READY) return false;
    auto& sock = _txSock ? *_txSock : *_rxSock;
    try {
        zmq::message_t z(_sendBuf.hdr.size);
        std::memcpy(z.data(), &_sendBuf, _sendBuf.hdr.size);
        if (portDebugEnabled())
            std::fprintf(stderr, "[PORT-SEND] %s type=%u ts=%lu dst=%u\n",
                         _name.c_str(), _sendBuf.hdr.type, _sendBuf.hdr.timestamp,
                         _sendBuf.hdr.targetId);
        sock.send(z, zmq::send_flags::none);
        return true;
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[PORT-SEND-ERR] %s: %s\n", _name.c_str(), e.what());
        return false;
    }
}

MemMessage*
Port::recv(uint64_t curT, ReceiveStatus* status)
{
    ReceiveStatus dummy;
    ReceiveStatus& st = status ? *status : dummy;

    if (_state == PortState::PEER_LOST || _state == PortState::CLOSED) {
        st = ReceiveStatus::kEmpty; return nullptr;
    }
    if (_state != PortState::READY) { st = ReceiveStatus::kEmpty; return nullptr; }

    if (_pending) {
        if (_pendingT <= curT) {
            _lastRxT = _pendingT;
            _pending = false;
            // A CONTROL_SYNC is delivered as an ordinary kMessage; the caller
            // recognizes and skips it via hdr.type (see 2.1.2 alignment).
            st = ReceiveStatus::kMessage;
            static thread_local MemMessage result;
            result = _pendingMsg;
            return &result;
        }
        st = ReceiveStatus::kPendingFuture;
        return nullptr;
    }

    MemMessage tmp;
    try {
        zmq::message_t z;
        auto r = _rxSock->recv(z, zmq::recv_flags::dontwait);
        if (!r.has_value()) { st = ReceiveStatus::kEmpty; return nullptr; }
        uint32_t sz = z.size();
        if (sz < kMemMessageHeaderSize || sz > sizeof(MemMessage)) {
            st = ReceiveStatus::kEmpty; return nullptr;
        }
        std::memcpy(&tmp, z.data(), sz);
    } catch (const zmq::error_t&) { st = ReceiveStatus::kEmpty; return nullptr; }

    _lastRxT = (uint64_t)tmp.hdr.timestamp;
    if (portDebugEnabled())
        std::fprintf(stderr, "[PORT-RECV] %s type=%u ts=%lu src=%u dst=%u curT=%lu\n",
                     _name.c_str(), tmp.hdr.type, tmp.hdr.timestamp,
                     tmp.hdr.sourceId, tmp.hdr.targetId, curT);

    // A CONTROL_SYNC carries no payload but has a timestamp; it is treated like
    // any other message here (_lastRxT updated above tracks the peer's latest
    // ts). It flows through the timestamp-visibility check below and is returned
    // as an ordinary kMessage for the caller to skip by hdr.type. We must NOT
    // advance _lastSyncTs from a received sync — that is our own heartbeat clock
    // and is only set by emitSync().
    if (tmp.hdr.type == static_cast<uint32_t>(MemMessageType::TERMINATE)) {
        // Peer is shutting down; stop accepting new traffic.
        failClosed("peer TERMINATE received");
        st = ReceiveStatus::kEmpty;
        return nullptr;
    }
    if (tmp.hdr.timestamp > curT) {
        _pending = true; _pendingT = tmp.hdr.timestamp; _pendingMsg = tmp;
        st = ReceiveStatus::kPendingFuture; return nullptr;
    }
    st = ReceiveStatus::kMessage;
    static thread_local MemMessage result; result = tmp; return &result;
}

// ── Sync ────────────────────────────────────────────────────────────
bool
Port::emitSync(uint64_t curTick)
{
    if (_lastSyncTs > 0 && curTick - _lastSyncTs < _linkLatency)
        return true;
    TxHandle* h = allocateSendBuffer(curTick);
    if (!h) return false;
    MemMessage* buf = h->buffer();
    buf->hdr.type = static_cast<uint32_t>(MemMessageType::CONTROL_SYNC);
    buf->hdr.size = sizeof(MemMessageHeader);
    bool ok = h->send();
    // send() releases the slot; if it failed the slot is already released.
    if (ok) { _lastSyncTs = curTick; return true; }
    return false;
}

// ── PortEnvLoader ───────────────────────────────────────────────────
static const std::string IPC_BASE = "/workspace/gem5/shared_ipc/ipc";

PortParams PortEnvLoader::gem5UbioPort(int nid) {
    PortParams p;
    p.name = "gem5_ubio";
    p.moduleId = nid; p.portId = 0;
    p.localRxEndpoint = "ipc://" + IPC_BASE + "_ubio_" + std::to_string(nid) + "_to_gem5_" + std::to_string(nid);
    p.peerRxEndpoint  = "ipc://" + IPC_BASE + "_gem5_" + std::to_string(nid) + "_to_ubio_" + std::to_string(nid);
    return p;
}
PortParams PortEnvLoader::ubioGem5Port(int nid, bool isUbio) {
    PortParams p;
    p.name = isUbio ? "gem5" : "gem5_ubio";
    p.moduleId = nid; p.portId = 0;
    if (isUbio) {
        p.localRxEndpoint = "ipc://" + IPC_BASE + "_gem5_" + std::to_string(nid) + "_to_ubio_" + std::to_string(nid);
        p.peerRxEndpoint  = "ipc://" + IPC_BASE + "_ubio_" + std::to_string(nid) + "_to_gem5_" + std::to_string(nid);
    } else {
        return gem5UbioPort(nid);
    }
    return p;
}
PortParams PortEnvLoader::ubioNetPort(int nid) {
    PortParams p;
    p.name = "net"; p.moduleId = nid; p.portId = 1;
    p.localRxEndpoint = "ipc://" + IPC_BASE + "_networksim_m" + std::to_string(nid) + "_to_ubio_" + std::to_string(nid);
    p.peerRxEndpoint  = "ipc://" + IPC_BASE + "_ubio_" + std::to_string(nid) + "_to_networksim_m" + std::to_string(nid);
    return p;
}
PortParams PortEnvLoader::nsimUbioPort(int mod) {
    PortParams p;
    p.name = "nsim_p" + std::to_string(mod); p.moduleId = mod; p.portId = 1;
    p.localRxEndpoint = "ipc://" + IPC_BASE + "_ubio_" + std::to_string(mod) + "_to_networksim_m" + std::to_string(mod);
    p.peerRxEndpoint  = "ipc://" + IPC_BASE + "_networksim_m" + std::to_string(mod) + "_to_ubio_" + std::to_string(mod);
    return p;
}
PortParams PortEnvLoader::barrierPort(int n) {
    // barrier uses a single endpoint pair per node: barrier binds, ubio/gem5
    // connect. Use a duplex pair for uniformity with the new init().
    PortParams p;
    p.name = "barrier_n" + std::to_string(n); p.moduleId = n; p.portId = 1;
    p.localRxEndpoint = "ipc:///tmp/barrier_m" + std::to_string(n) + "_p1";
    p.peerRxEndpoint  = "ipc:///tmp/barrier_m" + std::to_string(n) + "_p1";
    return p;
}

} // namespace framework
