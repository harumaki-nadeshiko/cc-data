// BarrierManager — standalone cross-node barrier coordinator
// Receives BARRIER_REACHED from each node's UBAdapter barrier Port.
// When all nodes in a mask have arrived, broadcasts BARRIER_RELEASE.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>
#include <vector>

#include "framework/Port.hh"
#include "modules/ubiomodule/CoherenceMessage.hh"

using namespace framework;
using cc::glob::CoherenceMessage;
using cc::glob::CoherenceMessageType;

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

    std::vector<std::unique_ptr<Port>> ports;
    for (int n = 0; n < numNodes; n++) {
        framework::PortParams pp = framework::PortEnvLoader::barrierPort(n);
        auto p = std::make_unique<Port>();
        if (!p->init(pp)) {
            std::fprintf(stderr, "barrier port init failed n=%d\n", n);
            return 1;
        }
        ports.push_back(std::move(p));
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
                // CONTROL_SYNC arrives as an ordinary kMessage; skip it so it
                // does not count as barrier activity.
                if (m->hdr.type == static_cast<uint32_t>(MemMessageType::CONTROL_SYNC)) {
                    m = p->recv(tick, &st);
                    continue;
                }
                any = true;
                // Barrier control is now a PAYLOAD CoherenceMessage (BarrierReached).
                const CoherenceMessage *bc =
                    (m->hdr.type == static_cast<uint32_t>(MemMessageType::PAYLOAD))
                        ? m->getPayload<CoherenceMessage>() : nullptr;
                if (bc && bc->h.type == CoherenceMessageType::BarrierReached) {
                    uint32_t mask = bc->b.barrier.mask;
                    uint32_t nodeId = bc->h.srcNode;
                    std::printf("[BarrierManager] ARRIVED node=%u mask=0x%x\n", nodeId, mask);

                    auto &bs = barriers[mask];
                    bs.mask = mask;
                    bs.arrived.insert(nodeId);

                    // Count expected nodes: popcount(mask)
                    uint32_t v = mask, expected = 0;
                    while (v) { expected++; v &= (v - 1); }

                    if (bs.arrived.size() >= expected) {
                        std::printf("[BarrierManager] RELEASE mask=0x%x\n", mask);
                        // Broadcast BarrierRelease to all nodes in the mask
                        for (uint32_t ni = 0; ni < (uint32_t)numNodes; ni++) {
                            if (mask & (1u << ni)) {
                                framework::MemMessage* buf = ports[ni]->allocateSendBuffer(tick);
                                if (buf) {
                                    buf->hdr.type = static_cast<uint32_t>(MemMessageType::PAYLOAD);
                                    buf->hdr.sourceId = 0;
                                    buf->hdr.targetId = ni;
                                    CoherenceMessage rmsg;
                                    rmsg.h.type = CoherenceMessageType::BarrierRelease;
                                    rmsg.b.barrier.mask = mask;
                                    buf->setPayload(rmsg);
                                    ports[ni]->send(buf);
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
