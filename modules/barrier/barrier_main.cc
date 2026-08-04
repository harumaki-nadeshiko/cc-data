// BarrierManager — standalone cross-node barrier coordinator
// Receives BARRIER_REACHED from each node's UBAdapter barrier Port.
// When all nodes in a mask have arrived, broadcasts BARRIER_RELEASE.
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <set>
#include <thread>
#include <vector>

#include "framework/iface/Message.hh"
#include "framework/iface/Port.hh"
#include "framework/iface/Log.hh"
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
        LogError("BarrierManager", "usage: barriermanager <num_nodes>");
        return 1;
    }
    int numNodes = std::atoi(argv[1]);
    if (numNodes < 1 || numNodes > 32) {
        LogError("BarrierManager", "num_nodes must be 1-32");
        return 1;
    }

    std::vector<Port *> ports;
    for (int n = 0; n < numNodes; n++) {
        PortConfig config;
        config.selfRole = "barrier";
        config.peerRole = "gem5";
        config.channelName = "barrier";
        config.nodeId = n;
        config.socketId = 0;
        config.numNodes = numNodes;
        config.numSockets = 1;
        Port *p = CreatePort(config);
        if (!p) {
            LogError("BarrierManager", "barrier port init failed n={}", n);
            for (Port *created : ports) DestroyPort(created);
            return 1;
        }
        ports.push_back(p);
    }

    std::map<uint32_t, BarrierState> barriers;
    uint64_t tick = 0;
    std::set<size_t> donePorts;

    LogInfo("BarrierManager", "[BarrierManager] listening on {} nodes", numNodes);

    while (donePorts.size() < ports.size()) {
        for (size_t i = 0; i < ports.size(); ++i) {
            if (!donePorts.count(i)) EmitSync(ports[i], tick);
        }

        for (size_t i = 0; i < ports.size(); i++) {
            if (donePorts.count(i)) continue;
            Port *p = ports[i];
            ReceiveStatus st;
            const Message *m = ReceiveMessage(p, tick, &st);
            while (m && st == ReceiveStatus::Message) {
                // CONTROL_SYNC arrives as an ordinary Message; skip it so it
                // does not count as barrier activity.
                if (GetMessageType(m) == MessageType::ControlSync) {
                    m = ReceiveMessage(p, tick, &st);
                    continue;
                }
                if (GetMessageType(m) == MessageType::Terminate) {
                    donePorts.insert(i);
                    break;
                }
                // Barrier control is now a PAYLOAD CoherenceMessage (BarrierReached).
                const CoherenceMessage *bc = nullptr;
                if (GetMessageType(m) == MessageType::Payload &&
                    GetMessagePayloadSize(m) == sizeof(CoherenceMessage)) {
                    bc = static_cast<const CoherenceMessage *>(GetMessagePayloadData(m));
                }
                if (bc && bc->h.type == CoherenceMessageType::BarrierReached) {
                    uint32_t mask = bc->b.barrier.mask;
                    uint32_t nodeId = bc->h.srcNode;
                    LogInfo("BarrierManager", "[BarrierManager] ARRIVED node={} mask=0x{:x}", nodeId, mask);

                    auto &bs = barriers[mask];
                    bs.mask = mask;
                    bs.arrived.insert(nodeId);

                    // Count expected nodes: popcount(mask)
                    uint32_t v = mask, expected = 0;
                    while (v) { expected++; v &= (v - 1); }

                    if (bs.arrived.size() >= expected) {
                        LogInfo("BarrierManager", "[BarrierManager] RELEASE mask=0x{:x}", mask);
                        // Broadcast BarrierRelease to all nodes in the mask
                        for (uint32_t ni = 0; ni < (uint32_t)numNodes; ni++) {
                            if (mask & (1u << ni)) {
                                Message *buf = AllocateSendMessage(ports[ni], tick);
                                if (buf) {
                                    SetMessageType(buf, MessageType::Payload);
                                    SetMessageSourceId(buf, 0);
                                    SetMessageTargetId(buf, ni);
                                    CoherenceMessage rmsg;
                                    rmsg.h.type = CoherenceMessageType::BarrierRelease;
                                    rmsg.b.barrier.mask = mask;
                                    if (sizeof(rmsg) > GetMaxPayloadSize()) {
                                        ReleaseMessage(buf);
                                        continue;
                                    }
                                    SetMessagePayload(buf, &rmsg, sizeof(rmsg));
                                    SendMessage(ports[ni], buf);
                                }
                            }
                        }
                        bs.arrived.clear();
                    }
                }
                m = ReceiveMessage(p, tick, &st);
            }
        }
        uint64_t minTs = UINT64_MAX;
        for (size_t i = 0; i < ports.size(); i++) {
            if (donePorts.count(i)) continue;
            uint64_t ts = SafeTimestamp(ports[i], tick);
            if (ts < minTs) minTs = ts;
        }
        if (minTs != UINT64_MAX && minTs > tick)
            tick = minTs;
        else
            std::this_thread::yield();
    }
    for (Port *p : ports) {
        TerminatePort(p);
        DestroyPort(p);
    }
    return 0;
}
