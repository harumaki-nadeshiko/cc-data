#include "framework/Port.hh"
#include "framework/MemMessage.hh"
#include "modules/ubiomodule/CoherenceMessage.hh"
#include <cstdio>
#include <unistd.h>
int main() {
    zmq::context_t ctx(1);
    framework::Port p("peer", 99, 0, "ipc:///tmp/int_final", false, ctx, 1000);
    gem5::ruby::CoherenceMessage req;
    req.h.type = gem5::ruby::CoherenceMessageType::ReadReq;
    req.h.homeLinePa = 0xBEEF; req.h.epoch=1; req.h.reqId=99;
    req.h.srcNode=99; req.h.dstNode=0; req.h.homeNode=0;
    framework::MemMessage* buf = p.sendAllocateBuffer(0);
    buf->hdr.type = (uint32_t)framework::MemMessageType::COH_MSG;
    buf->hdr.dst_module=0; buf->hdr.dst_port=0; buf->hdr.req_id=99;
    buf->setPayload(req);
    if (!p.send(buf)) { printf("PEER: send FAIL\n"); return 1; }
    printf("PEER: sent ReadReq\n");
    for (int i=0; i<200; ++i) {
        framework::MemMessage* m = p.recv(100000);
        if (m && m->hdr.req_id==99) {
            printf("PEER: GOT RESPONSE\n"); return 0;
        }
        usleep(10000);
    }
    printf("PEER: timeout\n"); return 1;
}
