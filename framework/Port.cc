#include "framework/Port.hh"
#include <algorithm>
#include <cstdio>

namespace framework {

Port::Port(const std::string& name, uint32_t module_id, uint32_t port_id,
           const std::string& endpoint, bool bind,
           zmq::context_t& ctx, uint64_t syncWindow)
    : _name(name), _moduleId(module_id), _portId(port_id),
      _ctx(ctx), _syncWindow(syncWindow),
      _lastSyncTick(0), _nextVisibleTick(0), _sendBufInUse(false)
{
    _socket = std::make_unique<zmq::socket_t>(_ctx, zmq::socket_type::pair);
    try {
        if (bind) _socket->bind(endpoint);
        else      _socket->connect(endpoint);
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[Port %s] %s(%s) failed: %s\n",
                     _name.c_str(), bind?"bind":"connect",
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
Port::recv(uint64_t visibleTick)
{
    for (auto it = _deferred.begin(); it != _deferred.end(); ++it) {
        if (it->ts <= visibleTick) {
            static thread_local MemMessage r;
            r = it->msg; _deferred.erase(it); return &r;
        }
    }
    // Direct non-blocking ZMQ receive — no internal poll
    MemMessage tmp;
    try {
        zmq::message_t zmq_msg;
        auto r = _socket->recv(zmq_msg, zmq::recv_flags::dontwait);
        if (!r.has_value()) return nullptr;
        uint32_t sz = zmq_msg.size();
        if (sz < kMemMessageHeaderSize || sz > sizeof(MemMessage)) return nullptr;
        std::memcpy(&tmp, zmq_msg.data(), sz);
    } catch (const zmq::error_t&) { return nullptr; }

    if (tmp.hdr.timestamp <= visibleTick) {
        static thread_local MemMessage result; result = tmp; return &result;
    }
    _deferred.push_back({tmp.hdr.timestamp, tmp});
    std::sort(_deferred.begin(), _deferred.end(),
              [](auto& a, auto& b) { return a.ts < b.ts; });
    return nullptr;
}

bool
Port::emitSync(uint64_t curTick)
{
    if (curTick - _lastSyncTick < _syncWindow) return true;
    MemMessage* buf = sendAllocateBuffer(curTick);
    if (!buf) return false;
    buf->hdr.type = static_cast<uint32_t>(MemMessageType::CONTROL_SYNC);
    buf->hdr.size = sizeof(MemMessageHeader);
    bool ok = send(buf);
    _sendBufInUse = false;
    if (ok) { _lastSyncTick = curTick; return true; }
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
        while (p->recv(tick)) {}
        uint64_t b = p->nextVisibleTick();
        if (b < safe) safe = b;
        p->advanceVisibleTick(tick);
    }
    return (safe == UINT64_MAX) ? tick + 1 : safe;
}

} // namespace framework
