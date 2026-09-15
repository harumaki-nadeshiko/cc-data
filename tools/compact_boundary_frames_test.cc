#include "../protocol/CoherenceMessage.hh"
#include "../protocol/ControlFrame.hh"
#include "../protocol/ResponseFrame.hh"
#include "../protocol/ControlCredits.hh"
#include "../protocol/FixedQueue.hh"
#include <cassert>
#include <cstring>
#include <iostream>
using namespace cc::glob;
int main() {
    CoherenceMessage message;
    message.h.type = CoherenceMessageType::HAPresenceProbeReq;
    message.h.reqId = 0xf123456789abcdefULL;
    message.h.epoch = 0xe123456789abcdefULL;
    message.h.seqNum = 0xd123456789abcdefULL;
    message.h.homeLinePa = 0x10007c00;
    message.h.localLinePa = 0x20007c00;
    message.h.srcNode = 7; message.h.srcSocket = 1;
    message.h.dstNode = 4; message.h.dstSocket = 0;
    message.h.requesterNode = 3; message.h.targetNode = 4;
    message.h.homeNode = 7; message.h.homeSocket = 1;
    message.h.ingressSocket = 1; message.h.flags = 0x123;
    message.h.enqueueTick = 991; message.h.readyTick = 992;
    message.b.haPresenceProbeReq.action = HAProbeAction::Validate;
    message.b.haPresenceProbeReq.expectedEpoch = 0xfedcba9876543210ULL;
    ControlFrame frame(message);
    auto restored = frame.expand();
    assert(std::memcmp(&restored.h, &message.h, sizeof(message.h)) == 0);
    assert(restored.b.haPresenceProbeReq.expectedEpoch == message.b.haPresenceProbeReq.expectedEpoch);
    FixedQueue<ControlFrame> queue(12);
    for (unsigned i = 0; i < 12; ++i) assert(queue.push_back(frame));
    assert(!queue.push_back(frame));
    queue.erase(queue.begin());
    assert(queue.push_back(frame));
    message.h.type = CoherenceMessageType::ReadResp;
    message.b.readResp = UBReadRespBody{};
    message.b.readResp.authEpoch = 0xfedcba9876543210ULL;
    message.b.readResp.pendingInvMask = ~uint64_t(0);
    for (unsigned i = 0; i < 64; ++i) message.b.readResp.grantData[i] = i;
    restored = ResponseFrame(message);
    assert(restored.b.readResp.authEpoch == message.b.readResp.authEpoch);
    assert(restored.b.readResp.pendingInvMask == message.b.readResp.pendingInvMask);
    assert(std::memcmp(restored.b.readResp.grantData, message.b.readResp.grantData, 64) == 0);
    for (unsigned peers : {3u, 6u, 32u, 256u}) {
        ControlCredits<CoherenceMessageHeader> credits(peers);
        std::cout << "peers=" << peers << " input=" << peers * 4 * sizeof(ControlFrame)
                  << " reply=" << peers * 4 * (sizeof(ControlReply) + 8)
                  << " leases=" << credits.storageBytes() << '\n';
    }
    std::cout << "PASS wire=" << sizeof(CoherenceMessage)
              << " control=" << sizeof(ControlFrame) << " reply=" << sizeof(ControlReply)
              << " response=" << sizeof(ResponseFrame) << '\n';
}
