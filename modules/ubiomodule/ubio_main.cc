/**
 * Standalone UBIO with real UBCCController.
 *
 * 网络侧约定：networksim 负责 bind，ubio 必须 connect。
 * 用法：
 *   ubio_main --gem5-ep=ipc:///tmp/ubio_n0 --net-ep=ipc:///tmp/networksim_m0_p1 --node=0
 */

#include "framework/Port.hh"
#include "framework/MemMessage.hh"
#include "framework/Log.hh"
#include "modules/ubiomodule/UBCCController.hh"
#include "modules/ubiomodule/BackstoreSchemaA.hh"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <thread>
#include <vector>

using namespace framework;
using namespace cc::glob;

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

// ── Debug fault injection (ubio-side, multi-process split) ──────────
// Re-wires the fault injection that previously lived in gem5's UBIOModule
// (removed during decoupling). Rules are passed via the UBIO_FAULT_RULES env
// var, one or more rules separated by ';'. Each rule:
//   name:type:src:dst:pa:action[:delayTicks[:matchCount]]
// action ∈ {drop, dup, delay}. Matching messages emit a [UBFAULT] marker that
// the split-mode verifier scans for as fault evidence.
enum class UbioFaultAction { Drop, Duplicate, Delay, Reorder };

struct UbioFaultRule {
    std::string name;
    CoherenceMessageType matchType = CoherenceMessageType::ReadReq;
    bool matchAnyType = false;          // matchType==ReadReq used as wildcard
    int matchSrc = -1;
    int matchDst = -1;
    uint64_t matchPa = 0;
    UbioFaultAction action = UbioFaultAction::Duplicate;
    uint64_t delayTicks = 0;
    int matchCount = 0;                 // 0 = unlimited
    int firedCount = 0;
};

CoherenceMessageType
parseMsgTypeName(const std::string &s)
{
    static const std::map<std::string, CoherenceMessageType> m = {
        {"ReadReq", CoherenceMessageType::ReadReq},
        {"ReadResp", CoherenceMessageType::ReadResp},
        {"RecallReq", CoherenceMessageType::RecallReq},
        {"RecallResp", CoherenceMessageType::RecallResp},
        {"InvalidateReq", CoherenceMessageType::InvalidateReq},
        {"InvalidateAck", CoherenceMessageType::InvalidateAck},
        {"WritebackReq", CoherenceMessageType::WritebackReq},
        {"WritebackResp", CoherenceMessageType::WritebackResp},
        {"EvictReq", CoherenceMessageType::EvictReq},
        {"EvictResp", CoherenceMessageType::EvictResp},
        {"UpgradeReq", CoherenceMessageType::UpgradeReq},
        {"UpgradeResp", CoherenceMessageType::UpgradeResp},
        {"UpgradeDoneReq", CoherenceMessageType::UpgradeDoneReq},
        {"UpgradeDoneResp", CoherenceMessageType::UpgradeDoneResp},
        {"ClearReq", CoherenceMessageType::ClearReq},
        {"ClearResp", CoherenceMessageType::ClearResp},
        {"UpgradeAckNotify", CoherenceMessageType::UpgradeAckNotify},
    };
    auto it = m.find(s);
    return it != m.end() ? it->second : CoherenceMessageType::ReadReq;
}

std::vector<UbioFaultRule> g_faultRules;

// ── Delayed message queue (3.3 reorder + 4.6 delay real) ──────────────
struct DelayedMsg {
    uint64_t fireTick;          // tick when this message should be delivered
    CoherenceMessage coh;       // the buffered message
    bool fromNetwork;           // original ingress direction
    int faultCopies;            // copies to apply at delivery time
};
static std::deque<DelayedMsg> g_delayedQueue;

void
parseFaultRules(const std::string &all)
{
    if (all.empty()) return;
    size_t start = 0;
    while (start < all.size()) {
        size_t semi = all.find(';', start);
        std::string rule_str = all.substr(start, semi == std::string::npos
                                          ? std::string::npos : semi - start);
        start = (semi == std::string::npos) ? all.size() : semi + 1;
        if (rule_str.empty()) continue;

        std::vector<std::string> parts;
        size_t pos = 0, next = 0;
        while ((next = rule_str.find(':', pos)) != std::string::npos) {
            parts.push_back(rule_str.substr(pos, next - pos));
            pos = next + 1;
        }
        parts.push_back(rule_str.substr(pos));
        if (parts.size() < 6) {
            std::fprintf(stderr, "[UBFAULT] malformed rule '%s' — skipping\n",
                         rule_str.c_str());
            continue;
        }
        UbioFaultRule r;
        r.name = parts[0];
        r.matchType = parseMsgTypeName(parts[1]);
        r.matchAnyType = (parts[1] == "*" || parts[1] == "any");
        r.matchSrc = parts[2].empty() ? -1 : std::stoi(parts[2]);
        r.matchDst = parts[3].empty() ? -1 : std::stoi(parts[3]);
        r.matchPa = parts[4].empty() ? 0 : std::stoull(parts[4], nullptr, 0);
        const std::string &a = parts[5];
        if (a == "drop" || a == "Drop") r.action = UbioFaultAction::Drop;
        else if (a == "delay" || a == "Delay") {
            r.action = UbioFaultAction::Delay;
            r.delayTicks = (parts.size() > 6 && !parts[6].empty())
                           ? std::stoull(parts[6]) : 1000;
        } else if (a == "reorder" || a == "Reorder") {
            r.action = UbioFaultAction::Reorder;
            r.delayTicks = (parts.size() > 6 && !parts[6].empty())
                           ? std::stoull(parts[6]) : 1000;
        } else r.action = UbioFaultAction::Duplicate;  // dup default
        if (parts.size() > 7 && !parts[7].empty())
            r.matchCount = std::stoi(parts[7]);
        g_faultRules.push_back(r);
        std::fprintf(stderr, "[UBFAULT] loaded rule '%s' type=%s src=%d dst=%d "
                     "action=%d count=%d\n", r.name.c_str(), parts[1].c_str(),
                     r.matchSrc, r.matchDst, (int)r.action, r.matchCount);
    }
}

// Returns number of times the message should be processed:
//   0 = drop, 1 = normal, 2 = duplicate. Emits [UBFAULT] on a match.
// For Delay/Reorder actions, enqueues to g_delayedQueue and returns 0.
int
applyUbioFault(const CoherenceMessage &coh, int nid, uint64_t currentTick)
{
    if (g_faultRules.empty()) return 1;
    int copies = 1;
    for (auto &r : g_faultRules) {
        if (r.matchCount > 0 && r.firedCount >= r.matchCount) continue;
        if (!r.matchAnyType && r.matchType != coh.h.type) continue;
        if (r.matchSrc >= 0 && r.matchSrc != (int)coh.h.srcNode) continue;
        if (r.matchDst >= 0 && r.matchDst != (int)coh.h.dstNode) continue;
        if (r.matchPa != 0 && r.matchPa != coh.h.homeLinePa) continue;
        r.firedCount++;
        const char *tn = coherenceMsgTypeName(coh.h.type);
        switch (r.action) {
          case UbioFaultAction::Drop:
            std::fprintf(stderr, "[UBFAULT] node=%d rule='%s' action=Drop "
                         "type=%s src=%d dst=%d pa=0x%lx reqId=%lu\n",
                         nid, r.name.c_str(), tn, coh.h.srcNode, coh.h.dstNode,
                         coh.h.homeLinePa, coh.h.reqId);
            copies = 0;
            break;
          case UbioFaultAction::Duplicate:
            std::fprintf(stderr, "[UBFAULT] node=%d rule='%s' action=Duplicate "
                         "type=%s src=%d dst=%d pa=0x%lx reqId=%lu\n",
                         nid, r.name.c_str(), tn, coh.h.srcNode, coh.h.dstNode,
                         coh.h.homeLinePa, coh.h.reqId);
            copies = 2;
            break;
          case UbioFaultAction::Delay:
            // 4.6: real delay — enqueue to delayed queue, drop original copy
            std::fprintf(stderr, "[UBFAULT] node=%d rule='%s' action=Delay "
                         "ticks=%lu type=%s src=%d dst=%d pa=0x%lx reqId=%lu\n",
                         nid, r.name.c_str(), r.delayTicks, tn, coh.h.srcNode,
                         coh.h.dstNode, coh.h.homeLinePa, coh.h.reqId);
            g_delayedQueue.push_back({currentTick + r.delayTicks, coh, false, 1});
            copies = 0;
            break;
          case UbioFaultAction::Reorder:
            // 3.3: reorder — buffer and deliver after delayTicks
            std::fprintf(stderr, "[UBFAULT] node=%d rule='%s' action=Reorder "
                         "ticks=%lu type=%s src=%d dst=%d pa=0x%lx reqId=%lu\n",
                         nid, r.name.c_str(), r.delayTicks, tn, coh.h.srcNode,
                         coh.h.dstNode, coh.h.homeLinePa, coh.h.reqId);
            g_delayedQueue.push_back({currentTick + r.delayTicks, coh, false, 1});
            copies = 0;
        }
    }
    return copies;
}

// Socket-plane addressing: each (node, socket) pair is a distinct ubio process
// = network module. Global module id encodes both. With num_sockets=1 this
// degenerates to gid == node (legacy per-node behavior).
static int g_numSockets = 1;
static int g_numNodes = 3;
static ResidentDirConfig g_rdcfg;    // may be overridden by argv
static uint64_t g_dramDelayPs = 0;   // argv override, else UBIO_DRAM_DELAY_PS env
static bool g_batchRs = true;        // argv override, else UBCC_BATCH_RS env
static inline uint32_t gidOf(int node, int socket) {
    return static_cast<uint32_t>(node * g_numSockets + socket);
}

bool
sendCoh(Port *port, uint64_t tick, uint32_t dstModule,
        const CoherenceMessage &msg, bool toNetwork = false)
{
    const bool traceReadPath =
        (msg.h.type == CoherenceMessageType::ReadReq) ||
        (msg.h.type == CoherenceMessageType::ReadResp);
    if (msg.h.type == CoherenceMessageType::ClearReq ||
        msg.h.type == CoherenceMessageType::ClearResp) {
        std::fprintf(stderr,
                     "[UBIO-CLEAR] send type=%s reqId=%lu pa=0x%lx srcNode=%d dstNode=%d routeModule=%u routePort=%u tick=%lu\n",
                     coherenceMsgTypeName(msg.h.type),
                     msg.h.reqId, msg.h.homeLinePa,
                     msg.h.srcNode, msg.h.dstNode,
                     dstModule,  tick);
    }
    if (!port) {
        if (traceReadPath) {
            std::fprintf(stderr,
                         "[UBIO-RR-SEND] type=%s sendCoh ret=false reason=no_port reqId=%lu srcNode=%d dstNode=%d dstModule=%u tick=%lu\n",
                         coherenceMsgTypeName(msg.h.type),
                         msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                         dstModule,  tick);
        }
        return false;
    }
    framework::MemMessage *buf = port->allocateSendBuffer(tick);
    if (traceReadPath) {
        std::fprintf(stderr,
                     "[UBIO-RR-SEND] type=%s alloc ptr=%p reqId=%lu srcNode=%d dstNode=%d dstModule=%u tick=%lu\n",
                     coherenceMsgTypeName(msg.h.type),
                     static_cast<void*>(buf),
                     msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                     dstModule,  tick);
    }
    if (!buf) {
        if (traceReadPath) {
            std::fprintf(stderr,
                         "[UBIO-RR-SEND] type=%s sendCoh ret=false reason=sendAllocateBuffer_null reqId=%lu srcNode=%d dstNode=%d dstModule=%u tick=%lu\n",
                         coherenceMsgTypeName(msg.h.type),
                         msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                         dstModule,  tick);
        }
        return false;
    }
    buf->hdr.type = static_cast<uint32_t>(MemMessageType::PAYLOAD);
    buf->hdr.targetId = dstModule;
    buf->hdr.req_id = msg.h.reqId;
    if (!buf->setPayload(msg)) {
        if (traceReadPath) {
            std::fprintf(stderr,
                         "[UBIO-RR-SEND] type=%s sendCoh ret=false reason=setPayload_fail reqId=%lu srcNode=%d dstNode=%d dstModule=%u tick=%lu\n",
                         coherenceMsgTypeName(msg.h.type),
                         msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                         dstModule,  tick);
        }
        delete buf;
        return false;
    }
    uint64_t sendTs = buf->hdr.timestamp;
    bool ok = port->send(buf);
    if (ok) {
        std::fprintf(stderr, "[TRACE-PERF] %lu|%u|ubio|%lu|0x%lx|%s|%s\n",
                     sendTs, dstModule, msg.h.reqId, msg.h.homeLinePa,
                     toNetwork ? "SEND_NET" : "SEND_GEM5",
                     coherenceMsgTypeName(msg.h.type));
    }
    if (traceReadPath) {
        std::fprintf(stderr,
                     "[UBIO-RR-SEND] type=%s sendCoh ret=%s reason=%s reqId=%lu srcNode=%d dstNode=%d dstModule=%u tick=%lu\n",
                     coherenceMsgTypeName(msg.h.type),
                     ok ? "true" : "false",
                     ok ? "ok" : "port_send_fail",
                     msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                     dstModule,  tick);
    }
    return ok;
}

bool
matchesNetEndpoint(const std::string &ep, int nid)
{
    return ep == ("ipc:///tmp/networksim_m" + std::to_string(nid) + "_p1");
}

    struct PendingBackstoreFill {
        uint64_t fireTick;
        uint64_t pa;
        bool found;
        UBCCController::BackstoreEntry entry;
    };

struct DsmDataStore {
    std::map<uint64_t, std::array<uint8_t, 64>> data;
    struct PendingDataOp { uint64_t fireTick; uint64_t pa; bool isWrite;
        std::array<uint8_t, 64> buf; std::function<void(const uint8_t*)> cb; };
    uint64_t _dsmDramDelayPs = 50000;
    std::vector<PendingDataOp> pending;
    void drain(uint64_t tick) {
        auto it = pending.begin();
        while (it != pending.end()) {
            if (tick >= it->fireTick) {
                if (it->isWrite) { data[it->pa] = it->buf; }
                else { auto d = data.find(it->pa); if (it->cb) it->cb(d != data.end() ? d->second.data() : nullptr); }
                it = pending.erase(it);
            } else ++it;
        }
    }
    void readData(uint64_t pa, uint64_t t, std::function<void(const uint8_t*)> cb) { pending.push_back({t + _dsmDramDelayPs, pa, false, {}, cb}); }
    void writeData(uint64_t pa, const uint8_t *buf, uint64_t t) { std::array<uint8_t, 64> a; memcpy(a.data(), buf, 64); pending.push_back({t + _dsmDramDelayPs, pa, true, a, nullptr}); }
};

// Phase 3: MetaRNFClient — async metadata page read/write via gem5 MetaRNFController
struct MetaRNFClient {
    Port *_gem5Port = nullptr;
    uint64_t &_tickRef;
    int _nodeId = 0;
    int _socketId = 0;
    uint64_t _nextReqId = 0x8000000000000000ULL; // high bit set to avoid collision with normal reqIds

    struct PendingRead {
        uint64_t reqId;
        std::function<void(const uint8_t* data256)> callback;
    };
    std::map<uint64_t, PendingRead> _pendingReads;

    MetaRNFClient(uint64_t &tick) : _tickRef(tick) {}

    void init(Port *gem5Port, int nid, int sid) {
        _gem5Port = gem5Port;
        _nodeId = nid;
        _socketId = sid;
    }

    // Send MetaRNFReadReq to gem5; callback invoked when MetaRNFReadResp arrives
    void readPage(uint64_t pagePa, std::function<void(const uint8_t* data256)> callback) {
        uint64_t rid = _nextReqId++;
        CoherenceMessage req;
        req.h.type = CoherenceMessageType::MetaRNFReadReq;
        req.h.srcNode = _nodeId;
        req.h.srcSocket = _socketId;
        req.h.dstNode = _nodeId;
        req.h.dstSocket = _socketId;
        req.h.homeLinePa = pagePa;
        req.h.reqId = rid;
        req.b.metaRNF.pagePa = pagePa;
        _pendingReads[rid] = {rid, callback};
        sendCoh(_gem5Port, _tickRef, _nodeId, req);
    }

    // Send MetaRNFWriteReq to gem5 (fire-and-forget)
    void writePage(uint64_t pagePa, const cc::glob::BackstorePage &page) {
        CoherenceMessage req;
        req.h.type = CoherenceMessageType::MetaRNFWriteReq;
        req.h.srcNode = _nodeId;
        req.h.srcSocket = _socketId;
        req.h.dstNode = _nodeId;
        req.h.dstSocket = _socketId;
        req.h.homeLinePa = pagePa;
        req.h.reqId = _nextReqId++;
        req.b.metaRNF.pagePa = pagePa;
        memcpy(req.b.metaRNF.data, &page, std::min(sizeof(page), (size_t)256));
        sendCoh(_gem5Port, _tickRef, _nodeId, req);
    }

    // Handle MetaRNFReadResp from gem5
    void handleResp(const CoherenceMessage &msg) {
        uint64_t rid = msg.h.reqId;
        auto it = _pendingReads.find(rid);
        if (it != _pendingReads.end()) {
            if (it->second.callback) {
                it->second.callback(msg.b.metaRNF.data);
            }
            _pendingReads.erase(it);
        } else {
            std::fprintf(stderr, "[MetaRNF] WARN: no pending read for reqId=%lu\n", rid);
        }
    }
};

struct UbioBackstoreHost : public UBCCHostIf, public UBCCOutboundIf {
    UBCCController &ubcc;
    Port *gem5Port;
    Port *netPort;
    int nodeId;
    int socketId;
    uint64_t &tickRef;
    // Phase 2: BackstoreSchema-based page store replaces flat std::map
    cc::glob::BackstoreSchemaA _schema;
    cc::glob::GroupIndex _groupIdx[cc::glob::BackstoreNumGroups];
    std::map<uint64_t, cc::glob::BackstorePage> _pages;
    uint64_t _nextPageId = 1;

    uint64_t _ubioDramDelayPs = 0;
    std::vector<PendingBackstoreFill> _pendingFills;
    DsmDataStore dsmData;
    MetaRNFClient _metaRNF;

    explicit UbioBackstoreHost(UBCCController &ctrl, Port *gport, Port *nport,
                               int nid, int sid, uint64_t &t)
        : ubcc(ctrl), gem5Port(gport), netPort(nport),
          nodeId(nid), socketId(sid), tickRef(t), _metaRNF(t)
    {
        _metaRNF.init(gport, nid, sid);
    }

    cc::glob::BackstorePage* _getPage(uint64_t pagePa) {
        auto it = _pages.find(pagePa);
        return (it != _pages.end()) ? &it->second : nullptr;
    }

    bool routeControlToTarget(const CoherenceMessage &msg) {
        if (msg.h.dstNode == nodeId && msg.h.dstSocket == socketId)
            return sendCoh(gem5Port, tickRef, nodeId, msg);
        return sendCoh(netPort, tickRef, gidOf(msg.h.dstNode, msg.h.dstSocket), msg, true);
    }
    bool sendRecallReq(const CoherenceMessage &msg) override { return routeControlToTarget(msg); }
    bool sendInvalidateReq(const CoherenceMessage &msg) override { return routeControlToTarget(msg); }
    bool sendUpgradeAckNotify(const CoherenceMessage &msg) override { return routeControlToTarget(msg); }
    bool sendGrantPush(const CoherenceMessage &msg) override { return routeControlToTarget(msg); }

    void hostIssueBackstoreRead(uint64_t pa) override {
        UBCCController::BackstoreEntry e{};
        bool found = false;
        int g = _schema.groupForPa(pa);
        std::vector<uint64_t> pages = _schema.candidatePagesForLookup(pa, _groupIdx[g]);

        // Try local cache first (L1 cache role — keep _pages as write-through cache)
        for (auto pagePa : pages) {
            cc::glob::BackstorePage* p = _getPage(pagePa);
            if (!p) continue;
            cc::glob::BackstoreEntry schemaEntry;
            if (_schema.lookupInPage(pa, *p, schemaEntry) && !schemaEntry.deleted) {
                e.state = static_cast<MESIState>(schemaEntry.state);
                e.sharersMask = schemaEntry.sharersMask;
                e.epoch = schemaEntry.epoch;
                found = true;
                break;
            }
        }

        if (found || pages.empty()) {
            // Local cache hit or no candidates — complete immediately
            if (_ubioDramDelayPs > 0)
                _pendingFills.push_back({tickRef + _ubioDramDelayPs, pa, found, e});
            else
                ubcc.onBackstoreFillComplete(pa, found, e);
            return;
        }

        // Local miss — issue MetaRNF read for the first candidate page
        uint64_t targetPagePa = pages[0];
        _metaRNF.readPage(targetPagePa, [this, pa, targetPagePa](const uint8_t* data256) {
            UBCCController::BackstoreEntry e2{};
            bool found2 = false;
            if (data256) {
                // Cache the page locally
                cc::glob::BackstorePage pg;
                memcpy(&pg, data256, std::min(sizeof(pg), (size_t)256));
                _pages[targetPagePa] = pg;
                // Now do the lookup
                cc::glob::BackstoreEntry schemaEntry;
                if (_schema.lookupInPage(pa, pg, schemaEntry) && !schemaEntry.deleted) {
                    e2.state = static_cast<MESIState>(schemaEntry.state);
                    e2.sharersMask = schemaEntry.sharersMask;
                    e2.epoch = schemaEntry.epoch;
                    found2 = true;
                }
            }
            ubcc.onBackstoreFillComplete(pa, found2, e2);
        });
    }

    void hostIssueBackstoreWrite(uint64_t pa) override {
        UBCCController::BackstoreEntry e{};
        if (!ubcc.snapshotResidentForBackstore(pa, e)) {
            ubcc.onBackstoreWriteAck(pa);
            return;
        }
        ubcc.directory().bloomInsert(pa);
        int g = _schema.groupForPa(pa);
        cc::glob::BackstoreEntry schemaEntry;
        schemaEntry.pa = pa;
        schemaEntry.state = static_cast<cc::glob::UBCCMESIState>(e.state);
        schemaEntry.sharersMask = e.sharersMask;
        schemaEntry.epoch = e.epoch;
        schemaEntry.deleted = false;

        auto plan = _schema.planUpsert(pa, schemaEntry, _groupIdx[g]);
        cc::glob::BackstorePage* p = nullptr;
        if (plan.needs_new_page) {
            cc::glob::BackstorePage np; np.clear();
            np.hdr.page_id = _nextPageId++;
            _pages[plan.target_page_pa] = np;
            p = &_pages[plan.target_page_pa];
        } else if (plan.needs_read_before) {
            p = _getPage(plan.target_page_pa);
        }
        if (!p) { ubcc.onBackstoreWriteAck(pa); return; }
        _schema.applyUpsert(*p, pa, schemaEntry, plan);
        _schema.updateIndexAfterWrite(_groupIdx[g], plan, plan.target_page_pa);
        // Phase 3: Write-through to gem5 MetaRNF for persistence
        _metaRNF.writePage(plan.target_page_pa, *p);
        ubcc.onBackstoreWriteAck(pa);
    }

    void hostIssueBackstoreDelete(uint64_t pa) override {
        int g = _schema.groupForPa(pa);
        auto plan = _schema.planDelete(pa, _groupIdx[g]);
        cc::glob::BackstorePage* p = _getPage(plan.target_page_pa);
        bool existed = p && _schema.applyDelete(*p, pa, plan);
        ubcc.directory().bloomRemove(pa);
        // Phase 3: Write-through modified page back to MetaRNF
        if (existed && p) {
            _metaRNF.writePage(plan.target_page_pa, *p);
        }
        ubcc.onBackstoreDeleteAck(pa, existed);
    }

    void readDsmData(uint64_t pa, std::function<void(const uint8_t*)> cb) override { dsmData.readData(pa, tickRef, cb); }
    void writeDsmData(uint64_t pa, const uint8_t *buf) override { dsmData.writeData(pa, buf, tickRef); }

    // Drain expired pending backstore fills (T_ubio_dram expiry).
    // Must be called from the main loop every tick after clock advances,
    // so delayed fills fire at the correct simulated time.
    void drainPendingFills(uint64_t tick) {
        if (_pendingFills.empty()) return;
        auto it = _pendingFills.begin();
        while (it != _pendingFills.end()) {
            if (tick >= it->fireTick) {
                ubcc.onBackstoreFillComplete(it->pa, it->found, it->entry);
                it = _pendingFills.erase(it);
            } else {
                ++it;
            }
        }
    }

};

bool
handleUbccMessage(UBCCController &ubcc, UbioBackstoreHost &host, int nid,
                  const CoherenceMessage &msg,
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

        cc::Tick grantVisibleTick = 0;
        cc::Tick sentinelVisibleTick = 0;
        bool recallNeeded = false;
        int recallOwnerNode = -1;
        GrantDataSource dataSource = GrantDataSource::HomeMemory;
        uint64_t authEpoch = 0;

        auto grant = ubcc.processOuterRequest(
            msg.h.homeLinePa, reqType,
            (msg.h.flags & static_cast<uint32_t>(CFLAG_WRITE_INTENT)) != 0,
            msg.h.requesterNode, msg.h.srcSocket,
            msg.h.epoch, msg.h.reqId,
            &grantVisibleTick, &sentinelVisibleTick,
            &recallNeeded, &recallOwnerNode,
            &dataSource, &authEpoch);

        // BUSY - don"t send poison ReadResp; caller will retry
        if (static_cast<int>(grant) < 0)
            return true;

        int pendingInvCount = ubcc.getPendingInvalidationCount(msg.h.homeLinePa);
        uint64_t pendingInvMask = ubcc.getPendingInvalidationMask(msg.h.homeLinePa);
        uint64_t committedEpoch = ubcc.getEpochForLine(msg.h.homeLinePa);
        cc::glob::DataBlock grantData(64);
        // Always try to source grant data from ubio-side stores:
        // 1. Outstanding grant data (recall-sourced, highest priority)
        // 2. Immediate grant data (G_S+RS fast path)
        // 3. _lineDataCache (persisted from prior recall/writeback)
        // 4. DsmDataStore (persistent home DRAM — synchronous lookup)
        // gem5 local physMem is NOT a valid source in split-mode.
        bool hasGrantData =
            ubcc.copyOutstandingGrantData(msg.h.homeLinePa, grantData) ||
            ubcc.copyImmediateGrantData(msg.h.homeLinePa, grantData) ||
            ubcc.copyLineDataCache(msg.h.homeLinePa, grantData);

        response.h.type = CoherenceMessageType::ReadResp;
        response.h.srcNode = nid;
        response.h.dstNode = msg.h.srcNode;
        response.h.dstSocket = msg.h.srcSocket;
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
        // Persist dirty data from writeback into DsmDataStore + _lineDataCache
        if (success && msg.b.writebackReq.hasData) {
            host.writeDsmData(msg.h.homeLinePa, msg.b.writebackReq.data);
            ubcc.updateLineDataCache(msg.h.homeLinePa, msg.b.writebackReq.data);
        }
        response.h.type = CoherenceMessageType::WritebackResp;
        response.h.srcNode = nid;
        response.h.dstNode = msg.h.srcNode;
        response.h.dstSocket = msg.h.srcSocket;
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
        response.h.dstSocket = msg.h.srcSocket;
        response.h.homeLinePa = msg.h.homeLinePa;
        response.h.epoch = msg.h.epoch;
        response.h.reqId = msg.h.reqId;
        response.b.evictResp.success = success;
        hasResponse = true;
        return true;
      }

      case CoherenceMessageType::UpgradeReq: {
        bool notSharer = false;
        bool accepted = ubcc.processOuterUpgradeReq(
            msg.h.homeLinePa, msg.h.requesterNode, msg.h.epoch, msg.h.reqId,
            msg.b.upgradeReq.desiredPerm,
            static_cast<UBCC_UpgradeCause>(msg.b.upgradeReq.cause),
            &notSharer);
        response.h.type = CoherenceMessageType::UpgradeResp;
        response.h.srcNode = nid;
        response.h.dstNode = msg.h.srcNode;
        response.h.dstSocket = msg.h.srcSocket;
        response.h.homeLinePa = msg.h.homeLinePa;
        response.h.epoch = msg.h.epoch;
        response.h.reqId = msg.h.reqId;
        // CFLAG_ACCEPTED => granted. On reject, CFLAG_BUSY distinguishes a
        // PERMANENT reject (notSharer: requester lost the race, must abandon +
        // ReadUnique) from a TEMPORARY reject (retry once home drains).
        response.h.flags = accepted
            ? static_cast<uint32_t>(CFLAG_ACCEPTED)
            : (notSharer ? static_cast<uint32_t>(CFLAG_BUSY) : 0);
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
        response.h.dstSocket = msg.h.srcSocket;
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
        response.h.dstSocket = msg.h.srcSocket;
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
        cc::glob::DataBlock db(64);
        if (hasData && dataReturned)
            std::memcpy(db.data, msg.b.recallResp.data, 64);
        // processRecallResponse expects ownerNode = the node that held the dirty
        // copy and responded. RecallResp.h.srcNode is the responder (owner).
        // Previously this passed msg.h.requesterNode (the Read requester), which
        // mismatched ost->targetNode in the recall validity check, leaving the
        // RECALL outstanding forever and blocking all future upgrades (TC16).
        ubcc.processRecallResponse(msg.h.homeLinePa, msg.h.srcNode,
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
        response.h.dstSocket = msg.h.srcSocket;
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

// Drain delayed messages whose fireTick has arrived. Each message is re-injected
// as if it were a fresh network ingress (fromNetwork=true) so it goes through
// the normal handleUbccMessage / forwarding path.
static void drainDelayedQueue(Port *gem5Port, Port *netPort, int nid, int sid,
                               UBCCController &ubcc, UbioBackstoreHost &host,
                               uint64_t tick) {
    while (!g_delayedQueue.empty() && g_delayedQueue.front().fireTick <= tick) {
        DelayedMsg dm = g_delayedQueue.front();
        g_delayedQueue.pop_front();
        const CoherenceMessage &coh = dm.coh;
        std::fprintf(stderr, "[UBFAULT-DELIVER] node=%d delivering delayed "
                     "type=%s reqId=%lu pa=0x%lx fireTick=%lu currentTick=%lu\n",
                     nid, coherenceMsgTypeName(coh.h.type), coh.h.reqId,
                     coh.h.homeLinePa, dm.fireTick, tick);
        // Re-inject: if it was from network, process as network message; else as gem5 message.
        // We push through the same handleUbccMessage path.
        for (int rep = 0; rep < dm.faultCopies; ++rep) {
            CoherenceMessage response;
            bool hasResponse = false;
            bool handled = handleUbccMessage(ubcc, host, nid, coh, response, hasResponse);
            if (dm.fromNetwork) {
                if (handled && hasResponse) {
                    sendCoh(netPort, tick, gidOf(coh.h.srcNode, coh.h.srcSocket),
                            response, true);
                } else if (!handled && isGem5Ingress(coh.h.type)) {
                    sendCoh(gem5Port, tick, gidOf(coh.h.srcNode, coh.h.srcSocket), coh);
                }
            } else {
                if (handled && hasResponse) {
                    sendCoh(gem5Port, tick, (uint32_t)nid, response, false);
                }
            }
        }
    }
}

} // anonymous namespace

int
main(int argc, char **argv)
{
    std::string gem5Ep;
    std::string netEp;
    int nid = 0;
    int sid = 0;

    for (int i = 1; i < argc; ++i) {
        if (!std::strncmp(argv[i], "--node=", 7)) nid = std::atoi(argv[i] + 7);
        if (!std::strncmp(argv[i], "--socket=", 9)) sid = std::atoi(argv[i] + 9);
        if (!std::strncmp(argv[i], "--num-sockets=", 14)) g_numSockets = std::atoi(argv[i] + 14);
        if (!std::strncmp(argv[i], "--num-nodes=", 12)) g_numNodes = std::atoi(argv[i] + 12);
        if (!std::strncmp(argv[i], "--fault-rules=", 14)) {
            const char *rules = argv[i] + 14;
            parseFaultRules(rules);
        }
        // ResidentDir config (argv override env/defaults, §7.3)
        if (!std::strncmp(argv[i], "--bloom-bytes=", 14))
            g_rdcfg.bloom_bytes = (size_t)std::strtoull(argv[i] + 14, nullptr, 10);
        if (!std::strncmp(argv[i], "--sram-bytes=", 13))
            g_rdcfg.sram_bytes = (size_t)std::strtoull(argv[i] + 13, nullptr, 10);
        if (!std::strncmp(argv[i], "--sharers-bits=", 15))
            g_rdcfg.sharers_bits = std::atoi(argv[i] + 15);
        if (!std::strncmp(argv[i], "--epoch-bits=", 13))
            g_rdcfg.epoch_bits = std::atoi(argv[i] + 13);
        if (!std::strncmp(argv[i], "--ways=", 7))
            g_rdcfg.ways = std::atoi(argv[i] + 7);
        if (!std::strncmp(argv[i], "--set-bits=", 11))
            g_rdcfg.set_bits = std::atoi(argv[i] + 11);
        // UBCC runtime params
        if (!std::strncmp(argv[i], "--dram-delay-ps=", 16))
            g_dramDelayPs = std::strtoull(argv[i] + 16, nullptr, 10);
        if (!std::strncmp(argv[i], "--batch-rs=", 11))
            g_batchRs = (std::atoi(argv[i] + 11) != 0);
    }

    if (nid < 0 || nid > 31) {
        std::fprintf(stderr, "[ubio:%d] ERROR: need --node=\n", nid);
        return 1;
    }

    // Socket-plane model: this ubio process is the home directory + router for
    // exactly one (node, socket) plane. num_sockets from --num-sockets arg.
    if (sid < 0 || sid >= g_numSockets) {
        std::fprintf(stderr, "[ubio:%d] ERROR: --socket=%d out of range [0,%d)\n",
                     nid, sid, g_numSockets);
        return 1;
    }
    int gid = static_cast<int>(gidOf(nid, sid));

    std::fprintf(stderr, "[UBIO-START] node=%d socket=%d gid=%d creating ports...\n",
                 nid, sid, gid); fflush(stderr);
    framework::PortParams gem5Pp = framework::PortEnvLoader::ubioGem5Port(gid, true);
    framework::PortParams netPp = framework::PortEnvLoader::ubioNetPort(gid);
    Port *gem5Port = new Port();
    Port *netPort = new Port();
    if (!gem5Port->init(gem5Pp) || !netPort->init(netPp)) {
        std::fprintf(stderr, "[ubio:%d] port init failed\n", nid);
        return 1;
    }
    std::string gem5Rx = gem5Pp.localRxEndpoint, gem5Tx = gem5Pp.peerRxEndpoint;
    std::string netRx = netPp.localRxEndpoint, netTx = netPp.peerRxEndpoint;
    std::fprintf(stderr,
                 "[UBIO-IPC] nid=%d gem5.rx=%s gem5.tx=%s net.rx=%s net.tx=%s\n",
                 nid,
                 gem5Rx.c_str(), gem5Tx.c_str(),
                 netRx.c_str(), netTx.c_str());

    uint64_t tick = 0;

    UBCCController ubcc(nid, sid, nullptr, 64,
                          g_rdcfg.bloom_bytes > 0 ? g_rdcfg.bloom_bytes
                              : ResidentDir::DefaultBloomBytes,
                          0, g_numSockets, g_numNodes, &g_rdcfg);
    ubcc.setBatchRsEnabled(g_batchRs);
    UbioBackstoreHost host(ubcc, gem5Port, netPort, nid, sid, tick);
    // T_ubio_dram: argv has priority, then env UBIO_DRAM_DELAY_PS
    if (g_dramDelayPs > 0) {
        host._ubioDramDelayPs = g_dramDelayPs;
    } else {
        const char* envDramDelay = std::getenv("UBIO_DRAM_DELAY_PS");
        if (envDramDelay) host._ubioDramDelayPs = std::strtoull(envDramDelay, nullptr, 10);
    }
    ubcc.setHost(&host);
    ubcc.setOutbound(&host);
    bool gem5Done = false, netDone = false;

    auto pollAndProcess = [&](Port *port, Port *replyPort, bool fromNetwork, bool *doneFlag) {
        if (!port) return;
        ReceiveStatus st;
        MemMessage *m = port->recv(tick, &st);
        int drain_cnt = 0;
        while (m && st == ReceiveStatus::kMessage) {
            if (++drain_cnt > 200) break;  // prevent starvation of other ports
            if (m->hdr.type == static_cast<uint32_t>(MemMessageType::TERMINATE)) {
                std::fprintf(stderr, "[ubio:%d] recv TERMINATE ts=%lu from_net=%d\n",
                             nid, m->hdr.timestamp, fromNetwork);
                if (!fromNetwork) {
                    // TERMINATE from local gem5: mark gem5 done and forward
                    // to networksim so other nodes can exclude this peer from
                    // PDES safeTs (TC90/TC98 deadlock fix).
                    *doneFlag = true;
                    if (netPort) {
                        framework::MemMessage* fwd = netPort->allocateSendBuffer(tick);
                        if (fwd) {
                            *fwd = *m;
                            fwd->hdr.timestamp = tick;
                            fwd->hdr.targetId = 0;
                            netPort->send(fwd);
                            std::fprintf(stderr, "[ubio:%d] TERMINATE forwarded to networksim\n", nid);
                        }
                    }
                } else {
                    // TERMINATE from another node via networksim: do NOT set
                    // netDone=true — other nodes may still be active. Ignore
                    // and continue polling.
                }
                if (!fromNetwork) break;
                m = port->recv(tick, &st);
                continue;
            }
            if (m->hdr.type == static_cast<uint32_t>(MemMessageType::CONTROL_SYNC)) {
                m = port->recv(tick, &st);
                continue;
            }
            if (m->hdr.type != static_cast<uint32_t>(MemMessageType::PAYLOAD)) {
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

            // Forward BarrierRelease from network to local gem5 (per-socket barrier).
            if (coh->h.type == CoherenceMessageType::BarrierRelease) {
                if (fromNetwork) {
                    // Arrived from a peer ubio (another local socket) — forward
                    // to local gem5's UBAdapter via gem5Port.
                    framework::MemMessage* rel = gem5Port->allocateSendBuffer(tick);
                    if (rel) {
                        *rel = *m;
                        rel->hdr.timestamp = tick;
                        rel->hdr.targetId = gidOf(nid, sid);
                        gem5Port->send(rel);
                        std::fprintf(stderr, "[ubio:%d] BarrierRelease fwd to gem5 mask=0x%x\n",
                                     nid, coh->b.barrier.mask);
                    }
                }
                // Already handled via gem5Port send above; skip further processing.
                m = port->recv(tick, &st);
                continue;
            }

            // Cross-node barrier (now a PAYLOAD CoherenceMessage, not a
                // dedicated MemMessageType). A node reports BarrierReached; once all
                // (node,socket) planes in the mask have arrived, reply BarrierRelease
                // to ALL local socket planes.
                // TC90 fix: key by (mask, seq) to distinguish successive barriers
                // sharing the same mask. Without this, interleaved BarrierReached
                // messages from different generations pollute the set and get
                // cleared together, causing later barriers to never complete.
                if (coh->h.type == CoherenceMessageType::BarrierReached) {
                    uint32_t mask = coh->b.barrier.mask;
                    uint32_t seq  = coh->b.barrier.seq;
                    int src = coh->h.srcNode;
                    std::fprintf(stderr,"[ubio:%d] BarrierReached mask=0x%x seq=%u src=%d\n", nid, mask, seq, src);
                    using BarrierKey = std::pair<uint32_t, uint32_t>;
                    static std::map<BarrierKey, std::set<int>> barrierNodes;
                    BarrierKey bk{mask, seq};
                    barrierNodes[bk].insert(src);
                    // Forward to ALL sockets of every node (per-socket barrier:
                    // each socket independently fires BarrierReached).
                    if (netPort && !fromNetwork) {
                        int numNodes = g_numNodes;
                        if (numNodes < 1) numNodes = 3;
                        for (int i = 0; i < numNodes; ++i) {
                            for (int s = 0; s < g_numSockets; ++s) {
                                if (i == nid && s == sid) continue;
                                framework::MemMessage* fwd = netPort->allocateSendBuffer(m->hdr.timestamp);
                                if (fwd) {
                                    *fwd = *m;
                                    fwd->hdr.timestamp = tick;
                                    fwd->hdr.targetId = gidOf(i, s);
                                    netPort->send(fwd);
                                }
                            }
                        }
                    }
                    uint32_t expected = __builtin_popcount(mask);
                    if (barrierNodes[bk].size() >= expected) {
                        // Send BarrierRelease to ALL local socket planes via netPort
                        // (each local ubio will forward to its own gem5).
                        bool allSent = true;
                        for (int s = 0; s < g_numSockets; ++s) {
                            framework::MemMessage* rel = netPort
                                ? netPort->allocateSendBuffer(tick)
                                : gem5Port->allocateSendBuffer(tick);
                            if (rel) {
                                rel->hdr.timestamp = tick;
                                rel->hdr.type = (uint32_t)MemMessageType::PAYLOAD;
                                CoherenceMessage rmsg;
                                rmsg.h.type = CoherenceMessageType::BarrierRelease;
                                rmsg.b.barrier.mask = mask;
                                rmsg.b.barrier.seq = seq;
                                rel->setPayload(rmsg);
                                if (netPort && s != sid) {
                                    // Send to other local sockets via nsim
                                    rel->hdr.targetId = gidOf(nid, s);
                                    if (!netPort->send(rel)) allSent = false;
                                } else {
                                    // Send to local UBAdapter via gem5Port
                                    rel->hdr.targetId = gidOf(nid, s);
                                    if (!gem5Port->send(rel)) allSent = false;
                                }
                            } else {
                                allSent = false;
                            }
                        }
                        if (allSent) {
                            barrierNodes.erase(bk);
                            std::fprintf(stderr,"[ubio:%d] BarrierRelease mask=0x%x seq=%u\n", nid, mask, seq);
                        } else {
                            std::fprintf(stderr,"[ubio:%d] BarrierRelease mask=0x%x seq=%u RETRY (send/alloc fail)\n", nid, mask, seq);
                        }
                    }
                m = port->recv(tick, &st);
                continue;
            }

            std::fprintf(stderr, "[ubio:%d] %s recv %s reqId=%lu src=%u dst=%u\n",
                         nid, fromNetwork ? "net" : "gem5",
                         coherenceMsgTypeName(coh->h.type), coh->h.reqId,
                         m->hdr.sourceId, m->hdr.targetId);
            std::fprintf(stderr, "[TRACE-PERF] %lu|%d|ubio|%lu|0x%lx|%s|%s\n",
                         m->hdr.timestamp, nid, coh->h.reqId, coh->h.homeLinePa,
                         fromNetwork ? "RECV_NET" : "RECV_GEM5",
                         coherenceMsgTypeName(coh->h.type));

            // Debug fault injection: evaluate rules against this message.
            // copies: 0 = drop (skip processing+forwarding), 1 = normal,
            // 2 = duplicate (process/forward twice). Only fire on the node the
            // message is destined for, matching the original UBIOModule's
            // per-node semantics.
            int faultCopies = 1;
            if (!g_faultRules.empty() && (int)coh->h.dstNode == nid) {
                faultCopies = applyUbioFault(*coh, nid, tick);
                if (faultCopies == 0) {
                    // Dropped — neither processed nor forwarded.
                    m = port->recv(tick, &st);
                    continue;
                }
            }

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

            if (coh->h.dstNode != nid || coh->h.dstSocket != sid) {
                // If this PA belongs to our local DSM plane, force local processing
                bool isDsm = ubcc.isDsmAddr(coh->h.homeLinePa);
                if (coh->h.type == CoherenceMessageType::ReadReq) {
                    std::fprintf(stderr,
                                 "[UBIO-RR-PATH] reqId=%lu dstNode!=nid true, isDsmAddr=%s -> pass_non_dsm_check=%s homeLinePa=0x%lx\n",
                                 coh->h.reqId,
                                 isDsm ? "true" : "false",
                                 (!isDsm) ? "true" : "false",
                                 coh->h.homeLinePa);
                }
                if (!isDsm || !isUbccIngress(coh->h.type)) {
                    // Forward cross-node. The isDsm "force local" only applies to
                    // UBCC-ingress requests (ReadReq/Writeback/Upgrade/...) whose
                    // PA determines local ownership. Transit control messages
                    // (InvalidateReq/RecallReq/UpgradeAckNotify/...Resp — anything
                    // not isUbccIngress) are point-to-point: route by dstNode
                    // even if homeLinePa happens to fall in our DSM range.
                    // (Without this, an InvalidateReq from gem5 to a remote
                    // sharer was dropped as "unsupported local type" and the
                    // upgrade's invalidation acks never came back → deadlock.)
                    if (netPort) {
                        std::fprintf(stderr, "[TRACE-2] n%d FWD %s dst=%d:%d via net\n",
                                     nid, coherenceMsgTypeName(coh->h.type),
                                     coh->h.dstNode, coh->h.dstSocket);
                        bool sent = sendCoh(netPort, tick,
                                            gidOf(coh->h.dstNode, coh->h.dstSocket), *coh, true);
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
                // faultCopies==2 (Duplicate) delivers the message to the home
                // UBCC twice, exercising idempotent ack / tombstone-replay paths.
                for (int rep = 0; rep < faultCopies; ++rep) {
                    CoherenceMessage response;
                    bool hasResponse = false;
                    bool handled = handleUbccMessage(ubcc, host, nid, *coh, response, hasResponse);
                    if (handled && coh->h.type == CoherenceMessageType::RecallResp) {
                        // RECALL.DONE only flips state inside the home UBCC; there is
                        // no normal response packet back to gem5. Mirror the RecallResp
                        // to the local UBAdapter as a wake-only notification so the
                        // requester's EP-SNF retries immediately instead of waiting for
                        // the 20k-cycle fallback timer.
                        bool sentToGem5 = sendCoh(gem5Port, tick,
                            gidOf(nid, sid), *coh);
                        std::fprintf(stderr,
                                     "[TRACE-4-RECALL] n%d net->gem5 recall-done sendCoh_ret=%s reqId=%lu dstSocket=%d\n",
                                     nid, sentToGem5 ? "true" : "false",
                                     coh->h.reqId, sid);
                    }
                    if (handled && hasResponse) {
                        std::fprintf(stderr, "[TRACE-3] n%d net->UBCC grant, sending %s back to %d:%d\n",
                                     nid, coherenceMsgTypeName(response.h.type),
                                     coh->h.srcNode, coh->h.srcSocket);
                        // Response returns to the requester's (node, socket) plane.
                        sendCoh(netPort, tick,
                                gidOf(coh->h.srcNode, coh->h.srcSocket), response, true);
                    } else if (!handled && isGem5Ingress(coh->h.type)) {
                        if (gem5Done && coh->h.type == CoherenceMessageType::RecallReq) {
                            // gem5 已退出，无法处理 RECALL。合成 RecallResp 返回给 home。
                            // 注意：此时 gem5 的 L1/L2 可能有未写回的 dirty 数据，
                            // 但 barrier 设计保证 verify 在 gem5 退出前完成，
                            // 此路径仅作防御性兜底。
                            std::fprintf(stderr,
                                "[RECALL-PROXY] n%d gem5Done=true, synthesizing RecallResp "
                                "for PA=0x%lx reqId=%lu homeNode=%d\n",
                                nid, coh->h.homeLinePa, coh->h.reqId, coh->h.homeNode);
                            CoherenceMessage resp;
                            resp.h = coh->h;
                            resp.h.type = CoherenceMessageType::RecallResp;
                            resp.h.srcNode = nid;
                            resp.h.srcSocket = sid;
                            resp.h.dstNode = coh->h.homeNode;
                            resp.h.dstSocket = coh->h.homeSocket;
                            // 尝试从 DsmDataStore 获取数据，而不是直接填零
                            // DsmDataStore 缓存了最近访问的 DSM 行数据
                            {
                                auto dsmIt = host.dsmData.data.find(coh->h.homeLinePa);
                                if (dsmIt != host.dsmData.data.end()) {
                                    memcpy(resp.b.recallResp.data, dsmIt->second.data(), 64);
                                    std::fprintf(stderr,
                                        "[RECALL-PROXY] n%d using DsmDataStore data for PA=0x%lx\n",
                                        nid, coh->h.homeLinePa);
                                } else {
                                    memset(resp.b.recallResp.data, 0, 64);
                                    std::fprintf(stderr,
                                        "[RECALL-PROXY] n%d no DsmDataStore data for PA=0x%lx, filling zeros\n",
                                        nid, coh->h.homeLinePa);
                                }
                            }
                            resp.h.flags |= static_cast<uint32_t>(CFLAG_HAS_DATA);
                            sendCoh(netPort, tick,
                                    gidOf(coh->h.homeNode, coh->h.homeSocket),
                                    resp, true);
                        } else if (gem5Done) {
                            // gem5 已退出，其他 gem5Ingress 消息无法处理，记录告警
                            std::fprintf(stderr,
                                "[WARN-GEM5DONE] n%d gem5Done=true, dropping %s "
                                "reqId=%lu PA=0x%lx\n",
                                nid, coherenceMsgTypeName(coh->h.type),
                                coh->h.reqId, coh->h.homeLinePa);
                        } else {
                            // 正常路径：转发给 gem5
                            std::fprintf(stderr, "[TRACE-4] n%d net->gem5 fwd %s reqId=%lu\n",
                                         nid, coherenceMsgTypeName(coh->h.type), coh->h.reqId);
                            bool sentToGem5 = sendCoh(gem5Port, tick,
                                gidOf(coh->h.srcNode, coh->h.srcSocket), *coh);
                            std::fprintf(stderr,
                                         "[TRACE-4-SEND] n%d net->gem5 sendCoh_ret=%s type=%s reqId=%lu dstModule=%d dstPort=%d srcSocket=%d\n",
                                         nid, sentToGem5 ? "true" : "false",
                                         coherenceMsgTypeName(coh->h.type), coh->h.reqId,
                                         coh->h.srcNode, coh->h.srcSocket, coh->h.srcSocket);
                        }
                    }
                }
                m = port->recv(tick, &st);
                continue;
            }

            // MetaRNFReadResp: response from gem5 MetaRNFController (Phase 3)
            if (coh->h.type == CoherenceMessageType::MetaRNFReadResp) {
                host._metaRNF.handleResp(*coh);
                m = port->recv(tick, &st);
                continue;
            }

            if (!isUbccIngress(coh->h.type)) {
                std::fprintf(stderr, "[ubio:%d] drop unsupported local type=%s\n",
                             nid, coherenceMsgTypeName(coh->h.type));
                m = port->recv(tick, &st);
                continue;
            }

            for (int rep = 0; rep < faultCopies; ++rep) {
                CoherenceMessage response;
                bool hasResponse = false;
                if (!handleUbccMessage(ubcc, host, nid, *coh, response, hasResponse)) {
                    std::fprintf(stderr, "[ubio:%d] UBCC unhandled type=%s\n",
                                 nid, coherenceMsgTypeName(coh->h.type));
                    break;
                }
                if (hasResponse) {
                    Port *out = fromNetwork ? netPort : gem5Port;
                    sendCoh(out, tick, fromNetwork ? (uint32_t)coh->h.srcNode : (uint32_t)nid,
                            response, fromNetwork);
                }
            }

            m = port->recv(tick, &st);
        }
    };

    bool ubioDebug = []{ const char* e = std::getenv("EP_DEBUG_PORT"); return e && e[0]=='1'; }();
    uint64_t loop_count = 0;
    while (!(gem5Done && (netPort == nullptr || netDone))) {
        loop_count++;
        if (ubioDebug && loop_count % 1000000 == 0) {
            std::fprintf(stderr, "[UBIO-LOOP] tick=%lu loop=%lu\n", tick, loop_count);
            fflush(stderr);
        }
        // 1. Heartbeat: emitSync for all ports (even silent ones)
        if (loop_count <= 5) { std::fprintf(stderr, "[UBIO-PRE-EMIT] tick=%lu\n", tick); fflush(stderr); }
        if (!gem5Done) gem5Port->emitSync(tick);
        if (loop_count <= 5) { std::fprintf(stderr, "[UBIO-POST-EMIT] tick=%lu\n", tick); fflush(stderr); }
        if (netPort && !netDone) netPort->emitSync(tick);

        // 2. Drain all ready messages from each port
        if (!gem5Done) pollAndProcess(gem5Port, gem5Port, false, &gem5Done);
        if (netPort && !netDone) pollAndProcess(netPort, netPort, true, &netDone);

        // Always advance via safeTs (even before first message aligned)
        uint64_t minTs = UINT64_MAX;
        if (!gem5Done) minTs = gem5Port->safeTs(tick);
        if (netPort && !netDone) {
            uint64_t netSafe = netPort->safeTs(tick);
            if (netSafe < minTs) minTs = netSafe;
        }
        if (minTs > tick) {
            tick = minTs;
        } else {
            // Bounded by a peer: do NOT drift forward with ++tick (that let the
            // native side crawl billions of ticks ahead of gem5, skewing message
            // timestamps into gem5's far future). Yield and re-poll instead, so
            // we stay clock-locked to the slowest peer.
            std::this_thread::yield();
        }
        // 3.3/4.6: Drain delayed fault-injection queue (reorder/delay)
        drainDelayedQueue(gem5Port, netPort, nid, sid, ubcc, host, tick);

        // Fire any expired backstore fills (T_ubio_dram).  Tick-gated deferred
        // callbacks simulate real DRAM read latency.
        host.drainPendingFills(tick);
        host.dsmData.drain(tick);
    }

    // 3.4: Dump ResidentDir performance counters
    ubcc.directory().dumpStatsJson();
    fprintf(stderr, "[UBCC-STATS] {\"asyncWbCount\":%lu}\n",
            ubcc.getAsyncWbCount());

    return 0;
}
