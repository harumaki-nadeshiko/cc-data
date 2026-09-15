#include "../protocol/CoherenceMessage.hh"
#include "../protocol/ControlCredits.hh"
#include <cassert>
#include <iostream>
using namespace cc::glob;
int main() {
    ControlCredits<CoherenceMessageHeader, 2> sender, receiver;
    CoherenceMessageHeader requests[5];
    for (unsigned i = 0; i < 5; ++i) {
        auto &h = requests[i];
        h.type = CoherenceMessageType::InvalidateReq;
        h.srcNode = 0; h.dstNode = 1;
        h.srcSocket = h.dstSocket = 0;
        h.reqId = i + 1; h.epoch = 7; h.homeLinePa = i * 64;
        assert(sender.acquire(1, h) == (i < 4));
        if (i < 4) assert(receiver.receive(0, h) == decltype(receiver)::Receipt::New);
    }
    auto duplicate = requests[0];
    assert(sender.acquire(1, duplicate));
    assert(duplicate.seqNum == requests[0].seqNum);
    assert(receiver.receive(0, duplicate) == decltype(receiver)::Receipt::Duplicate);
    CoherenceMessageHeader ack = requests[0];
    ack.type = CoherenceMessageType::InvalidateAck;
    ack.srcNode = 1; ack.dstNode = 0;
    assert(receiver.complete(0, ack.seqNum));
    assert(sender.release(1, ack, static_cast<unsigned>(CoherenceMessageType::InvalidateReq)));
    assert(!sender.release(1, ack, static_cast<unsigned>(CoherenceMessageType::InvalidateReq)));
    assert(sender.acquire(1, requests[4]));
    assert(receiver.receive(0, requests[4]) == decltype(receiver)::Receipt::New);
    assert(receiver.receive(0, requests[0]) == decltype(receiver)::Receipt::Stale);
    assert(!sender.release(1, ack, static_cast<unsigned>(CoherenceMessageType::InvalidateReq)));
    assert(sender.occupancy(1) == 4);
    std::cout << "PASS four control leases; fifth backpressured; exact ACK reopens; duplicate/stale ACK no inflation\n";
}
