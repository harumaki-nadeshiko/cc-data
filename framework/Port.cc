#include "framework/Port.hh"
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <new>
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

// ── Port ────────────────────────────────────────────────────────────
Port::Port() {}

Port::~Port() { _releaseSockets(); }

void Port::_releaseSockets() {
    if (!_open) return;
    _open = false;
    if (_rxSock) _rxSock.reset();
    if (_txSock) _txSock.reset();
    if (_ctx)    _ctx.reset();
}

bool
Port::init(const PortParams& params, const PortRuntime& runtime)
{
    if (_open) return false;
    _name = params.name;
    _moduleId = params.moduleId;
    _portId = params.portId;
    _syncInterval = runtime.syncInterval;
    _linkLatency  = runtime.linkLatency;

    const char* env_link = std::getenv("EP_LINK_LATENCY_PS");
    if (env_link) _linkLatency = std::strtoull(env_link, nullptr, 10);
    const char* env_sync = std::getenv("EP_SYNC_INTERVAL_PS");
    if (env_sync) _syncInterval = std::strtoull(env_sync, nullptr, 10);
    if (_syncInterval < _linkLatency) {
        std::fprintf(stderr, "[PORT-CFG-WARN] %s syncInterval(%lu) < linkLatency(%lu), "
                     "clamping syncInterval=linkLatency\n",
                     params.name.c_str(), _syncInterval, _linkLatency);
        _syncInterval = _linkLatency;
    }

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
        _releaseSockets();
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
            _releaseSockets();
            return false;
        }
    } else {
        _txSock.reset();  // bind-only; send via _rxSock
    }
    _open = true;
    std::fprintf(stderr, "[Port %s] rx=%s tx->%s\n",
                 _name.c_str(), params.localRxEndpoint.c_str(),
                 params.peerRxEndpoint.c_str());
    return true;
}

void Port::terminate() {
    if (!_open) { _releaseSockets(); return; }
    // best-effort TERMINATE notice
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
    _releaseSockets();
}

uint64_t Port::receiveTimestamp() const { return _pending ? _pendingT : _lastRxT; }

uint64_t Port::safeTs(uint64_t curT) const {
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

MemMessage*
Port::allocateSendBuffer(uint64_t timestamp)
{
    if (!_open) return nullptr;
    MemMessage* msg = new (std::nothrow) MemMessage();
    if (!msg) return nullptr;
    msg->clear();
    msg->hdr.timestamp = timestamp + _linkLatency;
    msg->hdr.sourceId = _moduleId;
    msg->hdr.size = sizeof(MemMessageHeader);
    return msg;
}

bool
Port::send(MemMessage* msg)
{
    if (!msg) return false;
    if (!_open) {
        delete msg;
        return false;
    }
    bool ok = false;
    auto& sock = _txSock ? *_txSock : *_rxSock;
    try {
        zmq::message_t z(msg->hdr.size);
        std::memcpy(z.data(), msg, msg->hdr.size);
        if (portDebugEnabled())
            std::fprintf(stderr, "[PORT-SEND] %s type=%u ts=%lu dst=%u\n",
                         _name.c_str(), msg->hdr.type, msg->hdr.timestamp,
                         msg->hdr.targetId);
        sock.send(z, zmq::send_flags::none);
        ok = true;
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[PORT-SEND-ERR] %s: %s\n", _name.c_str(), e.what());
        ok = false;
    }
    delete msg;
    return ok;
}

MemMessage*
Port::recv(uint64_t curT, ReceiveStatus* status)
{
    ReceiveStatus dummy;
    ReceiveStatus& st = status ? *status : dummy;

    if (!_open) {
        st = ReceiveStatus::kEmpty; return nullptr;
    }

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
        // Deliver TERMINATE to the caller so the application can mark this
        // port done and stop polling it (Port no longer tracks peer state).
        st = ReceiveStatus::kMessage;
        static thread_local MemMessage result; result = tmp; return &result;
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
    MemMessage* msg = allocateSendBuffer(curTick);
    if (!msg) return false;
    msg->hdr.type = static_cast<uint32_t>(MemMessageType::CONTROL_SYNC);
    msg->hdr.size = sizeof(MemMessageHeader);
    if (send(msg)) { _lastSyncTs = curTick; return true; }
    return false;
}

// ── PortEnvLoader ───────────────────────────────────────────────────
static std::string
ipcBase()
{
    const char *dir = std::getenv("UBCC_IPC_DIR");
    return std::string((dir && *dir) ? dir : "/workspace/gem5/shared_ipc") +
        "/ipc";
}

PortParams PortEnvLoader::gem5UbioPort(int nid) {
    PortParams p;
    p.name = "gem5_ubio";
    p.moduleId = nid; p.portId = 0;
    const auto base = ipcBase();
    p.localRxEndpoint = "ipc://" + base + "_ubio_" + std::to_string(nid) + "_to_gem5_" + std::to_string(nid);
    p.peerRxEndpoint  = "ipc://" + base + "_gem5_" + std::to_string(nid) + "_to_ubio_" + std::to_string(nid);
    return p;
}
PortParams PortEnvLoader::ubioGem5Port(int nid, bool isUbio) {
    PortParams p;
    p.name = isUbio ? "gem5" : "gem5_ubio";
    p.moduleId = nid; p.portId = 0;
    if (isUbio) {
        const auto base = ipcBase();
        p.localRxEndpoint = "ipc://" + base + "_gem5_" + std::to_string(nid) + "_to_ubio_" + std::to_string(nid);
        p.peerRxEndpoint  = "ipc://" + base + "_ubio_" + std::to_string(nid) + "_to_gem5_" + std::to_string(nid);
    } else {
        return gem5UbioPort(nid);
    }
    return p;
}
PortParams PortEnvLoader::ubioNetPort(int nid) {
    PortParams p;
    p.name = "net"; p.moduleId = nid; p.portId = 1;
    const auto base = ipcBase();
    p.localRxEndpoint = "ipc://" + base + "_networksim_m" + std::to_string(nid) + "_to_ubio_" + std::to_string(nid);
    p.peerRxEndpoint  = "ipc://" + base + "_ubio_" + std::to_string(nid) + "_to_networksim_m" + std::to_string(nid);
    return p;
}
PortParams PortEnvLoader::nsimUbioPort(int mod) {
    PortParams p;
    p.name = "nsim_p" + std::to_string(mod); p.moduleId = mod; p.portId = 1;
    const auto base = ipcBase();
    p.localRxEndpoint = "ipc://" + base + "_ubio_" + std::to_string(mod) + "_to_networksim_m" + std::to_string(mod);
    p.peerRxEndpoint  = "ipc://" + base + "_networksim_m" + std::to_string(mod) + "_to_ubio_" + std::to_string(mod);
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
