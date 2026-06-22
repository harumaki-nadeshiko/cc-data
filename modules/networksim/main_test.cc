#include "framework/PseudoMemPort.hh"
#include "framework/PseudoManager.hh"
#include "framework/PseudoMemPacket.hh"
#include "modules/networksim/NetworkSim.hh"
#include "modules/ubiomodule/CoherenceMessage.hh"

#include <cstdio>
#include <thread>
#include <chrono>

using namespace pseudo;
using namespace gem5::ruby;

static int failures = 0;

// Simulate Node0 UBIOModule — sends ReadReq through NetworkSim to Node1
void node0_side(PseudoManager* mgr)
{
    auto* port = mgr->getPort(100);  // connected to NetworkSim

    CoherenceMessage req;
    req.h.type = CoherenceMessageType::ReadReq;
    req.h.srcNode = 0;
    req.h.srcSocket = 0;
    req.h.dstNode = 1;
    req.h.dstSocket = 0;
    req.h.homeNode = 0;
    req.h.homeSocket = 0;
    req.h.ingressSocket = 0;
    req.h.requesterNode = 0;
    req.h.homeLinePa = 0xBEEF0000;
    req.h.epoch = 1;
    req.h.reqId = 100;
    req.h.seqNum = 0;

    PseudoMemPacket pkt;
    pkt.type = static_cast<uint32_t>(PacketType::CoherenceMessage);
    pkt.src_id = 100;
    pkt.dst_id = 300;  // target: Node1's port
    pkt.setPayload(req);

    port->send(pkt);
    std::printf("[Node0] sent ReadReq → dst=300\n");

    // Wait for response from NetworkSim
    PseudoMemPacket resp_pkt;
    bool ok = port->recv(resp_pkt, 5000);
    if (!ok) {
        std::fprintf(stderr, "FAIL: Node0 timeout\n");
        failures++; return;
    }

    const CoherenceMessage* resp = resp_pkt.getPayload<CoherenceMessage>();
    if (resp && resp->h.type == CoherenceMessageType::ReadResp) {
        std::printf("[Node0] got ReadResp: grant=%d epoch=%lu reqId=%lu\n",
                    resp->b.readResp.grantType, resp->h.epoch, resp->h.reqId);
    } else {
        std::fprintf(stderr, "FAIL: Node0 got wrong response\n");
        failures++;
    }
}

// Simulate Node1 UBIOModule — receives ReadReq from NetworkSim, sends response
void node1_side(PseudoManager* mgr)
{
    auto* port = mgr->getPort(300);

    PseudoMemPacket pkt;
    bool ok = port->recv(pkt, 5000);
    if (!ok) {
        std::fprintf(stderr, "FAIL: Node1 timeout\n");
        failures++; return;
    }

    const CoherenceMessage* req = pkt.getPayload<CoherenceMessage>();
    if (!req || req->h.type != CoherenceMessageType::ReadReq) {
        std::fprintf(stderr, "FAIL: Node1 wrong msg type\n");
        failures++; return;
    }
    std::printf("[Node1] got ReadReq: pa=0x%lx reqId=%lu\n",
                req->h.homeLinePa, req->h.reqId);

    // Send response back through NetworkSim
    CoherenceMessage resp;
    resp.h.type = CoherenceMessageType::ReadResp;
    resp.h.srcNode = 1;
    resp.h.dstNode = 0;
    resp.h.homeNode = req->h.homeNode;
    resp.h.requesterNode = req->h.requesterNode;
    resp.h.homeLinePa = req->h.homeLinePa;
    resp.h.epoch = req->h.epoch;
    resp.h.reqId = req->h.reqId;
    resp.b.readResp.grantType = 2;

    PseudoMemPacket resp_pkt;
    resp_pkt.type = static_cast<uint32_t>(PacketType::CoherenceMessage);
    resp_pkt.src_id = 300;
    resp_pkt.dst_id = 100;  // back to Node0
    resp_pkt.setPayload(resp);

    port->send(resp_pkt);
    std::printf("[Node1] sent ReadResp → dst=100\n");
}

int main()
{
    PseudoManager mgr;

    // Create ports for NetworkSim
    mgr.createPort(100);  // Node0 side
    mgr.createPort(200);  // spare
    mgr.createPort(300);  // Node1 side

    NetworkSim netsim(&mgr);
    netsim.configureFullMesh({100, 200, 300}, 1);

    // Start NetworkSim in a thread
    std::thread net_thread([&netsim]() {
        netsim.run(100);  // run for up to 100 steps
    });

    // Start node threads
    std::thread n1(node1_side, &mgr);
    std::this_thread::sleep_for(std::chrono::milliseconds(10));
    std::thread n0(node0_side, &mgr);

    n0.join();
    n1.join();
    netsim.requestStop();
    net_thread.join();

    if (failures == 0) {
        std::printf("PASS: CoherenceMessage through NetworkSim\n");
        return 0;
    }
    std::fprintf(stderr, "FAIL: %d failures\n", failures);
    return 1;
}
