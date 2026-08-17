#ifndef CC_EP_HAMODULE_HA_CONTROLLER_HH
#define CC_EP_HAMODULE_HA_CONTROLLER_HH

#include "modules/hamodule/FlatBitmapDirectory.hh"

#include <cstddef>
#include <cstdint>
#include <array>
#include <deque>
#include <optional>
#include <unordered_map>
#include <vector>

namespace cc::ha {

class HAController {
  public:
    enum class RequestKind { Read, Write };
    enum class EventKind {
        OwnerData, InvalidateAck, InstallAck, ProbeResponse, Writeback,
        PersistenceComplete, Unavailable, Evict, PeerExit
    };
    enum class ActionKind {
        FetchOwner, FetchMemory, Invalidate, GrantRead, GrantWrite, Probe,
        PersistMemory, Commit, Release, Reject
    };

    // This is deliberately the same shape for transactions, events, and
    // actions so an adapter can copy it directly to/from a 64-byte wire beat.
    struct Payload {
        std::array<std::uint8_t, 64> bytes{};
        bool valid = false;

        Payload() : bytes{}, valid(false) {}
        Payload(const std::array<std::uint8_t, 64> &bytes_, bool valid_)
            : bytes(bytes_), valid(valid_) {}
        static Payload fromU64(std::uint64_t value);
        bool operator==(const Payload &other) const noexcept
        { return valid == other.valid && bytes == other.bytes; }
    };

    struct Request {
        std::uint64_t address;
        std::uint32_t requester;
        RequestKind kind;
        std::uint64_t requestId;
        Payload data{};

        Request(std::uint64_t address_ = 0, std::uint32_t requester_ = 0,
                RequestKind kind_ = RequestKind::Read, std::uint64_t requestId_ = 0)
            : address(address_), requester(requester_), kind(kind_),
              requestId(requestId_), data()
        {}
        Request(std::uint64_t address_, std::uint32_t requester_,
                RequestKind kind_, std::uint64_t requestId_, Payload data_)
            : address(address_), requester(requester_), kind(kind_),
              requestId(requestId_), data(data_)
        {}
    };
    struct Event {
        EventKind kind = EventKind::InstallAck;
        std::uint64_t address = 0;
        std::uint32_t node = 0;
        std::uint64_t requestId = 0;
        Payload data{};
        bool present = false;
        bool dirty = false;
    };
    struct Action {
        ActionKind kind;
        std::uint64_t address;
        std::uint32_t source;
        std::uint32_t target;
        std::uint64_t requestId;
        Payload data{};
    };
    struct Config {
        FlatBitmapDirectory::Config directory;
        std::size_t perAddressQueueDepth = 8;
    };

    explicit HAController(const Config &config);
    const FlatBitmapDirectory &directory() const noexcept { return directory_; }
    FlatBitmapDirectory &directoryForTest() noexcept { return directory_; }

    bool submit(const Request &request);
    void accept(const Event &event);
    bool hasAction() const noexcept { return !actions_.empty(); }
    Action popAction();
    std::size_t queued(std::uint64_t address) const;
    bool busy(std::uint64_t address) const;

    // Used after bounded tracking overflow or uncertain peer state.  Probe
    // responses reconstruct the line's exact bitmap entirely in transient
    // state; only the N-bit result is persisted.
    bool beginBroadcastReconstruction(std::uint64_t address, std::uint64_t requestId);
    bool submitOverflow(const Request &request);

  private:
    enum class Phase { NeedDataAndInvalidates, NeedPersistence, NeedInstall, Reconstruct };
    struct Transaction {
        Request request;
        Phase phase = Phase::NeedDataAndInvalidates;
        std::uint64_t oldSharers = 0;
        std::uint64_t pendingInvalidates = 0;
        std::uint64_t probePending = 0;
        std::uint64_t reconstructed = 0;
        bool dataPending = false;
        std::uint32_t dataSource = 0;
        Payload data{};
        bool overflow = false;
        bool reconstructOnly = false;
        bool persistBeforeGrant = false;
    };
    struct LineWork {
        std::optional<Transaction> active;
        std::deque<Request> waiting;
    };
    struct PendingWriteback {
        std::uint32_t node = 0;
        std::uint64_t requestId = 0;
        Payload data{};
        bool retain = false;
    };

    void validateAddressNode(std::uint64_t address, std::uint32_t node) const;
    void start(LineWork &work, const Request &request);
    void startKnown(LineWork &work, Transaction &txn);
    void maybeGrant(LineWork &work);
    void finish(LineWork &work, std::uint64_t address);
    void emit(ActionKind kind, const Transaction &txn, std::uint32_t source,
              std::uint32_t target);
    void emit(ActionKind kind, const Transaction &txn, std::uint32_t source,
              std::uint32_t target, const Payload &data);
    void rejectUnavailable(LineWork &work);
    bool unavailable(std::uint64_t address) const noexcept;

    FlatBitmapDirectory directory_;
    std::size_t queueDepth_;
    std::unordered_map<std::uint64_t, LineWork> work_;
    std::unordered_map<std::uint64_t, PendingWriteback> writebacks_;
    std::vector<std::uint8_t> unavailable_;
    std::deque<Action> actions_;
};

} // namespace cc::ha

#endif
