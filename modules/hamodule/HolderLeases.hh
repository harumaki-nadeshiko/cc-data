#ifndef CC_EP_HOLDER_LEASES_HH
#define CC_EP_HOLDER_LEASES_HH
#include <array>
#include <cstdint>
#include <vector>
#include <stdexcept>

namespace cc::ha {
// Home-only receipt storage, independent of the flat directory bitmap. Each
// source EP may hold at most 4096 lines across all Homes. Allocation is fixed
// at construction; neither a full receipt bank nor a stale release evicts one.
// Home assigns globally unique, non-wrapping install IDs within this instance.
class HolderLeases {
  public:
    static constexpr unsigned PerNode = 4096;
    struct Entry {
        uint64_t lease = 0;
        uint32_t line = 0;
        uint16_t pending = 0;
        bool occupied = false;
    };
    enum class Result { Applied, Duplicate, Stale, Invalid, Busy, AlreadyRetiredMatch };
    explicit HolderLeases(unsigned nodes) : nodes_(nodes), entries_(nodes * PerNode) {
        if (!nodes || nodes > 64) throw std::invalid_argument("holder lease nodes");
    }
    bool reserve(uint32_t line, unsigned node) {
        if (node >= nodes_) return false;
        if (auto *e = find(line, node)) {
            if (e->pending == UINT16_MAX) return false;
            ++e->pending;
            return true;
        }
        for (unsigned i = node * PerNode; i < (node + 1) * PerNode; ++i) {
            auto &e = entries_[i];
            if (e.occupied) continue;
            e = {0, line, 1, true};
            return true;
        }
        return false;
    }
    void abandon(uint32_t line, unsigned node) {
        if (auto *e = find(line, node); e && e->pending) {
            --e->pending;
            if (!e->lease && !e->pending) e->occupied = false;
        }
    }
    bool commit(uint32_t line, unsigned node, uint64_t lease, uint64_t sharers) {
        auto *e = find(line, node);
        if (!e || !e->pending || !lease || !(sharers & (uint64_t(1) << node))) return false;
        --e->pending;
        if (e->lease != lease) satisfy(line, node, e->lease);
        e->lease = lease;
        for (unsigned n = 0; n < nodes_; ++n)
            if (!(sharers & (uint64_t(1) << n)))
                if (auto *old = find(line, n)) {
                    satisfy(line, n, old->lease);
                    old->lease = 0;
                    old->occupied = old->pending != 0;
                }
        return true;
    }
    uint64_t current(uint32_t line, unsigned node) const {
        const auto *e = find(line, node);
        return e ? e->lease : 0;
    }
    // One serial escape per source node. Its completion receipt cannot be
    // displaced by other nodes or ordinary traffic while its ACK is in flight.
    Result release(uint32_t line, unsigned node, uint64_t lease,
                   uint64_t request, unsigned socket, bool busy) {
        if (node >= nodes_ || socket >= 4 || !lease || !request)
            return Result::Invalid;
        auto &done = completed_[node];
        if (done.request == request) {
            if (done.line != line || done.lease != lease || done.socket != socket)
                return Result::Invalid;
            if (done.stage == Stage::ForeignRetired) return Result::AlreadyRetiredMatch;
            if (done.stage == Stage::Released) return Result::Duplicate;
        }
        auto *e = find(line, node);
        if (!e || e->lease != lease) return Result::Stale;
        // One outstanding escape credit per source. Never replace an owed proof,
        // even with a request for a newer lease of the same line.
        if (done.request && done.request != request) return Result::Busy;
        if (!done.request)
            done = {lease, request, line, uint16_t(socket), Stage::ObservedBusy};
        if (busy) return Result::Busy;
        e->lease = 0;
        e->occupied = e->pending != 0;
        done.stage = Stage::Released;
        return Result::Applied;
    }
    bool acknowledge(uint32_t line, unsigned node, uint64_t lease,
                     uint64_t request, unsigned socket) {
        if (node >= nodes_) return false;
        auto &done = completed_[node];
        if (!request || done.request != request || done.line != line ||
            done.lease != lease || done.socket != socket ||
            done.stage == Stage::ObservedBusy) return false;
        done = {};
        return true;
    }
    std::size_t bytes() const { return entries_.size() * sizeof(Entry) + sizeof(completed_); }
  private:
    enum class Stage : uint8_t { ObservedBusy, Released, ForeignRetired };
    struct Receipt {
        uint64_t lease = 0, request = 0;
        uint32_t line = 0;
        uint16_t socket = 0;
        Stage stage = Stage::ObservedBusy;
    };
    void satisfy(uint32_t line, unsigned node, uint64_t lease) {
        auto &proof = completed_[node];
        if (lease && proof.request && proof.line == line && proof.lease == lease &&
            proof.stage == Stage::ObservedBusy)
            proof.stage = Stage::ForeignRetired;
    }
    const Entry *find(uint32_t line, unsigned node) const {
        if (node >= nodes_) return nullptr;
        for (unsigned i = node * PerNode; i < (node + 1) * PerNode; ++i)
            if (entries_[i].occupied && entries_[i].line == line) return &entries_[i];
        return nullptr;
    }
    Entry *find(uint32_t line, unsigned node) {
        return const_cast<Entry *>(static_cast<const HolderLeases *>(this)->find(line, node));
    }
    const unsigned nodes_;
    std::vector<Entry> entries_; // fixed physical size; never resized
    std::array<Receipt, 64> completed_{};
};
static_assert(sizeof(HolderLeases::Entry) == 16, "Home lease receipt budget");
}
#endif
