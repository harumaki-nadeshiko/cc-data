#include "framework/Port.hh"
#include <algorithm>
#include <climits>
#include <cstdio>

namespace framework {

Port::Port(const std::string& name, uint32_t module_id, uint32_t port_id,
           const std::string& endpoint, bool bind,
           zmq::context_t& ctx, uint64_t syncWindow, uint64_t syncInterval)
    : _name(name), _moduleId(module_id), _portId(port_id),
      _ctx(ctx),
      _syncWindow(syncWindow),
      _syncInterval(syncInterval > 0 ? syncInterval : syncWindow),
      _lastSyncTs(0),
      _pending(false), _pendingT(0),
      _lastRxT(UINT64_MAX),    // no inbound bound yet; debug fallback=0
      _sendBufInUse(false)
{
    _socket = std::make_unique<zmq::socket_t>(_ctx, zmq::socket_type::pair);
    try {
        if (bind) _socket->bind(endpoint);
        else      _socket->connect(endpoint);
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[Port %s] %s(%s) failed: %s\n",
                     _name.c_str(), bind ? "bind" : "connect",
                     endpoint.c_str(), e.what());
    }
}

Port::~Port() { if (_socket) _socket->close(); }

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
    if (!msg || !_sendBufInUse) return false;
    _sendBufInUse = false;
    try {
        zmq::message_t zmq_msg(msg->hdr.size);
        std::memcpy(zmq_msg.data(), msg, msg->hdr.size);
        _socket->send(zmq_msg, zmq::send_flags::none);
        return true;
    } catch (const zmq::error_t&) { return false; }
}

MemMessage*
Port::recv(uint64_t curT, ReceiveStatus* status)
{
    ReceiveStatus dummy;
    ReceiveStatus& st = status ? *status : dummy;

    // 1. Check pending future message
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

    // 2. Pull from ZMQ (non-blocking)
    MemMessage tmp;
    try {
        zmq::message_t zmq_msg;
        auto r = _socket->recv(zmq_msg, zmq::recv_flags::dontwait);
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

    // 3. Update inbound time on every received message
    _lastRxT = std::max(_lastRxT, (uint64_t)tmp.hdr.timestamp);

    // 4. Sync messages: return as kSync; caller skips, but _lastRxT updated
    if (tmp.hdr.type == static_cast<uint32_t>(MemMessageType::CONTROL_SYNC)) {
        st = ReceiveStatus::kSync;
        static thread_local MemMessage result;
        result = tmp;
        return &result;
    }

    // 5. Future data message → cache as pending (single slot)
    if (tmp.hdr.timestamp > curT) {
        _pending = true;
        _pendingT = tmp.hdr.timestamp;
        _pendingMsg = tmp;
        st = ReceiveStatus::kPendingFuture;
        return nullptr;
    }

    // 6. Ready data message
    st = ReceiveStatus::kMessage;
    static thread_local MemMessage result;
    result = tmp;
    return &result;
}

bool
Port::emitSync(uint64_t curTick)
{
    // Throttle by _syncInterval (allow first sync unconditionally)
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

        // Drain all ready messages and syncs
        MemMessage* m;
        ReceiveStatus st;
        while ((m = p->recv(tick, &st)) != nullptr) {
            if (st == ReceiveStatus::kEmpty ||
                st == ReceiveStatus::kPendingFuture) break;
            // kSync and kMessage: consume (sync already updated _lastRxT)
        }

        uint64_t b = p->safeTs(tick);
        if (b < safe) safe = b;
    }
    if (safe == UINT64_MAX) return tick + 1;
    return safe;
}

} // namespace framework
