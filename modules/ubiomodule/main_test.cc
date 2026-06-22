#include "modules/ubiomodule/gem5_shim.hh"
#include "framework/PseudoMemPort.hh"
#include "framework/PseudoManager.hh"
#include "modules/ubiomodule/CoherenceMessage.hh"

#include <cstdio>
#include <cstring>
#include <thread>
#include <chrono>

using namespace pseudo;
using namespace gem5::ruby;

// Minimal standalone test: create two PseudoMemPorts, simulate
// a gem5 UBAdapter and UBIOModule exchanging a ReadReq/ReadResp.

static int failures = 0;

void gem5_side(PseudoManager* mgr)
{
    auto* port = mgr->getPort(100);

    // Construct ReadReq
    CoherenceMessage req;
    req.h.type = CoherenceMessageType::ReadReq;
    req.h.srcNode = 0;
    req.h.srcSocket = 0;
    req.h.dstNode = 0;
    req.h.dstSocket = 0;
    req.h.homeNode = 0;
    req.h.homeSocket = 0;
    req.h.ingressSocket = 0;
    req.h.requesterNode = 2;
    req.h.homeLinePa = 0xCAFE0000;
    req.h.epoch = 42;
    req.h.reqId = 1;
    req.h.seqNum = 0;
    req.b.readReq.neededPerm = 1;

    PseudoMemPacket pkt;
    pkt.type = static_cast<uint32_t>(PacketType::CoherenceMessage);
    pkt.src_id = 100;
    pkt.dst_id = 200;
    pkt.setPayload(req);

    port->send(pkt);
    std::printf("[gem5] sent ReadReq\n");

    // Wait for ReadResp
    PseudoMemPacket resp_pkt;
    bool ok = port->recv(resp_pkt, 5000);
    if (!ok) {
        std::fprintf(stderr, "FAIL: gem5 timeout waiting for ReadResp\n");
        failures++;
        return;
    }

    const CoherenceMessage* resp = resp_pkt.getPayload<CoherenceMessage>();
    if (!resp || resp->h.type != CoherenceMessageType::ReadResp) {
        std::fprintf(stderr, "FAIL: gem5 got wrong response type\n");
        failures++;
        return;
    }

    std::printf("[gem5] got ReadResp: grantType=%d epoch=%lu\n",
                resp->b.readResp.grantType, resp->h.epoch);
}

void ubiomodule_side(PseudoManager* mgr)
{
    auto* port = mgr->getPort(200);
    if (!port) {
        std::fprintf(stderr, "FAIL: ubiomodule port not found\n");
        failures++;
        return;
    }

    // Blocking recv
    PseudoMemPacket pkt;
    bool ok = port->recv(pkt, 5000);
    if (!ok) {
        std::fprintf(stderr, "FAIL: ubiomodule timeout waiting for ReadReq\n");
        failures++;
        return;
    }

    const CoherenceMessage* req = pkt.getPayload<CoherenceMessage>();
    if (!req || req->h.type != CoherenceMessageType::ReadReq) {
        std::fprintf(stderr, "FAIL: ubiomodule got wrong request type\n");
        failures++;
        return;
    }

    std::printf("[ubiomodule] got ReadReq: pa=0x%lx epoch=%lu reqId=%lu\n",
                req->h.homeLinePa, req->h.epoch, req->h.reqId);

    // Simulate UBCC processing and sending ReadResp
    CoherenceMessage resp;
    resp.h.type = CoherenceMessageType::ReadResp;
    resp.h.srcNode = req->h.dstNode;
    resp.h.dstNode = req->h.srcNode;
    resp.h.dstSocket = req->h.srcSocket;
    resp.h.homeNode = req->h.homeNode;
    resp.h.homeSocket = req->h.homeSocket;
    resp.h.ingressSocket = req->h.ingressSocket;
    resp.h.requesterNode = req->h.requesterNode;
    resp.h.homeLinePa = req->h.homeLinePa;
    resp.h.epoch = req->h.epoch;
    resp.h.reqId = req->h.reqId;
    resp.b.readResp.grantType = 2;  // GlobalGrantModified

    PseudoMemPacket resp_pkt;
    resp_pkt.type = static_cast<uint32_t>(PacketType::CoherenceMessage);
    resp_pkt.src_id = pkt.dst_id;
    resp_pkt.dst_id = pkt.src_id;
    resp_pkt.setPayload(resp);

    auto* gem5_port = mgr->getPort(100);
    gem5_port->send(resp_pkt);
    std::printf("[ubiomodule] sent ReadResp\n");
}

int main()
{
    PseudoManager mgr;

    // Create ports BEFORE spawning threads
    auto* gem5_port = mgr.createPort(100);
    auto* ubiomodule_port = mgr.createPort(200);
    mgr.connect(100, 200);

    std::thread ubiomodule_thread(ubiomodule_side, &mgr);
    gem5_side(&mgr);
    ubiomodule_thread.join();

    if (failures == 0) {
        std::printf("PASS: CoherenceMessage round-trip through PseudoMemPort\n");
        return 0;
    }
    std::fprintf(stderr, "FAIL: %d failures\n", failures);
    return 1;
}
