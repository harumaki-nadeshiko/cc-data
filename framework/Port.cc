#include "framework/Port.hh"
#include <algorithm>
#include <climits>
#include <cstdio>

namespace framework {

// ── Duplex constructor ──────────────────────────────────────────────
Port::Port(const std::string& name, uint32_t module_id, uint32_t port_id,
           const std::string& local_rx_endpoint,
           const std::string& peer_rx_endpoint,
           zmq::context_t& ctx, uint64_t syncInterval, uint64_t linkLatency)
    : _name(name), _moduleId(module_id), _portId(port_id),
      _ctx(ctx),
      _state(PortState::INIT),
      _helloSent(false), _helloRecvd(false), _ackSent(false), _ackRecvd(false),
      _syncInterval(syncInterval),
      _linkLatency(linkLatency),
      _lastSyncTs(0),
      _pending(false), _pendingT(0),
      _lastRxT(UINT64_MAX),
      _sendBufInUse(false)
{
    _txSock = std::make_unique<zmq::socket_t>(_ctx, zmq::socket_type::pair);
    _rxSock = std::make_unique<zmq::socket_t>(_ctx, zmq::socket_type::pair);

    int sndtimeo = 10;
    _txSock->set(zmq::sockopt::sndtimeo, sndtimeo);

    // Unbounded high-water marks (0 = no limit). Otherwise a transient backlog
    // of heartbeat/CONTROL_SYNC traffic can fill the default 1000-deep queue and
    // make send() block up to sndtimeo (10 ms) — and our send() ignores the
    // EAGAIN result, so it would be SILENTLY dropped. That 10 ms-per-message
    // stall is what throttled the clock leapfrog. Must be set before bind/connect.
    int hwm = 0;
    _txSock->set(zmq::sockopt::sndhwm, hwm);
    _txSock->set(zmq::sockopt::rcvhwm, hwm);
    _rxSock->set(zmq::sockopt::sndhwm, hwm);
    _rxSock->set(zmq::sockopt::rcvhwm, hwm);

    try {
        _rxSock->bind(local_rx_endpoint);
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[Port %s] rx bind(%s) failed: %s\n",
                     _name.c_str(), local_rx_endpoint.c_str(), e.what());
        return;
    }

    try {
        _txSock->connect(peer_rx_endpoint);
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[Port %s] tx connect(%s) failed: %s\n",
                     _name.c_str(), peer_rx_endpoint.c_str(), e.what());
        return;
    }

    _state = PortState::READY;
    std::fprintf(stderr, "[Port %s] rx=%s tx->%s\n",
                 _name.c_str(), local_rx_endpoint.c_str(), peer_rx_endpoint.c_str());
}

// ── Deprecated single-endpoint constructor ──────────────────────────
Port::Port(const std::string& name, uint32_t module_id, uint32_t port_id,
           const std::string& endpoint, bool bind,
           zmq::context_t& ctx, uint64_t syncInterval, uint64_t linkLatency)
    : _name(name), _moduleId(module_id), _portId(port_id),
      _ctx(ctx),
      _state(PortState::READY),
      _helloSent(true), _helloRecvd(true), _ackSent(true), _ackRecvd(true),
      _syncInterval(syncInterval),
      _linkLatency(linkLatency),
      _lastSyncTs(0),
      _pending(false), _pendingT(0),
      _lastRxT(UINT64_MAX),
      _sendBufInUse(false)
{
    _rxSock = std::make_unique<zmq::socket_t>(_ctx, zmq::socket_type::pair);
    _txSock = std::make_unique<zmq::socket_t>(_ctx, zmq::socket_type::pair);

    int sndtimeo = 10;
    _txSock->set(zmq::sockopt::sndtimeo, sndtimeo);

    try {
        if (bind) {
            _rxSock->bind(endpoint);
        } else {
            _txSock->connect(endpoint);
        }
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[Port %s] deprecated %s(%s) failed: %s\n",
                     _name.c_str(), bind ? "bind" : "connect",
                     endpoint.c_str(), e.what());
        return;
    }
}

Port::~Port() {}

void Port::pollHandshake() {}

void Port::failClosed(const char* reason) {
    _state = PortState::PEER_LOST;
    std::fprintf(stderr, "[Port %s] failClosed: %s\n", _name.c_str(), reason);
}

void Port::tryHandshakeStep() {}
void Port::sendHello() {}
void Port::sendHelloAck() {}
bool Port::tryRecvHello() { return true; }
bool Port::tryRecvHelloAck() { return true; }
bool Port::tryRecvControl() { return true; }

// ── Data plane ─────────────────────────────────────────────────────

MemMessage*
Port::sendAllocateBuffer(uint64_t timestamp)
{
    if (_sendBufInUse) return nullptr;
    _sendBuf.clear();
    _sendBuf.hdr.timestamp = timestamp + _linkLatency;
    _sendBuf.hdr.src_module = _moduleId;
    _sendBuf.hdr.src_port = _portId;
    _sendBuf.hdr.size = sizeof(MemMessageHeader);
    _sendBufInUse = true;
    return &_sendBuf;
}

bool
Port::send(MemMessage* msg)
{
    if (_state == PortState::PEER_LOST) return false;
    if (_state != PortState::READY) {
        pollHandshake();
        if (_state != PortState::READY) return false;
    }
    if (!msg || !_sendBufInUse) return false;
    _sendBufInUse = false;
    auto& sock = _txSock ? *_txSock : *_rxSock;
    try {
        zmq::message_t zmq_msg(msg->hdr.size);
        std::memcpy(zmq_msg.data(), msg, msg->hdr.size);
        std::fprintf(stderr, "[PORT-SEND] %s type=%u ts=%lu dst=%u:%u\n",
                     _name.c_str(), msg->hdr.type, msg->hdr.timestamp,
                     msg->hdr.dst_module, msg->hdr.dst_port);
        sock.send(zmq_msg, zmq::send_flags::none);
        return true;
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[PORT-SEND-ERR] %s: %s (errno=%d)\n",
                     _name.c_str(), e.what(), zmq_errno());
        return false;
    }
}

// ── Receive ────────────────────────────────────────────────────────

MemMessage*
Port::recv(uint64_t curT, ReceiveStatus* status)
{
    ReceiveStatus dummy;
    ReceiveStatus& st = status ? *status : dummy;

    if (_state == PortState::PEER_LOST) {
        st = ReceiveStatus::kEmpty;
        return nullptr;
    }
    if (_state != PortState::READY) {
        pollHandshake();
        st = ReceiveStatus::kEmpty;
        return nullptr;
    }

    if (_pending) {
        if (_pendingT <= curT) {
            _lastRxT = _pendingT;
            _pending = false;
            st = (_pendingMsg.hdr.type ==
                  static_cast<uint32_t>(MemMessageType::CONTROL_SYNC))
                     ? ReceiveStatus::kSync
                     : ReceiveStatus::kMessage;
            static thread_local MemMessage result;
            result = _pendingMsg;
            return &result;
        }
        st = ReceiveStatus::kPendingFuture;
        return nullptr;
    }

    MemMessage tmp;
    try {
        zmq::message_t zmq_msg;
        auto r = _rxSock->recv(zmq_msg, zmq::recv_flags::dontwait);
        if (!r.has_value()) {
            st = ReceiveStatus::kEmpty;
            return nullptr;
        }
        uint32_t sz = zmq_msg.size();
        if (sz < kMemMessageHeaderSize || sz > sizeof(MemMessage)) {
            st = ReceiveStatus::kEmpty;
            return nullptr;
        }
        std::memcpy(&tmp, zmq_msg.data(), sz);
    } catch (const zmq::error_t&) {
        st = ReceiveStatus::kEmpty;
        return nullptr;
    }

    _lastRxT = (uint64_t)tmp.hdr.timestamp;

    std::fprintf(stderr, "[PORT-RECV] %s type=%u ts=%lu src=%u:%u dst=%u:%u curT=%lu\n",
                 _name.c_str(), tmp.hdr.type, tmp.hdr.timestamp,
                 tmp.hdr.src_module, tmp.hdr.src_port,
                 tmp.hdr.dst_module, tmp.hdr.dst_port, curT);

    if (tmp.hdr.type == static_cast<uint32_t>(MemMessageType::CONTROL_SYNC)) {
        // _lastRxT was already updated above (line: _lastRxT = timestamp), which
        // is what receiveTimestamp()/safeTs() consume. Do NOT advance
        // _lastSyncTs from a *received* sync: _lastSyncTs is our OWN heartbeat
        // clock (the safeTs window base) and must only be set by emitSync().
        // Mixing the peer's timestamp in here distorts the sync window and the
        // emitSync rate-limit. Matches reference docs/all.cpp.
        st = ReceiveStatus::kSync;
        static thread_local MemMessage result;
        result = tmp;
        return &result;
    }

    if (tmp.hdr.timestamp > curT) {
        _pending = true;
        _pendingT = tmp.hdr.timestamp;
        _pendingMsg = tmp;
        st = ReceiveStatus::kPendingFuture;
        return nullptr;
    }

    st = ReceiveStatus::kMessage;
    static thread_local MemMessage result;
    result = tmp;
    return &result;
}

// ── Sync ───────────────────────────────────────────────────────────

bool
Port::emitSync(uint64_t curTick)
{
    // Heartbeat rate-limit MUST be at the link-latency granularity, NOT the
    // (much larger) syncInterval. Two peers advance toward each other in steps
    // of ~linkLatency (each side's CONTROL_SYNC carries ts = curTick +
    // linkLatency, which caps how far the other may advance). If we only re-emit
    // every syncInterval (=10x linkLatency), then once two clocks fall into
    // lockstep they both go silent after ~linkLatency of progress and neither
    // can lift the other's safeTs — a mutual stall (observed: gem5 node frozen
    // hot-spinning while its idle ubio peer was pinned 1 linkLatency behind).
    // Emitting every linkLatency keeps the leapfrog alive. curTick stays frozen
    // during a stall, so curTick-_lastSyncTs==0 < _linkLatency still prevents
    // flooding while we busy-wait.
    if (_lastSyncTs > 0 && curTick - _lastSyncTs < _linkLatency)
        return true;

    MemMessage* buf = sendAllocateBuffer(curTick);
    if (!buf) return false;
    buf->hdr.type = static_cast<uint32_t>(MemMessageType::CONTROL_SYNC);
    buf->hdr.size = sizeof(MemMessageHeader);
    buf->hdr.dst_module = 0;
    buf->hdr.dst_port = 0;

    bool ok = send(buf);
    _sendBufInUse = false;
    if (ok) {
        // NOTE: Only update _lastSyncTs (our own last heartbeat time).
        // Do NOT touch _lastRxT here: _lastRxT tracks the *peer's* latest
        // timestamp and feeds receiveTimestamp()/safeTs(). Bumping it with our
        // own curTick made receiveTimestamp() return curTick, which pinned
        // safeTs() to curTick forever and froze the simulation (mutual clock
        // deadlock). Matches reference TimeSync::emitSync in docs/all.cpp.
        _lastSyncTs = curTick;
        return true;
    }
    return false;
}

uint64_t
synced_receive_lower_bound(Port** ports, int n, uint64_t tick)
{
    uint64_t safe = UINT64_MAX;
    for (int i = 0; i < n; ++i) {
        Port* p = ports[i];
        if (!p) continue;
        p->emitSync(tick);
        MemMessage* m;
        ReceiveStatus st;
        while ((m = p->recv(tick, &st)) != nullptr) {
            if (st == ReceiveStatus::kEmpty ||
                st == ReceiveStatus::kPendingFuture) break;
        }
        uint64_t b = p->safeTs(tick);
        if (b < safe) safe = b;
    }
    if (safe == UINT64_MAX) return tick + 1;
    return safe;
}

} // namespace framework
