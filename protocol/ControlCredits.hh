#ifndef CC_PROTOCOL_CONTROL_CREDITS_HH
#define CC_PROTOCOL_CONTROL_CREDITS_HH

#include <array>
#include <cstdint>
#include <limits>
#include <memory>
#include <stdexcept>

namespace cc { namespace glob {

// One end-to-end window per (Home plane, endpoint plane). A control carries
// its lease in seqNum; retransmission preserves it and the matching response
// echoes it. This consumes no extra packet and changes no link timestamp.
// Physical routing hops must not allocate a second lease.
template<class Header, unsigned Peers = 256, unsigned Window = 4>
class ControlCredits
{
  public:
    explicit ControlCredits(unsigned peerCount = Peers)
        : peers(peerCount), slots(new std::array<Slot, Window>[peerCount]{}) {
        if (!peers || peers > Peers) throw std::invalid_argument("control peer geometry");
    }
    unsigned peerCount() const { return peers; }
    std::size_t storageBytes() const { return peers * Window * sizeof(Slot); }
    static_assert(Window == 4, "wire lease uses two slot bits");
    struct Slot {
        Header header{};
        uint64_t generation = 0;
        bool active = false;
    };
    enum class Receipt { New, Duplicate, Stale, Invalid };
    unsigned occupancy(unsigned peer) const {
        if (peer >= peers) return 0;
        unsigned n = 0;
        for (const auto &s : slots[peer]) n += s.active;
        return n;
    }
    bool acquire(unsigned peer, Header &h) {
        if (peer >= peers) return false;
        for (auto &s : slots[peer]) {
            if (s.active && identity(s.header, h)) {
                h.seqNum = s.header.seqNum;
                return true;
            }
        }
        for (unsigned i = 0; i < Window; ++i) {
            auto &s = slots[peer][i];
            if (s.active || s.generation == (std::numeric_limits<uint64_t>::max() >> 2))
                continue;
            h.seqNum = (++s.generation << 2) | i;
            s.header = h; s.active = true;
            return true;
        }
        return false;
    }
    bool available(unsigned peer, const Header &h) const {
        if (peer >= peers) return false;
        for (const auto &s : slots[peer])
            if (!s.active || identity(s.header, h)) return true;
        return false;
    }
    // A duplicate response, even after this slot has been reused, cannot
    // inflate credit. responseType is checked by the caller's typed mapping.
    bool release(unsigned peer, const Header &reply, unsigned requestType) {
        if (peer >= peers || !(reply.seqNum >> 2)) return false;
        auto &s = slots[peer][reply.seqNum & 3];
        const auto &h = s.header;
        if (!s.active || h.seqNum != reply.seqNum ||
            static_cast<unsigned>(h.type) != requestType ||
            h.reqId != reply.reqId || h.epoch != reply.epoch ||
            h.homeLinePa != reply.homeLinePa ||
            h.srcNode != reply.dstNode || h.srcSocket != reply.dstSocket ||
            h.dstNode != reply.srcNode || h.dstSocket != reply.srcSocket)
            return false;
        s.active = false;
        return true;
    }
    Receipt receive(unsigned peer, const Header &h) {
        if (peer >= peers || !(h.seqNum >> 2)) return Receipt::Invalid;
        auto &s = slots[peer][h.seqNum & 3];
        const auto generation = h.seqNum >> 2;
        if (generation < s.generation) return Receipt::Stale;
        if (generation == s.generation)
            return identity(s.header, h) ? Receipt::Duplicate : Receipt::Invalid;
        if (s.active) return Receipt::Invalid;
        s.header = h; s.generation = generation; s.active = true;
        return Receipt::New;
    }
    bool complete(unsigned peer, uint64_t lease) {
        if (peer >= peers) return false;
        auto &s = slots[peer][lease & 3];
        if (!s.active || s.header.seqNum != lease) return false;
        s.active = false;
        return true;
    }
    const Slot *get(unsigned peer, unsigned slot) const {
        return peer < peers && slot < Window ? &slots[peer][slot] : nullptr;
    }
  private:
    static bool identity(const Header &a, const Header &b) {
        return a.type == b.type && a.reqId == b.reqId && a.epoch == b.epoch &&
            a.homeLinePa == b.homeLinePa && a.srcNode == b.srcNode &&
            a.srcSocket == b.srcSocket && a.dstNode == b.dstNode &&
            a.dstSocket == b.dstSocket;
    }
    const unsigned peers;
    std::unique_ptr<std::array<Slot, Window>[]> slots;
};

} }
#endif
