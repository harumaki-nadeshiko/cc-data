#ifndef FRAMEWORK_ZMQ_PSEUDOMEMPORT_IMPL_HH
#define FRAMEWORK_ZMQ_PSEUDOMEMPORT_IMPL_HH

#include <cstdint>
#include <string>
#include <memory>
#include <zmq.hpp>

#include "framework/PseudoMemPacket.hh"

namespace pseudo
{

/**
 * ZMQTransport: wraps ZeroMQ DEALER/ROUTER sockets to implement
 * PseudoMemPort semantics over real ZeroMQ messaging.
 *
 * Each port binds or connects to an endpoint (tcp:// or ipc://).
 * send() pushes a PseudoMemPacket serialized to a ZMQ message.
 * recv() blocks on the ZMQ socket for incoming data.
 * poll() checks ZMQ socket for pending messages without blocking.
 */
class ZMQTransport
{
  public:
    ZMQTransport() = default;

    /**
     * Initialize as a server (bind) or client (connect).
     * @param endpoint  ZeroMQ endpoint (e.g., "tcp://127.0.0.1:5555")
     * @param bind      true = bind, false = connect
     */
    bool init(zmq::context_t* ctx, const std::string& endpoint, bool bind);

    bool send(const PseudoMemPacket& pkt);
    bool recv(PseudoMemPacket& pkt);
    bool recv(PseudoMemPacket& pkt, int timeout_ms);
    bool poll() const;

    void shutdown();

  private:
    std::unique_ptr<zmq::socket_t> _socket;
    bool _initialized = false;

    static std::string serialize(const PseudoMemPacket& pkt);
    static bool deserialize(const std::string& data, PseudoMemPacket& pkt);
};

} // namespace pseudo

#endif // FRAMEWORK_ZMQ_PSEUDOMEMPORT_IMPL_HH
