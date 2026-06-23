#ifndef FRAMEWORK_ZMQCHANNEL_HH
#define FRAMEWORK_ZMQCHANNEL_HH

#include <memory>
#include <string>
#include <zmq.hpp>

#include "framework/MemMessage.hh"

namespace framework {

/**
 * ZMQChannel wraps one ZMQ PAIR socket.
 * A Port owns two ZMQChannels (one for send, one for recv by convention),
 * but each ZMQChannel is a self-contained PAIR socket.
 * One side binds, the other connects.
 */
class ZMQChannel
{
  public:
    explicit ZMQChannel(zmq::context_t& ctx);
    ~ZMQChannel();

    ZMQChannel(const ZMQChannel&) = delete;
    ZMQChannel& operator=(const ZMQChannel&) = delete;

    /** Bind to an IPC endpoint. Returns true on success. */
    bool bind(const std::string& endpoint);

    /** Connect to an IPC endpoint. Returns true on success. */
    bool connect(const std::string& endpoint);

    /** Send a MemMessage. Non-blocking at API level; ZMQ may block at HWM. */
    bool send(const MemMessage& msg);

    /** Non-blocking receive. Returns nullptr if no message available. */
    MemMessage* recv(MemMessage& out);

    /** Close the socket. */
    void close();

    bool isOpen() const { return _open; }

  private:
    zmq::context_t& _ctx;
    std::unique_ptr<zmq::socket_t> _socket;
    bool _open = false;
    bool _isBind = false;
};

} // namespace framework

#endif // FRAMEWORK_ZMQCHANNEL_HH
