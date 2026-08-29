#include "framework/iface/Port.hh"

#include "framework/iface/Log.hh"

#include <algorithm>
#include <cctype>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <memory>
#include <new>
#include <string>
#include <utility>
#include <zmq.hpp>

namespace framework {
namespace {

constexpr std::size_t kMaxPayloadSize = 1024;
constexpr std::uint32_t kWireHeaderSize = 40;

struct WireHeader {
    std::uint64_t timestamp = 0;
    std::uint32_t size = kWireHeaderSize;
    std::uint32_t type = static_cast<std::uint32_t>(MessageType::Payload);
    std::uint32_t sourceId = 0;
    std::uint32_t reserved0 = 0;
    std::uint32_t targetId = 0;
    std::uint32_t reserved1 = 0;
    std::uint64_t requestId = 0;
};

static_assert(sizeof(WireHeader) == kWireHeaderSize,
              "local wire header must remain compatible");

std::string IpcBase()
{
    const char* directory = std::getenv("UBCC_IPC_DIR");
    return std::string(directory && *directory ? directory
                                                : "/workspace/gem5/shared_ipc") +
           "/ipc";
}

std::string Lower(std::string value)
{
    std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
        return static_cast<char>(std::tolower(c));
    });
    return value;
}

std::string CanonicalRole(const std::string& role)
{
    std::string result = Lower(role);
    if (result == "nsim" || result == "network" || result == "network_sim")
        return "networksim";
    return result;
}

bool ResolveEndpoints(const PortConfig& config, std::uint32_t gid,
                      std::string& local, std::string& peer)
{
    const std::string self = CanonicalRole(config.selfRole);
    const std::string other = CanonicalRole(config.peerRole);
    const std::string channel = Lower(config.channelName);
    const std::string suffix = std::to_string(gid);

    if (channel == "coherence" &&
        ((self == "gem5" && other == "ubio") ||
         (self == "ubio" && other == "gem5"))) {
        const std::string base = IpcBase();
        auto endpoint = [&](const std::string& from, const std::string& to) {
            return "ipc://" + base + "_" + from + "_" + suffix + "_to_" + to +
                   "_" + suffix;
        };
        local = endpoint(other, self);
        peer = endpoint(self, other);
        return true;
    }

    if (channel == "network" &&
        ((self == "ubio" && other == "networksim") ||
         (self == "networksim" && other == "ubio"))) {
        const std::string base = IpcBase();
        const std::string module = "m" + suffix;
        auto endpoint = [&](const std::string& from, const std::string& to) {
            const std::string fromName = from == "networksim" ? from + "_" + module
                                                               : from + "_" + suffix;
            const std::string toName = to == "networksim" ? to + "_" + module
                                                           : to + "_" + suffix;
            return "ipc://" + base + "_" + fromName + "_to_" + toName;
        };
        local = endpoint(other, self);
        peer = endpoint(self, other);
        return true;
    }

    // Compatibility with the legacy barrier's bind-only Port configuration.
    if (channel == "barrier" && self == "barrier") {
        local = "ipc:///tmp/barrier_m" + suffix + "_p1";
        peer = local;
        return true;
    }
    return false;
}

std::uint64_t ReadRuntimeEnv(const char* name, std::uint64_t fallback)
{
    const char* value = std::getenv(name);
    if (!value || !*value)
        return fallback;
    char* end = nullptr;
    const unsigned long long parsed = std::strtoull(value, &end, 10);
    return end != value && *end == '\0' ? static_cast<std::uint64_t>(parsed)
                                         : fallback;
}

} // namespace

struct Message {
    WireHeader header;
    unsigned char payload[kMaxPayloadSize]{};
    std::size_t capacity = kMaxPayloadSize;
    bool sourceIdSet = false;
    bool targetIdSet = false;
};

struct Port {
    std::string name;
    std::uint32_t gid = 0;
    std::uint64_t syncInterval = 2500;
    std::uint64_t linkLatency = 2500;
    std::uint64_t lastSyncTimestamp = 0;
    bool hasEmittedSync = false;
    std::uint32_t stalledSyncPolls = 0;
    std::uint64_t lastReceiveTimestamp = 0;
    bool open = false;
    bool pending = false;
    std::uint64_t pendingTimestamp = 0;
    Message pendingMessage;
    Message receiveMessage;
    std::unique_ptr<zmq::context_t> context;
    std::unique_ptr<zmq::socket_t> transmitSocket;
    std::unique_ptr<zmq::socket_t> receiveSocket;
};

namespace {

std::uint64_t ReceiveTimestampInternal(const Port* port)
{
    return port->pending ? port->pendingTimestamp : port->lastReceiveTimestamp;
}

void ClosePort(Port* port)
{
    if (!port)
        return;
    port->open = false;
    port->receiveSocket.reset();
    port->transmitSocket.reset();
    port->context.reset();
}

bool SendWire(Port* port, const Message& message, zmq::send_flags flags)
{
    if (!port || !port->open)
        return false;
    const std::size_t wireSize = message.header.size;
    if (wireSize < kWireHeaderSize ||
        wireSize > kWireHeaderSize + kMaxPayloadSize)
        return false;
    try {
        zmq::message_t wire(wireSize);
        std::memcpy(wire.data(), &message.header, kWireHeaderSize);
        if (wireSize > kWireHeaderSize) {
            std::memcpy(static_cast<unsigned char*>(wire.data()) + kWireHeaderSize,
                        message.payload, wireSize - kWireHeaderSize);
        }
        zmq::socket_t& socket = port->transmitSocket ? *port->transmitSocket
                                                     : *port->receiveSocket;
        return socket.send(wire, flags).has_value();
    } catch (const zmq::error_t& error) {
        LogError("framework", "send on {} failed: {}", port->name, error.what());
        return false;
    }
}

void CheckMessage(const Message* message)
{
    LogAssertIf(message != nullptr, "framework", "Message must not be null");
}

void CheckMutableMessage(Message* message)
{
    LogAssertIf(message != nullptr, "framework", "Message must not be null");
}

} // namespace

Port* CreatePort(const PortConfig& config, const PortRuntime& runtime)
{
    if (config.numNodes == 0 || config.numSockets == 0 ||
        config.nodeId >= config.numNodes || config.socketId >= config.numSockets) {
        LogError("framework", "invalid topology node={}/{} socket={}/{}",
                 config.nodeId, config.numNodes, config.socketId,
                 config.numSockets);
        return nullptr;
    }
    const std::uint64_t wideGid =
        static_cast<std::uint64_t>(config.nodeId) * config.numSockets +
        config.socketId;
    if (wideGid > std::numeric_limits<std::uint32_t>::max()) {
        LogError("framework", "port gid is out of range: {}", wideGid);
        return nullptr;
    }

    std::string localEndpoint;
    std::string peerEndpoint;
    if (!ResolveEndpoints(config, static_cast<std::uint32_t>(wideGid),
                          localEndpoint, peerEndpoint)) {
        LogError("framework", "unsupported port roles {}/{} channel {}",
                 config.selfRole, config.peerRole, config.channelName);
        return nullptr;
    }

    std::unique_ptr<Port> port(new (std::nothrow) Port);
    if (!port) {
        LogError("framework", "failed to allocate port");
        return nullptr;
    }
    port->name = config.selfRole + ":" + config.channelName;
    port->gid = static_cast<std::uint32_t>(wideGid);
    port->linkLatency = ReadRuntimeEnv("EP_LINK_LATENCY_PS", runtime.linkLatency);
    port->syncInterval =
        ReadRuntimeEnv("EP_SYNC_INTERVAL_PS", runtime.syncInterval);
    if (port->syncInterval < port->linkLatency) {
        LogWarn("framework", "{} syncInterval({}) < linkLatency({}); clamping",
                port->name, port->syncInterval, port->linkLatency);
        port->syncInterval = port->linkLatency;
    }

    try {
        port->context = std::make_unique<zmq::context_t>(1);
        port->transmitSocket = std::make_unique<zmq::socket_t>(
            *port->context, zmq::socket_type::pair);
        port->receiveSocket = std::make_unique<zmq::socket_t>(
            *port->context, zmq::socket_type::pair);
        port->transmitSocket->set(zmq::sockopt::sndtimeo, -1);
        int highWaterMark = 8192;
        if (const char* value = std::getenv("EP_PORT_HWM")) {
            const long requested = std::strtol(value, nullptr, 10);
            if (requested > 0 && requested <= 1048576)
                highWaterMark = static_cast<int>(requested);
        }
        for (zmq::socket_t* socket : {port->transmitSocket.get(),
                                     port->receiveSocket.get()}) {
            socket->set(zmq::sockopt::linger, 0);
            socket->set(zmq::sockopt::sndhwm, highWaterMark);
            socket->set(zmq::sockopt::rcvhwm, highWaterMark);
        }
        port->receiveSocket->bind(localEndpoint);
        if (peerEndpoint == localEndpoint) {
            port->transmitSocket.reset();
        } else {
            port->transmitSocket->connect(peerEndpoint);
        }
        port->open = true;
    } catch (const std::exception& error) {
        LogError("framework", "create port {} rx={} tx={} failed: {}", port->name,
                 localEndpoint, peerEndpoint, error.what());
        ClosePort(port.get());
        return nullptr;
    }
    LogDebug("framework", "port {} rx={} tx={}", port->name, localEndpoint,
             peerEndpoint);
    return port.release();
}

void TerminatePort(Port* port)
{
    if (!port)
        return;
    if (port->open) {
        Message terminate;
        terminate.header.type = static_cast<std::uint32_t>(MessageType::Terminate);
        (void)SendWire(port, terminate, zmq::send_flags::none);
    }
    ClosePort(port);
}

void DestroyPort(Port* port)
{
    if (!port)
        return;
    ClosePort(port);
    delete port;
}

Message* AllocateSendMessage(Port* port, std::uint64_t timestamp)
{
    LogAssertIf(port != nullptr, "framework", "Port must not be null");
    if (!port->open)
        return nullptr;
    Message* message = new (std::nothrow) Message;
    if (!message)
        return nullptr;
    LogAssertIf(timestamp <=
                    std::numeric_limits<std::uint64_t>::max() - port->linkLatency,
                "framework", "message timestamp {} plus latency {} overflows",
                timestamp, port->linkLatency);
    message->header.timestamp = timestamp + port->linkLatency;
    return message;
}

bool SendMessage(Port* port, Message* message)
{
    LogAssertIf(port != nullptr, "framework", "Port must not be null");
    LogAssertIf(message != nullptr, "framework", "Message must not be null");
    LogAssertIf(message->header.type ==
                    static_cast<std::uint32_t>(MessageType::Payload),
                "framework", "SendMessage only accepts Payload messages");
    LogAssertIf(message->sourceIdSet, "framework",
                "Payload sourceId must be set by the application");
    LogAssertIf(message->targetIdSet, "framework",
                "Payload targetId must be set by the application");
    const bool result = SendWire(port, *message, zmq::send_flags::none);
    delete message;
    return result;
}

const Message* ReceiveMessage(Port* port, std::uint64_t currentTimestamp,
                              ReceiveStatus* status)
{
    LogAssertIf(port != nullptr, "framework", "Port must not be null");
    LogAssertIf(status != nullptr, "framework", "ReceiveStatus must not be null");
    if (!port->open) {
        *status = ReceiveStatus::Empty;
        return nullptr;
    }
    if (port->pending) {
        if (port->pendingTimestamp > currentTimestamp) {
            *status = ReceiveStatus::PendingFuture;
            return nullptr;
        }
        port->lastReceiveTimestamp = port->pendingTimestamp;
        port->pending = false;
        port->receiveMessage = port->pendingMessage;
        *status = ReceiveStatus::Message;
        return &port->receiveMessage;
    }

    try {
        zmq::message_t wire;
        if (!port->receiveSocket->recv(wire, zmq::recv_flags::dontwait)) {
            *status = ReceiveStatus::Empty;
            return nullptr;
        }
        if (wire.size() < kWireHeaderSize ||
            wire.size() > kWireHeaderSize + kMaxPayloadSize) {
            LogWarn("framework", "discarding invalid wire message size {}",
                    wire.size());
            *status = ReceiveStatus::Empty;
            return nullptr;
        }
        Message incoming;
        std::memcpy(&incoming.header, wire.data(), kWireHeaderSize);
        if (incoming.header.type >
            static_cast<std::uint32_t>(MessageType::Payload)) {
            LogWarn("framework", "discarding invalid wire message type {}",
                    incoming.header.type);
            *status = ReceiveStatus::Empty;
            return nullptr;
        }
        if (incoming.header.size != wire.size()) {
            LogWarn("framework", "discarding wire size mismatch {} != {}",
                    incoming.header.size, wire.size());
            *status = ReceiveStatus::Empty;
            return nullptr;
        }
        if (wire.size() > kWireHeaderSize) {
            std::memcpy(incoming.payload,
                        static_cast<const unsigned char*>(wire.data()) +
                            kWireHeaderSize,
                        wire.size() - kWireHeaderSize);
        }
        if (incoming.header.type ==
                static_cast<std::uint32_t>(MessageType::Payload)) {
            incoming.sourceIdSet = true;
            incoming.targetIdSet = true;
        }
        port->lastReceiveTimestamp = incoming.header.timestamp;
        port->stalledSyncPolls = 0;
        if (incoming.header.timestamp > currentTimestamp) {
            port->pending = true;
            port->pendingTimestamp = incoming.header.timestamp;
            port->pendingMessage = incoming;
            *status = ReceiveStatus::PendingFuture;
            return nullptr;
        }
        port->receiveMessage = incoming;
        *status = ReceiveStatus::Message;
        return &port->receiveMessage;
    } catch (const zmq::error_t& error) {
        LogWarn("framework", "receive on {} failed: {}", port->name, error.what());
        *status = ReceiveStatus::Empty;
        return nullptr;
    }
}

bool EmitSync(Port* port, std::uint64_t currentTimestamp)
{
    LogAssertIf(port != nullptr, "framework", "Port must not be null");
    if (!port->open)
        return false;
    LogAssertIf(currentTimestamp <=
                    std::numeric_limits<std::uint64_t>::max() - port->linkLatency,
                "framework", "sync timestamp {} plus latency {} overflows",
                currentTimestamp, port->linkLatency);
    if (port->hasEmittedSync) {
        LogAssertIf(currentTimestamp >= port->lastSyncTimestamp, "framework",
                    "sync timestamp {} precedes last sync timestamp {}",
                    currentTimestamp, port->lastSyncTimestamp);
        if (currentTimestamp - port->lastSyncTimestamp < port->syncInterval) {
            if (port->lastReceiveTimestamp > currentTimestamp)
                return true;
            // A tick-zero heartbeat can be locally queued before the peer is
            // fully connected. Periodically retransmit while the peer has not
            // advertised a future timestamp, otherwise both conservative-PDES
            // endpoints can remain at the same tick forever.
            if (++port->stalledSyncPolls % 1024 != 0)
                return true;
        }
    }
    Message sync;
    sync.header.timestamp = currentTimestamp + port->linkLatency;
    sync.header.type = static_cast<std::uint32_t>(MessageType::ControlSync);
    // Heartbeats are retryable and must not block a simulator thread while
    // the peer is still binding or reconnecting.
    if (!SendWire(port, sync, zmq::send_flags::dontwait))
        return false;
    port->lastSyncTimestamp = currentTimestamp;
    port->hasEmittedSync = true;
    return true;
}

std::uint64_t SafeTimestamp(const Port* port, std::uint64_t currentTimestamp)
{
    LogAssertIf(port != nullptr, "framework", "Port must not be null");
    const std::uint64_t received = ReceiveTimestampInternal(port);
    const std::uint64_t base = port->hasEmittedSync
                                   ? port->lastSyncTimestamp
                                   : currentTimestamp;
    const std::uint64_t bound = base >
            std::numeric_limits<std::uint64_t>::max() - port->syncInterval
        ? std::numeric_limits<std::uint64_t>::max()
        : base + port->syncInterval;
    return std::min(received, bound);
}

std::uint64_t SyncInterval(const Port* port)
{
    LogAssertIf(port != nullptr, "framework", "Port must not be null");
    return port->syncInterval;
}

std::uint64_t GetMessageTimestamp(const Message* message)
{
    CheckMessage(message);
    return message->header.timestamp;
}

MessageType GetMessageType(const Message* message)
{
    CheckMessage(message);
    LogAssertIf(message->header.type <= static_cast<std::uint32_t>(MessageType::Payload),
                "framework", "invalid message type {}", message->header.type);
    return static_cast<MessageType>(message->header.type);
}

std::uint32_t GetMessageSourceId(const Message* message)
{
    CheckMessage(message);
    return message->header.sourceId;
}

void SetMessageSourceId(Message* message, std::uint32_t sourceId)
{
    CheckMutableMessage(message);
    message->header.sourceId = sourceId;
    message->sourceIdSet = true;
}

std::uint32_t GetMessageTargetId(const Message* message)
{
    CheckMessage(message);
    return message->header.targetId;
}

void SetMessageTargetId(Message* message, std::uint32_t targetId)
{
    CheckMutableMessage(message);
    message->header.targetId = targetId;
    message->targetIdSet = true;
}

std::uint64_t GetMessageRequestId(const Message* message)
{
    CheckMessage(message);
    return message->header.requestId;
}

void SetMessageRequestId(Message* message, std::uint64_t requestId)
{
    CheckMutableMessage(message);
    message->header.requestId = requestId;
}

void SetMessagePayload(Message* message, const void* data, std::size_t size)
{
    CheckMutableMessage(message);
    LogAssertIf(size <= message->capacity, "framework",
                "payload size {} exceeds message capacity {}", size,
                message->capacity);
    LogAssertIf(data != nullptr || size == 0, "framework",
                "payload data must not be null when size is {}", size);
    if (size)
        std::memcpy(message->payload, data, size);
    message->header.size = kWireHeaderSize + static_cast<std::uint32_t>(size);
}

const void* GetMessagePayloadData(const Message* message)
{
    CheckMessage(message);
    return message->payload;
}

std::size_t GetMessagePayloadSize(const Message* message)
{
    CheckMessage(message);
    LogAssertIf(message->header.size >= kWireHeaderSize &&
                    message->header.size <= kWireHeaderSize + message->capacity,
                "framework", "invalid message size {}", message->header.size);
    return message->header.size - kWireHeaderSize;
}

std::size_t GetMaxPayloadSize()
{
    return kMaxPayloadSize;
}

void CopyMessage(Message* destination, const Message* source)
{
    CheckMutableMessage(destination);
    CheckMessage(source);
    const std::size_t payloadSize = GetMessagePayloadSize(source);
    LogAssertIf(payloadSize <= destination->capacity, "framework",
                "source payload {} exceeds destination capacity {}", payloadSize,
                destination->capacity);
    const std::uint64_t timestamp = destination->header.timestamp;
    destination->header.type = source->header.type;
    destination->header.sourceId = source->header.sourceId;
    destination->header.targetId = source->header.targetId;
    destination->header.requestId = source->header.requestId;
    destination->sourceIdSet = source->sourceIdSet;
    destination->targetIdSet = source->targetIdSet;
    destination->header.size = kWireHeaderSize + payloadSize;
    if (payloadSize)
        std::memcpy(destination->payload, source->payload, payloadSize);
    destination->header.timestamp = timestamp;
}

void ReleaseMessage(Message* message)
{
    CheckMutableMessage(message);
    delete message;
}

} // namespace framework
