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
#include "framework/TracePerfPolicy.hh"
#include "modules/ubiomodule/UBCCController.hh"
#include "modules/ubiomodule/BackstoreSchemaA.hh"
#include "modules/ubiomodule/BackstoreSchemaC.hh"
#include "modules/ubiomodule/BackstoreHostH64.hh"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <map>
#include <memory>
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
static uint64_t g_dramDelayPs = 0;   // argv --dram-delay-ps= override
static bool g_batchRs = true;        // argv --batch-rs= override
static ResidentOverflowPolicy g_overflowPolicy = ResidentOverflowPolicy::Spill;
static bool g_debugUbioPerf = false;  // [DEBUG-UBIO-*] gate, set via UBIO_DEBUG_PERF=1
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
    if (g_debugUbioPerf && (msg.h.type == CoherenceMessageType::ClearReq ||
        msg.h.type == CoherenceMessageType::ClearResp)) {
        std::fprintf(stderr,
                     "[DEBUG-UBIO-CLEAR] send type=%s reqId=%lu pa=0x%lx srcNode=%d dstNode=%d routeModule=%u tick=%lu\n",
                     coherenceMsgTypeName(msg.h.type),
                     msg.h.reqId, msg.h.homeLinePa,
                     msg.h.srcNode, msg.h.dstNode,
                     dstModule,  tick);
    }
    if (!port) {
        if (g_debugUbioPerf && traceReadPath) {
            std::fprintf(stderr,
                         "[DEBUG-UBIO-RR-SEND] type=%s sendCoh ret=false reason=no_port reqId=%lu srcNode=%d dstNode=%d dstModule=%u tick=%lu\n",
                         coherenceMsgTypeName(msg.h.type),
                         msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                         dstModule,  tick);
        }
        return false;
    }
    framework::MemMessage *buf = port->allocateSendBuffer(tick);
    if (g_debugUbioPerf && traceReadPath) {
        std::fprintf(stderr,
                     "[DEBUG-UBIO-RR-SEND] type=%s alloc ptr=%p reqId=%lu srcNode=%d dstNode=%d dstModule=%u tick=%lu\n",
                     coherenceMsgTypeName(msg.h.type),
                     static_cast<void*>(buf),
                     msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                     dstModule,  tick);
    }
    if (!buf) {
        if (g_debugUbioPerf && traceReadPath) {
            std::fprintf(stderr,
                         "[DEBUG-UBIO-RR-SEND] type=%s sendCoh ret=false reason=sendAllocateBuffer_null reqId=%lu srcNode=%d dstNode=%d dstModule=%u tick=%lu\n",
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
        if (g_debugUbioPerf && traceReadPath) {
            std::fprintf(stderr,
                         "[DEBUG-UBIO-RR-SEND] type=%s sendCoh ret=false reason=setPayload_fail reqId=%lu srcNode=%d dstNode=%d dstModule=%u tick=%lu\n",
                         coherenceMsgTypeName(msg.h.type),
                         msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                         dstModule,  tick);
        }
        delete buf;
        return false;
    }
    uint64_t sendTs = buf->hdr.timestamp;
    bool ok = port->send(buf);
    if (ok && TracePerfPolicy::get().shouldEmit("ubio")) {
        std::fprintf(stderr, "[TRACE-PERF] %lu|%u|ubio|%lu|0x%lx|%s|%s\n",
                     sendTs, dstModule, msg.h.reqId, msg.h.homeLinePa,
                     toNetwork ? "SEND_NET" : "SEND_GEM5",
                     coherenceMsgTypeName(msg.h.type));
    }
    if (g_debugUbioPerf && traceReadPath) {
        std::fprintf(stderr,
                     "[DEBUG-UBIO-RR-SEND] type=%s sendCoh ret=%s reason=%s reqId=%lu srcNode=%d dstNode=%d dstModule=%u tick=%lu\n",
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

    struct PendingBackstoreAck {
        uint64_t fireTick;
        uint64_t pa;
        bool isDelete;
        bool existed;
    };

struct DsmDataStore {
    // Direct-indexed backing for the local 128 MiB home DSM segment. This is
    // home memory, not a PA-keyed cache or metadata shadow: the physical DSM
    // offset selects its fixed 64B location. A separate valid bit prevents a
    // zero-filled unwritten line from being mistaken for stored data.
    static constexpr size_t kSegmentBytes = 128ULL * 1024 * 1024;
    static constexpr size_t kLineBytes = 64;
    static constexpr size_t kLineCount = kSegmentBytes / kLineBytes;
    std::vector<uint8_t> data = std::vector<uint8_t>(kSegmentBytes);
    std::vector<uint8_t> valid = std::vector<uint8_t>(kLineCount, 0);
    struct PendingDataOp {
        bool valid = false;
        uint64_t fireTick; uint64_t pa; bool isWrite;
        std::array<uint8_t, 64> buf;
        std::function<void(DsmDataStatus, const uint8_t*)> readCb;
        std::function<void(DsmDataStatus)> writeCb;
    };
    static constexpr size_t kMaxPendingDataOps = 256;
    uint64_t _dsmDramDelayPs = 50000;
    std::array<PendingDataOp, kMaxPendingDataOps> pending;
    size_t pendingCount = 0;
    size_t pendingHighwater = 0;

    bool enqueue(PendingDataOp op) {
        for (auto &slot : pending) {
            if (slot.valid)
                continue;
            op.valid = true;
            slot = std::move(op);
            ++pendingCount;
            pendingHighwater = std::max(pendingHighwater, pendingCount);
            return true;
        }
        return false;
    }

    void drain(uint64_t tick) {
        for (auto &slot : pending) {
            if (!slot.valid || tick < slot.fireTick)
                continue;
            if (slot.isWrite) {
                    const size_t line = (slot.pa & (kSegmentBytes - 1)) / kLineBytes;
                    std::memcpy(data.data() + line * kLineBytes,
                                slot.buf.data(), kLineBytes);
                    valid[line] = 1;
                    if (slot.writeCb) slot.writeCb(DsmDataStatus::Ok);
            } else {
                    const size_t line = (slot.pa & (kSegmentBytes - 1)) / kLineBytes;
                    if (slot.readCb) {
                        slot.readCb(valid[line] ? DsmDataStatus::Ok
                                                 : DsmDataStatus::NotWritten,
                                    valid[line] ? data.data() + line * kLineBytes
                                                : nullptr);
                    }
            }
            slot = PendingDataOp{};
            --pendingCount;
        }
    }
    bool readData(uint64_t pa, uint64_t t,
                  std::function<void(DsmDataStatus, const uint8_t*)> cb) {
        return enqueue({false, t + _dsmDramDelayPs, pa, false, {}, std::move(cb), nullptr});
    }
    bool writeData(uint64_t pa, const uint8_t *buf, uint64_t) {
        const size_t line = (pa & (kSegmentBytes - 1)) / kLineBytes;
        std::memcpy(data.data() + line * kLineBytes, buf, kLineBytes);
        valid[line] = 1;
        return true;
    }
    // H64 async write: completion fires when data is in `data` map (visible to reads)
    bool writeDataAsync(uint64_t pa, const uint8_t *buf, uint64_t t,
                        std::function<void(DsmDataStatus)> cb) {
        std::array<uint8_t, 64> a; memcpy(a.data(), buf, 64);
        auto failCb = cb;
        PendingDataOp op{false, t + _dsmDramDelayPs, pa, true, a, nullptr,
                         std::move(cb)};
        if (enqueue(std::move(op)))
            return true;
        if (failCb)
            failCb(DsmDataStatus::RetryableBusy);
        return false;
    }

    bool copyData(uint64_t pa, uint8_t *out) const {
        const size_t line = (pa & (kSegmentBytes - 1)) / kLineBytes;
        if (!valid[line]) return false;
        std::memcpy(out, data.data() + line * kLineBytes, kLineBytes);
        return true;
    }
};

// Phase 3: MetaRNFClient — async metadata page read/write via gem5 MetaRNFController
// Implements MetaRNFClientIF for BackstoreHostH64 integration.
struct MetaRNFClient : public MetaRNFClientIF {
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

    // Phase D1: writePage variant that returns send success
    bool writePageD1(uint64_t pagePa, const cc::glob::BackstorePage &page) {
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
        return sendCoh(_gem5Port, _tickRef, _nodeId, req);
    }

    // Phase D2: per-page write contexts for durable callback
    struct PendingWriteCtx {
        uint64_t reqId;
        uint64_t pagePa;
        std::function<void(bool)> callback;
    };
    std::map<uint64_t, PendingWriteCtx> _pendingWrites;
    uint64_t _nextWriteReqId = 0x9000000000000000ULL;

    bool writePageD2(uint64_t pagePa, const cc::glob::BackstorePage &page,
                     std::function<void(bool)> cb) {
        CoherenceMessage req;
        req.h.type = CoherenceMessageType::MetaRNFWriteReq;
        req.h.srcNode = _nodeId;
        req.h.srcSocket = _socketId;
        req.h.dstNode = _nodeId;
        req.h.dstSocket = _socketId;
        req.h.homeLinePa = pagePa;
        uint64_t rid = _nextWriteReqId++;
        req.h.reqId = rid;
        req.b.metaRNF.pagePa = pagePa;
        memcpy(req.b.metaRNF.data, &page, std::min(sizeof(page), (size_t)256));
        bool sent = sendCoh(_gem5Port, _tickRef, _nodeId, req);
        if (sent) {
            _pendingWrites[rid] = {rid, pagePa, cb};
        } else if (cb) {
            cb(false);
        }
        return sent;
    }

    // ---- Phase 2+3: 64B line read with typed status ----
    // Req B: bucketOffset is logical flat-bucket index; UBAdapter maps to physical PA.
    static constexpr int kMaxLinePendingReads = 32;
    static constexpr int kMaxDeferredLineOps = 64;  // bounded deferred queue
    struct PendingLineRead {
        uint64_t reqId;
        uint64_t bucketOffset;
        std::function<void(MetaRNFLineStatus, const uint8_t* data64)> callback;
    };
    std::map<uint64_t, PendingLineRead> _pendingLineReads;
    uint64_t _nextLineReadReqId = 0xA000000000000000ULL;

    // Deferred-work mechanism: when called reentrantly from a response callback
    // (depth > 0), readLine/writeLine enqueue instead of calling sendCoh immediately.
    // The main loop drains deferred ops after each port message is processed.
    int _reentrantDepth = 0;
    struct DeferredLineOp {
        bool isWrite;
        uint64_t bucketOffset;
        uint8_t data[64];
        uint64_t reqId;
        std::function<void(MetaRNFLineStatus, const uint8_t* data64)> readCb;
        std::function<void(MetaRNFLineStatus)> writeCb;
    };
    DeferredLineOp _deferredOps[kMaxDeferredLineOps];
    int _deferredCount = 0;
    bool _deferredFull = false;  // set if a deferred op was dropped (Busy)
    bool _debugH64Pdes = false;  // [DEBUG-H64-PDES-*] gate

    void enterReentrant() { _reentrantDepth++; }
    void leaveReentrant() {
        if (_reentrantDepth > 0) _reentrantDepth--;
    }
    bool hasDeferred() const { return _deferredCount > 0; }

    void drainDeferred() {
        int drained = 0;
        while (_deferredCount > 0 && drained < kMaxDeferredLineOps) {
            int idx = 0;
            auto op = _deferredOps[idx];
            for (int i = 1; i < _deferredCount; ++i) _deferredOps[i-1] = _deferredOps[i];
            _deferredCount--;
            drained++;
            if (_debugH64Pdes) std::fprintf(stderr, "[DEBUG-H64-PDES-DRAIN-OP] n=%d op=%s off=%lu reqId=0x%lx remain=%d\n",
                         _nodeId, op.isWrite ? "WRITE" : "READ",
                         op.bucketOffset, op.reqId, _deferredCount);
            bool sent = false;
            if (op.isWrite) {
                _pendingLineWrites[op.reqId] = {op.reqId, op.bucketOffset, std::move(op.writeCb)};
                CoherenceMessage req;
                req.h.type = CoherenceMessageType::MetaRNFLineWriteReq;
                req.h.srcNode = _nodeId; req.h.srcSocket = _socketId;
                req.h.dstNode = _nodeId; req.h.dstSocket = _socketId;
                req.h.reqId = op.reqId;
                req.b.metaRNFLineWriteReq.bucketOffset = op.bucketOffset;
                memcpy(req.b.metaRNFLineWriteReq.data, op.data, 64);
                sent = sendCoh(_gem5Port, _tickRef, _nodeId, req);
                if (!sent) {
                    if (_debugH64Pdes) std::fprintf(stderr, "[DEBUG-H64-PDES-DRAIN-FAIL] n=%d write off=%lu\n",
                                 _nodeId, op.bucketOffset);
                    auto it = _pendingLineWrites.find(op.reqId);
                    if (it != _pendingLineWrites.end()) {
                        auto cb = std::move(it->second.callback);
                        _pendingLineWrites.erase(it);
                        if (cb) cb(MetaRNFLineStatus::IoError);
                    }
                }
            } else {
                _pendingLineReads[op.reqId] = {op.reqId, op.bucketOffset, std::move(op.readCb)};
                CoherenceMessage req;
                req.h.type = CoherenceMessageType::MetaRNFLineReadReq;
                req.h.srcNode = _nodeId; req.h.srcSocket = _socketId;
                req.h.dstNode = _nodeId; req.h.dstSocket = _socketId;
                req.h.reqId = op.reqId;
                req.b.metaRNFLineReadReq.bucketOffset = op.bucketOffset;
                sent = sendCoh(_gem5Port, _tickRef, _nodeId, req);
                if (!sent) {
                    if (_debugH64Pdes) std::fprintf(stderr, "[DEBUG-H64-PDES-DRAIN-FAIL] n=%d read off=%lu\n",
                                 _nodeId, op.bucketOffset);
                    auto it = _pendingLineReads.find(op.reqId);
                    if (it != _pendingLineReads.end()) {
                        auto cb = std::move(it->second.callback);
                        _pendingLineReads.erase(it);
                        if (cb) cb(MetaRNFLineStatus::IoError, nullptr);
                    }
                }
            }
        }
        _deferredFull = (_deferredCount >= kMaxDeferredLineOps);
    }

    void readLine(uint64_t bucketOffset,
                  std::function<void(MetaRNFLineStatus, const uint8_t* data64)> cb) {
        // Bounded: pending reads + deferred reads ≤ kMaxLinePendingReads (32)
        int combined = (int)_pendingLineReads.size() + _deferredCount;
        if (combined >= kMaxLinePendingReads) {
            if (cb) cb(MetaRNFLineStatus::RetryableBusy, nullptr);
            return;
        }
        uint64_t rid = _nextLineReadReqId++;
        if (_reentrantDepth > 0) {
            // Defer: enqueue for later send, bounded by kMaxDeferredLineOps
            if (_deferredCount >= kMaxDeferredLineOps) {
                _deferredFull = true;
                if (cb) cb(MetaRNFLineStatus::RetryableBusy, nullptr);
                return;
            }
            if (_debugH64Pdes) std::fprintf(stderr, "[DEBUG-H64-PDES-DEFER] n=%d read off=%lu depth=%d cnt=%d\n",
                         _nodeId, bucketOffset, _reentrantDepth, _deferredCount+1);
            auto& op = _deferredOps[_deferredCount++];
            op.isWrite = false; op.bucketOffset = bucketOffset;
            op.reqId = rid; op.readCb = std::move(cb);
            return;
        }
        // Non-reentrant: send immediately. On send failure, erase pending
        // and invoke callback with IoError — no leaked pending entry.
        _pendingLineReads[rid] = {rid, bucketOffset, std::move(cb)};
        CoherenceMessage req;
        req.h.type = CoherenceMessageType::MetaRNFLineReadReq;
        req.h.srcNode = _nodeId; req.h.srcSocket = _socketId;
        req.h.dstNode = _nodeId; req.h.dstSocket = _socketId;
        req.h.reqId = rid;
        req.b.metaRNFLineReadReq.bucketOffset = bucketOffset;
        if (!sendCoh(_gem5Port, _tickRef, _nodeId, req)) {
            auto it = _pendingLineReads.find(rid);
            if (it != _pendingLineReads.end()) {
                auto cb2 = std::move(it->second.callback);
                _pendingLineReads.erase(it);
                if (cb2) cb2(MetaRNFLineStatus::IoError, nullptr);
            }
        }
    }

    void handleLineReadResp(const CoherenceMessage &msg) {
        uint64_t rid = msg.h.reqId;
        auto it = _pendingLineReads.find(rid);
        if (it == _pendingLineReads.end()) {
            if (_debugH64Pdes) std::fprintf(stderr, "[DEBUG-H64-PDES-RESP-DROP] n=%d readResp reqId=%lu (not in pending)\n",
                         _nodeId, rid);
            return;
        }
        auto cb = std::move(it->second.callback);
        MetaRNFLineStatus st = msg.b.metaRNFLineReadResp.status;
        uint8_t data[64];
        memcpy(data, msg.b.metaRNFLineReadResp.data, 64);
        _pendingLineReads.erase(it);
        if (_debugH64Pdes) std::fprintf(stderr, "[DEBUG-H64-PDES-RESP-CB] n=%d readResp reqId=%lu st=%d depth=%d\n",
                     _nodeId, rid, (int)st, _reentrantDepth);
        if (cb) cb(st, data);
    }

    // ---- Phase 2+3: 64B line write with typed status ----
    // Req B: bucketOffset is logical flat-bucket index; UBAdapter maps to physical PA.
    static constexpr int kMaxLinePendingWrites = 32;
    struct PendingLineWrite {
        uint64_t reqId;
        uint64_t bucketOffset;
        std::function<void(MetaRNFLineStatus)> callback;
    };
    std::map<uint64_t, PendingLineWrite> _pendingLineWrites;
    uint64_t _nextLineWriteReqId = 0xB000000000000000ULL;

    void writeLine(uint64_t bucketOffset, const uint8_t* data64,
                   std::function<void(MetaRNFLineStatus)> cb) {
        // Bounded: pending writes + deferred writes ≤ kMaxLinePendingWrites (32)
        int combined = (int)_pendingLineWrites.size() + _deferredCount;
        if (combined >= kMaxLinePendingWrites) {
            if (cb) cb(MetaRNFLineStatus::RetryableBusy);
            return;
        }
        uint64_t rid = _nextLineWriteReqId++;
        if (_reentrantDepth > 0) {
            if (_deferredCount >= kMaxDeferredLineOps) {
                _deferredFull = true;
                if (cb) cb(MetaRNFLineStatus::RetryableBusy);
                return;
            }
            if (_debugH64Pdes) std::fprintf(stderr, "[DEBUG-H64-PDES-DEFER] n=%d write off=%lu depth=%d cnt=%d\n",
                         _nodeId, bucketOffset, _reentrantDepth, _deferredCount+1);
            auto& op = _deferredOps[_deferredCount++];
            op.isWrite = true; op.bucketOffset = bucketOffset;
            op.reqId = rid; op.writeCb = std::move(cb);
            if (data64) memcpy(op.data, data64, 64);
            return;
        }
        _pendingLineWrites[rid] = {rid, bucketOffset, std::move(cb)};
        CoherenceMessage req;
        req.h.type = CoherenceMessageType::MetaRNFLineWriteReq;
        req.h.srcNode = _nodeId; req.h.srcSocket = _socketId;
        req.h.dstNode = _nodeId; req.h.dstSocket = _socketId;
        req.h.reqId = rid;
        req.b.metaRNFLineWriteReq.bucketOffset = bucketOffset;
        memcpy(req.b.metaRNFLineWriteReq.data, data64, 64);
        if (!sendCoh(_gem5Port, _tickRef, _nodeId, req)) {
            auto it = _pendingLineWrites.find(rid);
            if (it != _pendingLineWrites.end()) {
                auto cb2 = std::move(it->second.callback);
                _pendingLineWrites.erase(it);
                if (cb2) cb2(MetaRNFLineStatus::IoError);
            }
        }
    }

    void handleLineWriteResp(const CoherenceMessage &msg) {
        uint64_t rid = msg.h.reqId;
        auto it = _pendingLineWrites.find(rid);
        if (it == _pendingLineWrites.end()) {
            if (_debugH64Pdes) std::fprintf(stderr, "[DEBUG-H64-PDES-WRESP-DROP] n=%d writeResp reqId=%lu (not in pending)\n",
                         _nodeId, rid);
            return;
        }
        auto cb = std::move(it->second.callback);
        MetaRNFLineStatus st = msg.b.metaRNFLineWriteResp.status;
        _pendingLineWrites.erase(it);
        if (_debugH64Pdes) std::fprintf(stderr, "[DEBUG-H64-PDES-WRESP-CB] n=%d writeResp reqId=%lu st=%d depth=%d\n",
                     _nodeId, rid, (int)st, _reentrantDepth);
        if (cb) cb(st);
    }

    // Handle MetaRNFWriteResp from gem5 (Phase D2)
    void handleWriteResp(const CoherenceMessage &msg) {
        uint64_t rid = msg.h.reqId;
        auto it = _pendingWrites.find(rid);
        if (it != _pendingWrites.end()) {
            bool durable = (msg.h.flags & 1) != 0;
            if (it->second.callback)
                it->second.callback(durable);
            _pendingWrites.erase(it);
        }
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
    // Legacy Schema A (Phase 3: retained only behind explicit opt-in)
    cc::glob::BackstoreSchemaA _schema;
    cc::glob::GroupIndex _groupIdx[cc::glob::BackstoreNumGroups];
    std::map<uint64_t, cc::glob::BackstorePage> _pages;
    uint64_t _nextPageId = 1;

    // Phase D5/D8: track locally-dirty pages
    std::set<uint64_t> _pagesDirty;
    std::map<uint64_t, std::vector<uint64_t>> _deferredReadsByPage;

    uint64_t _ubioDramDelayPs = 0;
    std::vector<PendingBackstoreFill> _pendingFills;
    std::vector<PendingBackstoreAck> _pendingBackstoreAcks;

    // Phase 3: H64 Host (production spill/fill path)
    bool _useH64;
    std::unique_ptr<cc::glob::BackstoreHostH64> _h64Host;

    DsmDataStore dsmData;
    // Exact H64 coverage becomes reportable only after every persisted group
    // has completed a validated scan. This is fixed group state, not a PA map.
    std::array<uint32_t, 256> h64GroupLive{};
    std::array<uint8_t, 256> h64GroupCovered{};
    bool h64CoverageScanInFlight = false;
    size_t h64CoverageCursor = 0;
    MetaRNFClient _metaRNF;

    explicit UbioBackstoreHost(UBCCController &ctrl, Port *gport, Port *nport,
                               int nid, int sid, uint64_t &t,
                               bool useH64 = false,
                               const cc::glob::H64HostConfig *h64cfg = nullptr)
        : ubcc(ctrl), gem5Port(gport), netPort(nport),
          nodeId(nid), socketId(sid), tickRef(t),
          _useH64(useH64), _metaRNF(t)
    {
        _metaRNF.init(gport, nid, sid);

        if (_useH64 && h64cfg) {
            _h64Host.reset(new cc::glob::BackstoreHostH64(*h64cfg, &_metaRNF));
            std::fprintf(stderr, "[UBIO-H64-HOST] node=%d socket=%d "
                    "H64 host initialized: %zu groups x %zu buckets/group, "
                    "logical_lines=%lu table_start=%zu\n",
                    nid, sid,
                    h64cfg->num_groups, h64cfg->buckets_per_group,
                    h64cfg->metadata_socket_lines, h64cfg->tableDataStartOffset());
        }
    }

    cc::glob::BackstorePage* _getPage(uint64_t pagePa) {
        auto it = _pages.find(pagePa);
        return (it != _pages.end()) ? &it->second : nullptr;
    }

    bool routeControlToTarget(const CoherenceMessage &msg) {
        if (msg.h.dstNode == nodeId && msg.h.dstSocket == socketId) {
            bool ok = sendCoh(gem5Port, tickRef, nodeId, msg);
            if (g_debugUbioPerf) {
                std::fprintf(stderr,
                             "[DEBUG-CTRL-ROUTE] node=%d sock=%d local type=%s reqId=%lu pa=0x%lx ok=%d tick=%lu\n",
                             nodeId, socketId, coherenceMsgTypeName(msg.h.type),
                             msg.h.reqId, msg.h.homeLinePa, ok ? 1 : 0, tickRef);
                std::fflush(stderr);
            }
            return ok;
        }
        bool ok = sendCoh(netPort, tickRef, gidOf(msg.h.dstNode, msg.h.dstSocket), msg, true);
        if (g_debugUbioPerf) {
            std::fprintf(stderr,
                         "[DEBUG-CTRL-ROUTE] node=%d sock=%d net type=%s reqId=%lu pa=0x%lx dst=%d:%d ok=%d tick=%lu\n",
                         nodeId, socketId, coherenceMsgTypeName(msg.h.type),
                         msg.h.reqId, msg.h.homeLinePa, msg.h.dstNode,
                         msg.h.dstSocket, ok ? 1 : 0, tickRef);
            std::fflush(stderr);
        }
        return ok;
    }
    bool sendRecallReq(const CoherenceMessage &msg) override { return routeControlToTarget(msg); }
    bool sendInvalidateReq(const CoherenceMessage &msg) override { return routeControlToTarget(msg); }
    bool sendUpgradeAckNotify(const CoherenceMessage &msg) override { return routeControlToTarget(msg); }
    bool sendGrantPush(const CoherenceMessage &msg) override {
        CoherenceMessage push = msg;
        // UBCC constructs replay pushes without direct access to the physical
        // home DSM backing. Match the pull-path HomeMemory fallback here so a
        // clean line restored from metadata after its owner wrote back carries
        // the durable 64B payload, not an implicit zero line.
        if (push.h.type == CoherenceMessageType::ReadResp &&
            !(push.h.flags & static_cast<uint32_t>(CFLAG_HAS_DATA)) &&
            dsmData.copyData(push.h.homeLinePa, push.b.readResp.grantData)) {
            push.h.flags |= static_cast<uint32_t>(CFLAG_HAS_DATA);
        }
        return routeControlToTarget(push);
    }
    uint64_t hostCurrentTick() const override { return tickRef; }

    void hostIssueBackstoreRead(uint64_t pa) override {
        // Phase 3: dispatch to H64 when active
        if (_useH64 && _h64Host) {
            _h64Host->lookup(pa, [this](const BackstoreCompletion &comp) {
                ubcc.onBackstoreH64Complete(comp);
            });
            return;
        }
        if (_useH64 && !_h64Host) {
            fprintf(stderr, "[BACKSTORE-READ-ERR] pa=0x%lx H64 host not initialized\n", pa);
            _pendingFills.push_back({tickRef + 1, pa, false, UBCCController::BackstoreEntry{}});
            return;
        }

        // Legacy Schema A path below (unchanged)
        UBCCController::BackstoreEntry e{};
        bool found = false;
        int g = _schema.groupForPa(pa);
        std::vector<uint64_t> pages = _schema.candidatePagesForLookup(pa, _groupIdx[g]);
        std::fprintf(stderr, "[BACKSTORE-READ] pa=0x%lx group=%d candidates=%zu head=0x%lx tail=0x%lx\n",
                     pa, g, pages.size(), _groupIdx[g].page_directory[0],
                     _groupIdx[g].page_directory[1]);

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
            // Always defer completion at least one tick.  A synchronous fill
            // can replay a waiter before its caller has finished enqueueing
            // its writeback payload.
            _pendingFills.push_back({tickRef + std::max<uint64_t>(1, _ubioDramDelayPs),
                                      pa, found, e});
            std::fprintf(stderr, "[BACKSTORE-READ-DONE] pa=0x%lx found=%d local=1\n",
                         pa, found ? 1 : 0);
            return;
        }

        // Phase D8: if any candidate page is dirty (MetaRNF write in-flight),
        // defer this fill until the durable callback fires.
        for (auto pagePa : pages) {
            if (_pagesDirty.count(pagePa)) {
                std::fprintf(stderr,
                    "[BACKSTORE-READ-WAIT-DURABLE] pa=0x%lx group=%d page=0x%lx\n",
                    pa, g, pagePa);
                std::fflush(stderr);
                _deferredReadsByPage[pagePa].push_back(pa);
                return;
            }
        }

        // Phase D13: chain-walk MetaRNF reads across all candidate pages.
        auto ctx = std::make_shared<ChainCtx>();
        ctx->idx = 0; ctx->maxSteps = (int)pages.size() + 4;

        // Store per-PA chain context so callback can access it
        _chainCtx[pa] = ctx;
        _chainPages[pa] = pages;
        _chainGroup[pa] = g;

        uint64_t firstPage = pages[0];
        std::fprintf(stderr, "[BACKSTORE-CHAIN-READ] pa=0x%lx group=%d page=0x%lx idx=0/%zu\n",
                     pa, g, firstPage, pages.size());
        std::fflush(stderr);
        ctx->idx = 1;

        _metaRNF.readPage(firstPage, [this, pa](const uint8_t* data256) {
            chainReadCallback(pa, data256);
        });
    }

    void hostScanH64BloomSlice(
        int slice, std::function<void(uint64_t)> onLive,
        std::function<void(bool)> completion) override {
        if (!_useH64 || !_h64Host || slice < 0 || slice >= ResidentDir::BloomGroups) {
            if (completion) completion(false);
            return;
        }
        struct SliceScan {
            int nextGroup = 0;
            int slice = 0;
            std::function<void(uint64_t)> onLive;
            std::function<void(bool)> completion;
        };
        auto scan = std::make_shared<SliceScan>();
        scan->slice = slice;
        scan->onLive = std::move(onLive);
        scan->completion = std::move(completion);
        auto issue = std::make_shared<std::function<void()>>();
        *issue = [this, scan, issue]() {
            while (scan->nextGroup < static_cast<int>(_h64Host->config().num_groups) &&
                   h64BloomSliceForGroup(scan->nextGroup) !=
                       static_cast<size_t>(scan->slice)) {
                ++scan->nextGroup;
            }
            if (scan->nextGroup >= static_cast<int>(_h64Host->config().num_groups)) {
                if (scan->completion) scan->completion(true);
                return;
            }
            const size_t group = static_cast<size_t>(scan->nextGroup++);
            auto liveCount = std::make_shared<uint32_t>(0);
            _h64Host->scanGroupLive(group,
                [scan, liveCount](const H64SlotEntry &entry) {
                    ++*liveCount;
                    if (scan->onLive) scan->onLive(entry.pa);
                },
                [this, scan, issue, group, liveCount](BackstoreStatus status) {
                    if (status != BackstoreStatus::Ok) {
                        if (scan->completion) scan->completion(false);
                        return;
                    }
                    recordH64CoverageGroup(group, *liveCount);
                    (*issue)();
                });
        };
        (*issue)();
    }

    bool h64ExactCoverageKnown() const {
        if (!_useH64 || !_h64Host ||
            _h64Host->config().num_groups != h64GroupCovered.size())
            return false;
        for (uint8_t covered : h64GroupCovered) {
            if (!covered) return false;
        }
        return true;
    }

    uint64_t h64ExactLiveCount() const {
        if (!h64ExactCoverageKnown()) return 0;
        uint64_t count = 0;
        for (uint32_t groupCount : h64GroupLive) count += groupCount;
        return count;
    }

    void invalidateH64Coverage(uint64_t pa) {
        if (!_useH64 || !_h64Host) return;
        const size_t group = BackstoreSchemaH64::groupForPaStatic(
            pa, _h64Host->config().num_groups, _h64Host->config().hash_seed);
        if (group < h64GroupCovered.size())
            h64GroupCovered[group] = 0;
    }

    void recordH64CoverageGroup(size_t group, uint32_t liveCount) {
        if (group < h64GroupLive.size()) {
            h64GroupLive[group] = liveCount;
            h64GroupCovered[group] = 1;
        }
    }

    void advanceH64Coverage() {
        if (!_useH64 || !_h64Host || h64CoverageScanInFlight ||
            !ubcc.allH64BloomSlicesValid() ||
            _h64Host->config().num_groups != h64GroupCovered.size() ||
            h64ExactCoverageKnown()) {
            return;
        }
        for (size_t n = 0; n < h64GroupCovered.size(); ++n) {
            const size_t group = (h64CoverageCursor + n) % h64GroupCovered.size();
            if (h64GroupCovered[group]) continue;
            h64CoverageCursor = (group + 1) % h64GroupCovered.size();
            h64CoverageScanInFlight = true;
            auto liveCount = std::make_shared<uint32_t>(0);
            _h64Host->scanGroupLive(group,
                [liveCount](const H64SlotEntry &) { ++*liveCount; },
                [this, group, liveCount](BackstoreStatus status) {
                    h64CoverageScanInFlight = false;
                    if (status == BackstoreStatus::Ok)
                        recordH64CoverageGroup(group, *liveCount);
                });
            return;
        }
    }

    // Phase D13: per-PA chain-walk state
    struct ChainCtx { size_t idx; int maxSteps; };
    std::map<uint64_t, std::shared_ptr<ChainCtx>> _chainCtx;
    std::map<uint64_t, std::vector<uint64_t>> _chainPages;
    std::map<uint64_t, int> _chainGroup;

    // Phase D8: replay reads deferred waiting for pagePa to become durable
    void replayDeferredReads(uint64_t pagePa) {
        auto it = _deferredReadsByPage.find(pagePa);
        if (it == _deferredReadsByPage.end()) return;
        auto pas = std::move(it->second);
        _deferredReadsByPage.erase(it);
        for (uint64_t pa : pas) {
            std::fprintf(stderr,
                "[BACKSTORE-READ-REPLAY-DURABLE] pa=0x%lx page=0x%lx\n",
                pa, pagePa);
            std::fflush(stderr);
            hostIssueBackstoreRead(pa);
        }
    }

    void chainReadCallback(uint64_t pa, const uint8_t* data256) {
        auto ctxIt = _chainCtx.find(pa);
        if (ctxIt == _chainCtx.end()) return;
        auto ctx = ctxIt->second;
        auto &pages = _chainPages[pa];
        int g = _chainGroup[pa];
        size_t curIdx = ctx->idx - 1; // the page we just read
        uint64_t pagePa = (curIdx < pages.size()) ? pages[curIdx] : 0;
        bool found2 = false;
        UBCCController::BackstoreEntry e2{};

        if (data256 && pagePa != 0) {
            cc::glob::BackstorePage pg;
            memcpy(&pg, data256, std::min(sizeof(pg), (size_t)256));
            auto localIt = _pages.find(pagePa);
            bool useLocal = (localIt != _pages.end());
            if (!useLocal && pg.hdr.page_id != 0) {
                _pages[pagePa] = pg; localIt = _pages.find(pagePa); useLocal = true;
            }
            cc::glob::BackstorePage* lp = useLocal ? &localIt->second : nullptr;
            cc::glob::BackstoreEntry schemaEntry;
            if (lp && _schema.lookupInPage(pa, *lp, schemaEntry) && !schemaEntry.deleted) {
                e2.state = static_cast<MESIState>(schemaEntry.state);
                e2.sharersMask = schemaEntry.sharersMask;
                e2.epoch = schemaEntry.epoch;
                found2 = true;
                std::fprintf(stderr, "[BACKSTORE-CHAIN-FOUND] pa=0x%lx page=0x%lx\n", pa, pagePa);
                std::fflush(stderr);
            }
            if (!found2 && lp && lp->hdr.next_page_ptr != 0) {
                bool already = false;
                for (auto p : pages) if (p == lp->hdr.next_page_ptr) { already = true; break; }
                if (!already) pages.push_back(lp->hdr.next_page_ptr);
            }
        }

        if (found2) {
            _chainCtx.erase(pa); _chainPages.erase(pa); _chainGroup.erase(pa);
            _pendingFills.push_back({tickRef + 1, pa, true, e2});
        } else if (ctx->idx < pages.size() && ctx->maxSteps > 0) {
            ctx->maxSteps--;
            uint64_t nextPage = pages[ctx->idx];
            std::fprintf(stderr, "[BACKSTORE-CHAIN-READ] pa=0x%lx group=%d page=0x%lx idx=%zu/%zu\n",
                         pa, g, nextPage, ctx->idx, pages.size());
            std::fflush(stderr);
            ctx->idx++;
            _metaRNF.readPage(nextPage, [this, pa](const uint8_t* d) { chainReadCallback(pa, d); });
        } else {
            std::fprintf(stderr, "[BACKSTORE-CHAIN-MISS] pa=0x%lx group=%d candidates=%zu\n", pa, g, pages.size());
            std::fflush(stderr);
            _chainCtx.erase(pa); _chainPages.erase(pa); _chainGroup.erase(pa);
            UBCCController::BackstoreEntry eMiss{};
            _pendingFills.push_back({tickRef + 1, pa, false, eMiss});
        }
    }

    void hostIssueBackstoreWrite(uint64_t pa) override {
        UBCCController::BackstoreEntry e{};
        if (!ubcc.snapshotResidentForBackstore(pa, e)) {
            std::fprintf(stderr, "[BACKSTORE-WRITE] pa=0x%lx snapshot=0\n", pa);
            _pendingBackstoreAcks.push_back({tickRef + 1, pa, false, false});
            return;
        }

        // Phase 3: dispatch to H64 when active
        if (_useH64 && _h64Host) {
            invalidateH64Coverage(pa);
            _h64Host->upsert(pa, e.state, e.sharersMask, e.epoch,
                [this, pa](const BackstoreCompletion &comp) {
                    ubcc.onBackstoreH64Complete(comp);
                });
            return;
        }

        // Legacy Schema A path below
        ubcc.publishBloomLive(pa);
        int g = _schema.groupForPa(pa);
        cc::glob::BackstoreEntry schemaEntry;
        schemaEntry.pa = pa;
        schemaEntry.state = static_cast<cc::glob::UBCCMESIState>(e.state);
        schemaEntry.sharersMask = e.sharersMask;
        schemaEntry.epoch = e.epoch;
        schemaEntry.deleted = false;

        auto plan = _schema.planUpsert(pa, schemaEntry, _groupIdx[g]);
        if (plan.needs_new_page) plan.target_page_pa = _nextPageId++;
        std::fprintf(stderr, "[BACKSTORE-WRITE] pa=0x%lx group=%d page=0x%lx new=%d state=%d sharers=0x%lx epoch=%lu\n",
                     pa, g, plan.target_page_pa, plan.needs_new_page ? 1 : 0,
                     static_cast<int>(schemaEntry.state), schemaEntry.sharersMask, schemaEntry.epoch);
        cc::glob::BackstorePage* p = nullptr;
        if (plan.needs_new_page) {
            cc::glob::BackstorePage np; np.clear(); np.hdr.page_id = plan.target_page_pa;
            _pages[plan.target_page_pa] = np; p = &_pages[plan.target_page_pa];
        } else if (plan.needs_read_before) {
            p = _getPage(plan.target_page_pa);
        }
        if (!p) {
            std::fprintf(stderr, "[BACKSTORE-WRITE-FAIL] pa=0x%lx page=0x%lx reason=no_page\n", pa, plan.target_page_pa);
            _pendingBackstoreAcks.push_back({tickRef + 1, pa, false, false});
            return;
        }

        // D1 overflow allocation
        if (!plan.needs_new_page && p->isFull()) {
            uint64_t oldPagePa = plan.target_page_pa;
            uint64_t newPagePa = _nextPageId++;
            p->hdr.next_page_ptr = newPagePa;
            std::fprintf(stderr, "[BACKSTORE-OVERFLOW-ALLOC] pa=0x%lx group=%d oldPage=0x%lx newPage=0x%lx entries=%u\n",
                         pa, g, oldPagePa, newPagePa, p->hdr.entry_count);
            std::fflush(stderr);
            _pagesDirty.insert(oldPagePa);
            _metaRNF.writePage(oldPagePa, *p);
            _pagesDirty.erase(oldPagePa);
            cc::glob::BackstorePage np; np.clear(); np.hdr.page_id = newPagePa;
            _pages[newPagePa] = np;
            cc::glob::BackstorePage* newP = &_pages[newPagePa];
            auto newPlan = plan;
            newPlan.needs_new_page = true; newPlan.target_page_pa = newPagePa; newPlan.needs_read_before = false;
            _schema.applyUpsert(*newP, pa, schemaEntry, newPlan);
            _schema.updateIndexAfterWrite(_groupIdx[g], newPlan, newPagePa);
            // D6: durable write of new overflow page via callback
            std::fprintf(stderr, "[BACKSTORE-WRITE-PENDING] pa=0x%lx page=0x%lx\n", pa, newPagePa);
            std::fflush(stderr);
            _pagesDirty.insert(newPagePa);
            uint64_t capPa = pa; uint64_t capPage = newPagePa;
            _metaRNF.writePageD2(capPage, *newP, [this, capPa, capPage](bool durable) {
                if (durable) {
                    std::fprintf(stderr, "[BACKSTORE-WRITE-DURABLE] pa=0x%lx page=0x%lx\n", capPa, capPage);
                    _pagesDirty.erase(capPage);
                    replayDeferredReads(capPage);
                    _pendingBackstoreAcks.push_back({tickRef + 1, capPa, false, true});
                } else {
                    std::fprintf(stderr, "[BACKSTORE-WRITE-FAIL] pa=0x%lx page=0x%lx reason=remote\n", capPa, capPage);
                }
                std::fflush(stderr);
            });
            return;
        }

        _schema.applyUpsert(*p, pa, schemaEntry, plan);
        _schema.updateIndexAfterWrite(_groupIdx[g], plan, plan.target_page_pa);
        // D6: durable write via callback
        std::fprintf(stderr, "[BACKSTORE-WRITE-PENDING] pa=0x%lx page=0x%lx\n", pa, plan.target_page_pa);
        std::fflush(stderr);
        _pagesDirty.insert(plan.target_page_pa);
        uint64_t capPa2 = pa; uint64_t capPage2 = plan.target_page_pa;
        _metaRNF.writePageD2(capPage2, *p, [this, capPa2, capPage2](bool durable) {
            if (durable) {
                std::fprintf(stderr, "[BACKSTORE-WRITE-DURABLE] pa=0x%lx page=0x%lx\n", capPa2, capPage2);
                _pagesDirty.erase(capPage2);
                replayDeferredReads(capPage2);
                _pendingBackstoreAcks.push_back({tickRef + 1, capPa2, false, true});
            } else {
                std::fprintf(stderr, "[BACKSTORE-WRITE-FAIL] pa=0x%lx page=0x%lx reason=remote\n", capPa2, capPage2);
            }
            std::fflush(stderr);
        });
    }


    void hostIssueBackstoreDelete(uint64_t pa) override {
        // Phase 3: dispatch to H64 when active
        if (_useH64 && _h64Host) {
            // Use a dummy epoch for now; the UBCC will pass the correct one
            // in the full integration.
            uint64_t deleteEpoch = ubcc.getEpochForLine(pa);
            invalidateH64Coverage(pa);
            _h64Host->erase(pa, deleteEpoch,
                [this, pa](const BackstoreCompletion &comp) {
                    ubcc.onBackstoreH64Complete(comp);
                });
            return;
        }

        // Legacy Schema A path below
        int g = _schema.groupForPa(pa);
        auto plan = _schema.planDelete(pa, _groupIdx[g]);
        cc::glob::BackstorePage* p = _getPage(plan.target_page_pa);
        bool existed = p && _schema.applyDelete(*p, pa, plan);
        ubcc.directory().bloomRemove(pa);
        // Phase 3: Write-through modified page back to MetaRNF
        if (existed && p) {
            _metaRNF.writePage(plan.target_page_pa, *p);
        }
        _pendingBackstoreAcks.push_back({tickRef + 1, pa, true, existed});
    }

    void readDsmData(uint64_t pa, std::function<void(const uint8_t*)> cb) override {
        readDsmDataAsync(pa, [cb = std::move(cb)](DsmDataStatus, const uint8_t *data) {
            if (cb) cb(data);
        });
    }
    void readDsmDataAsync(uint64_t pa,
                          std::function<void(DsmDataStatus, const uint8_t*)> completion) override {
        auto failCompletion = completion;
        if (!dsmData.readData(pa, tickRef, std::move(completion)) && failCompletion)
            failCompletion(DsmDataStatus::RetryableBusy, nullptr);
    }
    void writeDsmData(uint64_t pa, const uint8_t *buf) override { dsmData.writeData(pa, buf, tickRef); }
    void writeDsmDataAsync(uint64_t pa, const uint8_t *buf,
                           std::function<void(bool)> completion) override {
        writeDsmDataAsyncStatus(pa, buf,
            [completion = std::move(completion)](DsmDataStatus status) {
                if (completion) completion(status == DsmDataStatus::Ok);
            });
    }
    void writeDsmDataAsyncStatus(uint64_t pa, const uint8_t *buf,
                                 std::function<void(DsmDataStatus)> completion) override {
        dsmData.writeDataAsync(pa, buf, tickRef, std::move(completion));
    }

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

    void drainPendingBackstoreAcks(uint64_t tick) {
        auto it = _pendingBackstoreAcks.begin();
        while (it != _pendingBackstoreAcks.end()) {
            if (tick >= it->fireTick) {
                if (it->isDelete)
                    ubcc.onBackstoreDeleteAck(it->pa, it->existed);
                else {
                    ubcc.onBackstoreWriteAck(it->pa);
                    // Phase D5: clear dirty flags for pages that may
                    // have been pending when this PA was written.
                    // We don't know exact pagePa, but the ack means
                    // the write is durable; any reads will now see
                    // the MetaRNF data.
                }
                it = _pendingBackstoreAcks.erase(it);
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
        // Priority: transaction payload > immediate grant data > authoritative
        // home memory. No PA-keyed software data cache participates.
        // If DSM persistence is pending for this PA, defer with RetryableBusy.
        bool hasGrantData = false;
        hasGrantData =
            ubcc.copyOutstandingGrantData(msg.h.homeLinePa, grantData) ||
            ubcc.copyImmediateGrantData(msg.h.homeLinePa, grantData) ||
            host.dsmData.copyData(msg.h.homeLinePa, grantData.data);

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
        bool success = msg.b.writebackReq.hasData
            ? ubcc.processWritebackWithData(msg.h.homeLinePa, msg.h.requesterNode,
                                            msg.h.epoch, keepAsClean,
                                            msg.b.writebackReq.data)
            : ubcc.processWriteback(msg.h.homeLinePa, msg.h.requesterNode,
                                    msg.h.epoch, keepAsClean);
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
        if (g_debugUbioPerf) {
            std::fprintf(stderr,
                         "[DEBUG-UBIO-CLEAR] ubcc-enter nid=%d type=ClearReq reqId=%lu pa=0x%lx srcNode=%d dstNode=%d epoch=%lu\n",
                         nid, msg.h.reqId, msg.h.homeLinePa,
                         msg.h.srcNode, msg.h.dstNode, msg.h.epoch);
        }
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
        if (g_debugUbioPerf) {
            std::fprintf(stderr,
                         "[DEBUG-UBIO-CLEAR] ubcc-exit nid=%d type=ClearResp reqId=%lu pa=0x%lx accepted=%d dstNode=%d\n",
                         nid, msg.h.reqId, msg.h.homeLinePa,
                         accepted ? 1 : 0, response.h.dstNode);
        }
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

// Phase 0→3: Backstore schema selection (design §5, §11)
//
// Phase 3: H64 is now the PRODUCTION default when overflow_policy=spill.
// Legacy Schema A is retained behind explicit opt-in only.
// Schema C remains unwired.
enum class BackstoreSchemaMode {
    Disabled,
    LegacySchemaA,
    ExperimentalSchemaC,
    H64,
    Auto,
};

static const char*
backstoreSchemaModeName(BackstoreSchemaMode m) {
    switch (m) {
        case BackstoreSchemaMode::Disabled:           return "Disabled";
        case BackstoreSchemaMode::LegacySchemaA:      return "legacy_schema_a";
        case BackstoreSchemaMode::ExperimentalSchemaC: return "experimental_schema_c";
        case BackstoreSchemaMode::H64:                return "h64";
        case BackstoreSchemaMode::Auto:               return "Auto";
    }
    return "Unknown";
}

// Global schema selection (overridden by --backstore-schema=)
static BackstoreSchemaMode g_schemaMode = BackstoreSchemaMode::Auto;

// Global UBIO metadata DRAM config (Phase 0: capacity reporting)
static uint64_t g_metadataDramTotalBytes = 128ULL * 1024 * 1024; // default 128 MiB

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
        if (!std::strncmp(argv[i], "--dir-overflow-policy=", 22)) {
            const char *p = argv[i] + 22;
            if (!std::strcmp(p, "naive") || !std::strcmp(p, "naive-evict")) {
                g_overflowPolicy = ResidentOverflowPolicy::NaiveEvict;
            } else {
                g_overflowPolicy = ResidentOverflowPolicy::Spill;
            }
        }
        // Phase 3: H64 is now active — no longer fatal
        if (!std::strncmp(argv[i], "--backstore-schema=", 19)) {
            const char *p = argv[i] + 19;
            if (!std::strcmp(p, "h64") || !std::strcmp(p, "H64") ||
                !std::strcmp(p, "h64_future")) {
                g_schemaMode = BackstoreSchemaMode::H64;
            } else if (!std::strcmp(p, "experimental_schema_c") ||
                       !std::strcmp(p, "schema_c") ||
                       !std::strcmp(p, "legacy_schema_c")) {
                std::fprintf(stderr,
                    "[UBIO-FATAL] --backstore-schema=%s: "
                    "Schema C exists in source but is not wired in ubio_main. "
                    "Use --backstore-schema=legacy_schema_a, h64, or disabled.\n", p);
                std::exit(1);
            } else if (!std::strcmp(p, "disabled") || !std::strcmp(p, "none"))
                g_schemaMode = BackstoreSchemaMode::Disabled;
            else if (!std::strcmp(p, "experimental_schema_a") ||
                     !std::strcmp(p, "schema_a") ||
                     !std::strcmp(p, "legacy_schema_a"))
                g_schemaMode = BackstoreSchemaMode::LegacySchemaA;
            else if (!std::strcmp(p, "auto"))
                g_schemaMode = BackstoreSchemaMode::Auto;
            else {
                std::fprintf(stderr,
                    "[UBIO-FATAL] --backstore-schema=%s: unrecognized. "
                    "Valid: legacy_schema_a, h64, disabled, auto.\n", p);
                std::exit(1);
            }
        }
        // Phase 0: on-chip budget overrides (blc/desc only; group_index is fixed)
        if (!std::strncmp(argv[i], "--blc-bytes=", 12))
            g_rdcfg.blc_bytes = (size_t)std::strtoull(argv[i] + 12, nullptr, 10);
        if (!std::strncmp(argv[i], "--desc-scratch-bytes=", 21))
            g_rdcfg.desc_scratch_bytes = (size_t)std::strtoull(argv[i] + 21, nullptr, 10);
        // Phase 0: metadata DRAM capacity (for startup manifest)
        if (!std::strncmp(argv[i], "--metadata-dram-bytes=", 22))
            g_metadataDramTotalBytes = std::strtoull(argv[i] + 22, nullptr, 10);
    }

    // Phase 0: Naive eviction never persists or probes metadata backstore,
    // so it has no use for Bloom bits.  However, GroupIndex[16] is always
    // an in-object member (4096 bytes).  We zero the bloom budget but
    // DO NOT zero group_index_bytes — the GroupIndex storage still exists
    // and must be counted in the on-chip budget.  (Previously zeroing
    // index_bytes caused total on-chip to exceed sram_bytes by 4 KiB.)
    if (g_rdcfg.bloom_bytes == 0) {
        // Keep group_index_bytes at its true value (4096); it reflects the real
        // in-object storage.  BLC and desc are not needed without bloom/backstore.
        g_rdcfg.blc_bytes = 0;
        g_rdcfg.desc_scratch_bytes = 0;
    }

    // Phase 3: resolve schema mode
    if (g_schemaMode == BackstoreSchemaMode::Auto) {
        // Phase 3: default spill → H64 (production); naive → Disabled
        if (g_overflowPolicy == ResidentOverflowPolicy::Spill)
            g_schemaMode = BackstoreSchemaMode::H64;
        else
            g_schemaMode = BackstoreSchemaMode::Disabled;
    }

    // Phase 3: budget constraints. H64 and LegacySchemaA both use
    // Bloom + ResidentDir; BLC/desc_scratch reserved for future H64 profile.
    // LegacySchemaA forces them to 0 (not implemented in Schema A).
    if (g_schemaMode == BackstoreSchemaMode::LegacySchemaA) {
        g_rdcfg.blc_bytes = 0;
        g_rdcfg.desc_scratch_bytes = 0;
    }

    // Phase 0: group_index_bytes must match the real allocation.
    // GroupIndex[16] is a fixed-size member (4096 B).  Any other value
    // would misrepresent the on-chip budget.  Tiny test configs
    // (sram < 64 KiB) are exempt from this check.
    {
        constexpr size_t kRealGroupIndexStorage = ResidentDir::BloomGroups
                                                  * sizeof(GroupIndex);
        static_assert(kRealGroupIndexStorage == 4096,
                      "GroupIndex[16] must be exactly 4096 bytes");
        if (g_rdcfg.sram_bytes >= 64 * 1024) {
            // For production configs: config value must match reality.
            size_t eff = g_rdcfg.effectiveGroupIndexBytes();
            if (eff != kRealGroupIndexStorage) {
                std::fprintf(stderr,
                    "[UBIO-FATAL] group_index_bytes=%zu must equal %zu "
                    "(sizeof(GroupIndex)*BloomGroups). "
                    "Remove --group-index-bytes= override or use "
                    "--sram-bytes < 65536 for tiny test configs.\n",
                    eff, kRealGroupIndexStorage);
                std::exit(1);
            }
        }
    }

    // ── Debug gates: default-off, opt-in via env vars ─────────────────
    if (const char *env = std::getenv("UBIO_DEBUG_PERF")) {
        g_debugUbioPerf = (std::atoi(env) != 0);
        if (g_debugUbioPerf) std::fprintf(stderr, "[UBIO-DEBUG] perf tracing enabled\n");
    }
    bool ubccDebugClear = false;
    if (const char *env = std::getenv("UBCC_DEBUG_CLEAR")) {
        ubccDebugClear = (std::atoi(env) != 0);
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
    cc::setUbioTickSource(&tick);

    UBCCController ubcc(nid, sid, nullptr, 64,
                           g_rdcfg.bloom_bytes,
                           0, g_numSockets, g_numNodes, &g_rdcfg);
    ubcc.setBatchRsEnabled(g_batchRs);
    ubcc.setResidentOverflowPolicy(g_overflowPolicy);
    if (ubccDebugClear) {
        ubcc.setDebugClearTrace(true);
    }
    // Phase 3: H64 mode disables Bloom-negative shortcut
    if (g_schemaMode == BackstoreSchemaMode::H64) {
        ubcc.setH64BloomAllMisses(true);
    }

    // Phase 3: Build H64HostConfig if schema is H64 (production default)
    bool useH64 = (g_schemaMode == BackstoreSchemaMode::H64);
    cc::glob::H64HostConfig h64cfg;
    if (useH64) {
        // Logical metadata sizing: total 64B lines available in metadata DRAM.
        // Control records occupy offsets 0..num_groups-1; table data starts at num_groups.
        uint64_t perSocketLines = (g_metadataDramTotalBytes / ((uint64_t)g_numSockets)) / 64ULL;
        h64cfg.num_groups = 256;
        h64cfg.buckets_per_group = (perSocketLines >= h64cfg.num_groups)
            ? (perSocketLines - h64cfg.num_groups) / h64cfg.num_groups : 1;
        if (h64cfg.buckets_per_group < 1) h64cfg.buckets_per_group = 1;
        if (h64cfg.buckets_per_group > 16384) h64cfg.buckets_per_group = 16384;
        h64cfg.metadata_socket_lines = perSocketLines;
        h64cfg.hash_seed = 0x9e3779b97f4a7c15ULL;
        h64cfg.max_active_rmw = 8;
        h64cfg.max_pending_ops = 128;
        h64cfg.max_waiters_per_bucket = 8;
    }

    UbioBackstoreHost host(ubcc, gem5Port, netPort, nid, sid, tick,
                            useH64, useH64 ? &h64cfg : nullptr);
    // T_ubio_dram: argv --dram-delay-ps= has priority (no env fallback)
    host._ubioDramDelayPs = g_dramDelayPs;
    ubcc.setHost(&host);
    ubcc.setOutbound(&host);

    // ── Phase 3: Startup manifest & diagnostics ──────────────────────
    {
        const auto &layout = ubcc.directory().layout();
        constexpr size_t groupIndexStorage = ResidentDir::BloomGroups
                                             * sizeof(GroupIndex);
        // Phase 3: When H64 is active, the Host _groupIdx[16] (4 KiB) is
        // eliminated; no legacy host page-directory tracking.
        size_t hostLegacyGroupIndexDupe =
            (g_schemaMode == BackstoreSchemaMode::LegacySchemaA)
                ? groupIndexStorage : 0;
        const size_t dir_bytes   = layout.dir_bytes;
        const size_t bloom_bytes = g_rdcfg.bloom_bytes;
        const size_t blc_reserved= g_rdcfg.blc_bytes;
        const size_t desc_reserved=g_rdcfg.desc_scratch_bytes;
        const size_t total_on_chip = dir_bytes + bloom_bytes
                                     + groupIndexStorage
                                     + hostLegacyGroupIndexDupe
                                     + blc_reserved + desc_reserved;
        const size_t per_socket_dram = g_metadataDramTotalBytes
                                       / ((size_t)g_numSockets);
        const size_t capacity = layout.capacity;
        std::fprintf(stderr,
            "[UBIO-MANIFEST] node=%d socket=%d num_sockets=%d\n"
            "[UBIO-MANIFEST] schema_mode=%s overflow_policy=%s\n"
            "[UBIO-MANIFEST] metadata_dram_configured=%lu MiB per_socket=%lu MiB "
                "(authoritative range: see [EPBACKEND-MANIFEST])\n"
            "[UBIO-MANIFEST] resident_capacity=%zu entries (%d-way x %d-set)\n"
            "[UBIO-MANIFEST] on_chip_budget_total=%zu KiB (limit=512 KiB)\n"
            "[UBIO-MANIFEST] on_chip_breakdown: dir=%zu KiB bloom=%zu KiB "
                "residentGroupIndex=%zu KiB hostLegacyGroupIndex=%zu KiB "
                "blc_reserved=%zu KiB desc_reserved=%zu KiB\n",
            nid, sid, g_numSockets,
            backstoreSchemaModeName(g_schemaMode),
            g_overflowPolicy == ResidentOverflowPolicy::Spill ? "spill" : "naive",
            g_metadataDramTotalBytes / (1024 * 1024),
            per_socket_dram / (1024 * 1024),
            capacity, layout.ways, layout.num_sets,
            total_on_chip / 1024,
            dir_bytes / 1024, bloom_bytes / 1024,
            groupIndexStorage / 1024,
            hostLegacyGroupIndexDupe / 1024,
            blc_reserved / 1024, desc_reserved / 1024);

        // Phase 3: H64 active status
        if (g_schemaMode == BackstoreSchemaMode::H64) {
            std::fprintf(stderr,
                "[UBIO-MANIFEST] H64_ACTIVE: bounded_txn_max=%d active_rmw_max=%d "
                "num_groups=%zu buckets_per_group=%zu "
                "metadata_lines=%lu table_start_offset=%zu "
                "(logical offsets only, clean [DEBUG-H64-*] gating)\n",
                h64cfg.max_pending_ops, h64cfg.max_active_rmw,
                h64cfg.num_groups, h64cfg.buckets_per_group,
                h64cfg.metadata_socket_lines, h64cfg.tableDataStartOffset());
        } else if (g_schemaMode == BackstoreSchemaMode::LegacySchemaA) {
            std::fprintf(stderr,
                "[UBIO-MANIFEST] H64_INACTIVE: using legacy_schema_a "
                "(page-chain with unbounded page cache)\n");
        }

        std::fflush(stderr);

        // Hard budget assertion (includes host duplicate)
        if (total_on_chip > 512 * 1024) {
            std::fprintf(stderr,
                "[UBIO-FATAL] total on-chip budget %zu KiB exceeds 512 KiB "
                "limit. Reduce bloom/blc/desc or increase sram.\n",
                total_on_chip / 1024);
            std::exit(1);
        }
    }
    // ── End Phase 0 manifest ─────────────────────────────────────────

    bool gem5Done = false, netDone = false;
    using BarrierKey = std::pair<uint32_t, uint32_t>;
    using BarrierArrivals = std::map<int, uint32_t>;
    std::map<BarrierKey, BarrierArrivals> barrierArrivals;
    auto releaseBarrier = [&](const BarrierKey &bk) {
        const uint32_t expected = __builtin_popcount(bk.first) * g_numSockets;
        auto it = barrierArrivals.find(bk);
        if (it == barrierArrivals.end() || it->second.size() < expected)
            return;

        bool allSent = true;
        for (const auto &arrival : it->second) {
            const int targetPlane = arrival.first;
            const int targetNode = targetPlane / g_numSockets;
            const int targetSocket = targetPlane % g_numSockets;
            Port *deliveryPort = (targetNode == nid && targetSocket == sid)
                ? gem5Port : netPort;
            framework::MemMessage* rel = deliveryPort->allocateSendBuffer(tick);
            if (!rel) {
                allSent = false;
                continue;
            }
            rel->hdr.timestamp = tick;
            rel->hdr.type = static_cast<uint32_t>(MemMessageType::PAYLOAD);
            rel->hdr.targetId = gidOf(targetNode, targetSocket);
            CoherenceMessage rmsg;
            rmsg.h.type = CoherenceMessageType::BarrierRelease;
            rmsg.b.barrier.mask = bk.first;
            // Each isolated gem5 process may have an independently observed
            // generation; release exactly the generation it reported.
            rmsg.b.barrier.seq = arrival.second;
            rel->setPayload(rmsg);
            if (!deliveryPort->send(rel))
                allSent = false;
        }
        if (allSent) {
            barrierArrivals.erase(it);
        }
    };

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
                const TerminatePayload *term = m->getPayload<TerminatePayload>();
                if (fromNetwork && term && term->reason == 2) {
                    const int peerPlane = static_cast<int>(term->sender);
                    const int peerNode = peerPlane / g_numSockets;
                    const int peerSocket = peerPlane % g_numSockets;
                    ubcc.markPeerPlaneExited(peerNode, peerSocket);
                    m = port->recv(tick, &st);
                    continue;
                }
                if (!fromNetwork) {
                    // TERMINATE from local gem5: mark gem5 done and forward
                    // to networksim so other nodes can exclude this peer from
                    // PDES safeTs (TC90/TC98 deadlock fix).
                    ubcc.directory().dumpStatsJson();
                    std::fprintf(stderr, "[UBCC-STATS] %s\n", ubcc.dumpStatsJson().c_str());
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
                    // networksim terminates after receiving one marker from
                    // every UBIO; it does not send a marker back. Once the
                    // local marker is enqueued this UBIO has no remaining
                    // producer, so do not wait forever for a nonexistent ack.
                    netDone = true;
                } else {
                    // NetworkSim emits its terminal marker only after every
                    // local UBIO has terminated.  It is therefore the peer
                    // shutdown acknowledgement, not merely another node's
                    // guest completion.  Without this, gem5Done becomes true
                    // but netDone never does and each UBIO leaks after a clean
                    // test completion.
                    *doneFlag = true;
                }
                if (*doneFlag) break;
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
                        if (g_debugUbioPerf)
                            std::fprintf(stderr, "[DEBUG-UBIO-BARRIER] n%d release fwd mask=0x%x\n",
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
                    int src = static_cast<int>(m->hdr.sourceId);
                    // Arrival generations are local to isolated gem5
                    // processes. Aggregate one in-flight generation per mask
                    // and retain each plane's generation for its release.
                    BarrierKey bk{mask, 0};
                    const int leaderNode = __builtin_ctz(mask);
                    if (nid == leaderNode && sid == 0) {
                        const int sourceNode = src / g_numSockets;
                        if (sourceNode < 0 || sourceNode >= 32 ||
                            (mask & (1U << sourceNode)) == 0) {
                            std::fprintf(stderr,
                                         "[UBIO-BARRIER-WARN] n%d ignored source=%d mask=0x%x\n",
                                         nid, src, mask);
                            m = port->recv(tick, &st);
                            continue;
                        }
                        barrierArrivals[bk][src] = seq;
                        releaseBarrier(bk);
                    } else if (!fromNetwork && netPort) {
                        // A single deterministic leader aggregates arrivals.
                        // Broadcast coordination allowed different nodes to
                        // independently release incompatible generations.
                        framework::MemMessage* fwd = netPort->allocateSendBuffer(tick);
                        if (fwd) {
                            *fwd = *m;
                            fwd->hdr.timestamp = tick;
                            fwd->hdr.targetId = gidOf(leaderNode, 0);
                            netPort->send(fwd);
                        }
                    }
                m = port->recv(tick, &st);
                continue;
            }

            std::fprintf(stderr, "[ubio:%d] %s recv %s reqId=%lu src=%u dst=%u\n",
                         nid, fromNetwork ? "net" : "gem5",
                         coherenceMsgTypeName(coh->h.type), coh->h.reqId,
                         m->hdr.sourceId, m->hdr.targetId);
            if (TracePerfPolicy::get().shouldEmit("ubio")) {
                std::fprintf(stderr, "[TRACE-PERF] %lu|%d|ubio|%lu|0x%lx|%s|%s\n",
                             m->hdr.timestamp, nid, coh->h.reqId, coh->h.homeLinePa,
                             fromNetwork ? "RECV_NET" : "RECV_GEM5",
                             coherenceMsgTypeName(coh->h.type));
            }

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

            if (g_debugUbioPerf && (coh->h.type == CoherenceMessageType::ClearReq ||
                coh->h.type == CoherenceMessageType::ClearResp)) {
                std::fprintf(stderr,
                             "[DEBUG-UBIO-CLEAR] recv nid=%d from=%s type=%s reqId=%lu pa=0x%lx srcNode=%d dstNode=%d requester=%d epoch=%lu\n",
                             nid, fromNetwork ? "net" : "gem5",
                             coherenceMsgTypeName(coh->h.type),
                             coh->h.reqId, coh->h.homeLinePa,
                             coh->h.srcNode, coh->h.dstNode,
                             coh->h.requesterNode, coh->h.epoch);
            }

            if (g_debugUbioPerf && (coh->h.type == CoherenceMessageType::RecallReq ||
                coh->h.type == CoherenceMessageType::RecallResp)) {
                std::fprintf(stderr, "[DEBUG-RECALL-TRACE-C] ubio:%d %s %s reqId=%lu cohDst=%d\n",
                             nid, fromNetwork ? "net" : "gem5",
                             coherenceMsgTypeName(coh->h.type), coh->h.reqId, coh->h.dstNode);
            }

            if (g_debugUbioPerf && coh->h.type == CoherenceMessageType::ReadReq) {
                std::fprintf(stderr,
                             "[DEBUG-UBIO-RR-PATH] reqId=%lu from=%s srcNode=%d dstNode=%d nid=%d enter_dstNode_check=%s homeLinePa=0x%lx\n",
                             coh->h.reqId, fromNetwork ? "net" : "gem5",
                             coh->h.srcNode, coh->h.dstNode, nid,
                             (coh->h.dstNode != nid) ? "true" : "false",
                             coh->h.homeLinePa);
            }

            if (coh->h.dstNode != nid || coh->h.dstSocket != sid) {
                // If this PA belongs to our local DSM plane, force local processing
                bool isDsm = ubcc.isDsmAddr(coh->h.homeLinePa);
                if (g_debugUbioPerf && coh->h.type == CoherenceMessageType::ReadReq) {
                    std::fprintf(stderr,
                                 "[DEBUG-UBIO-RR-PATH] reqId=%lu dstNode!=nid true, isDsmAddr=%s -> pass_non_dsm_check=%s homeLinePa=0x%lx\n",
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
                        if (g_debugUbioPerf) {
                            std::fprintf(stderr, "[DEBUG-TRACE-2] n%d FWD %s dst=%d:%d via net\n",
                                         nid, coherenceMsgTypeName(coh->h.type),
                                         coh->h.dstNode, coh->h.dstSocket);
                        }
                        bool sent = sendCoh(netPort, tick,
                                            gidOf(coh->h.dstNode, coh->h.dstSocket), *coh, true);
                        if (g_debugUbioPerf && coh->h.type == CoherenceMessageType::ReadReq) {
                            std::fprintf(stderr,
                                         "[DEBUG-UBIO-RR-PATH] reqId=%lu forward_sendCoh_called=true sendCoh_ret=%s dstNode=%d\n",
                                         coh->h.reqId, sent ? "true" : "false", coh->h.dstNode);
                        }
                    } else {
                        if (g_debugUbioPerf) {
                            std::fprintf(stderr, "[ubio:%d] DROP cross-node %s (no net)\n",
                                         nid, coherenceMsgTypeName(coh->h.type));
                        }
                        if (g_debugUbioPerf && coh->h.type == CoherenceMessageType::ReadReq) {
                            std::fprintf(stderr,
                                         "[DEBUG-UBIO-RR-PATH] reqId=%lu forward_sendCoh_called=false reason=no_netPort\n",
                                         coh->h.reqId);
                        }
                    }
                    m = port->recv(tick, &st);
                    continue;
                }
            }

            if (fromNetwork) {
                for (int rep = 0; rep < faultCopies; ++rep) {
                    CoherenceMessage response;
                    bool hasResponse = false;
                    host._metaRNF.enterReentrant();
                    bool handled = handleUbccMessage(ubcc, host, nid, *coh, response, hasResponse);
                    host._metaRNF.leaveReentrant();
                    if (handled && coh->h.type == CoherenceMessageType::RecallResp) {
                        // RECALL.DONE only flips state inside the home UBCC; there is
                        // no normal response packet back to gem5. Mirror the RecallResp
                        // to the local UBAdapter as a wake-only notification so the
                        // requester's EP-SNF retries immediately instead of waiting for
                        // the 20k-cycle fallback timer.
                        bool sentToGem5 = sendCoh(gem5Port, tick,
                            gidOf(nid, sid), *coh);
                        if (g_debugUbioPerf) {
                            std::fprintf(stderr,
                                         "[DEBUG-TRACE-4-RECALL] n%d net->gem5 recall-done sendCoh_ret=%s reqId=%lu dstSocket=%d\n",
                                         nid, sentToGem5 ? "true" : "false",
                                         coh->h.reqId, sid);
                        }
                    }
                    if (handled && hasResponse) {
                        if (g_debugUbioPerf) {
                            std::fprintf(stderr, "[DEBUG-TRACE-3] n%d net->UBCC grant, sending %s back to %d:%d\n",
                                         nid, coherenceMsgTypeName(response.h.type),
                                         coh->h.srcNode, coh->h.srcSocket);
                        }
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
                                if (host.dsmData.copyData(coh->h.homeLinePa,
                                                          resp.b.recallResp.data)) {
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
                            if (g_debugUbioPerf) {
                                std::fprintf(stderr, "[DEBUG-TRACE-4] n%d net->gem5 fwd %s reqId=%lu\n",
                                             nid, coherenceMsgTypeName(coh->h.type), coh->h.reqId);
                            }
                            bool sentToGem5 = sendCoh(gem5Port, tick,
                                gidOf(coh->h.srcNode, coh->h.srcSocket), *coh);
                            if (g_debugUbioPerf) {
                                std::fprintf(stderr,
                                             "[DEBUG-TRACE-4-SEND] n%d net->gem5 sendCoh_ret=%s type=%s reqId=%lu dstModule=%d dstPort=%d srcSocket=%d\n",
                                             nid, sentToGem5 ? "true" : "false",
                                             coherenceMsgTypeName(coh->h.type), coh->h.reqId,
                                             coh->h.srcNode, coh->h.srcSocket, coh->h.srcSocket);
                            }
                        }
                    }
                }
                m = port->recv(tick, &st);
                continue;
            }

            // MetaRNFReadResp: response from gem5 MetaRNFController (Phase 3)
            if (coh->h.type == CoherenceMessageType::MetaRNFReadResp) {
                host._metaRNF.enterReentrant();
                host._metaRNF.handleResp(*coh);
                host._metaRNF.leaveReentrant();
                // Deferred ops drained at outer loop boundary (after pollAndProcess)
                m = port->recv(tick, &st);
                continue;
            }
            // MetaRNFWriteResp: durable write ack from gem5 (Phase D2)
            if (coh->h.type == CoherenceMessageType::MetaRNFWriteResp) {
                host._metaRNF.enterReentrant();
                host._metaRNF.handleWriteResp(*coh);
                host._metaRNF.leaveReentrant();
                m = port->recv(tick, &st);
                continue;
            }
            // MetaRNFLineReadResp: typed 64B line read response (Phase 2)
            if (coh->h.type == CoherenceMessageType::MetaRNFLineReadResp) {
                host._metaRNF.enterReentrant();
                host._metaRNF.handleLineReadResp(*coh);
                host._metaRNF.leaveReentrant();
                m = port->recv(tick, &st);
                continue;
            }
            // MetaRNFLineWriteResp: typed 64B line write ack (Phase 2)
            if (coh->h.type == CoherenceMessageType::MetaRNFLineWriteResp) {
                host._metaRNF.enterReentrant();
                host._metaRNF.handleLineWriteResp(*coh);
                host._metaRNF.leaveReentrant();
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
                // Wrap UBCC ingress with reentrant guard: any H64 backstore
                // operations triggered during handleUbccMessage will defer their
                // MetaRNF sends, avoiding reentrant sendCoh during port->recv.
                host._metaRNF.enterReentrant();
                bool handled = handleUbccMessage(ubcc, host, nid, *coh, response, hasResponse);
                host._metaRNF.leaveReentrant();
                if (!handled) {
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

    uint64_t loop_count = 0;
    while (!(gem5Done && (netPort == nullptr || netDone))) {
        loop_count++;
        // 1. Heartbeat: emitSync for all ports (even silent ones)
        if (loop_count <= 5) { std::fprintf(stderr, "[UBIO-PRE-EMIT] tick=%lu\n", tick); fflush(stderr); }
        if (!gem5Done) gem5Port->emitSync(tick);
        if (loop_count <= 5) { std::fprintf(stderr, "[UBIO-POST-EMIT] tick=%lu\n", tick); fflush(stderr); }
        if (netPort && !netDone) netPort->emitSync(tick);

        // 2. Drain all ready messages from each port
        if (!gem5Done) pollAndProcess(gem5Port, gem5Port, false, &gem5Done);
        if (netPort && !netDone) pollAndProcess(netPort, netPort, true, &netDone);

        // 2.5 Drain deferred H64 MetaRNF operations.  These were enqueued
        // during port message dispatch (reentrantDepth > 0) and must be sent
        // OUTSIDE the port receive/message-dispatch stack to avoid PDES
        // reentrant-send deadlocks.  One deferred send may trigger a callback
        // that creates MORE deferred ops; these are drained in the NEXT outer
        // loop iteration (bounded to avoid starvation).
        // Call stack: main() → while(!done) → drainDeferred() → sendCoh().
        if (host._metaRNF.hasDeferred()) {
            static int dd_cnt = 0;
            if (host._metaRNF._debugH64Pdes && (++dd_cnt <= 5 || dd_cnt % 1000 == 0))
                std::fprintf(stderr, "[DEBUG-H64-PDES-DRAIN] n=%d cnt=%d deferred=%d tick=%lu\n",
                             nid, dd_cnt, host._metaRNF._deferredCount, tick);
            host._metaRNF.drainDeferred();
        }

        // A release send can temporarily backpressure after all arrivals have
        // been observed. Retry only those already-satisfied generations.
        std::vector<BarrierKey> readyBarriers;
        for (const auto &kv : barrierArrivals) {
            if (kv.second.size() >= static_cast<size_t>(
                    __builtin_popcount(kv.first.first) * g_numSockets))
                readyBarriers.push_back(kv.first);
        }
        for (const BarrierKey &bk : readyBarriers)
            releaseBarrier(bk);

        // 3. Advance tick via safeTs
        uint64_t minTs = UINT64_MAX;
        if (!gem5Done) minTs = gem5Port->safeTs(tick);
        if (netPort && !netDone) {
            uint64_t netSafe = netPort->safeTs(tick);
            if (netSafe < minTs) minTs = netSafe;
        }
        if (minTs > tick) {
            tick = minTs;
            // Run controller maintenance only when virtual time advances.
            // This expires tombstones and recalls without repeatedly scanning
            // state while PDES is parked at the same timestamp.
            ubcc.wakeup();
            host.advanceH64Coverage();
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
        host.drainPendingBackstoreAcks(tick);
        host.dsmData.drain(tick);
    }

    // 3.4: Dump ResidentDir performance counters
    ubcc.directory().dumpStatsJson();
    fprintf(stderr, "[UBCC-STATS] %s\n", ubcc.dumpStatsJson().c_str());
    fprintf(stderr, "[UBCC-STATS] {\"asyncWbCount\":%lu}\n",
            ubcc.getAsyncWbCount());
    fprintf(stderr, "[UBCC-STATS] {\"h64ExactLiveKnown\":%d,\"h64ExactLiveCount\":%lu}\n",
            host.h64ExactCoverageKnown() ? 1 : 0, host.h64ExactLiveCount());

    return 0;
}
