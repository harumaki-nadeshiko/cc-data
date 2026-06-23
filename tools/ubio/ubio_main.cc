/**
 * UBIOModule standalone process with synced_receive.
 * Usage: ubio_main --endpoint=ipc:///path [--bind] [--node=N]
 *
 * Tick alignment: initially learns gem5 tick from first message timestamp,
 * then uses synced_receive_lower_bound for conservative sync.
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

static CoherenceMessage makeResponse(const CoherenceMessage& coh, int nodeId)
{
    CoherenceMessage resp;
    resp.h.epoch = coh.h.epoch;
    resp.h.reqId = coh.h.reqId;
    resp.h.homeLinePa = coh.h.homeLinePa;
    resp.h.srcNode = nodeId;
    resp.h.homeNode = coh.h.homeNode;
    resp.h.requesterNode = coh.h.requesterNode;

    switch (coh.h.type) {
    case CoherenceMessageType::ReadReq:
        resp.h.type = CoherenceMessageType::ReadResp;
        resp.b.readResp.grantType = 2;
        resp.b.readResp.authEpoch = coh.h.epoch;
        resp.b.readResp.committedEpoch = coh.h.epoch;
        break;
    case CoherenceMessageType::ClearReq:
        resp.h.type = CoherenceMessageType::ClearResp;
        resp.b.clearResp.accepted = true;
        break;
    case CoherenceMessageType::WritebackReq:
        resp.h.type = CoherenceMessageType::WritebackResp;
        resp.b.writebackResp.success = true;
        break;
    case CoherenceMessageType::EvictReq:
        resp.h.type = CoherenceMessageType::EvictResp;
        resp.b.evictResp.success = true;
        break;
    default:
        resp.h.type = CoherenceMessageType::ReadResp;
        resp.b.readResp.grantType = 2;
        break;
    }
    return resp;
}

int main(int argc, char** argv)
{
    std::string ep; bool bind = false; uint64_t sw = 100000; int nid = 0;
    for (int i=1; i<argc; ++i) {
        if (!std::strncmp(argv[i],"--endpoint=",11)) ep=argv[i]+11;
        else if (!std::strcmp(argv[i],"--bind")) bind=true;
        else if (!std::strncmp(argv[i],"--node=",7)) nid=std::atoi(argv[i]+7);
        else if (!std::strncmp(argv[i],"--sync=",7)) sw=std::atoll(argv[i]+7);
    }
    if (ep.empty()) { std::fprintf(stderr,"usage: ubio_main --endpoint=ipc:///path [--bind]\n"); return 1; }

    zmq::context_t ctx(1);
    Port port("ubio", nid, 0, ep, bind, ctx, sw);
    std::fprintf(stderr,"[ubio:%d] ready ep=%s\n", nid, ep.c_str());

    uint64_t tick = 0;
    bool aligned = false;
    bool done = false;

    while (!done) {
        // Pre-alignment: use unbounded recv to catch first message
        uint64_t visible = aligned ? tick : ~0ULL;
        MemMessage* msg = port.recv(visible);
        while (msg) {
            if (msg->hdr.type == (uint32_t)MemMessageType::TERMINATE) {
                done = true; break;
            }
            if (msg->hdr.type == (uint32_t)MemMessageType::CONTROL_SYNC) {
                if (msg->hdr.timestamp > tick) tick = msg->hdr.timestamp;
                msg = port.recv(visible); continue;
            }
            if (msg->hdr.type == (uint32_t)MemMessageType::COH_MSG) {
                // Learn gem5 tick from first message
                if (!aligned && msg->hdr.timestamp > tick) {
                    tick = msg->hdr.timestamp;
                    aligned = true;
                    std::fprintf(stderr,"[ubio:%d] aligned tick=%lu\n", nid, tick);
                }

                const CoherenceMessage* coh = msg->getPayload<CoherenceMessage>();
                if (coh) {
                    std::fprintf(stderr,"[ubio:%d] tick=%lu recv %s pa=0x%lx reqId=%lu\n",
                                 nid, tick, coherenceMsgTypeName(coh->h.type),
                                 coh->h.homeLinePa, coh->h.reqId);

                    CoherenceMessage resp = makeResponse(*coh, nid);
                    MemMessage* buf = port.sendAllocateBuffer(tick);
                    if (buf) {
                        buf->hdr.type = (uint32_t)MemMessageType::COH_MSG;
                        buf->hdr.dst_module = msg->hdr.src_module;
                        buf->hdr.dst_port = msg->hdr.src_port;
                        buf->hdr.req_id = coh->h.reqId;
                        buf->setPayload(resp);
                        port.send(buf);
                    }
                }
            }
            msg = port.recv(visible);
        }

        // Once aligned, use proper emitSync + synced_receive
        if (aligned) {
            port.emitSync(tick);
            Port* ps[1] = {&port};
            uint64_t safe = synced_receive_lower_bound(ps, 1, tick);
            if (safe > tick) tick = safe; else tick++;
        } else {
            tick++;
        }
        std::this_thread::sleep_for(std::chrono::microseconds(100));
    }
    return 0;
}
