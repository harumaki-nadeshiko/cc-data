#include "framework/iface/Log.hh"
#include "framework/iface/Port.hh"

#include <chrono>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstddef>
#include <limits>
#include <string>
#include <thread>
#include <vector>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

using namespace framework;

namespace {

int failures = 0;

void Check(bool condition, const char* text)
{
    if (!condition) {
        std::fprintf(stderr, "FAIL: %s\n", text);
        ++failures;
    }
}

std::string CaptureInfoLog()
{
    int descriptors[2];
    if (pipe(descriptors) != 0)
        return {};
    std::fflush(stdout);
    const int saved = dup(STDOUT_FILENO);
    dup2(descriptors[1], STDOUT_FILENO);
    close(descriptors[1]);
    const std::string text = "str";
    LogInfo("ignored", "{} {:x} {{}} {} {}", -7, 255u, text,
            reinterpret_cast<void*>(0x1234));
    std::fflush(stdout);
    dup2(saved, STDOUT_FILENO);
    close(saved);
    char buffer[256]{};
    const ssize_t count = read(descriptors[0], buffer, sizeof(buffer));
    close(descriptors[0]);
    return count > 0 ? std::string(buffer, static_cast<std::size_t>(count))
                     : std::string();
}

bool CheckLogAssertIfContract()
{
    int descriptors[2];
    if (pipe(descriptors) != 0)
        return false;
    const pid_t child = fork();
    if (child == 0) {
        close(descriptors[0]);
        dup2(descriptors[1], STDERR_FILENO);
        close(descriptors[1]);
        LogAssertIf(true, "contract", "success predicate must not fire");
        LogAssertIf(false, "contract", "failed predicate {}", 7);
        _exit(99);
    }
    close(descriptors[1]);
    int status = 0;
    if (child < 0 || waitpid(child, &status, 0) != child) {
        close(descriptors[0]);
        return false;
    }
    std::string output;
    char buffer[512];
    ssize_t count;
    while ((count = read(descriptors[0], buffer, sizeof(buffer))) > 0)
        output.append(buffer, static_cast<std::size_t>(count));
    close(descriptors[0]);
    return WIFSIGNALED(status) && WTERMSIG(status) == SIGABRT &&
           output.find("failed predicate 7\n") != std::string::npos &&
           output.find("success predicate must not fire") == std::string::npos;
}

PortConfig Config(const char* self, const char* peer)
{
    PortConfig config;
    config.selfRole = self;
    config.peerRole = peer;
    config.channelName = "coherence";
    config.nodeId = 0;
    config.socketId = 0;
    config.numNodes = 1;
    config.numSockets = 1;
    return config;
}

bool CheckChildAborts(int testCase)
{
    const pid_t child = fork();
    if (child == 0) {
        const std::string directory = "/tmp/framework_death_test_" +
                                      std::to_string(getpid());
        if (mkdir(directory.c_str(), 0700) != 0)
            _exit(90);
        setenv("UBCC_IPC_DIR", directory.c_str(), 1);
        PortRuntime runtime;
        runtime.linkLatency = 10;
        runtime.syncInterval = 20;
        Port* port = CreatePort(Config("gem5", "ubio"), runtime);
        if (!port)
            _exit(91);
        Port* peer = CreatePort(Config("ubio", "gem5"), runtime);
        if (!peer)
            _exit(94);
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
        if (testCase == 0 || testCase == 1) {
            Message* message = AllocateSendMessage(port, 0);
            if (!message)
                _exit(92);
            if (testCase == 0) {
                std::vector<unsigned char> payload(GetMaxPayloadSize() + 1, 0);
                SetMessagePayload(message, payload.data(), payload.size());
            } else {
                SetMessagePayload(message, nullptr, 1);
            }
        } else if (testCase == 2) {
            EmitSync(port, std::numeric_limits<std::uint64_t>::max() - 9);
        } else {
            if (!EmitSync(port, 100))
                _exit(93);
            EmitSync(port, 99);
        }
        _exit(99);
    }
    int status = 0;
    return child > 0 && waitpid(child, &status, 0) == child &&
           WIFSIGNALED(status) && WTERMSIG(status) == SIGABRT;
}

const Message* WaitReceive(Port* port, std::uint64_t time,
                           ReceiveStatus& status)
{
    for (int attempt = 0; attempt != 200; ++attempt) {
        const Message* message = ReceiveMessage(port, time, &status);
        if (message || status == ReceiveStatus::PendingFuture)
            return message;
        std::this_thread::sleep_for(std::chrono::milliseconds(2));
    }
    return nullptr;
}

} // namespace

int main()
{
    Check(sizeof(TerminateInfo) == 12 &&
              offsetof(TerminateInfo, reason) == 0 &&
              offsetof(TerminateInfo, exitCode) == 4 &&
              offsetof(TerminateInfo, sender) == 8,
          "TerminateInfo stable 12-byte wire layout");
    Check(CaptureInfoLog() == "-7 ff {} str 0x1234\n",
          "format parser and exactly-one newline");
    Check(CheckLogAssertIfContract(),
          "LogAssertIf accepts true and logs then aborts on false");
    // Keep every fork-based death test ahead of parent-side ZMQ creation.
    Check(CheckChildAborts(0), "oversized payload is a contract violation");
    Check(CheckChildAborts(1),
          "nonzero null payload is a contract violation");
    Check(CheckChildAborts(2), "EmitSync timestamp overflow is rejected");
    Check(CheckChildAborts(3), "EmitSync timestamp rollback is rejected");

    const std::string ipcDirectory =
        "/tmp/framework_iface_test_" + std::to_string(getpid());
    Check(mkdir(ipcDirectory.c_str(), 0700) == 0, "create test IPC directory");
    setenv("UBCC_IPC_DIR", ipcDirectory.c_str(), 1);
    PortRuntime runtime;
    runtime.linkLatency = 10;
    runtime.syncInterval = 20;
    Port* gem5 = CreatePort(Config("gem5", "ubio"), runtime);
    Port* ubio = CreatePort(Config("ubio", "gem5"), runtime);
    Check(gem5 != nullptr && ubio != nullptr, "create local port pair");
    if (!gem5 || !ubio)
        return 1;
    std::this_thread::sleep_for(std::chrono::milliseconds(20));

    Message* source = AllocateSendMessage(gem5, 100);
    Message* destination = AllocateSendMessage(gem5, 900);
    Check(source && destination, "allocate opaque messages");
    if (!source || !destination) {
        if (source)
            ReleaseMessage(source);
        if (destination)
            ReleaseMessage(destination);
        TerminatePort(gem5);
        DestroyPort(gem5);
        TerminatePort(ubio);
        DestroyPort(ubio);
        return 1;
    }
    const unsigned char payload[] = {1, 2, 3, 4};
    SetMessageType(source, MessageType::Payload);
    SetMessageSourceId(source, 11);
    SetMessageTargetId(source, 22);
    SetMessageRequestId(source, 33);
    SetMessagePayload(source, payload, sizeof(payload));
    Check(GetMessagePayloadSize(source) == sizeof(payload), "set payload");
    const std::uint64_t destinationTimestamp = GetMessageTimestamp(destination);
    CopyMessage(destination, source);
    Check(GetMessageTimestamp(destination) == destinationTimestamp,
          "CopyMessage preserves timestamp");
    Check(GetMessageType(destination) == MessageType::Payload &&
              GetMessageSourceId(destination) == 11 &&
              GetMessageTargetId(destination) == 22 &&
              GetMessageRequestId(destination) == 33,
          "CopyMessage copies metadata");
    Check(GetMessagePayloadSize(destination) == sizeof(payload) &&
              std::memcmp(GetMessagePayloadData(destination), payload,
                          sizeof(payload)) == 0,
          "CopyMessage copies payload");
    ReleaseMessage(destination);

    // source timestamp is 110, so it must remain pending at 109.
    Check(SendMessage(gem5, source), "send consumes source");
    ReceiveStatus status = ReceiveStatus::Empty;
    const Message* borrowed = WaitReceive(ubio, 109, status);
    Check(borrowed == nullptr && status == ReceiveStatus::PendingFuture,
           "future receive status");
    Check(ReceiveTimestamp(ubio) == 110,
          "ReceiveTimestamp exposes pending future timestamp");
    Check(SafeTimestamp(ubio, 109) == 110,
          "SafeTimestamp accounts for pending future timestamp");
    borrowed = ReceiveMessage(ubio, 110, &status);
    Check(borrowed && status == ReceiveStatus::Message,
          "pending message becomes visible");
    Check(borrowed && GetMessageRequestId(borrowed) == 33,
          "borrowed message contents");

    Message* second = AllocateSendMessage(gem5, 110);
    Check(second != nullptr, "allocate second message");
    if (second) {
        SetMessageRequestId(second, 44);
        Check(SendMessage(gem5, second), "send second message");
    }
    const Message* secondBorrow = WaitReceive(ubio, 1000, status);
    Check(second && secondBorrow && GetMessageRequestId(secondBorrow) == 44,
           "next receive replaces per-port borrowed storage");
    Check(second && secondBorrow == borrowed,
           "receive borrow uses storage valid until next receive");

    Check(SyncInterval(gem5) == 20, "sync interval accessor");

    Check(EmitSync(gem5, 200), "emit initial sync smoke");
    const Message* sync = WaitReceive(ubio, 210, status);
    Check(sync && status == ReceiveStatus::Message &&
              GetMessageType(sync) == MessageType::ControlSync &&
              GetMessageTimestamp(sync) == 210,
          "sync is delivered with link latency");
    Check(EmitSync(gem5, 205), "throttled EmitSync succeeds without sending");
    sync = WaitReceive(ubio, 1000, status);
    Check(sync == nullptr && status == ReceiveStatus::Empty,
          "throttled EmitSync emits no duplicate");
    Check(EmitSync(gem5, 210), "EmitSync resumes at link-latency boundary");
    sync = WaitReceive(ubio, 219, status);
    Check(sync == nullptr && status == ReceiveStatus::PendingFuture &&
              ReceiveTimestamp(ubio) == 220,
          "future sync contributes pending receive timestamp");
    Check(SafeTimestamp(ubio, 219) == 220,
          "SafeTimestamp observes a pending future sync");
    sync = ReceiveMessage(ubio, 220, &status);
    Check(sync && GetMessageType(sync) == MessageType::ControlSync,
          "pending sync becomes visible at its timestamp");
    const std::uint64_t lastNonOverflowingSync =
        std::numeric_limits<std::uint64_t>::max() - runtime.linkLatency;
    Check(EmitSync(gem5, lastNonOverflowingSync),
          "EmitSync accepts the last non-overflowing timestamp");
    sync = WaitReceive(ubio, std::numeric_limits<std::uint64_t>::max(), status);
    Check(sync && GetMessageType(sync) == MessageType::ControlSync &&
              GetMessageTimestamp(sync) ==
                  std::numeric_limits<std::uint64_t>::max(),
          "EmitSync boundary timestamp does not overflow");

    Message* terminate = AllocateSendMessage(gem5, 1000);
    Check(terminate != nullptr, "allocate explicit terminate message");
    if (terminate) {
        SetMessageType(terminate, MessageType::Terminate);
        Check(SendMessage(gem5, terminate), "send explicit terminate message");
        const Message* notice = WaitReceive(ubio, 0, status);
        Check(notice && status == ReceiveStatus::Message &&
                  GetMessageType(notice) == MessageType::Terminate,
              "terminate notification is immediately visible");
    }
    // TerminatePort intentionally sends only a non-blocking, best-effort notice.
    TerminatePort(gem5);
    DestroyPort(gem5);
    TerminatePort(ubio);
    DestroyPort(ubio);

    if (failures == 0)
        std::printf("PASS: iface contract\n");
    return failures == 0 ? 0 : 1;
}
