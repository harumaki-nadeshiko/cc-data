#include "framework/Port.hh"
#include <algorithm>
#include <climits>
#include <cstdio>

namespace framework {

// ── Duplex constructor ──────────────────────────────────────────────
Port::Port(const std::string& name, uint32_t module_id, uint32_t port_id,
           const std::string& local_rx_endpoint,
           const std::string& peer_rx_endpoint,
           zmq::context_t& ctx, uint64_t syncWindow, uint64_t syncInterval)
    : _name(name), _moduleId(module_id), _portId(port_id),
      _ctx(ctx),
      _state(PortState::INIT),
      _helloSent(false), _helloRecvd(false), _ackSent(false), _ackRecvd(false),
      _syncWindow(syncWindow),
      _syncInterval(syncInterval > 0 ? syncInterval : syncWindow),
      _lastSyncTs(0),
      _pending(false), _pendingT(0),
      _lastRxT(UINT64_MAX),
      _sendBufInUse(false)
{
    _txSock = std::make_unique<zmq::socket_t>(_ctx, zmq::socket_type::pair);
    _rxSock = std::make_unique<zmq::socket_t>(_ctx, zmq::socket_type::pair);

     int sndtimeo = 10;
    _txSock->set(zmq::sockopt::sndtimeo, sndtimeo);
    _txSock->set(zmq::sockopt::immediate, 1);
    _rxSock->set(zmq::sockopt::immediate, 1);

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
           zmq::context_t& ctx, uint64_t syncWindow, uint64_t syncInterval)
    : _name(name), _moduleId(module_id), _portId(port_id),
      _ctx(ctx),
      _state(PortState::READY),  // deprecated: no handshake
      _helloSent(true), _helloRecvd(true), _ackSent(true), _ackRecvd(true),
      _syncWindow(syncWindow),
      _syncInterval(syncInterval > 0 ? syncInterval : syncWindow),
      _lastSyncTs(0),
      _pending(false), _pendingT(0),
      _lastRxT(UINT64_MAX),
      _sendBufInUse(false)
{
    // Deprecated: dual PAIR sockets for full-duplex IPC.
    _rxSock = std::make_unique<zmq::socket_t>(_ctx, zmq::socket_type::pair);
    _txSock = std::make_unique<zmq::socket_t>(_ctx, zmq::socket_type::pair);

    int sndtimeo = 10;
    _txSock->set(zmq::sockopt::sndtimeo, sndtimeo);
    _txSock->set(zmq::sockopt::immediate, 1);
    _rxSock->set(zmq::sockopt::immediate, 1);

    try {
        if (bind) {
            std::fprintf(stderr, "[Port %s] BIND rx=%s_rx  tx.connect=%s_tx\n",
                         _name.c_str(), endpoint.c_str(), endpoint.c_str());
            _rxSock->bind(endpoint + "_rx");
            _txSock->connect(endpoint + "_tx");
        } else {
            std::fprintf(stderr, "[Port %s] CONNECT mode: rx.bind=%s_tx  tx.connect=%s_rx\n",
                         _name.c_str(), endpoint.c_str(), endpoint.c_str());
            _rxSock->bind(endpoint + "_tx");
            _txSock->connect(endpoint + "_rx");
        }
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[Port %s] %s(%s) failed: %s\n",
                     _name.c_str(), bind?"bind":"connect",
                     endpoint.c_str(), e.what());
    }
}

Port::~Port()
{
    if (_rxSock) _rxSock->close();
    if (_txSock) _txSock->close();
}

// ── Handshake ───────────────────────────────────────────────────────

void Port::sendHello()
{
    MemMessage hello;
    hello.hdr.timestamp = 0;
    hello.hdr.size = kMemMessageHeaderSize;
    hello.hdr.type = static_cast<uint32_t>(MemMessageType::PORT_HELLO);
    hello.hdr.src_module = _moduleId;
    hello.hdr.src_port = _portId;

    auto& sock = _txSock ? *_txSock : *_rxSock;
    try {
        zmq::message_t zmq_msg(kMemMessageHeaderSize);
        std::memcpy(zmq_msg.data(), &hello, kMemMessageHeaderSize);
        sock.send(zmq_msg, zmq::send_flags::none);
        _helloSent = true;
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[Port %s] sendHello failed: %s\n", _name.c_str(), e.what());
    }
}

void Port::sendHelloAck()
{
    MemMessage ack;
    ack.hdr.timestamp = 0;
    ack.hdr.size = kMemMessageHeaderSize;
    ack.hdr.type = static_cast<uint32_t>(MemMessageType::PORT_HELLO_ACK);
    ack.hdr.src_module = _moduleId;
    ack.hdr.src_port = _portId;

    auto& sock = _txSock ? *_txSock : *_rxSock;
    try {
        zmq::message_t zmq_msg(kMemMessageHeaderSize);
        std::memcpy(zmq_msg.data(), &ack, kMemMessageHeaderSize);
        sock.send(zmq_msg, zmq::send_flags::none);
        _ackSent = true;
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[Port %s] sendHelloAck failed: %s\n", _name.c_str(), e.what());
    }
}

bool Port::tryRecvHello()
{
    try {
        zmq::message_t zmq_msg;
        auto r = _rxSock->recv(zmq_msg, zmq::recv_flags::dontwait);
        if (!r.has_value()) return false;

        MemMessage tmp;
        uint32_t sz = zmq_msg.size();
        if (sz < kMemMessageHeaderSize) return false;
        std::memcpy(&tmp, zmq_msg.data(), std::min(sz, (uint32_t)sizeof(MemMessage)));

        if (tmp.hdr.type == static_cast<uint32_t>(MemMessageType::PORT_HELLO)) {
            if (!_helloRecvd) {
                _helloRecvd = true;
                sendHelloAck();
            }
            return true;
        }
        if (tmp.hdr.type == static_cast<uint32_t>(MemMessageType::PORT_HELLO_ACK)) {
            if (!_ackRecvd) {
                _ackRecvd = true;
            }
            return true;
        }
        // control sync or other during handshake: ignore
        return true;
    } catch (const zmq::error_t&) {
        return false;
    }
}

void Port::pollHandshake()
{
    if (_state == PortState::READY || _state == PortState::PEER_LOST)
        return;
    if (!_txSock) { _state = PortState::READY; return; }

    if (_state == PortState::HANDSHAKING) {
        if (!_helloSent) sendHello();
        tryRecvHello();
    }

    if (_helloSent && _helloRecvd && _ackSent && _ackRecvd) {
        _state = PortState::READY;
    }
}

void Port::failClosed(const char* reason)
{
    std::fprintf(stderr, "[Port %s] PEER_LOST: %s\n", _name.c_str(), reason);
    _state = PortState::PEER_LOST;
}

// ── Data plane ──────────────────────────────────────────────────────

MemMessage*
Port::sendAllocateBuffer(uint64_t timestamp)
{
    if (_sendBufInUse) return nullptr;
    _sendBuf.clear();
    _sendBuf.hdr.timestamp = timestamp;
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
    auto& sock = _txSock ? *_txSock : *_rxSock;  // deprecated: share rx for tx
    if (!_txSock) {
        static int warned = 0;
        if (++warned <= 3)
            std::fprintf(stderr, "[PORT-SEND-WARN] %s: _txSock is null, fallback to _rxSock\n", _name.c_str());
    }
    try {
        zmq::message_t zmq_msg(msg->hdr.size);
        std::memcpy(zmq_msg.data(), msg, msg->hdr.size);
        sock.send(zmq_msg, zmq::send_flags::none);
        return true;
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[PORT-SEND-ERR] %s: %s (errno=%d)\n",
                     _name.c_str(), e.what(), zmq_errno());
        return false;
    }
}

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
            _lastRxT = std::max(_lastRxT, _pendingT);
            _pending = false;
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
        zmq::message_t zmq_msg;
        auto r = _rxSock->recv(zmq_msg, zmq::recv_flags::dontwait);
        if (!r.has_value()) {
            static int empty_ct = 0;
            if (++empty_ct <= 3)
                std::fprintf(stderr, "[PORT-RECV-EMPTY] %s tick=%lu\n", _name.c_str(), curT);
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

    _lastRxT = std::max(_lastRxT, (uint64_t)tmp.hdr.timestamp);

    if (tmp.hdr.type == static_cast<uint32_t>(MemMessageType::CONTROL_SYNC)) {
        st = ReceiveStatus::kSync;
        static thread_local MemMessage result;
        result = tmp;
        return &result;
    }

    if (tmp.hdr.timestamp > curT) {
        if (tmp.hdr.type == static_cast<uint32_t>(MemMessageType::CONTROL_SYNC)) {
            _pending = true;
            _pendingT = tmp.hdr.timestamp;
            _pendingMsg = tmp;
            st = ReceiveStatus::kPendingFuture;
            return nullptr;
        }
    }

    st = ReceiveStatus::kMessage;
    static thread_local MemMessage result;
    result = tmp;
    static int coh_ct = 0;
    if (tmp.hdr.type == static_cast<uint32_t>(MemMessageType::COH_MSG)) {
        if (++coh_ct <= 5)
            std::fprintf(stderr, "[PORT-RECV-COH] %s tick=%lu msg_ts=%lu sz=%u\n",
                         _name.c_str(), curT, tmp.hdr.timestamp, tmp.hdr.size);
    }
    return &result;
}

bool
Port::emitSync(uint64_t curTick)
{
    if (_lastSyncTs > 0 && curTick - _lastSyncTs < _syncInterval)
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
        _lastSyncTs = curTick;
        _lastRxT = std::max(_lastRxT, curTick);
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
