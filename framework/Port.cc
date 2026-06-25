#include "framework/Port.hh"
#include <algorithm>
#include <climits>
#include <cstdio>

namespace framework {

Port::Port(const std::string& name, uint32_t module_id, uint32_t port_id,
           const std::string& endpoint, bool bindTx,
           zmq::context_t& ctx, uint64_t syncWindow, uint64_t syncInterval)
    : _name(name), _moduleId(module_id), _portId(port_id),
      _ctx(ctx),
      _syncWindow(syncWindow),
      _syncInterval(syncInterval > 0 ? syncInterval : syncWindow),
      _lastSyncTs(0),
      _pending(false), _pendingT(0),
      _lastRxT(UINT64_MAX),
      _sendBufInUse(false)
{
    std::string txEp = endpoint + "_tx";
    std::string rxEp = endpoint + "_rx";

    _txSock = std::make_unique<zmq::socket_t>(_ctx, zmq::socket_type::pair);
    _rxSock = std::make_unique<zmq::socket_t>(_ctx, zmq::socket_type::pair);

    int sndtimeo = 10;
    _txSock->set(zmq::sockopt::sndtimeo, sndtimeo);

    try {
        if (bindTx) {
            _txSock->bind(txEp);
            _rxSock->connect(rxEp);
        } else {
            _txSock->connect(txEp);
            _rxSock->bind(rxEp);
        }
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[Port %s] init(%s,%s) failed: %s\n",
                     _name.c_str(), txEp.c_str(), rxEp.c_str(), e.what());
    }
}

Port::~Port() {
    if (_txSock) _txSock->close();
    if (_rxSock) _rxSock->close();
}

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
        _txSock->send(zmq_msg, zmq::send_flags::none);
        return true;
    } catch (const zmq::error_t&) { return false; }
}

MemMessage*
Port::recv(uint64_t curT, ReceiveStatus* status)
{
    ReceiveStatus dummy;
    ReceiveStatus& st = status ? *status : dummy;

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
