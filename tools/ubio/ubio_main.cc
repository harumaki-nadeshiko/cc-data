/**
 * Standalone UBIO with real UBCCController.
 *
 * 网络侧约定：networksim 负责 bind，ubio 必须 connect。
 * 用法：
 *   ubio_main --gem5-ep=ipc:///tmp/ubio_n0 --net-ep=ipc:///tmp/networksim_m0_p1 --node=0
 */

#include "framework/Port.hh"
#include "framework/MemMessage.hh"
#include "modules/ubiomodule/UBCCController.hh"

#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <thread>
#include <zmq.hpp>

using namespace framework;
using namespace gem5::ruby;

namespace
{

bool
isUbccIngress(CoherenceMessageType t)
{
    switch (t) {
      case CoherenceMessageType::ReadReq:
      case CoherenceMessageType::WritebackReq:
      case CoherenceMessageType::EvictReq:
      case CoherenceMessageType::UpgradeReq:
      case CoherenceMessageType::UpgradeDoneReq:
      case CoherenceMessageType::ClearReq:
      case CoherenceMessageType::RecallResp:
      case CoherenceMessageType::InvalidateAck:
      case CoherenceMessageType::QueryLineMetaReq:
      case CoherenceMessageType::HomeWritebackNotify:
        return true;
      default:
        return false;
    }
}

bool
isGem5Ingress(CoherenceMessageType t)
{
    switch (t) {
      case CoherenceMessageType::RecallReq:
      case CoherenceMessageType::InvalidateReq:
      case CoherenceMessageType::ReadResp:
      case CoherenceMessageType::WritebackResp:
      case CoherenceMessageType::EvictResp:
      case CoherenceMessageType::UpgradeResp:
      case CoherenceMessageType::UpgradeDoneResp:
      case CoherenceMessageType::ClearResp:
      case CoherenceMessageType::UpgradeAckNotify:
      case CoherenceMessageType::QueryLineMetaResp:
        return true;
      default:
        return false;
    }
}

bool
sendCoh(Port *port, uint64_t tick, uint32_t dstModule, uint32_t dstPort,
        const CoherenceMessage &msg)
{
    if (!port) {
        return false;
    }
    MemMessage *buf = port->sendAllocateBuffer(tick);
    if (!buf) {
        return false;
    }
    buf->hdr.type = static_cast<uint32_t>(MemMessageType::COH_MSG);
    buf->hdr.dst_module = dstModule;
    buf->hdr.dst_port = dstPort;
    buf->hdr.req_id = msg.h.reqId;
    if (!buf->setPayload(msg)) {
        return false;
    }
    return port->send(buf);
}

bool
matchesNetEndpoint(const std::string &ep, int nid)
{
    return ep == ("ipc:///tmp/networksim_m" + std::to_string(nid) + "_p1");
}

struct UbioBackstoreHost : public UBCCHostIf {
    explicit UbioBackstoreHost(UBCCController &ctrl) : ubcc(ctrl) {}

    void hostIssueBackstoreRead(uint64_t pa) override {
        UBCCController::BackstoreEntry e{};
        auto it = store.find(pa);
        const bool found = it != store.end();
        if (found) {
            e = it->second;
        }
        ubcc.onBackstoreFillComplete(pa, found, e);
    }

    void hostIssueBackstoreWrite(uint64_t pa) override {
        UBCCController::BackstoreEntry e{};
        if (ubcc.snapshotResidentForBackstore(pa, e)) {
            store[pa] = e;
            ubcc.directory().bloomInsert(pa);
        }
        ubcc.onBackstoreWriteAck(pa);
    }

    void hostIssueBackstoreDelete(uint64_t pa) override {
        const bool existed = store.erase(pa) > 0;
        ubcc.directory().bloomRemove(pa);
        ubcc.onBackstoreDeleteAck(pa, existed);
    }

    UBCCController &ubcc;
    std::map<uint64_t, UBCCController::BackstoreEntry> store;
};

bool
handleUbccMessage(UBCCController &ubcc, int nid, const CoherenceMessage &msg,
                  CoherenceMessage &response, bool &hasResponse)
{
    hasResponse = false;

    switch (msg.h.type) {
      case CoherenceMessageType::ReadReq: {
        UBCC_OuterReqType reqType =
            ((msg.h.flags & static_cast<uint32_t>(CFLAG_WRITE_INTENT)) ||
             msg.b.readReq.neededPerm == 1)
                ? UBCC_OuterReqType::GlobalReadUnique
                : UBCC_OuterReqType::GlobalReadShared;

        gem5::Tick grantVisibleTick = 0;
        gem5::Tick sentinelVisibleTick = 0;
        bool recallNeeded = false;
        int recallOwnerNode = -1;
        GrantDataSource dataSource = GrantDataSource::HomeMemory;
        uint64_t authEpoch = 0;

        auto grant = ubcc.processOuterRequest(
            msg.h.homeLinePa, reqType,
            (msg.h.flags & static_cast<uint32_t>(CFLAG_WRITE_INTENT)) != 0,
            msg.h.requesterNode, msg.h.epoch, msg.h.reqId,
            &grantVisibleTick, &sentinelVisibleTick,
            &recallNeeded, &recallOwnerNode,
            &dataSource, &authEpoch);

        int pendingInvCount = ubcc.getPendingInvalidationCount(msg.h.homeLinePa);
        uint64_t pendingInvMask = ubcc.getPendingInvalidationMask(msg.h.homeLinePa);
        uint64_t committedEpoch = ubcc.getEpochForLine(msg.h.homeLinePa);
        gem5::ruby::DataBlock grantData(64);
        bool hasGrantData =
            (dataSource == GrantDataSource::RecallBuffer) &&
            ubcc.copyOutstandingGrantData(msg.h.homeLinePa, grantData);

        response.h.type = CoherenceMessageType::ReadResp;
        response.h.srcNode = nid;
        response.h.dstNode = msg.h.srcNode;
        response.h.homeNode = nid;
        response.h.requesterNode = msg.h.requesterNode;
        response.h.homeLinePa = msg.h.homeLinePa;
        response.h.epoch = msg.h.epoch;
        response.h.reqId = msg.h.reqId;
        response.h.flags = hasGrantData ? static_cast<uint32_t>(CFLAG_HAS_DATA) : 0;
        response.b.readResp.grantType = static_cast<int8_t>(grant);
        response.b.readResp.dataSource = static_cast<int8_t>(dataSource);
        response.b.readResp.pendingInvCount = pendingInvCount;
        response.b.readResp.grantVisibleTick = grantVisibleTick;
        response.b.readResp.sentinelVisibleTick = sentinelVisibleTick;
        response.b.readResp.recallNeeded = recallNeeded;
        response.b.readResp.recallOwnerNode = recallOwnerNode;
        response.b.readResp.authEpoch = authEpoch;
        response.b.readResp.committedEpoch = committedEpoch;
        response.b.readResp.pendingInvMask = pendingInvMask;
        if (hasGrantData) {
            std::memcpy(response.b.readResp.grantData, grantData.data, 64);
        }
        hasResponse = true;
        return true;
      }

      case CoherenceMessageType::WritebackReq: {
        bool keepAsClean =
            (msg.h.flags & static_cast<uint32_t>(CFLAG_KEEP_AS_CLEAN)) != 0;
        bool success = ubcc.processWriteback(
            msg.h.homeLinePa, msg.h.requesterNode, msg.h.epoch, keepAsClean);
        response.h.type = CoherenceMessageType::WritebackResp;
        response.h.srcNode = nid;
        response.h.dstNode = msg.h.srcNode;
        response.h.homeLinePa = msg.h.homeLinePa;
        response.h.epoch = msg.h.epoch;
        response.h.reqId = msg.h.reqId;
        response.b.writebackResp.success = success;
        hasResponse = true;
        return true;
      }

      case CoherenceMessageType::EvictReq: {
        bool success = ubcc.processEvict(
            msg.h.homeLinePa, msg.h.requesterNode, msg.h.epoch);
        response.h.type = CoherenceMessageType::EvictResp;
        response.h.srcNode = nid;
        response.h.dstNode = msg.h.srcNode;
        response.h.homeLinePa = msg.h.homeLinePa;
        response.h.epoch = msg.h.epoch;
        response.h.reqId = msg.h.reqId;
        response.b.evictResp.success = success;
        hasResponse = true;
        return true;
      }

      case CoherenceMessageType::UpgradeReq: {
        bool accepted = ubcc.processOuterUpgradeReq(
            msg.h.homeLinePa, msg.h.requesterNode, msg.h.epoch, msg.h.reqId,
            msg.b.upgradeReq.desiredPerm,
            static_cast<UBCC_UpgradeCause>(msg.b.upgradeReq.cause));
        response.h.type = CoherenceMessageType::UpgradeResp;
        response.h.srcNode = nid;
        response.h.dstNode = msg.h.srcNode;
        response.h.homeLinePa = msg.h.homeLinePa;
        response.h.epoch = msg.h.epoch;
        response.h.reqId = msg.h.reqId;
        response.h.flags = accepted ? static_cast<uint32_t>(CFLAG_ACCEPTED) : 0;
        response.b.upgradeResp.upgradeTargetMask =
            ubcc.getUpgradePendingTargetMask(msg.h.homeLinePa);
        response.b.upgradeResp.committedEpoch =
            ubcc.getEpochForLine(msg.h.homeLinePa);
        hasResponse = true;
        return true;
      }

      case CoherenceMessageType::UpgradeDoneReq: {
        bool accepted = ubcc.processOuterUpgradeDone(
            msg.h.homeLinePa, msg.h.requesterNode, msg.h.epoch, msg.h.reqId);
        response.h.type = CoherenceMessageType::UpgradeDoneResp;
        response.h.srcNode = nid;
        response.h.dstNode = msg.h.srcNode;
        response.h.homeLinePa = msg.h.homeLinePa;
        response.h.epoch = msg.h.epoch;
        response.h.reqId = msg.h.reqId;
        response.b.upgradeDoneResp.accepted = accepted;
        hasResponse = true;
        return true;
      }

      case CoherenceMessageType::ClearReq: {
        bool accepted = ubcc.processClear(
            msg.h.homeLinePa, msg.h.requesterNode, msg.h.epoch, msg.h.reqId);
        response.h.type = CoherenceMessageType::ClearResp;
        response.h.srcNode = nid;
        response.h.dstNode = msg.h.srcNode;
        response.h.homeLinePa = msg.h.homeLinePa;
        response.h.epoch = msg.h.epoch;
        response.h.reqId = msg.h.reqId;
        response.b.clearResp.accepted = accepted;
        hasResponse = true;
        return true;
      }

      case CoherenceMessageType::RecallResp:
        ubcc.processRecallResponse(msg.h.homeLinePa, msg.h.requesterNode,
                                   (msg.h.flags & static_cast<uint32_t>(CFLAG_DATA_RETURNED)) != 0,
                                   msg.h.epoch, msg.h.reqId);
        return true;

      case CoherenceMessageType::InvalidateAck:
        ubcc.processInvalidationAck(msg.h.homeLinePa, msg.h.requesterNode,
                                    msg.h.epoch, msg.h.reqId);
        return true;

      case CoherenceMessageType::QueryLineMetaReq: {
        uint64_t qEpoch = 0;
        int qOwnerNode = -1;
        UBCCMESIState qState = UBCCMESIState::G_I;
        bool qFound = false;
        ubcc.queryLineMeta(msg.h.homeLinePa, qEpoch, qOwnerNode, qState, qFound);
        response.h.type = CoherenceMessageType::QueryLineMetaResp;
        response.h.srcNode = nid;
        response.h.dstNode = msg.h.srcNode;
        response.h.homeLinePa = msg.h.homeLinePa;
        response.h.epoch = msg.h.epoch;
        response.h.reqId = msg.h.reqId;
        response.b.queryLineMetaResp.found = qFound;
        response.b.queryLineMetaResp.epoch = qEpoch;
        response.b.queryLineMetaResp.ownerNode = qOwnerNode;
        hasResponse = true;
        return true;
      }

      case CoherenceMessageType::HomeWritebackNotify:
        ubcc.processHomeWritebackNotify(msg.h.homeLinePa, msg.h.epoch);
        return true;

      default:
        return false;
    }
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    std::string gem5Ep;
    std::string netEp;
    int nid = 0;
    uint64_t sw = 100000;
    bool gem5Bind = false;
    bool netBind = false;

    for (int i = 1; i < argc; ++i) {
        if (!std::strncmp(argv[i], "--gem5-ep=", 10)) gem5Ep = argv[i] + 10;
        else if (!std::strncmp(argv[i], "--net-ep=", 9)) netEp = argv[i] + 9;
        else if (!std::strncmp(argv[i], "--node=", 7)) nid = std::atoi(argv[i] + 7);
        else if (!std::strncmp(argv[i], "--sync=", 7)) sw = std::strtoull(argv[i] + 7, nullptr, 10);
        else if (!std::strcmp(argv[i], "--gem5-bind")) gem5Bind = true;
        else if (!std::strcmp(argv[i], "--net-bind")) netBind = true;
    }

    if (gem5Ep.empty()) {
        std::fprintf(stderr, "need --gem5-ep\n");
        return 1;
    }
    if (netBind) {
        std::fprintf(stderr,
                     "[ubio:%d] ERROR: --net-bind 已废弃；networksim 必须 bind，ubio 必须 connect\n",
                     nid);
        return 2;
    }
    if (!netEp.empty() && !matchesNetEndpoint(netEp, nid)) {
        std::fprintf(stderr,
                     "[ubio:%d] ERROR: --net-ep 必须匹配 ipc:///tmp/networksim_m%d_p1，当前=%s\n",
                     nid, nid, netEp.c_str());
        return 3;
    }

    zmq::context_t ctx(1);
    Port *gem5Port = new Port("gem5", nid, 0, gem5Ep, gem5Bind, ctx, sw);
    Port *netPort = netEp.empty() ? nullptr :
        new Port("net", nid, 1, netEp, false, ctx, sw);

    UBCCController ubcc(nid, 0, nullptr);
    UbioBackstoreHost host(ubcc);
    ubcc.setHost(&host);

    std::fprintf(stderr, "[ubio:%d] gem5=%s net=%s bind(gem5=%d net=0 connect)\n",
                 nid, gem5Ep.c_str(), netEp.empty() ? "<none>" : netEp.c_str(), gem5Bind);

    uint64_t tick = 0;
    bool aligned = false;
    bool done = false;

    auto handleIncoming = [&](Port *srcPort, Port *replyPort, bool fromNetwork) {
        if (!srcPort) {
            return;
        }
        const uint64_t visible = ~0ULL;
        MemMessage *m = srcPort->recv(visible);
        while (m) {
            if (m->hdr.type == static_cast<uint32_t>(MemMessageType::TERMINATE)) {
                std::fprintf(stderr, "[ubio:%d] recv TERMINATE ts=%lu\n", nid, m->hdr.timestamp);
                done = true;
                break;
            }
            if (m->hdr.type == static_cast<uint32_t>(MemMessageType::CONTROL_SYNC)) {
                m = srcPort->recv(visible);
                continue;
            }
            if (m->hdr.type == static_cast<uint32_t>(MemMessageType::BARRIER_REACHED)) {
                uint32_t mask = (uint32_t)m->hdr.req_id;
                std::fprintf(stderr,"[ubio:%d] BARRIER_REACHED mask=0x%x\n", nid, mask);
                // Count unique nodes that have reached barrier
                static std::map<uint32_t, std::set<int>> barrier_nodes;
                barrier_nodes[mask].insert(nid);
                uint32_t expected = __builtin_popcount(mask);
                if (barrier_nodes[mask].size() >= expected) {
                    // All arrived: release barrier
                    for (int node : barrier_nodes[mask]) {
                        MemMessage* rel = sendAllocateBuffer ? port.sendAllocateBuffer(tick);
                        // Send to each node via gem5 port
                    }
                    barrier_nodes[mask].clear();
                }
                m = srcPort->recv(visible);
                continue;
            }
            if (m->hdr.type != static_cast<uint32_t>(MemMessageType::COH_MSG)) {
                std::fprintf(stderr, "[ubio:%d] drop MemMessage type=%u ts=%lu size=%u\n",
                             nid, m->hdr.type, m->hdr.timestamp, m->hdr.size);
                m = srcPort->recv(visible);
                continue;
            }

            const CoherenceMessage *coh = m->getPayload<CoherenceMessage>();
            if (!coh) {
                std::fprintf(stderr, "[ubio:%d] bad payload size=%u req_id=%lu\n",
                             nid, m->payloadLen(), m->hdr.req_id);
                m = srcPort->recv(visible);
                continue;
            }
            if (!aligned && m->hdr.timestamp > tick) {
                tick = m->hdr.timestamp;
                aligned = true;
            }

            std::fprintf(stderr, "[ubio:%d] %s recv %s reqId=%lu src=%u dst=%u\n",
                         nid, fromNetwork ? "net" : "gem5",
                         coherenceMsgTypeName(coh->h.type), coh->h.reqId,
                         m->hdr.src_module, m->hdr.dst_module);

            if (coh->h.dstNode != nid) {
                // Route cross-node: forward via network
                if (netPort) {
                    sendCoh(netPort, tick, coh->h.dstNode, 1, *coh);
                } else {
                    std::fprintf(stderr, "[ubio:%d] DROP cross-node %s (no net)\n",
                                 nid, coherenceMsgTypeName(coh->h.type));
                }
                m = srcPort->recv(visible);
                continue;
            }

            // dstNode == nid: process locally
            // From network: process with UBCC, respond back via network
            // From gem5: process with UBCC, respond back via gem5 port
            if (fromNetwork) {
                // Message from net for this node: process+respond locally
                CoherenceMessage response;
                bool hasResponse = false;
                if (handleUbccMessage(ubcc, nid, *coh, response, hasResponse) && hasResponse) {
                    sendCoh(netPort, tick, coh->h.srcNode, 1, response);
                }
                m = srcPort->recv(visible);
                continue;
            }

            if (!isUbccIngress(coh->h.type)) {
                std::fprintf(stderr, "[ubio:%d] drop unsupported local type=%s\n",
                             nid, coherenceMsgTypeName(coh->h.type));
                m = srcPort->recv(visible);
                continue;
            }

            CoherenceMessage response;
            bool hasResponse = false;
            if (!handleUbccMessage(ubcc, nid, *coh, response, hasResponse)) {
                std::fprintf(stderr, "[ubio:%d] UBCC unhandled type=%s\n",
                             nid, coherenceMsgTypeName(coh->h.type));
                m = srcPort->recv(visible);
                continue;
            }

            if (hasResponse) {
                Port *out = fromNetwork ? netPort : gem5Port;
                uint32_t dstModule = fromNetwork ? coh->h.srcNode : nid;
                uint32_t dstPort = fromNetwork ? 1 : 0;
                sendCoh(out, tick, dstModule, dstPort, response);
            }

            m = srcPort->recv(visible);
        }
    };

    while (!done) {
        handleIncoming(gem5Port, gem5Port, false);
        handleIncoming(netPort, netPort, true);

        // Cross-process barrier coordinator
        static std::map<uint32_t, std::set<int>> barrierNodes;
        if (gem5Port) {
            MemMessage* m = gem5Port->recv(~0ULL);
            while (m) {
                if (m->hdr.type == (uint32_t)MemMessageType::BARRIER_REACHED) {
                    uint32_t mask = (uint32_t)m->hdr.req_id;
                    int srcNode = m->hdr.src_module;
                    barrierNodes[mask].insert(srcNode);
                    // Forward to other ubios via networksim
                    if (netPort) {
                        for (int i = 0; i < 4; ++i) {
                            if (i != nid) {
                                MemMessage* fwd = gem5Port->sendAllocateBuffer(m->hdr.timestamp);
                                if (fwd) { *fwd = *m; fwd->hdr.dst_module = i; netPort->send(fwd); }
                            }
                        }
                    }
                }
                m = gem5Port->recv(~0ULL);
            }
        }
        if (netPort) {
            MemMessage* m = netPort->recv(~0ULL);
            while (m) {
                if (m->hdr.type == (uint32_t)MemMessageType::BARRIER_REACHED) {
                    uint32_t mask = (uint32_t)m->hdr.req_id;
                    int srcNode = m->hdr.src_module;
                    barrierNodes[mask].insert(srcNode);
                }
                m = netPort->recv(~0ULL);
            }
        }
        // Release barriers that are complete
        for (auto it = barrierNodes.begin(); it != barrierNodes.end(); ) {
            uint32_t mask = it->first;
            uint32_t expected = __builtin_popcount(mask);
            if (it->second.size() >= expected) {
                if (gem5Port) {
                    MemMessage* rel = gem5Port->sendAllocateBuffer(tick);
                    if (rel) {
                        rel->hdr.type = (uint32_t)MemMessageType::BARRIER_RELEASE;
                        rel->hdr.req_id = mask;
                        rel->hdr.size = sizeof(MemMessageHeader);
                        gem5Port->send(rel);
                    }
                }
                it = barrierNodes.erase(it);
            } else { ++it; }
        }

        if (aligned) {
            gem5Port->emitSync(tick);
            if (netPort) {
                netPort->emitSync(tick);
            }
            ++tick;
        } else {
            ++tick;
        }
        std::this_thread::sleep_for(std::chrono::microseconds(10));
    }

    return 0;
}
