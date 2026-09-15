#ifndef CC_PROTOCOL_PROGRESS_FRAME_HH
#define CC_PROTOCOL_PROGRESS_FRAME_HH

#include <cassert>

namespace cc { namespace glob {

// Clear and permission ACK are header-sized completion obligations. Their
// dedicated reserved queues do not need metadata page or cacheline payloads.
struct ProgressFrame {
    CoherenceMessageHeader h;
    union Body {
        UBClearReqBody clear;
        UBHAPermissionAckBody permission;
        Body() : clear() {}
    } b;
    ProgressFrame() = default;
    ProgressFrame(const CoherenceMessage &message) : h(message.h) {
        if (h.type == CoherenceMessageType::ClearReq) b.clear = message.b.clearReq;
        else {
            assert(h.type == CoherenceMessageType::HAPermissionAck);
            b.permission = message.b.haPermissionAck;
        }
    }
    operator CoherenceMessage() const {
        CoherenceMessage message;
        message.h = h;
        if (h.type == CoherenceMessageType::ClearReq) message.b.clearReq = b.clear;
        else {
            assert(h.type == CoherenceMessageType::HAPermissionAck);
            message.b.haPermissionAck = b.permission;
        }
        return message;
    }
};
} }
#endif
