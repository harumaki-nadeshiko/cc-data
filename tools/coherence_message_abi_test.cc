#include "protocol/CoherenceMessage.hh"

#include <cstddef>
#include <cstdint>

using namespace cc::glob;

static_assert(static_cast<uint16_t>(CoherenceMessageType::PeerExit) == 30);
static_assert(static_cast<uint16_t>(CoherenceMessageType::HAPermissionReq) == 31);
static_assert(static_cast<uint16_t>(CoherenceMessageType::HAPermissionResp) == 32);
static_assert(static_cast<uint16_t>(CoherenceMessageType::HAPermissionAck) == 33);
static_assert(static_cast<uint16_t>(CoherenceMessageType::HAPresenceProbeReq) == 34);
static_assert(static_cast<uint16_t>(CoherenceMessageType::HAPresenceProbeResp) == 35);
static_assert(static_cast<uint16_t>(CoherenceMessageType::NetworkExit) == 36);
static_assert(CFLAG_PEER_EXIT_ACK == (1u << 8));
static_assert(alignof(CoherenceMessageHeader) == 8);
static_assert(offsetof(CoherenceMessageHeader, type) == 0);
static_assert(offsetof(CoherenceMessageHeader, flags) == 20);
static_assert(offsetof(CoherenceMessageHeader, homeLinePa) == 24);
static_assert(offsetof(CoherenceMessageHeader, localLinePa) == 32);
static_assert(offsetof(CoherenceMessageHeader, epoch) == 40);
static_assert(offsetof(CoherenceMessageHeader, reqId) == 48);
static_assert(offsetof(CoherenceMessageHeader, seqNum) == 56);
static_assert(offsetof(CoherenceMessageHeader, enqueueTick) == 64);
static_assert(offsetof(CoherenceMessageHeader, readyTick) == 72);
static_assert(sizeof(CoherenceMessageHeader) == 80);

static_assert(sizeof(UBWritebackKind) == 1);
static_assert(sizeof(UBWriteDisposition) == 1);
static_assert(offsetof(UBWritebackReqBody, kind) == 0);
static_assert(offsetof(UBWritebackReqBody, disposition) == 1);
static_assert(offsetof(UBWritebackReqBody, hasData) == 2);
static_assert(offsetof(UBWritebackReqBody, reserved) == 3);
static_assert(offsetof(UBWritebackReqBody, byteMask) == 8);
static_assert(offsetof(UBWritebackReqBody, data) == 16);
static_assert(sizeof(UBWritebackReqBody) == 80);

static_assert(offsetof(UBHAPermissionReqBody, operation) == 0);
static_assert(offsetof(UBHAPermissionReqBody, reserved) == 1);
static_assert(offsetof(UBHAPermissionReqBody, permissionEpoch) == 8);
static_assert(offsetof(UBHAPermissionReqBody, byteMask) == 16);
static_assert(offsetof(UBHAPermissionReqBody, data) == 24);
static_assert(sizeof(UBHAPermissionReqBody) == 88);
static_assert(offsetof(UBHAPermissionRespBody, operation) == 0);
static_assert(offsetof(UBHAPermissionRespBody, status) == 1);
static_assert(offsetof(UBHAPermissionRespBody, hasData) == 2);
static_assert(offsetof(UBHAPermissionRespBody, permissionEpoch) == 8);
static_assert(offsetof(UBHAPermissionRespBody, data) == 16);
static_assert(sizeof(UBHAPermissionRespBody) == 80);
static_assert(sizeof(UBHAPermissionAckBody) == 16);
static_assert(offsetof(UBHAPermissionAckBody, permissionEpoch) == 8);
static_assert(sizeof(UBHAPresenceProbeReqBody) == 16);
static_assert(sizeof(UBHAPresenceProbeRespBody) == 16);
static_assert(offsetof(UBHAPresenceProbeRespBody, observedEpoch) == 8);
static_assert(sizeof(UBReadReqBody) == 1);
static_assert(sizeof(UBReadRespBody) == 128);
static_assert(offsetof(UBReadRespBody, grantData) == 64);
static_assert(sizeof(UBRecallRespBody) == 64);
static_assert(sizeof(UBWritebackRespBody) == 1);
static_assert(alignof(CoherenceMessageBody) == 8);
static_assert(sizeof(CoherenceMessageBody) == 264);
static_assert(offsetof(CoherenceMessage, h) == 0);
static_assert(offsetof(CoherenceMessage, b) == 80);
static_assert(alignof(CoherenceMessage) == 8);
static_assert(sizeof(CoherenceMessage) == 344);

int main()
{
    UBWritebackReqBody body;
    return body.byteMask == 0 ? 0 : 1;
}
