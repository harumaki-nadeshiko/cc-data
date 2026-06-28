// BarrierManager — standalone cross-node barrier coordinator
// Receives BARRIER_REACHED from each node's UBAdapter barrier Port.
// When all nodes in a mask have arrived, broadcasts BARRIER_RELEASE.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>
#include <vector>
#include <zmq.hpp>

#include "framework/Port.hh"

using namespace framework;

struct BarrierState {
    uint32_t mask;
    std::set<uint32_t> arrived;  // nodes that have reported arrival
};

int main(int argc, char **argv) {
    if (argc < 2) {
        std::fprintf(stderr, "usage: barriermanager <num_nodes>\n");
        return 1;
    }
    int numNodes = std::atoi(argv[1]);
    if (numNodes < 1 || numNodes > 32) {
        std::fprintf(stderr, "num_nodes must be 1-32\n");
        return 1;
    }

    zmq::context_t ctx(1);
    std::vector<std::unique_ptr<Port>> ports;
    for (int n = 0; n < numNodes; n++) {
        std::string ep = "ipc:///tmp/barrier_m" + std::to_string(n) + "_p1";
        ports.push_back(std::make_unique<Port>(
            "barrier_n" + std::to_string(n), n, 1, ep, true, ctx));
    }

    std::map<uint32_t, BarrierState> barriers;
    uint64_t tick = 0;

    std::printf("[BarrierManager] listening on %d nodes\n", numNodes);

    while (true) {
        for (auto &p : ports) p->emitSync(tick);

        bool any = false;
        for (auto &p : ports) {
            ReceiveStatus st;
            MemMessage *m = p->recv(tick, &st);
            while (m && st == ReceiveStatus::kMessage) {
                any = true;
                if (m->hdr.type == static_cast<uint32_t>(MemMessageType::BARRIER_REACHED)) {
                    uint32_t mask = static_cast<uint32_t>(m->hdr.req_id);
                    uint32_t nodeId = m->hdr.src_module;
                    std::printf("[BarrierManager] ARRIVED node=%u mask=0x%x\n", nodeId, mask);

                    auto &bs = barriers[mask];
                    bs.mask = mask;
                    bs.arrived.insert(nodeId);

                    // Count expected nodes: popcount(mask)
                    uint32_t v = mask, expected = 0;
                    while (v) { expected++; v &= (v - 1); }

                    if (bs.arrived.size() >= expected) {
                        std::printf("[BarrierManager] RELEASE mask=0x%x\n", mask);
                        // Broadcast BARRIER_RELEASE to all nodes in the mask
                        for (uint32_t ni = 0; ni < (uint32_t)numNodes; ni++) {
                            if (mask & (1u << ni)) {
                                MemMessage *rel = p->sendAllocateBuffer(tick);
                                if (rel) {
                                    rel->hdr.type = static_cast<uint32_t>(MemMessageType::BARRIER_RELEASE);
                                    rel->hdr.req_id = mask;
                                    rel->hdr.src_module = 0;
                                    rel->hdr.dst_module = ni;
                                    rel->hdr.size = sizeof(MemMessageHeader);
                                    ports[ni]->send(rel);
                                }
                            }
                        }
                        bs.arrived.clear();
                    }
                }
                m = p->recv(tick, &st);
            }
        }
        if (!any) tick++;
        else {
            uint64_t minTs = ports[0]->safeTs(tick);
            for (auto &p : ports) { uint64_t ts = p->safeTs(tick); if (ts < minTs) minTs = ts; }
            tick = (minTs > tick) ? minTs : tick + 1;
        }
    }
    return 0;
}
