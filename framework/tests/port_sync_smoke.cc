#include "framework/Port.hh"
#include "framework/MemMessage.hh"
#include <cstdio>
#include <thread>
#include <chrono>
using namespace framework;
static int failures = 0;

int main()
{
    zmq::context_t ctx(1);

    // One side binds, other connects
    Port port_a("portA", 10, 1, "ipc:///tmp/test_sync_pair", true,  ctx, 1000);
    Port port_b("portB", 20, 2, "ipc:///tmp/test_sync_pair", false, ctx, 1000);

    // A → B: COH_MSG
    MemMessage* buf = port_a.sendAllocateBuffer(50);
    if (!buf) { failures++; std::fprintf(stderr, "FAIL: alloc\n"); }
    else {
        buf->hdr.type = static_cast<uint32_t>(MemMessageType::COH_MSG);
        buf->hdr.dst_module=20; buf->hdr.dst_port=2; buf->hdr.req_id=42;
        if (!port_a.send(buf)) { failures++; std::fprintf(stderr, "FAIL: send\n"); }
        else std::printf("[A] sent COH_MSG\n");
    }

    // B recv
    for (int r=0; r<200; ++r) {
        MemMessage* m = port_b.recv(10000);
        if (m && m->hdr.type == (uint32_t)MemMessageType::COH_MSG) {
            std::printf("[B] got COH_MSG req_id=%lu\n", m->hdr.req_id);
            MemMessage* rb = port_b.sendAllocateBuffer(100);
            if (rb) {
                rb->hdr.type=(uint32_t)MemMessageType::COH_MSG;
                rb->hdr.dst_module=10; rb->hdr.dst_port=1; rb->hdr.req_id=42;
                port_b.send(rb); std::printf("[B] sent response\n");
            }
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    // A recv response
    for (int r=0; r<200; ++r) {
        MemMessage* m = port_a.recv(10000);
        if (m && m->hdr.req_id==42) { std::printf("[A] got response\n"); break; }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    // sync test
    port_a.emitSync(2000); port_b.emitSync(2000);
    Port* ps[2]={&port_a, &port_b};
    uint64_t safe = synced_receive_lower_bound(ps, 2, 0);
    std::printf("[sync] safeTick=%lu\n", safe);

    if (!failures) { std::printf("PASS: Port sync smoke\n"); return 0; }
    std::fprintf(stderr, "FAIL: %d\n", failures); return 1;
}
