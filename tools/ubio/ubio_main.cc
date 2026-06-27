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
    if (msg.h.type == CoherenceMessageType::ClearReq ||
        msg.h.type == CoherenceMessageType::ClearResp) {
        std::fprintf(stderr,
                     "[UBIO-CLEAR] send type=%s reqId=%lu pa=0x%lx srcNode=%d dstNode=%d routeModule=%u routePort=%u tick=%lu\n",
                     coherenceMsgTypeName(msg.h.type),
                     msg.h.reqId, msg.h.homeLinePa,
                     msg.h.srcNode, msg.h.dstNode,
                     dstModule, dstPort, tick);
    }
    if (!port) {
        if (msg.h.type == CoherenceMessageType::ReadReq) {
            std::fprintf(stderr,
                         "[UBIO-RR-SEND] sendCoh ret=false reason=no_port reqId=%lu srcNode=%d dstNode=%d dstModule=%u dstPort=%u tick=%lu\n",
                         msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                         dstModule, dstPort, tick);
        }
        return false;
    }
    MemMessage *buf = port->sendAllocateBuffer(tick);
    if (!buf) {
        if (msg.h.type == CoherenceMessageType::ReadReq) {
            std::fprintf(stderr,
                         "[UBIO-RR-SEND] sendCoh ret=false reason=sendAllocateBuffer_null reqId=%lu srcNode=%d dstNode=%d dstModule=%u dstPort=%u tick=%lu\n",
                         msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                         dstModule, dstPort, tick);
        }
        return false;
    }
    buf->hdr.type = static_cast<uint32_t>(MemMessageType::COH_MSG);
    buf->hdr.dst_module = dstModule;
    buf->hdr.dst_port = dstPort;
    buf->hdr.req_id = msg.h.reqId;
    if (!buf->setPayload(msg)) {
        if (msg.h.type == CoherenceMessageType::ReadReq) {
            std::fprintf(stderr,
                         "[UBIO-RR-SEND] sendCoh ret=false reason=setPayload_fail reqId=%lu srcNode=%d dstNode=%d dstModule=%u dstPort=%u tick=%lu\n",
                         msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                         dstModule, dstPort, tick);
        }
        return false;
    }
    bool ok = port->send(buf);
    if (msg.h.type == CoherenceMessageType::ReadReq) {
        std::fprintf(stderr,
                     "[UBIO-RR-SEND] sendCoh ret=%s reason=%s reqId=%lu srcNode=%d dstNode=%d dstModule=%u dstPort=%u tick=%lu\n",
                     ok ? "true" : "false",
                     ok ? "ok" : "port_send_fail",
                     msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                     dstModule, dstPort, tick);
    }
    return ok;
}

bool
matchesNetEndpoint(const std::string &ep, int nid)
{
    return ep == ("ipc:///tmp/networksim_m" + std::to_string(nid) + "_p1");
}

struct UbioBackstoreHost : public UBCCHostIf, public UBCCOutboundIf {
    UBCCController &ubcc;
    Port *gem5Port;
    Port *netPort;
    int nodeId;
    int socketId;
    uint64_t &tickRef;
    std::map<uint64_t, UBCCController::BackstoreEntry> store;

    explicit UbioBackstoreHost(UBCCController &ctrl, Port *gport, Port *nport,
                               int nid, int sid, uint64_t &t)
        : ubcc(ctrl), gem5Port(gport), netPort(nport),
          nodeId(nid), socketId(sid), tickRef(t) {}

    bool routeControlToTarget(const CoherenceMessage &msg) {
        if (msg.h.dstNode == nodeId && msg.h.dstSocket == socketId) {
            return sendCoh(gem5Port, tickRef, nodeId, msg.h.dstSocket, msg);
        }
        if (!netPort) { return false; }
        return sendCoh(netPort, tickRef,
                       static_cast<uint32_t>(msg.h.dstNode), 1, msg);
    }

    bool sendRecallReq(const CoherenceMessage &msg) override {
        return routeControlToTarget(msg);
    }

    bool sendInvalidateReq(const CoherenceMessage &msg) override {
        return routeControlToTarget(msg);
    }
    bool sendUpgradeAckNotify(const CoherenceMessage &msg) override {
        return routeControlToTarget(msg);
    }

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

        // BUSY - don"t send poison ReadResp; caller will retry
        if (static_cast<int>(grant) < 0)
            return true;

        // BUSY — don't send poison ReadResp; caller will retry
        if (static_cast<int>(grant) < 0)
            return true;

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
        std::fprintf(stderr,
                     "[UBIO-CLEAR] ubcc-enter nid=%d type=ClearReq reqId=%lu pa=0x%lx srcNode=%d dstNode=%d epoch=%lu\n",
                     nid, msg.h.reqId, msg.h.homeLinePa,
                     msg.h.srcNode, msg.h.dstNode, msg.h.epoch);
        bool accepted = ubcc.processClear(
            msg.h.homeLinePa, msg.h.requesterNode, msg.h.epoch, msg.h.reqId);
        response.h.type = CoherenceMessageType::ClearResp;
        response.h.srcNode = nid;
        response.h.dstNode = msg.h.srcNode;
        response.h.homeLinePa = msg.h.homeLinePa;
        response.h.epoch = msg.h.epoch;
        response.h.reqId = msg.h.reqId;
        response.b.clearResp.accepted = accepted;
        std::fprintf(stderr,
                     "[UBIO-CLEAR] ubcc-exit nid=%d type=ClearResp reqId=%lu pa=0x%lx accepted=%d dstNode=%d\n",
                     nid, msg.h.reqId, msg.h.homeLinePa,
                     accepted ? 1 : 0, response.h.dstNode);
        hasResponse = true;
        return true;
      }

      case CoherenceMessageType::RecallResp: {
        bool dataReturned = (msg.h.flags & static_cast<uint32_t>(CFLAG_DATA_RETURNED)) != 0;
        bool hasData = (msg.h.flags & static_cast<uint32_t>(CFLAG_HAS_DATA)) != 0;
        gem5::ruby::DataBlock db(64);
        if (hasData && dataReturned)
            std::memcpy(db.data, msg.b.recallResp.data, 64);
        ubcc.processRecallResponse(msg.h.homeLinePa, msg.h.requesterNode,
                                    dataReturned, msg.h.epoch, msg.h.reqId,
                                    (hasData && dataReturned) ? &db : nullptr);
        return true;
      }

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
    uint64_t sw = 1000;

    for (int i = 1; i < argc; ++i) {
        if (!std::strncmp(argv[i], "--node=", 7)) nid = std::atoi(argv[i] + 7);
        else if (!std::strncmp(argv[i], "--sync=", 7)) sw = std::strtoull(argv[i] + 7, nullptr, 10);
    }

    if (nid < 0 || nid > 31) {
        std::fprintf(stderr, "[ubio:%d] ERROR: need --node=\n", nid);
        return 1;
    }

    zmq::context_t ctx(1);
    std::fprintf(stderr, "[UBIO-START] creating ports...\n"); fflush(stderr);
    std::string base = "/workspace/gem5/shared_ipc/ipc";
    std::string gem5Rx = base + "_gem5_" + std::to_string(nid) + "_to_ubio_" + std::to_string(nid);
    std::string gem5Tx = base + "_ubio_" + std::to_string(nid) + "_to_gem5_" + std::to_string(nid);
    Port *gem5Port = new Port("gem5", nid, 0, "ipc://" + gem5Rx, "ipc://" + gem5Tx, ctx, sw);
    std::string netRx = base + "_networksim_m" + std::to_string(nid) + "_to_ubio_" + std::to_string(nid);
    std::string netTx = base + "_ubio_" + std::to_string(nid) + "_to_networksim_m" + std::to_string(nid);
    Port *netPort = new Port("net", nid, 1, "ipc://" + netRx, "ipc://" + netTx, ctx, sw);

    uint64_t tick = 0;

    UBCCController ubcc(nid, 0, nullptr);
    UbioBackstoreHost host(ubcc, gem5Port, netPort, nid, 0, tick);
    ubcc.setHost(&host);
    ubcc.setOutbound(&host);
    bool done = false;

    auto pollAndProcess = [&](Port *port, Port *replyPort, bool fromNetwork) {
        if (!port) return;
        ReceiveStatus st;
        MemMessage *m = port->recv(tick, &st);
        int drain_cnt = 0;
        while (m && st == ReceiveStatus::kMessage) {
            if (++drain_cnt > 200) break;  // prevent starvation of other ports
            if (m->hdr.type == static_cast<uint32_t>(MemMessageType::TERMINATE)) {
                std::fprintf(stderr, "[ubio:%d] recv TERMINATE ts=%lu\n", nid, m->hdr.timestamp);
                done = true;
                break;
            }
            if (m->hdr.type == static_cast<uint32_t>(MemMessageType::CONTROL_SYNC)) {
                m = port->recv(tick, &st);
                continue;
            }
            if (m->hdr.type == static_cast<uint32_t>(MemMessageType::BARRIER_REACHED)) {
                uint32_t mask = (uint32_t)m->hdr.req_id;
                int src = m->hdr.src_module;
                std::fprintf(stderr,"[ubio:%d] BARRIER_REACHED mask=0x%x src=%d\n", nid, mask, src);
                static std::map<uint32_t, std::set<int>> barrierNodes;
                barrierNodes[mask].insert(src);
                if (netPort) {
                    for (int i = 0; i < 4; ++i) {
                        if (i != nid) {
                            MemMessage* fwd = netPort->sendAllocateBuffer(m->hdr.timestamp);
                            if (fwd) { *fwd = *m; fwd->hdr.dst_module = i; netPort->send(fwd); }
                            else { std::fprintf(stderr,"[ubio:%d] BARRIER-FWD-FAIL to=%d\n", nid, i); }
                        }
                    }
                }
                uint32_t expected = __builtin_popcount(mask);
                if (barrierNodes[mask].size() >= expected) {
                    MemMessage* rel = gem5Port->sendAllocateBuffer(tick);
                    if (rel) {
                        rel->hdr.type = (uint32_t)MemMessageType::BARRIER_RELEASE;
                        rel->hdr.req_id = mask;
                        rel->hdr.size = sizeof(MemMessageHeader);
                        gem5Port->send(rel);
                        std::fprintf(stderr,"[ubio:%d] BARRIER_RELEASE mask=0x%x\n", nid, mask);
                    }
                    barrierNodes[mask].clear();
                }
                m = port->recv(tick, &st);
                continue;
            }
            if (m->hdr.type != static_cast<uint32_t>(MemMessageType::COH_MSG)) {
                std::fprintf(stderr, "[ubio:%d] drop MemMessage type=%u ts=%lu size=%u\n",
                             nid, m->hdr.type, m->hdr.timestamp, m->hdr.size);
                m = port->recv(tick, &st);
                continue;
            }

            const CoherenceMessage *coh = m->getPayload<CoherenceMessage>();
            if (!coh) {
                std::fprintf(stderr, "[ubio:%d] bad payload size=%u req_id=%lu\n",
                             nid, m->payloadLen(), m->hdr.req_id);
                m = port->recv(tick, &st);
                continue;
            }

            std::fprintf(stderr, "[ubio:%d] %s recv %s reqId=%lu src=%u dst=%u\n",
                         nid, fromNetwork ? "net" : "gem5",
                         coherenceMsgTypeName(coh->h.type), coh->h.reqId,
                         m->hdr.src_module, m->hdr.dst_module);

            if (coh->h.type == CoherenceMessageType::ClearReq ||
                coh->h.type == CoherenceMessageType::ClearResp) {
                std::fprintf(stderr,
                             "[UBIO-CLEAR] recv nid=%d from=%s type=%s reqId=%lu pa=0x%lx srcNode=%d dstNode=%d requester=%d epoch=%lu\n",
                             nid, fromNetwork ? "net" : "gem5",
                             coherenceMsgTypeName(coh->h.type),
                             coh->h.reqId, coh->h.homeLinePa,
                             coh->h.srcNode, coh->h.dstNode,
                             coh->h.requesterNode, coh->h.epoch);
            }

            if (coh->h.type == CoherenceMessageType::RecallReq ||
                coh->h.type == CoherenceMessageType::RecallResp) {
                std::fprintf(stderr, "[RECALL-TRACE-C] ubio:%d %s %s reqId=%lu cohDst=%d\n",
                             nid, fromNetwork ? "net" : "gem5",
                             coherenceMsgTypeName(coh->h.type), coh->h.reqId, coh->h.dstNode);
            }

            if (coh->h.type == CoherenceMessageType::ReadReq) {
                std::fprintf(stderr,
                             "[UBIO-RR-PATH] reqId=%lu from=%s srcNode=%d dstNode=%d nid=%d enter_dstNode_check=%s homeLinePa=0x%lx\n",
                             coh->h.reqId, fromNetwork ? "net" : "gem5",
                             coh->h.srcNode, coh->h.dstNode, nid,
                             (coh->h.dstNode != nid) ? "true" : "false",
                             coh->h.homeLinePa);
            }

            if (coh->h.dstNode != nid) {
                // If this PA belongs to our local DSM, force local processing
                bool isDsm = ubcc.isDsmAddr(coh->h.homeLinePa);
                if (coh->h.type == CoherenceMessageType::ReadReq) {
                    std::fprintf(stderr,
                                 "[UBIO-RR-PATH] reqId=%lu dstNode!=nid true, isDsmAddr=%s -> pass_non_dsm_check=%s homeLinePa=0x%lx\n",
                                 coh->h.reqId,
                                 isDsm ? "true" : "false",
                                 (!isDsm) ? "true" : "false",
                                 coh->h.homeLinePa);
                }
                if (!isDsm) {
                    if (netPort) {
                        std::fprintf(stderr, "[TRACE-2] n%d FWD %s dst=%d via net\n",
                                     nid, coherenceMsgTypeName(coh->h.type), coh->h.dstNode);
                        bool sent = sendCoh(netPort, tick, coh->h.dstNode, 1, *coh);
                        if (coh->h.type == CoherenceMessageType::ReadReq) {
                            std::fprintf(stderr,
                                         "[UBIO-RR-PATH] reqId=%lu forward_sendCoh_called=true sendCoh_ret=%s dstNode=%d\n",
                                         coh->h.reqId, sent ? "true" : "false", coh->h.dstNode);
                        }
                    } else {
                        std::fprintf(stderr, "[ubio:%d] DROP cross-node %s (no net)\n",
                                     nid, coherenceMsgTypeName(coh->h.type));
                        if (coh->h.type == CoherenceMessageType::ReadReq) {
                            std::fprintf(stderr,
                                         "[UBIO-RR-PATH] reqId=%lu forward_sendCoh_called=false reason=no_netPort\n",
                                         coh->h.reqId);
                        }
                    }
                    m = port->recv(tick, &st);
                    continue;
                }
            }

            if (fromNetwork) {
                CoherenceMessage response;
                bool hasResponse = false;
                if (handleUbccMessage(ubcc, nid, *coh, response, hasResponse) && hasResponse) {
                    std::fprintf(stderr, "[TRACE-3] n%d net->UBCC grant, sending %s back\n",
                                 nid, coherenceMsgTypeName(response.h.type));
                    sendCoh(netPort, tick, coh->h.srcNode, 1, response);
                } else if (isGem5Ingress(coh->h.type)) {
                    std::fprintf(stderr, "[TRACE-4] n%d net->gem5 fwd %s reqId=%lu\n",
                                 nid, coherenceMsgTypeName(coh->h.type), coh->h.reqId);
                    sendCoh(gem5Port, tick, coh->h.srcNode, coh->h.srcSocket, *coh);
                } else if (isGem5Ingress(coh->h.type)) {
                    // Response from remote UBCC → forward to local gem5
                    sendCoh(gem5Port, tick, coh->h.srcNode, coh->h.srcSocket, *coh);
                }
                m = port->recv(tick, &st);
                continue;
            }

            if (!isUbccIngress(coh->h.type)) {
                std::fprintf(stderr, "[ubio:%d] drop unsupported local type=%s\n",
                             nid, coherenceMsgTypeName(coh->h.type));
                m = port->recv(tick, &st);
                continue;
            }

            CoherenceMessage response;
            bool hasResponse = false;
            if (!handleUbccMessage(ubcc, nid, *coh, response, hasResponse)) {
                std::fprintf(stderr, "[ubio:%d] UBCC unhandled type=%s\n",
                             nid, coherenceMsgTypeName(coh->h.type));
                m = port->recv(tick, &st);
                continue;
            }

            if (hasResponse) {
                Port *out = fromNetwork ? netPort : gem5Port;
                sendCoh(out, tick, fromNetwork ? (uint32_t)coh->h.srcNode : (uint32_t)nid,
                        fromNetwork ? 1U : 0U, response);
            }

            m = port->recv(tick, &st);
        }
    };

    uint64_t loop_count = 0;
    while (!done) {
        loop_count++;
        if (loop_count % 10000 == 0) {
            std::fprintf(stderr, "[UBIO-LOOP] tick=%lu loop=%lu\n", tick, loop_count);
            fflush(stderr);
        }
        // 1. Heartbeat: emitSync for all ports (even silent ones)
        if (loop_count <= 5) { std::fprintf(stderr, "[UBIO-PRE-EMIT] tick=%lu\n", tick); fflush(stderr); }
        gem5Port->emitSync(tick);
        if (loop_count <= 5) { std::fprintf(stderr, "[UBIO-POST-EMIT] tick=%lu\n", tick); fflush(stderr); }
        if (netPort) netPort->emitSync(tick);

        // 2. Drain all ready messages from each port
        pollAndProcess(gem5Port, gem5Port, false);
        pollAndProcess(netPort, netPort, true);

        // Always advance via safeTs (even before first message aligned)
        uint64_t minTs = gem5Port->safeTs(tick);
        if (netPort) {
            uint64_t netSafe = netPort->safeTs(tick);
            if (netSafe < minTs) minTs = netSafe;
        }
        if (minTs > tick) {
            tick = minTs;
        } else {
            ++tick;
        }
    }

    return 0;
}
