#ifndef EP_BOUNDARY_LIFECYCLE_HH
#define EP_BOUNDARY_LIFECYCLE_HH

// Standalone lifecycle prototype. Not yet connected to EPBackend or CHI.
// No data is stored here: custody means a native CHI transaction retains data.
#include <array>
#include <cstddef>
#include <cstdint>
#include <limits>

namespace ep_boundary {

struct LineKey {
    uint64_t offset = 0;
    uint16_t domain = 0;
    uint8_t homeNode = 0;
    uint8_t homeSocket = 0;
    bool operator==(const LineKey &b) const {
        return offset == b.offset && domain == b.domain &&
            homeNode == b.homeNode && homeSocket == b.homeSocket;
    }
};

struct Identity {
    uint64_t epoch = 0;
    uint8_t owner = 0;
    bool operator==(const Identity &b) const {
        return epoch == b.epoch && owner == b.owner;
    }
};

// A source socket is routing, never part of the owner bit namespace.
struct Request {
    uint64_t id = 0;
    uint8_t sourceSocket = 0;
    bool operator==(const Request &b) const {
        return id == b.id && sourceSocket == b.sourceSocket;
    }
};

struct Token {
    uint64_t generation = 0;
    uint16_t slot = 0;
};

enum class Result { Accepted, Duplicate, Retry, Stale, Conflict };
enum class Role : uint8_t { Ordinary, Control };

// Slots retain incarnation and custody independently of access permission.
// A necessary same-line write attaches without consuming another entry. Its
// reserved descriptor cannot be stolen by ordinary admission or another WB.
// Each control callback is represented by a distinct bounded request identity;
// the caller retains the actual transport descriptor until receipt/cleanup.
template<size_t Slots = 64, size_t ReservedControl = 8, size_t Waiters = 4>
class Table {
  public:
    static_assert(Slots > ReservedControl && ReservedControl > 0, "reserve");
    static_assert(Slots <= 65536 && Waiters > 0, "token widths");
    struct Entry {
        LineKey key;
        Identity committed;
        Request parent;
        Request write;
        std::array<Request, Waiters> controls{};
        uint64_t generation = 0;
        size_t controlCount = 0;
        Role role = Role::Ordinary;
        bool live = false;
        bool access = false;
        bool custody = false;
        bool parentChiDone = false;
        bool homePublished = false;
        bool writeAttached = false;
        bool writeDataComplete = false;
        bool writeChiDone = false;
        bool controlAttached = false;
        bool controlChiDone = false;
        bool controlsDelivered = false;
    };

    Result admit(LineKey key, Identity committed, Request parent, Role role,
                 bool custody, Token &token) {
        for (size_t i = 0; i < Slots; ++i) {
            const auto &e = entries[i];
            if (!e.live || !(e.key == key))
                continue;
            if (!(e.committed == committed))
                return Result::Conflict;
            if (e.parent == parent && e.role == role) {
                token = {e.generation, static_cast<uint16_t>(i)};
                return Result::Duplicate;
            }
            return Result::Retry;
        }
        const size_t limit = role == Role::Ordinary ?
            Slots - ReservedControl : Slots;
        // Control first uses its reserve, protecting ordinary service capacity.
        for (size_t n = 0; n < limit; ++n) {
            const size_t i = role == Role::Control ?
                (n + Slots - ReservedControl) % Slots : n;
            auto &e = entries[i];
            if (e.live || e.generation == std::numeric_limits<uint64_t>::max())
                continue; // Never wrap and relabel a stale token.
            const uint64_t generation = e.generation + 1;
            e = Entry{};
            e.generation = generation;
            e.live = true;
            e.key = key;
            e.committed = committed;
            e.parent = parent;
            e.role = role;
            e.access = role == Role::Ordinary;
            e.custody = custody;
            token = {generation, static_cast<uint16_t>(i)};
            return Result::Accepted;
        }
        return Result::Retry;
    }

    const Entry *snapshot(Token t) const {
        if (t.slot >= Slots)
            return nullptr;
        const auto &e = entries[t.slot];
        return e.live && e.generation == t.generation ? &e : nullptr;
    }

    Result attachControl(Token t, Identity identity, Request request) {
        auto *e = mutableEntry(t);
        if (!e) return Result::Stale;
        if (!(e->committed == identity)) return Result::Conflict;
        for (size_t i = 0; i < e->controlCount; ++i)
            if (e->controls[i] == request) return Result::Duplicate;
        // No admission after publication: a later request must retry at Home.
        if (e->homePublished || e->controlCount == Waiters)
            return Result::Retry;
        e->controls[e->controlCount++] = request;
        e->controlAttached = true;
        e->access = false; // Do not modify committed identity or custody.
        return Result::Accepted;
    }

    Result attachWrite(Token t, Identity identity, Request request) {
        auto *e = mutableEntry(t);
        if (!e) return Result::Stale;
        if (!(e->committed == identity)) return Result::Conflict;
        if (e->writeAttached)
            return e->write == request ? Result::Duplicate : Result::Retry;
        if (e->homePublished) return Result::Retry;
        e->write = request;
        e->writeAttached = true;
        return Result::Accepted;
    }

    Result dataComplete(Token t, Request write, uint64_t byteMask) {
        auto *e = mutableEntry(t);
        if (!e) return Result::Stale;
        if (!e->writeAttached || !(e->write == write)) return Result::Conflict;
        if (byteMask != std::numeric_limits<uint64_t>::max())
            return Result::Retry; // Never fabricate zero/full data on a miss.
        if (e->writeDataComplete) return Result::Duplicate;
        e->writeDataComplete = true;
        return Result::Accepted;
    }

    Result publish(Token t, Identity identity, bool matchingDataPersisted) {
        auto *e = mutableEntry(t);
        if (!e) return Result::Stale;
        if (!(e->committed == identity)) return Result::Conflict;
        if (e->homePublished) return Result::Duplicate;
        if (e->writeAttached && !e->writeDataComplete) return Result::Retry;
        if (e->custody && !matchingDataPersisted) return Result::Conflict;
        e->homePublished = true;
        return Result::Accepted;
    }

    Result completeParent(Token t) {
        auto *e = mutableEntry(t);
        if (!e) return Result::Stale;
        if (e->parentChiDone) return Result::Duplicate;
        e->parentChiDone = true;
        return Result::Accepted;
    }

    Result completeWrite(Token t, Request request) {
        auto *e = mutableEntry(t);
        if (!e) return Result::Stale;
        if (!e->writeAttached || !(e->write == request)) return Result::Conflict;
        if (!e->homePublished) return Result::Retry;
        if (e->writeChiDone) return Result::Duplicate;
        e->writeChiDone = true;
        return Result::Accepted;
    }

    Result completeControl(Token t, bool noData) {
        auto *e = mutableEntry(t);
        if (!e) return Result::Stale;
        if (!e->controlAttached) return Result::Conflict;
        if (noData && e->custody && !e->homePublished) return Result::Retry;
        if (e->controlChiDone) return Result::Duplicate;
        e->controlChiDone = true;
        return Result::Accepted;
    }

    // Transport must reserve its response capacity before consuming a callback.
    Result deliverControls(Token t, bool allResponsesAccepted) {
        auto *e = mutableEntry(t);
        if (!e) return Result::Stale;
        if (!e->homePublished || !e->controlChiDone || !allResponsesAccepted)
            return Result::Retry;
        if (e->controlsDelivered) return Result::Duplicate;
        e->controlsDelivered = true;
        return Result::Accepted;
    }

    Result retire(Token t) {
        auto *e = mutableEntry(t);
        if (!e) return Result::Stale;
        if (!e->parentChiDone || !e->homePublished ||
            (e->writeAttached && !e->writeChiDone) ||
            (e->controlAttached &&
             (!e->controlChiDone || !e->controlsDelivered)))
            return Result::Retry;
        e->live = false;
        return Result::Accepted;
    }

    size_t occupancy() const {
        size_t count = 0;
        for (const auto &e : entries) count += e.live;
        return count;
    }

  private:
    Entry *mutableEntry(Token t) {
        return const_cast<Entry *>(snapshot(t));
    }
    std::array<Entry, Slots> entries{};
};

} // namespace ep_boundary
#endif
