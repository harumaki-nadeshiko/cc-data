#ifndef FRAMEWORK_IFACE_PORT_HH
#define FRAMEWORK_IFACE_PORT_HH

#include <cstdint>
#include <string>

#include "framework/iface/Message.hh"

namespace framework {

struct Port;

enum class ReceiveStatus : std::uint32_t {
    Message = 0,
    Empty = 1,
    PendingFuture = 2,
};

struct PortConfig {
    std::string selfRole;
    std::string peerRole;
    std::string channelName;
    std::uint32_t nodeId = 0;
    std::uint32_t socketId = 0;
    std::uint32_t numNodes = 1;
    std::uint32_t numSockets = 1;
};

struct PortRuntime {
    std::uint64_t syncInterval = 2500;
    std::uint64_t linkLatency = 2500;
};

Port* CreatePort(const PortConfig& config,
                 const PortRuntime& runtime = PortRuntime{});
void TerminatePort(Port* port);
void DestroyPort(Port* port);

Message* AllocateSendMessage(Port* port, std::uint64_t timestamp);
// SendMessage consumes message whether the send succeeds or fails.
bool SendMessage(Port* port, Message* message);

// The returned Message is borrowed and remains valid only until the next call
// to ReceiveMessage on the same Port.
const Message* ReceiveMessage(Port* port, std::uint64_t currentTimestamp,
                              ReceiveStatus* status);

bool EmitSync(Port* port, std::uint64_t currentTimestamp);
std::uint64_t SafeTimestamp(const Port* port,
                            std::uint64_t currentTimestamp);
std::uint64_t SyncInterval(const Port* port);

} // namespace framework

#endif // FRAMEWORK_IFACE_PORT_HH
