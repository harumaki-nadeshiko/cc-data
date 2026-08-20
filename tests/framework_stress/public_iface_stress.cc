#include "framework/iface/Port.hh"

#include <algorithm>
#include <chrono>
#include <cstdlib>
#include <cstdint>
#include <cstring>
#include <iostream>
#include <limits>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

namespace {

using framework::Message;
using framework::MessageType;
using framework::Port;
using framework::PortConfig;
using framework::PortRuntime;
using framework::ReceiveStatus;

constexpr std::uint64_t kMagic = 0x5354524553537631ULL; // "STRESSv1"
constexpr std::uint32_t kGem5Id = 0x47454d35U;
constexpr std::uint32_t kUbioId = 0x5542494fU;
constexpr std::uint64_t kReqA = 0x100000000ULL;
constexpr std::uint64_t kReqAckA = 0x200000000ULL;
constexpr std::uint64_t kReqB = 0x300000000ULL;
constexpr std::uint64_t kReqAckB = 0x400000000ULL;
constexpr std::uint64_t kReqDone = 0x500000000ULL;

struct PayloadHeader {
    std::uint64_t magic;
    std::uint64_t sequence;
    std::uint64_t total;
    std::uint64_t bodyChecksum;
    std::uint32_t phase;
    std::uint32_t reserved;
};

struct Options {
    std::string role;
    std::uint64_t messages = 100000;
    std::size_t payloadBytes = 256;
    std::uint64_t startTimestamp = 1000;
    std::uint64_t timestampStep = 3;
    std::uint64_t linkLatency = 100;
    std::uint64_t syncInterval = 1000000;
    std::uint64_t timeoutMs = 120000;
};

struct Stats {
    std::uint64_t payloadReceived = 0;
    std::uint64_t pendingObserved = 0;
    std::uint64_t syncReceived = 0;
};

std::uint64_t ParseU64(const char* text, const char* option)
{
    try {
        std::size_t consumed = 0;
        const std::string value(text);
        const auto result = std::stoull(value, &consumed, 0);
        if (consumed != value.size())
            throw std::invalid_argument("trailing characters");
        return result;
    } catch (const std::exception& error) {
        throw std::runtime_error(std::string("invalid ") + option + ": " +
                                 error.what());
    }
}

Options ParseOptions(int argc, char** argv)
{
    Options options;
    for (int i = 1; i < argc; ++i) {
        const std::string arg(argv[i]);
        auto value = [&](const char* name) -> const char* {
            if (i + 1 >= argc)
                throw std::runtime_error(std::string("missing value for ") + name);
            return argv[++i];
        };
        if (arg == "--role")
            options.role = value("--role");
        else if (arg == "--messages")
            options.messages = ParseU64(value("--messages"), "--messages");
        else if (arg == "--payload-bytes")
            options.payloadBytes = static_cast<std::size_t>(
                ParseU64(value("--payload-bytes"), "--payload-bytes"));
        else if (arg == "--start-timestamp")
            options.startTimestamp =
                ParseU64(value("--start-timestamp"), "--start-timestamp");
        else if (arg == "--timestamp-step")
            options.timestampStep =
                ParseU64(value("--timestamp-step"), "--timestamp-step");
        else if (arg == "--link-latency")
            options.linkLatency =
                ParseU64(value("--link-latency"), "--link-latency");
        else if (arg == "--sync-interval")
            options.syncInterval =
                ParseU64(value("--sync-interval"), "--sync-interval");
        else if (arg == "--timeout-ms")
            options.timeoutMs = ParseU64(value("--timeout-ms"), "--timeout-ms");
        else if (arg == "--help") {
            std::cout << "usage: public_iface_stress --role gem5|ubio "
                         "[--messages N] [--payload-bytes N] "
                         "[--start-timestamp N] [--timestamp-step N] "
                         "[--link-latency N] [--sync-interval N] "
                         "[--timeout-ms N]\n";
            std::exit(0);
        } else {
            throw std::runtime_error("unknown option: " + arg);
        }
    }
    if (options.role != "gem5" && options.role != "ubio")
        throw std::runtime_error("--role must be gem5 or ubio");
    if (options.messages == 0 || options.timestampStep == 0 ||
        options.timeoutMs == 0)
        throw std::runtime_error("messages, timestamp-step, and timeout must be nonzero");
    if (options.payloadBytes < sizeof(PayloadHeader) ||
        options.payloadBytes > framework::GetMaxPayloadSize())
        throw std::runtime_error("payload-bytes is outside the public interface limits");
    if (options.syncInterval < options.linkLatency)
        throw std::runtime_error("sync-interval must be at least link-latency");
    return options;
}

std::uint64_t AddChecked(std::uint64_t a, std::uint64_t b, const char* what)
{
    if (a > std::numeric_limits<std::uint64_t>::max() - b)
        throw std::runtime_error(std::string("timestamp overflow: ") + what);
    return a + b;
}

std::uint64_t MulAddChecked(std::uint64_t base, std::uint64_t count,
                            std::uint64_t step, const char* what)
{
    if (count && step > (std::numeric_limits<std::uint64_t>::max() - base) / count)
        throw std::runtime_error(std::string("timestamp overflow: ") + what);
    return base + count * step;
}

std::uint64_t Hash(const unsigned char* data, std::size_t size)
{
    std::uint64_t hash = 1469598103934665603ULL;
    for (std::size_t i = 0; i < size; ++i) {
        hash ^= data[i];
        hash *= 1099511628211ULL;
    }
    return hash;
}

unsigned char Pattern(std::uint32_t phase, std::uint64_t sequence, std::size_t i)
{
    std::uint64_t x = sequence * 0x9e3779b97f4a7c15ULL;
    x ^= static_cast<std::uint64_t>(phase) << 48;
    x ^= static_cast<std::uint64_t>(i) * 0xbf58476d1ce4e5b9ULL;
    x ^= x >> 30;
    x *= 0xbf58476d1ce4e5b9ULL;
    x ^= x >> 27;
    return static_cast<unsigned char>(x ^ (x >> 32));
}

std::vector<unsigned char> MakePayload(std::size_t size, std::uint32_t phase,
                                       std::uint64_t sequence, std::uint64_t total)
{
    std::vector<unsigned char> bytes(size);
    for (std::size_t i = sizeof(PayloadHeader); i < size; ++i)
        bytes[i] = Pattern(phase, sequence, i);
    PayloadHeader header{kMagic, sequence, total,
                         Hash(bytes.data() + sizeof(PayloadHeader),
                              size - sizeof(PayloadHeader)),
                         phase, 0};
    std::memcpy(bytes.data(), &header, sizeof(header));
    return bytes;
}

class Runner {
  public:
    explicit Runner(const Options& options) : o(options)
    {
        PortConfig config;
        config.selfRole = o.role;
        config.peerRole = o.role == "gem5" ? "ubio" : "gem5";
        config.channelName = "coherence";
        config.nodeId = 0;
        config.socketId = 0;
        config.numNodes = 1;
        config.numSockets = 1;
        PortRuntime runtime;
        runtime.linkLatency = o.linkLatency;
        runtime.syncInterval = o.syncInterval;
        port = framework::CreatePort(config, runtime);
        if (!port)
            throw std::runtime_error("CreatePort failed for coherence gid 0");
        actualSyncInterval = framework::SyncInterval(port);
    }

    ~Runner()
    {
        framework::DestroyPort(port);
    }

    void Run()
    {
        const std::uint64_t aStart = o.startTimestamp;
        const std::uint64_t aAck = MulAddChecked(aStart, o.messages, o.timestampStep,
                                                  "phase A ack");
        const std::uint64_t bStart = AddChecked(aAck, o.timestampStep, "phase B start");
        const std::uint64_t bAck = MulAddChecked(bStart, o.messages, o.timestampStep,
                                                  "phase B ack");
        const std::uint64_t done = AddChecked(bAck, o.timestampStep, "completion");
        (void)AddChecked(done, o.linkLatency, "last wire timestamp");

        if (o.role == "gem5") {
            EmitOneSync(aStart);
            SendBurst(1, kReqA, kGem5Id, kUbioId, aStart);
            ReceivePayload(3, 0, 1, kReqAckA, kUbioId, kGem5Id, aAck);
            ReceiveSync(bStart);
            ReceiveBurst(2, kReqB, kUbioId, kGem5Id, bStart);
            SendPayload(4, 0, 1, kReqAckB, kGem5Id, kUbioId, bAck);
            ReceivePayload(5, 0, 1, kReqDone, kUbioId, kGem5Id, done);
        } else {
            ReceiveSync(aStart);
            ReceiveBurst(1, kReqA, kGem5Id, kUbioId, aStart);
            SendPayload(3, 0, 1, kReqAckA, kUbioId, kGem5Id, aAck);
            EmitOneSync(bStart);
            SendBurst(2, kReqB, kUbioId, kGem5Id, bStart);
            ReceivePayload(4, 0, 1, kReqAckB, kGem5Id, kUbioId, bAck);
            SendPayload(5, 0, 1, kReqDone, kUbioId, kGem5Id, done);
        }
    }

    const Stats& GetStats() const { return stats; }

  private:
    void EmitOneSync(std::uint64_t timestamp)
    {
        const auto deadline = Deadline();
        while (!framework::EmitSync(port, timestamp)) {
            CheckDeadline(deadline, "EmitSync");
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        if (!framework::EmitSync(port, timestamp))
            throw std::runtime_error("throttled EmitSync unexpectedly failed");
        hasEmittedSync = true;
        lastEmittedSync = timestamp;
    }

    void SendBurst(std::uint32_t phase, std::uint64_t requestBase,
                   std::uint32_t source, std::uint32_t target,
                   std::uint64_t timestampBase)
    {
        for (std::uint64_t sequence = 0; sequence < o.messages; ++sequence) {
            SendPayload(phase, sequence, o.messages, requestBase + sequence,
                        source, target,
                        MulAddChecked(timestampBase, sequence, o.timestampStep,
                                      "burst send"));
        }
    }

    void SendPayload(std::uint32_t phase, std::uint64_t sequence,
                     std::uint64_t total, std::uint64_t requestId,
                     std::uint32_t source, std::uint32_t target,
                     std::uint64_t timestamp)
    {
        Message* message = framework::AllocateSendMessage(port, timestamp);
        if (!message)
            throw std::runtime_error("AllocateSendMessage failed");
        const auto payload = MakePayload(o.payloadBytes, phase, sequence, total);
        framework::SetMessageSourceId(message, source);
        framework::SetMessageTargetId(message, target);
        framework::SetMessageRequestId(message, requestId);
        framework::SetMessagePayload(message, payload.data(), payload.size());
        if (!framework::SendMessage(port, message))
            throw std::runtime_error("SendMessage failed");
    }

    void ReceiveSync(std::uint64_t logicalTimestamp)
    {
        const std::uint64_t wireTimestamp =
            AddChecked(logicalTimestamp, o.linkLatency, "sync wire timestamp");
        const Message* message = ReceiveExpected(wireTimestamp);
        if (framework::GetMessageType(message) != MessageType::ControlSync ||
            framework::GetMessageTimestamp(message) != wireTimestamp ||
            framework::GetMessageSourceId(message) != 0 ||
            framework::GetMessageTargetId(message) != 0 ||
            framework::GetMessageRequestId(message) != 0 ||
            framework::GetMessagePayloadSize(message) != 0)
            throw std::runtime_error("invalid or duplicate synchronization message");
        ++stats.syncReceived;
    }

    void ReceiveBurst(std::uint32_t phase, std::uint64_t requestBase,
                      std::uint32_t source, std::uint32_t target,
                      std::uint64_t timestampBase)
    {
        for (std::uint64_t sequence = 0; sequence < o.messages; ++sequence) {
            ReceivePayload(phase, sequence, o.messages, requestBase + sequence,
                           source, target,
                           MulAddChecked(timestampBase, sequence, o.timestampStep,
                                         "burst receive"));
        }
    }

    void ReceivePayload(std::uint32_t phase, std::uint64_t sequence,
                        std::uint64_t total, std::uint64_t requestId,
                        std::uint32_t source, std::uint32_t target,
                        std::uint64_t logicalTimestamp)
    {
        const std::uint64_t wireTimestamp =
            AddChecked(logicalTimestamp, o.linkLatency, "payload wire timestamp");
        const Message* message = ReceiveExpected(wireTimestamp);
        if (framework::GetMessageType(message) != MessageType::Payload)
            throw std::runtime_error("unexpected non-payload message (extra sync/terminate)");
        if (framework::GetMessageTimestamp(message) != wireTimestamp)
            throw std::runtime_error("message timestamp mismatch");
        if (framework::GetMessageSourceId(message) != source ||
            framework::GetMessageTargetId(message) != target ||
            framework::GetMessageRequestId(message) != requestId)
            throw std::runtime_error("source/target/request ID mismatch");
        if (framework::GetMessagePayloadSize(message) != o.payloadBytes)
            throw std::runtime_error("payload size mismatch");
        std::vector<unsigned char> actual(o.payloadBytes);
        std::memcpy(actual.data(), framework::GetMessagePayloadData(message),
                    actual.size());
        PayloadHeader header{};
        std::memcpy(&header, actual.data(), sizeof(header));
        if (header.magic != kMagic || header.phase != phase ||
            header.sequence != sequence || header.total != total ||
            header.reserved != 0)
            throw std::runtime_error("payload sequence/phase metadata mismatch");
        const auto checksum = Hash(actual.data() + sizeof(PayloadHeader),
                                   actual.size() - sizeof(PayloadHeader));
        if (header.bodyChecksum != checksum)
            throw std::runtime_error("payload checksum mismatch");
        const auto expected = MakePayload(o.payloadBytes, phase, sequence, total);
        if (actual != expected)
            throw std::runtime_error("payload byte pattern mismatch");
        ++stats.payloadReceived;
    }

    const Message* ReceiveExpected(std::uint64_t expectedTimestamp)
    {
        const std::uint64_t before = expectedTimestamp == 0 ? 0 : expectedTimestamp - 1;
        const auto deadline = Deadline();
        ReceiveStatus status = ReceiveStatus::Empty;
        for (;;) {
            const Message* message = framework::ReceiveMessage(port, before, &status);
            if (message)
                throw std::runtime_error("message became visible before its timestamp");
            if (status == ReceiveStatus::PendingFuture)
                break;
            CheckDeadline(deadline, "waiting for pending-future message");
            std::this_thread::sleep_for(std::chrono::milliseconds(1));
        }
        ++stats.pendingObserved;
        const std::uint64_t base = hasEmittedSync ? lastEmittedSync : before;
        const std::uint64_t bound = base >
                std::numeric_limits<std::uint64_t>::max() - actualSyncInterval
            ? std::numeric_limits<std::uint64_t>::max()
            : base + actualSyncInterval;
        const std::uint64_t expectedSafe = std::min(expectedTimestamp, bound);
        if (framework::SafeTimestamp(port, before) != expectedSafe)
            throw std::runtime_error("SafeTimestamp did not account for pending future data");
        const Message* message =
            framework::ReceiveMessage(port, expectedTimestamp, &status);
        if (!message || status != ReceiveStatus::Message)
            throw std::runtime_error("pending message did not become visible at timestamp");
        return message;
    }

    std::chrono::steady_clock::time_point Deadline() const
    {
        return std::chrono::steady_clock::now() +
               std::chrono::milliseconds(o.timeoutMs);
    }

    static void CheckDeadline(std::chrono::steady_clock::time_point deadline,
                              const char* operation)
    {
        if (std::chrono::steady_clock::now() >= deadline)
            throw std::runtime_error(std::string("timeout: ") + operation);
    }

    const Options& o;
    Port* port = nullptr;
    std::uint64_t actualSyncInterval = 0;
    std::uint64_t lastEmittedSync = 0;
    bool hasEmittedSync = false;
    Stats stats;
};

} // namespace

int main(int argc, char** argv)
{
    std::string role = "unknown";
    try {
        const Options options = ParseOptions(argc, argv);
        role = options.role;
        const auto begin = std::chrono::steady_clock::now();
        Runner runner(options);
        runner.Run();
        const auto elapsed = std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::steady_clock::now() - begin).count();
        const Stats& stats = runner.GetStats();
        std::cout << "{\"status\":\"PASS\",\"role\":\"" << role
                  << "\",\"messages_per_phase\":" << options.messages
                  << ",\"payload_bytes\":" << options.payloadBytes
                  << ",\"payload_received\":" << stats.payloadReceived
                  << ",\"pending_observed\":" << stats.pendingObserved
                  << ",\"sync_received\":" << stats.syncReceived
                  << ",\"elapsed_ms\":" << elapsed << "}\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "framework stress " << role << " failed: " << error.what()
                  << '\n';
        std::cout << "{\"status\":\"FAIL\",\"role\":\"" << role
                  << "\",\"error\":\"test failure; see stderr\"}\n";
        return 1;
    }
}
