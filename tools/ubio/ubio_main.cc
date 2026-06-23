/**
 * UBIOModule standalone with 2 Ports (gem5 + network).
 * Usage: ubio_main --gem5-ep=ipc:///path --net-ep=ipc:///path --node=N
 */
#include "framework/Port.hh"
#include "framework/MemMessage.hh"
#include "modules/ubiomodule/CoherenceMessage.hh"

#include <cstdio>
#include <cstring>
#include <thread>
#include <chrono>
#include <zmq.hpp>

using namespace framework;
using namespace gem5::ruby;

static CoherenceMessage makeResponse(const CoherenceMessage& coh, int nid) {
    CoherenceMessage r;
    r.h.epoch=coh.h.epoch; r.h.reqId=coh.h.reqId;
    r.h.homeLinePa=coh.h.homeLinePa; r.h.srcNode=nid;
    r.h.homeNode=coh.h.homeNode; r.h.requesterNode=coh.h.requesterNode;
    switch(coh.h.type){
    case CoherenceMessageType::ReadReq:
        r.h.type=CoherenceMessageType::ReadResp; r.b.readResp.grantType=2; break;
    case CoherenceMessageType::ClearReq:
        r.h.type=CoherenceMessageType::ClearResp; r.b.clearResp.accepted=true; break;
    case CoherenceMessageType::WritebackReq:
        r.h.type=CoherenceMessageType::WritebackResp; r.b.writebackResp.success=true; break;
    case CoherenceMessageType::EvictReq:
        r.h.type=CoherenceMessageType::EvictResp; r.b.evictResp.success=true; break;
    default: r.h.type=CoherenceMessageType::ReadResp; r.b.readResp.grantType=2; break;
    }
    return r;
}

int main(int argc, char** argv) {
    std::string gem5Ep, netEp;
    int nid=0; uint64_t sw=100000; bool netBind=false, gem5Bind=false;

    for(int i=1; i<argc; ++i) {
        if(!std::strncmp(argv[i],"--gem5-ep=",10)) gem5Ep=argv[i]+10;
        else if(!std::strncmp(argv[i],"--net-ep=",9)) netEp=argv[i]+9;
        else if(!std::strncmp(argv[i],"--node=",7)) nid=std::atoi(argv[i]+7);
        else if(!std::strncmp(argv[i],"--sync=",7)) sw=std::atoll(argv[i]+7);
        else if(!std::strcmp(argv[i],"--gem5-bind")) gem5Bind=true;
        else if(!std::strcmp(argv[i],"--net-bind")) netBind=true;
    }
    if(gem5Ep.empty()){ std::fprintf(stderr,"need --gem5-ep\n"); return 1; }

    zmq::context_t ctx(1);
    Port* gem5Port = netEp.empty() ? nullptr :
        new Port("gem5",nid,0,gem5Ep,gem5Bind,ctx,sw);
    Port* netPort = netEp.empty() ? nullptr :
        new Port("net",nid,1,netEp,netBind,ctx,sw);

    std::fprintf(stderr,"[ubio:%d] gem5=%s net=%s\n",nid,gem5Ep.c_str(),netEp.c_str());

    uint64_t tick=0; bool aligned=false, done=false;
    while(!done){
        uint64_t vis = aligned ? tick : ~0ULL;

        // Process gem5 port
        if(gem5Port){
            MemMessage* m = gem5Port->recv(vis);
            while(m){
                if(m->hdr.type==(uint32_t)MemMessageType::TERMINATE){done=true;break;}
                if(m->hdr.type==(uint32_t)MemMessageType::COH_MSG){
                    auto* coh = m->getPayload<CoherenceMessage>();
                    if(coh){
                        if(!aligned && m->hdr.timestamp>tick){
                            tick=m->hdr.timestamp; aligned=true;
                            std::fprintf(stderr,"[ubio:%d] aligned tick=%lu\n",nid,tick);
                        }
                        std::fprintf(stderr,"[ubio:%d] gem5 recv %s reqId=%lu\n",
                                     nid,coherenceMsgTypeName(coh->h.type),coh->h.reqId);

                        // If cross-node (dstNode != nid) and we have net port, forward
                        if(netPort && coh->h.dstNode != nid && coh->h.dstNode >= 0){
                            MemMessage* fwd = gem5Port->sendAllocateBuffer(tick);
                            if(fwd){
                                *fwd = *m;
                                fwd->hdr.src_module=nid; fwd->hdr.dst_module=coh->h.dstNode;
                                netPort->send(fwd);
                                std::fprintf(stderr,"[ubio:%d] forwarded to node %d\n",
                                             nid, coh->h.dstNode);
                            }
                        } else {
                            auto resp = makeResponse(*coh, nid);
                            MemMessage* buf = gem5Port->sendAllocateBuffer(tick);
                            if(buf){
                                buf->hdr.type=(uint32_t)MemMessageType::COH_MSG;
                                buf->hdr.dst_module=m->hdr.src_module;
                                buf->hdr.dst_port=m->hdr.src_port;
                                buf->hdr.req_id=coh->h.reqId;
                                buf->setPayload(resp);
                                gem5Port->send(buf);
                            }
                        }
                    }
                }
                m = gem5Port->recv(vis);
            }
        }

        // Process network port (incoming from other ubios)
        if(netPort){
            MemMessage* m = netPort->recv(vis);
            while(m){
                if(m->hdr.type==(uint32_t)MemMessageType::COH_MSG){
                    auto* coh = m->getPayload<CoherenceMessage>();
                    if(coh && coh->h.dstNode==nid){
                        std::fprintf(stderr,"[ubio:%d] net recv %s from node %d\n",
                                     nid,coherenceMsgTypeName(coh->h.type),m->hdr.src_module);
                        auto resp = makeResponse(*coh, nid);
                        MemMessage* buf = netPort->sendAllocateBuffer(tick);
                        if(buf){
                            buf->hdr.type=(uint32_t)MemMessageType::COH_MSG;
                            buf->hdr.dst_module=m->hdr.src_module;
                            buf->hdr.dst_port=m->hdr.src_port;
                            buf->hdr.req_id=coh->h.reqId;
                            buf->setPayload(resp);
                            netPort->send(buf);
                        }
                    }
                }
                m = netPort->recv(vis);
            }
        }

        if(aligned){ if(gem5Port) gem5Port->emitSync(tick); tick++; }
        else tick++;
        std::this_thread::sleep_for(std::chrono::microseconds(100));
    }
    return 0;
}
