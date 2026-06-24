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

    // A -> B: COH_MSG
    MemMessage* buf = port_a.sendAllocateBuffer(50);
    if (!buf) { failures++; std::fprintf(stderr, "FAIL: alloc\n"); }
    else {
        buf->hdr.type = static_cast<uint32_t>(MemMessageType::COH_MSG);
        buf->hdr.dst_module=20; buf->hdr.dst_port=2; buf->hdr.req_id=42;
        if (!port_a.send(buf)) { failures++; std::fprintf(stderr, "FAIL: send\n"); }
        else std::printf("[A] sent COH_MSG\n");
    }

    // B recv (new three-state API)
    ReceiveStatus st;
    for (int r=0; r<200; ++r) {
        MemMessage* m = port_b.recv(10000, &st);
        if (m && st == ReceiveStatus::kMessage &&
            m->hdr.type == static_cast<uint32_t>(MemMessageType::COH_MSG)) {
            std::printf("[B] got COH_MSG req_id=%lu\n", m->hdr.req_id);
            MemMessage* rb = port_b.sendAllocateBuffer(100);
            if (rb) {
                rb->hdr.type=static_cast<uint32_t>(MemMessageType::COH_MSG);
                rb->hdr.dst_module=10; rb->hdr.dst_port=1; rb->hdr.req_id=42;
                port_b.send(rb); std::printf("[B] sent response\n");
            }
            break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    // A recv response
    for (int r=0; r<200; ++r) {
        MemMessage* m = port_a.recv(10000, &st);
        if (m && st == ReceiveStatus::kMessage && m->hdr.req_id==42) {
            std::printf("[A] got response\n"); break;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(10));
    }

    // sync test
    port_a.emitSync(2000); port_b.emitSync(2000);
    Port* ps[2]={&port_a, &port_b};
    uint64_t safe = synced_receive_lower_bound(ps, 2, 0);
    std::printf("[sync] safeTick=%lu\n", safe);

    // future message test: B sends msg with future timestamp
    buf = port_b.sendAllocateBuffer(5000);
    if (buf) {
        buf->hdr.type = static_cast<uint32_t>(MemMessageType::COH_MSG);
        buf->hdr.dst_module=10; buf->hdr.dst_port=1; buf->hdr.req_id=99;
        buf->hdr.timestamp=5000;
        buf->hdr.size = sizeof(MemMessageHeader);
        if (!port_b.send(buf)) { failures++; std::fprintf(stderr, "FAIL: future send\n"); }
        else std::printf("[B] sent future msg (t=5000)\n");
    }

    std::this_thread::sleep_for(std::chrono::milliseconds(10));

    // A recv at t=1000 — sync may arrive first, drain it
    MemMessage* fm = nullptr;
    for (int r=0; r<10; ++r) {
        fm = port_a.recv(1000, &st);
        if (!fm) break;
        if (st == ReceiveStatus::kSync) {
            std::printf("[A] drained sync\n");
            continue;
        }
        break;
    }
    if (st != ReceiveStatus::kPendingFuture) {
        failures++; std::fprintf(stderr, "FAIL: expected kPendingFuture, got %d\n", (int)st);
    } else {
        std::printf("[A] future msg pending (st=PendingFuture)\n");
    }

    // A tries recv at t=6000 -> should get the message
    for (int r=0; r<10; ++r) {
        fm = port_a.recv(6000, &st);
        if (!fm) break;
        if (st == ReceiveStatus::kSync) { std::printf("[A] drained sync\n"); continue; }
        break;
    }
    if (!fm || st != ReceiveStatus::kMessage || fm->hdr.req_id != 99) {
        failures++;
        std::fprintf(stderr, "FAIL: expected future msg at t=6000, req=99, st=%d\n", (int)st);
    } else {
        std::printf("[A] got future msg req_id=%lu\n", fm->hdr.req_id);
    }

    // safeTs test
    port_a.emitSync(2000);
    uint64_t s = port_a.safeTs(2000);
    std::printf("[safeTs] port_a safeTs(2000)=%lu\n", s);

    if (!failures) { std::printf("PASS: Port sync smoke\n"); return 0; }
    std::fprintf(stderr, "FAIL: %d\n", failures); return 1;
}
