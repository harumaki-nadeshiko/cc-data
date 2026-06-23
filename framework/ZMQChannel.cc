#include "framework/ZMQChannel.hh"

#include <cstdio>

namespace framework {

ZMQChannel::ZMQChannel(zmq::context_t& ctx)
    : _ctx(ctx)
{
}

ZMQChannel::~ZMQChannel()
{
    close();
}

bool
ZMQChannel::bind(const std::string& endpoint)
{
    try {
        _socket = std::make_unique<zmq::socket_t>(_ctx, zmq::socket_type::pair);
        _socket->bind(endpoint);
        _open = true;
        _isBind = true;
        return true;
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[ZMQChannel] bind(%s) failed: %s\n",
                     endpoint.c_str(), e.what());
        return false;
    }
}

bool
ZMQChannel::connect(const std::string& endpoint)
{
    try {
        _socket = std::make_unique<zmq::socket_t>(_ctx, zmq::socket_type::pair);
        _socket->connect(endpoint);
        _open = true;
        _isBind = false;
        return true;
    } catch (const zmq::error_t& e) {
        std::fprintf(stderr, "[ZMQChannel] connect(%s) failed: %s\n",
                     endpoint.c_str(), e.what());
        return false;
    }
}

bool
ZMQChannel::send(const MemMessage& msg)
{
    if (!_open || !_socket) return false;

    try {
        zmq::message_t zmq_msg(msg.hdr.size);
        std::memcpy(zmq_msg.data(), &msg, msg.hdr.size);
        auto result = _socket->send(zmq_msg, zmq::send_flags::dontwait);
        return result.has_value();
    } catch (const zmq::error_t& e) {
        return false;
    }
}

MemMessage*
ZMQChannel::recv(MemMessage& out)
{
    if (!_open || !_socket) return nullptr;

    try {
        zmq::message_t zmq_msg;
        auto result = _socket->recv(zmq_msg, zmq::recv_flags::dontwait);
        if (!result.has_value()) return nullptr;

        uint32_t size = zmq_msg.size();
        if (size < kMemMessageHeaderSize || size > sizeof(MemMessage))
            return nullptr;

        std::memcpy(&out, zmq_msg.data(), size);
        return &out;
    } catch (const zmq::error_t& e) {
        return nullptr;
    }
}

void
ZMQChannel::close()
{
    if (_socket) {
        _socket->close();
        _socket.reset();
    }
    _open = false;
}

} // namespace framework
