#include "framework/ZMQTransport.hh"
#include "framework/PseudoMemPacket.hh"
#include "modules/ubiomodule/CoherenceMessage.hh"

#include <cstdio>
#include <cstring>
#include <thread>
#include <zmq.hpp>

using namespace pseudo;
using namespace gem5::ruby;

static int failures = 0;

void server_side(zmq::context_t* ctx)
{
    ZMQTransport transport;
    if (!transport.init(ctx, "tcp://127.0.0.1:5555", true)) {
        std::fprintf(stderr, "FAIL: server init\n");
        failures++; return;
    }
    std::printf("[server] bound to tcp://127.0.0.1:5555\n");

    // Blocking recv
    PseudoMemPacket pkt;
    bool ok = transport.recv(pkt);
    if (!ok) {
        std::fprintf(stderr, "FAIL: server recv\n");
        failures++; return;
    }

    const CoherenceMessage* req = pkt.getPayload<CoherenceMessage>();
    if (req && req->h.type == CoherenceMessageType::ReadReq) {
        std::printf("[server] got ReadReq: pa=0x%lx\n", req->h.homeLinePa);

        // Send response
        CoherenceMessage resp;
        resp.h.type = CoherenceMessageType::ReadResp;
        resp.h.epoch = req->h.epoch;
        resp.h.reqId = req->h.reqId;
        resp.b.readResp.grantType = 2;

        PseudoMemPacket resp_pkt;
        resp_pkt.src_id = 200;
        resp_pkt.dst_id = 100;
        resp_pkt.setPayload(resp);
        transport.send(resp_pkt);
        std::printf("[server] sent ReadResp\n");
    }

    transport.shutdown();
}

int main()
{
    zmq::context_t ctx(1);

    std::thread server(server_side, &ctx);
    std::this_thread::sleep_for(std::chrono::milliseconds(100));

    // Client side
    ZMQTransport client;
    if (!client.init(&ctx, "tcp://127.0.0.1:5555", false)) {
        std::fprintf(stderr, "FAIL: client init\n");
        failures++;
    } else {
        std::printf("[client] connected\n");

        CoherenceMessage req;
        req.h.type = CoherenceMessageType::ReadReq;
        req.h.homeLinePa = 0xDEAD0000;
        req.h.epoch = 99;
        req.h.reqId = 42;

        PseudoMemPacket pkt;
        pkt.src_id = 100;
        pkt.dst_id = 200;
        pkt.setPayload(req);
        client.send(pkt);
        std::printf("[client] sent ReadReq\n");

        PseudoMemPacket resp;
        if (client.recv(resp, 5000)) {
            const CoherenceMessage* r = resp.getPayload<CoherenceMessage>();
            if (r && r->h.type == CoherenceMessageType::ReadResp) {
                std::printf("[client] got ReadResp: grant=%d\n",
                            r->b.readResp.grantType);
            }
        } else {
            std::fprintf(stderr, "FAIL: client recv timeout\n");
            failures++;
        }

        client.shutdown();
    }

    server.join();

    if (failures == 0) {
        std::printf("PASS: CoherenceMessage through ZeroMQ\n");
        return 0;
    }
    return 1;
}
