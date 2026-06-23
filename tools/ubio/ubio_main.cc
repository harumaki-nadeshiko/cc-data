/**
 * UBIOModule standalone process.
 * Usage: ubio_main --endpoint=ipc:///path [--bind] [--node=N]
 */
#include "framework/Port.hh"
#include "framework/MemMessage.hh"
#include "modules/ubiomodule/CoherenceMessage.hh"
#include "modules/ubiomodule/gem5_shim.hh"

#include <cstdio>
#include <cstring>
#include <thread>
#include <chrono>
#include <zmq.hpp>

using namespace framework;
using namespace gem5::ruby;

int main(int argc, char** argv)
{
    std::string endpoint;
    bool bind = false;
    uint64_t syncWindow = 100000;
    int node_id = 0;

    for (int i = 1; i < argc; ++i) {
        if (std::strncmp(argv[i], "--endpoint=", 11) == 0)
            endpoint = argv[i] + 11;
        else if (std::strcmp(argv[i], "--bind") == 0)
            bind = true;
        else if (std::strncmp(argv[i], "--node=", 7) == 0)
            node_id = std::atoi(argv[i] + 7);
        else if (std::strncmp(argv[i], "--sync=", 7) == 0)
            syncWindow = std::atoll(argv[i] + 7);
    }

    if (endpoint.empty()) {
        std::fprintf(stderr, "usage: ubio_main --endpoint=ipc:///path [--bind] [--node=N]\n");
        return 1;
    }

    std::fprintf(stderr, "[ubio:%d] endpoint=%s bind=%d sync=%lu\n",
                 node_id, endpoint.c_str(), bind, syncWindow);

    zmq::context_t ctx(1);
    Port port("ubio", node_id, 0, endpoint, bind, ctx, syncWindow);
    std::fprintf(stderr, "[ubio:%d] port ready, entering loop\n", node_id);

    uint64_t tick = 0;
    bool done = false;

    while (!done) {
        port.emitSync(tick);
        MemMessage* msg = port.recv(~0ULL);  // accept all timestamps
        if (msg) {
            if (msg->hdr.type == (uint32_t)MemMessageType::TERMINATE) {
                std::fprintf(stderr, "[ubio:%d] got TERMINATE, exiting\n", node_id);
                done = true; break;
            }
            if (msg->hdr.type == (uint32_t)MemMessageType::COH_MSG) {
                const CoherenceMessage* coh = msg->getPayload<CoherenceMessage>();
                if (coh) {
                    std::fprintf(stderr, "[ubio:%d] recv ReadReq pa=0x%lx reqId=%lu\n",
                                 node_id, coh->h.homeLinePa, coh->h.reqId);

                    MemMessage* buf = port.sendAllocateBuffer(tick);
                    if (buf) {
                        buf->hdr.type = (uint32_t)MemMessageType::COH_MSG;
                        buf->hdr.dst_module = msg->hdr.src_module;
                        buf->hdr.dst_port = msg->hdr.src_port;
                        buf->hdr.req_id = coh->h.reqId;

                        CoherenceMessage resp;
                        resp.h.type = CoherenceMessageType::ReadResp;
                        resp.h.epoch = coh->h.epoch;
                        resp.h.reqId = coh->h.reqId;
                        resp.h.homeLinePa = coh->h.homeLinePa;
                        resp.b.readResp.grantType = 2;
                        resp.b.readResp.authEpoch = coh->h.epoch;
                        resp.b.readResp.committedEpoch = coh->h.epoch;
                        buf->setPayload(resp);
                        port.send(buf);
                        std::fprintf(stderr, "[ubio:%d] sent ReadResp grant=2\n", node_id);
                    }
                }
            }
        }
        tick++;
        if (tick % 100 == 0)
            std::this_thread::sleep_for(std::chrono::microseconds(100));
    }
    std::fprintf(stderr, "[ubio:%d] done\n", node_id);
    return 0;
}
