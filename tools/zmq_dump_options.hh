#ifndef TOOLS_ZMQ_DUMP_OPTIONS_HH
#define TOOLS_ZMQ_DUMP_OPTIONS_HH

#include <cstdint>
#include <ostream>

#include <zmq.h>
#include <zmq.hpp>

namespace tools {

inline const char* zmqSocketTypeName(int type)
{
    switch (type) {
      case ZMQ_PAIR: return "PAIR";
      case ZMQ_PUB: return "PUB";
      case ZMQ_SUB: return "SUB";
      case ZMQ_REQ: return "REQ";
      case ZMQ_REP: return "REP";
      case ZMQ_DEALER: return "DEALER";
      case ZMQ_ROUTER: return "ROUTER";
      case ZMQ_PULL: return "PULL";
      case ZMQ_PUSH: return "PUSH";
      case ZMQ_XPUB: return "XPUB";
      case ZMQ_XSUB: return "XSUB";
      case ZMQ_STREAM: return "STREAM";
      default: return "UNKNOWN";
    }
}

inline void dumpZmqContextOptions(zmq::context_t& context, std::ostream& out)
{
    int major = 0;
    int minor = 0;
    int patch = 0;
    zmq_version(&major, &minor, &patch);

    out << "libzmq=" << major << '.' << minor << '.' << patch << '\n'
        << "ZMQ_IO_THREADS=" << context.get(zmq::ctxopt::io_threads) << '\n'
        << "ZMQ_MAX_SOCKETS=" << context.get(zmq::ctxopt::max_sockets) << '\n';
}

inline void dumpZmqSocketOptions(zmq::socket_t& socket, std::ostream& out)
{
    const int type = socket.get(zmq::sockopt::type);
    const int events = socket.get(zmq::sockopt::events);

    out << "ZMQ_TYPE=" << type << " (" << zmqSocketTypeName(type) << ")\n"
        << "ZMQ_SNDHWM=" << socket.get(zmq::sockopt::sndhwm) << '\n'
        << "ZMQ_RCVHWM=" << socket.get(zmq::sockopt::rcvhwm) << '\n'
        << "ZMQ_SNDTIMEO=" << socket.get(zmq::sockopt::sndtimeo) << " ms\n"
        << "ZMQ_RCVTIMEO=" << socket.get(zmq::sockopt::rcvtimeo) << " ms\n"
        << "ZMQ_LINGER=" << socket.get(zmq::sockopt::linger) << " ms\n"
        << "ZMQ_IMMEDIATE=" << socket.get(zmq::sockopt::immediate) << '\n'
        << "ZMQ_SNDBUF=" << socket.get(zmq::sockopt::sndbuf) << " bytes\n"
        << "ZMQ_RCVBUF=" << socket.get(zmq::sockopt::rcvbuf) << " bytes\n"
        << "ZMQ_RECONNECT_IVL="
        << socket.get(zmq::sockopt::reconnect_ivl) << " ms\n"
        << "ZMQ_RECONNECT_IVL_MAX="
        << socket.get(zmq::sockopt::reconnect_ivl_max) << " ms\n"
        << "ZMQ_MAXMSGSIZE=" << socket.get(zmq::sockopt::maxmsgsize)
        << " bytes\n"
        << "ZMQ_EVENTS=" << events
        << " (POLLIN=" << ((events & ZMQ_POLLIN) != 0)
        << ", POLLOUT=" << ((events & ZMQ_POLLOUT) != 0) << ")\n";
}

} // namespace tools

#endif // TOOLS_ZMQ_DUMP_OPTIONS_HH
