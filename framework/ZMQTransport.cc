#include "framework/ZMQTransport.hh"

#include <cstdio>
#include <cstring>
#include <zmq.hpp>

namespace pseudo
{

bool
ZMQTransport::init(zmq::context_t* ctx, const std::string& endpoint, bool bind)
{
    try {
        _socket = std::make_unique<zmq::socket_t>(*ctx, zmq::socket_type::dealer);
        if (bind)
            _socket->bind(endpoint);
        else
            _socket->connect(endpoint);
        _initialized = true;
        return true;
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[ZMQTransport] init error: %s\n", e.what());
        return false;
    }
}

bool
ZMQTransport::send(const PseudoMemPacket& pkt)
{
    if (!_initialized) return false;
    try {
        std::string data = serialize(pkt);
        zmq::message_t msg(data.size());
        std::memcpy(msg.data(), data.data(), data.size());
        _socket->send(msg, zmq::send_flags::none);
        return true;
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[ZMQTransport] send error: %s\n", e.what());
        return false;
    }
}

bool
ZMQTransport::recv(PseudoMemPacket& pkt)
{
    if (!_initialized) return false;

    try {
        zmq::message_t data;
        auto ret = _socket->recv(data, zmq::recv_flags::none);
        if (!ret) return false;

        std::string raw(static_cast<const char*>(data.data()), data.size());
        return deserialize(raw, pkt);
    } catch (const zmq::error_t& e) {
        return false;
    }
}

bool
ZMQTransport::recv(PseudoMemPacket& pkt, int timeout_ms)
{
    if (!_initialized) return false;

    try {
        zmq::pollitem_t items[1];
        items[0].socket = _socket->handle();
        items[0].fd = 0;
        items[0].events = ZMQ_POLLIN;
        items[0].revents = 0;

        int rc = zmq::poll(items, 1, timeout_ms);
        if (rc <= 0 || !(items[0].revents & ZMQ_POLLIN))
            return false;

        return recv(pkt);
    } catch (const zmq::error_t&) {
        return false;
    }
}

bool
ZMQTransport::poll() const
{
    if (!_initialized) return false;

    zmq::pollitem_t items[1];
    items[0].socket = _socket->handle();
    items[0].fd = 0;
    items[0].events = ZMQ_POLLIN;
    items[0].revents = 0;

    int rc = zmq::poll(items, 1, 0);
    return rc > 0 && (items[0].revents & ZMQ_POLLIN);
}

void
ZMQTransport::shutdown()
{
    if (_socket) {
        _socket->close();
        _initialized = false;
    }
}

std::string
ZMQTransport::serialize(const PseudoMemPacket& pkt)
{
    std::string out;
    out.reserve(16 + pkt.payload_len);
    out.append(reinterpret_cast<const char*>(&pkt.type), 4);
    out.append(reinterpret_cast<const char*>(&pkt.src_id), 4);
    out.append(reinterpret_cast<const char*>(&pkt.dst_id), 4);
    out.append(reinterpret_cast<const char*>(&pkt.payload_len), 4);
    out.append(reinterpret_cast<const char*>(pkt.payload), pkt.payload_len);
    return out;
}

bool
ZMQTransport::deserialize(const std::string& data, PseudoMemPacket& pkt)
{
    if (data.size() < 16) return false;
    std::memcpy(&pkt.type, data.data(), 4);
    std::memcpy(&pkt.src_id, data.data() + 4, 4);
    std::memcpy(&pkt.dst_id, data.data() + 8, 4);
    std::memcpy(&pkt.payload_len, data.data() + 12, 4);
    if (pkt.payload_len > kMaxPayloadSize) return false;
    if (data.size() < 16 + pkt.payload_len) return false;
    std::memcpy(pkt.payload, data.data() + 16, pkt.payload_len);
    return true;
}

} // namespace pseudo
