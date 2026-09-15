#ifndef CC_PROTOCOL_CONTROL_FRAME_HH
#define CC_PROTOCOL_CONTROL_FRAME_HH

#include <cassert>

namespace cc { namespace glob {

// The caller includes its wire CoherenceMessage definition first. Preserve
// the complete envelope (including timestamps) while omitting inactive union
// members. Recall/Invalidate requests have no body; Probe has a typed body.
struct ControlFrame {
    CoherenceMessageHeader h;
    UBHAPresenceProbeReqBody probe{};
    ControlFrame() = default;
    explicit ControlFrame(const CoherenceMessage &message) : h(message.h) {
        assert(h.type == CoherenceMessageType::RecallReq ||
               h.type == CoherenceMessageType::InvalidateReq ||
               h.type == CoherenceMessageType::HAPresenceProbeReq);
        if (h.type == CoherenceMessageType::HAPresenceProbeReq)
            probe = message.b.haPresenceProbeReq;
    }
    CoherenceMessage expand() const {
        CoherenceMessage message;
        message.h = h;
        if (h.type == CoherenceMessageType::HAPresenceProbeReq)
            message.b.haPresenceProbeReq = probe;
        return message;
    }
};

struct ControlReply {
    CoherenceMessageHeader h;
    // Only Recall needs a cache line; the other reply bodies fit this union.
    union Body {
        UBRecallRespBody recall;
        UBHAPresenceProbeRespBody probe;
        Body() : recall() {}
    } b;
    ControlReply() = default;
    explicit ControlReply(const CoherenceMessage &message) : h(message.h) {
        assert(h.type == CoherenceMessageType::RecallResp ||
               h.type == CoherenceMessageType::InvalidateAck ||
               h.type == CoherenceMessageType::HAPresenceProbeResp);
        if (h.type == CoherenceMessageType::RecallResp) b.recall = message.b.recallResp;
        if (h.type == CoherenceMessageType::HAPresenceProbeResp) b.probe = message.b.haPresenceProbeResp;
    }
    CoherenceMessage expand() const {
        CoherenceMessage message;
        message.h = h;
        if (h.type == CoherenceMessageType::RecallResp) message.b.recallResp = b.recall;
        if (h.type == CoherenceMessageType::HAPresenceProbeResp) message.b.haPresenceProbeResp = b.probe;
        return message;
    }
};

} }
#endif
