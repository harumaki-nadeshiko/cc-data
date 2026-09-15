#ifndef CC_PROTOCOL_RESPONSE_FRAME_HH
#define CC_PROTOCOL_RESPONSE_FRAME_HH

#include <cassert>

namespace cc { namespace glob {

// No metadata-page or write-request payload in a response reservation. Keep
// every field of all nine supported bodies, with the original wire header.
struct ResponseFrame {
    CoherenceMessageHeader h;
    union Body {
        UBReadRespBody readResp;
        UBWritebackRespBody writebackResp;
        UBEvictRespBody evictResp;
        UBUpgradeRespBody upgradeResp;
        UBUpgradeDoneRespBody upgradeDoneResp;
        UBClearRespBody clearResp;
        UBQueryLineMetaRespBody queryLineMetaResp;
        UBHAPermissionRespBody haPermissionResp;
        UBHAPresenceProbeRespBody haPresenceProbeResp;
        Body() : readResp() {}
    } b;
    ResponseFrame() = default;
    ResponseFrame(const CoherenceMessage &message) : h(message.h) {
        switch (h.type) {
#define COPY_BODY(type, field) case CoherenceMessageType::type: b.field = message.b.field; break
          COPY_BODY(ReadResp, readResp);
          COPY_BODY(WritebackResp, writebackResp);
          COPY_BODY(EvictResp, evictResp);
          COPY_BODY(UpgradeResp, upgradeResp);
          COPY_BODY(UpgradeDoneResp, upgradeDoneResp);
          COPY_BODY(ClearResp, clearResp);
          COPY_BODY(QueryLineMetaResp, queryLineMetaResp);
          COPY_BODY(HAPermissionResp, haPermissionResp);
          COPY_BODY(HAPresenceProbeResp, haPresenceProbeResp);
#undef COPY_BODY
          default: break; // reservation passes a request header, not a payload
        }
    }
    operator CoherenceMessage() const {
        CoherenceMessage message;
        message.h = h;
        switch (h.type) {
#define COPY_BODY(type, field) case CoherenceMessageType::type: message.b.field = b.field; break
          COPY_BODY(ReadResp, readResp);
          COPY_BODY(WritebackResp, writebackResp);
          COPY_BODY(EvictResp, evictResp);
          COPY_BODY(UpgradeResp, upgradeResp);
          COPY_BODY(UpgradeDoneResp, upgradeDoneResp);
          COPY_BODY(ClearResp, clearResp);
          COPY_BODY(QueryLineMetaResp, queryLineMetaResp);
          COPY_BODY(HAPermissionResp, haPermissionResp);
          COPY_BODY(HAPresenceProbeResp, haPresenceProbeResp);
#undef COPY_BODY
          default: assert(false);
        }
        return message;
    }
};
} }
#endif
