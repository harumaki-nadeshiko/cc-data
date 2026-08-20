/**
 * Standalone UBIO with real UBCCController.
 *
 * 网络侧约定：networksim 负责 bind，ubio 必须 connect。
 * 用法：
 *   ubio_main --gem5-ep=ipc:///tmp/ubio_n0 --net-ep=ipc:///tmp/networksim_m0_p1 --node=0
 */

#include "framework/iface/Message.hh"
#include "framework/iface/Port.hh"
#include "framework/iface/Log.hh"
#include "protocol/TracePerfPolicy.hh"
#include "protocol/NodeAddressMap.hh"
#include "modules/ubiomodule/UBCCController.hh"
#include "modules/ubiomodule/BackstoreSchemaA.hh"
#include "modules/ubiomodule/BackstoreSchemaC.hh"
#include "modules/ubiomodule/BackstoreHostH64.hh"
#include "modules/ubiomodule/PeerExitCoordinator.hh"
#include "modules/hamodule/HAController.hh"

#include <algorithm>
#include <array>
#include <chrono>
#include <cerrno>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <functional>
#include <limits>
#include <map>
#include <memory>
#include <string>
#include <thread>
#include <tuple>
#include <vector>

#include <sys/random.h>
#include <unistd.h>

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
      case CoherenceMessageType::HAPermissionReq:
      case CoherenceMessageType::HAPermissionAck:
      case CoherenceMessageType::HAPresenceProbeResp:
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
      case CoherenceMessageType::HAPermissionResp:
      case CoherenceMessageType::HAPresenceProbeReq:
        return true;
      default:
        return false;
    }
}

// HA mode deliberately constructs neither UBCCController nor
// UbioBackstoreHost. Reject legacy request/response pairs without touching
// either object. One-way notifications and unknown types return false so the
// dispatch site can emit an explicit fatal diagnostic.
bool
buildHaLegacyReject(const CoherenceMessage &request, int nid, int sid,
                    CoherenceMessage &response)
{
    response.h.srcNode = nid;
    response.h.srcSocket = sid;
    response.h.dstNode = request.h.srcNode;
    response.h.dstSocket = request.h.srcSocket;
    response.h.homeNode = nid;
    response.h.homeSocket = sid;
    response.h.requesterNode = request.h.requesterNode;
    response.h.homeLinePa = request.h.homeLinePa;
    response.h.localLinePa = request.h.localLinePa;
    response.h.epoch = request.h.epoch;
    response.h.reqId = request.h.reqId;

    switch (request.h.type) {
      case CoherenceMessageType::ReadReq:
        response.h.type = CoherenceMessageType::ReadResp;
        response.h.flags = static_cast<uint32_t>(CFLAG_BUSY);
        response.b.readResp.grantType = -1;
        response.b.readResp.dataSource =
            static_cast<int8_t>(GrantDataSource::NoData);
        return true;
      case CoherenceMessageType::WritebackReq:
        response.h.type = CoherenceMessageType::WritebackResp;
        response.b.writebackResp.success = false;
        return true;
      case CoherenceMessageType::EvictReq:
        response.h.type = CoherenceMessageType::EvictResp;
        response.b.evictResp.success = false;
        return true;
      case CoherenceMessageType::UpgradeReq:
        response.h.type = CoherenceMessageType::UpgradeResp;
        response.h.flags = static_cast<uint32_t>(CFLAG_BUSY);
        response.b.upgradeResp.upgradeTargetMask = 0;
        response.b.upgradeResp.committedEpoch = request.h.epoch;
        return true;
      case CoherenceMessageType::UpgradeDoneReq:
        response.h.type = CoherenceMessageType::UpgradeDoneResp;
        response.b.upgradeDoneResp.accepted = false;
        return true;
      case CoherenceMessageType::ClearReq:
        response.h.type = CoherenceMessageType::ClearResp;
        response.b.clearResp.accepted = false;
        return true;
      case CoherenceMessageType::QueryLineMetaReq:
        response.h.type = CoherenceMessageType::QueryLineMetaResp;
        response.b.queryLineMetaResp.found = false;
        response.b.queryLineMetaResp.epoch = 0;
        response.b.queryLineMetaResp.ownerNode = -1;
        return true;
      default:
        return false;
    }
}

// ── Debug fault injection (ubio-side, multi-process split) ──────────
// Re-wires the fault injection that previously lived in gem5's UBIOModule
// (removed during decoupling). Rules are passed via --fault-rules=, with one
// or more rules separated by ';'. Each rule:
//   name:type:src:dst:pa:action[:delayTicks[:matchCount]]
// action ∈ {drop, dup, delay, reorder}. Matching messages emit explicit
// UBFAULT load, trigger, and (for buffered actions) delivery events.
enum class UbioFaultAction { Drop, Duplicate, Delay, Reorder };
enum class PeerExitFaultMatch { Both, Notify, Ack };

struct UbioFaultRule {
    std::string name;
    CoherenceMessageType matchType = CoherenceMessageType::ReadReq;
    bool matchAnyType = false;          // matchType==ReadReq used as wildcard
    PeerExitFaultMatch peerExitMatch = PeerExitFaultMatch::Both;
    int matchSrc = -1;
    int matchDst = -1;
    uint64_t matchPa = 0;
    UbioFaultAction action = UbioFaultAction::Duplicate;
    uint64_t delayTicks = 0;
    int matchCount = 0;                 // 0 = unlimited
    int firedCount = 0;
};

const char *
faultActionName(UbioFaultAction action)
{
    switch (action) {
      case UbioFaultAction::Drop:      return "Drop";
      case UbioFaultAction::Duplicate: return "Duplicate";
      case UbioFaultAction::Delay:     return "Delay";
      case UbioFaultAction::Reorder:   return "Reorder";
    }
    return "Unknown";
}

bool
parseMsgTypeName(const std::string &s, CoherenceMessageType &type,
                 PeerExitFaultMatch &peerExitMatch)
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
        {"QueryLineMetaReq", CoherenceMessageType::QueryLineMetaReq},
        {"QueryLineMetaResp", CoherenceMessageType::QueryLineMetaResp},
        {"HomeWritebackNotify", CoherenceMessageType::HomeWritebackNotify},
        {"BarrierReached", CoherenceMessageType::BarrierReached},
        {"BarrierRelease", CoherenceMessageType::BarrierRelease},
        {"MetaRNFReadReq", CoherenceMessageType::MetaRNFReadReq},
        {"MetaRNFReadResp", CoherenceMessageType::MetaRNFReadResp},
        {"MetaRNFWriteReq", CoherenceMessageType::MetaRNFWriteReq},
        {"MetaRNFWriteResp", CoherenceMessageType::MetaRNFWriteResp},
        {"MetaRNFLineReadReq", CoherenceMessageType::MetaRNFLineReadReq},
        {"MetaRNFLineReadResp", CoherenceMessageType::MetaRNFLineReadResp},
        {"MetaRNFLineWriteReq", CoherenceMessageType::MetaRNFLineWriteReq},
        {"MetaRNFLineWriteResp", CoherenceMessageType::MetaRNFLineWriteResp},
        {"PeerExit", CoherenceMessageType::PeerExit},
        {"PeerExitNotify", CoherenceMessageType::PeerExit},
        {"PeerExitAck", CoherenceMessageType::PeerExit},
    };
    auto it = m.find(s);
    if (it == m.end()) return false;
    type = it->second;
    if (s == "PeerExitNotify") peerExitMatch = PeerExitFaultMatch::Notify;
    if (s == "PeerExitAck") peerExitMatch = PeerExitFaultMatch::Ack;
    return true;
}

bool
parseIntField(const std::string &s, int &value)
{
    if (s.empty()) return false;
    errno = 0;
    char *end = nullptr;
    long parsed = std::strtol(s.c_str(), &end, 10);
    if (errno == ERANGE || end == s.c_str() || *end != '\0' ||
        parsed < std::numeric_limits<int>::min() ||
        parsed > std::numeric_limits<int>::max()) {
        return false;
    }
    value = static_cast<int>(parsed);
    return true;
}

bool
parseUint64Field(const std::string &s, int base, uint64_t &value)
{
    if (s.empty() || s[0] == '-') return false;
    errno = 0;
    char *end = nullptr;
    unsigned long long parsed = std::strtoull(s.c_str(), &end, base);
    if (errno == ERANGE || end == s.c_str() || *end != '\0') return false;
    value = static_cast<uint64_t>(parsed);
    return true;
}

uint64_t
peerExitIntervalFromEnv(const char *name, uint64_t defaultValue,
                        uint64_t minValue, uint64_t maxValue)
{
    const char *env = std::getenv(name);
    if (!env || !*env)
        return defaultValue;
    uint64_t value = 0;
    if (!parseUint64Field(env, 10, value) || value < minValue ||
        value > maxValue) {
        LogError("UBIO", "[UBIO-FATAL] {}='{}' must be in [{},{}] ms",
                 name, env, minValue, maxValue);
        std::exit(1);
    }
    return value;
}

bool
logPeerExitAttempt(uint64_t attempt)
{
    // Preserve proof of the first retry (attempt=2), then logarithmically
    // bound output during prolonged transient failures.
    return attempt != 0 && (attempt & (attempt - 1)) == 0;
}

uint64_t
peerExitNonce(int node, int socket)
{
    uint64_t value = 0;
    size_t received = 0;
    while (received < sizeof(value)) {
        const ssize_t count = getrandom(
            reinterpret_cast<unsigned char *>(&value) + received,
            sizeof(value) - received, 0);
        if (count > 0) {
            received += static_cast<size_t>(count);
            continue;
        }
        if (count < 0 && errno == EINTR)
            continue;
        LogError("UBIO", "[UBIO-FATAL] getrandom failed for PeerExit nonce "
                 "local={}:{} errno={}", node, socket, errno);
        std::exit(1);
    }
    return value == 0 ? 1 : value;
}

std::vector<UbioFaultRule> g_faultRules;

// ── Delayed message queue (3.3 reorder + 4.6 delay real) ──────────────
struct DelayedMsg {
    uint64_t fireTick;          // tick when this message should be delivered
    CoherenceMessage coh;       // the buffered message
    bool fromNetwork;           // original ingress direction
    int faultCopies;            // copies to apply at delivery time
    std::string ruleName;       // triggering rule, retained for delivery event
    UbioFaultAction action;     // original buffered action
};
static std::deque<DelayedMsg> g_delayedQueue;

void
parseFaultRules(const std::string &all, int localNode)
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
            LogWarn("UBIO", "[UBFAULT-LOAD] malformed rule='{}' "
                    "error=too_few_fields — skipping",
                    rule_str);
            continue;
        }
        UbioFaultRule r;
        r.name = parts[0];
        r.matchAnyType = (parts[1] == "*" || parts[1] == "any");
        if (!r.matchAnyType &&
            !parseMsgTypeName(parts[1], r.matchType, r.peerExitMatch)) {
            LogWarn("UBIO", "[UBFAULT-LOAD] malformed rule='{}' "
                    "error=unknown_type type='{}' — skipping",
                    rule_str, parts[1]);
            continue;
        }
        if (!parts[2].empty() && !parseIntField(parts[2], r.matchSrc)) {
            LogWarn("UBIO", "[UBFAULT-LOAD] malformed rule='{}' "
                    "error=invalid_src value='{}' — skipping",
                    rule_str, parts[2]);
            continue;
        }
        if (!parts[3].empty() && !parseIntField(parts[3], r.matchDst)) {
            LogWarn("UBIO", "[UBFAULT-LOAD] malformed rule='{}' "
                    "error=invalid_dst value='{}' — skipping",
                    rule_str, parts[3]);
            continue;
        }
        if (!parts[4].empty() && !parseUint64Field(parts[4], 0, r.matchPa)) {
            LogWarn("UBIO", "[UBFAULT-LOAD] malformed rule='{}' "
                    "error=invalid_pa value='{}' — skipping",
                    rule_str, parts[4]);
            continue;
        }
        const std::string &a = parts[5];
        if (a == "drop" || a == "Drop") r.action = UbioFaultAction::Drop;
        else if (a == "dup" || a == "Dup" || a == "duplicate" ||
                 a == "Duplicate") r.action = UbioFaultAction::Duplicate;
        else if (a == "delay" || a == "Delay") {
            r.action = UbioFaultAction::Delay;
        } else if (a == "reorder" || a == "Reorder") {
            r.action = UbioFaultAction::Reorder;
        } else {
            LogWarn("UBIO", "[UBFAULT-LOAD] malformed rule='{}' "
                    "error=unknown_action action='{}' — skipping",
                    rule_str, a);
            continue;
        }
        if (r.action == UbioFaultAction::Delay ||
            r.action == UbioFaultAction::Reorder) {
            r.delayTicks = 1000;
            if (parts.size() > 6 && !parts[6].empty() &&
                !parseUint64Field(parts[6], 10, r.delayTicks)) {
                LogWarn("UBIO", "[UBFAULT-LOAD] malformed rule='{}' "
                        "error=invalid_delay value='{}' — skipping",
                        rule_str, parts[6]);
                continue;
            }
        } else if (parts.size() > 6 && !parts[6].empty()) {
            uint64_t ignoredDelay = 0;
            if (!parseUint64Field(parts[6], 10, ignoredDelay)) {
                LogWarn("UBIO", "[UBFAULT-LOAD] malformed rule='{}' "
                        "error=invalid_delay value='{}' — skipping",
                        rule_str, parts[6]);
                continue;
            }
        }
        if (!r.matchAnyType &&
            r.matchType == CoherenceMessageType::PeerExit &&
            (r.action == UbioFaultAction::Delay ||
             r.action == UbioFaultAction::Reorder)) {
            LogWarn("UBIO", "[UBFAULT-LOAD] malformed rule='{}' "
                    "error=peer_exit_requires_wall_clock_fault — skipping",
                    rule_str);
            continue;
        }
        if (parts.size() > 7 && !parts[7].empty() &&
            !parseIntField(parts[7], r.matchCount)) {
            LogWarn("UBIO", "[UBFAULT-LOAD] malformed rule='{}' "
                    "error=invalid_count value='{}' — skipping",
                    rule_str, parts[7]);
            continue;
        }
        if (r.matchCount < 0) {
            LogWarn("UBIO", "[UBFAULT-LOAD] malformed rule='{}' "
                    "error=invalid_count value='{}' — skipping",
                    rule_str, parts[7]);
            continue;
        }
        if (r.matchDst >= 0 && r.matchDst != localNode) continue;
        g_faultRules.push_back(r);
        LogInfo("UBIO", "[UBFAULT-LOAD] rule='{}' name='{}' type={} src={} "
                "dst={} action={} count={} pa=0x{:x} delayTicks={}",
                r.name, r.name, parts[1], r.matchSrc, r.matchDst,
                faultActionName(r.action), r.matchCount, r.matchPa,
                r.delayTicks);
    }
}

void
insertDelayed(DelayedMsg dm)
{
    auto pos = std::upper_bound(
        g_delayedQueue.begin(), g_delayedQueue.end(), dm.fireTick,
        [](uint64_t fireTick, const DelayedMsg &queued) {
            return fireTick < queued.fireTick;
        });
    g_delayedQueue.insert(pos, std::move(dm));
}

// Returns number of times the message should be processed:
//   0 = drop, 1 = normal, 2 = duplicate. Emits [UBFAULT] on a match.
// For Delay/Reorder actions, enqueues to g_delayedQueue and returns 0.
int
applyUbioFault(const CoherenceMessage &coh, int nid, uint64_t currentTick,
               bool fromNetwork)
{
    if (g_faultRules.empty()) return 1;
    int copies = 1;
    for (auto &r : g_faultRules) {
        if (r.matchCount > 0 && r.firedCount >= r.matchCount) continue;
        if (!r.matchAnyType && r.matchType != coh.h.type) continue;
        if (!r.matchAnyType && coh.h.type == CoherenceMessageType::PeerExit) {
            const bool isAck =
                (coh.h.flags & static_cast<uint32_t>(CFLAG_PEER_EXIT_ACK)) != 0;
            if (r.peerExitMatch == PeerExitFaultMatch::Notify && isAck) continue;
            if (r.peerExitMatch == PeerExitFaultMatch::Ack && !isAck) continue;
        }
        if (r.matchSrc >= 0 && r.matchSrc != (int)coh.h.srcNode) continue;
        if (r.matchDst >= 0 && r.matchDst != (int)coh.h.dstNode) continue;
        if (r.matchPa != 0 && r.matchPa != coh.h.homeLinePa) continue;
        // reason=1 is emitted only by clear_profile=lossless-oneway. Such a
        // run explicitly assumes eventual delivery and has no requester-side
        // retransmission/recovery path, so fault injection must not weaken the
        // Clear channel (including delay/reorder experiments).
        if (coh.h.type == CoherenceMessageType::ClearReq &&
            coh.b.clearReq.reason == 1) {
            LogWarn("UBIO", "[UBFAULT-REJECT] rule='{}' type=ClearReq "
                    "reqId={} reason=lossless-oneway", r.name, coh.h.reqId);
            continue;
        }
        const char *tn = coherenceMsgTypeName(coh.h.type);
        if (coh.h.type == CoherenceMessageType::PeerExit &&
            (r.action == UbioFaultAction::Delay ||
             r.action == UbioFaultAction::Reorder)) {
            LogWarn("UBIO", "[UBFAULT-SKIP] node={} rule='{}' action={} "
                    "type={} src={} dst={} exitId={} currentTick={} "
                    "warning=peer_exit_wall_clock_protocol_cannot_use_sim_tick "
                    "— injection skipped",
                    nid, r.name, faultActionName(r.action),
                    (coh.h.flags & static_cast<uint32_t>(CFLAG_PEER_EXIT_ACK))
                        ? "PeerExitAck" : "PeerExitNotify",
                    coh.h.srcNode, coh.h.dstNode, coh.h.reqId, currentTick);
            continue;
        }
        uint64_t fireTick = currentTick;
        if ((r.action == UbioFaultAction::Delay ||
             r.action == UbioFaultAction::Reorder) &&
            r.delayTicks > std::numeric_limits<uint64_t>::max() - currentTick) {
            LogWarn("UBIO", "[UBFAULT-TRIGGER] node={} rule='{}' action={} "
                    "type={} src={} dst={} pa=0x{:x} reqId={} matchCount={} "
                    "firedCount={} delayTicks={} currentTick={} "
                    "error=fire_tick_overflow — injection skipped",
                    nid, r.name, faultActionName(r.action), tn, coh.h.srcNode,
                    coh.h.dstNode, coh.h.homeLinePa, coh.h.reqId, r.matchCount,
                    r.firedCount, r.delayTicks, currentTick);
            continue;
        }
        if (r.action == UbioFaultAction::Delay ||
            r.action == UbioFaultAction::Reorder) {
            fireTick = currentTick + r.delayTicks;
        }
        r.firedCount++;
        LogWarn("UBIO", "[UBFAULT-TRIGGER] node={} rule='{}' action={} "
                "type={} src={} dst={} pa=0x{:x} reqId={} matchCount={} "
                "firedCount={} delayTicks={} fireTick={} currentTick={}",
                nid, r.name, faultActionName(r.action), tn, coh.h.srcNode,
                coh.h.dstNode, coh.h.homeLinePa, coh.h.reqId, r.matchCount,
                r.firedCount, r.delayTicks, fireTick, currentTick);
        switch (r.action) {
          case UbioFaultAction::Drop:
            copies = 0;
            break;
          case UbioFaultAction::Duplicate:
            copies = 2;
            break;
          case UbioFaultAction::Delay:
            // 4.6: real delay — enqueue to delayed queue, drop original copy
            insertDelayed({fireTick, coh, fromNetwork, 1, r.name, r.action});
            copies = 0;
            break;
          case UbioFaultAction::Reorder:
            // 3.3: reorder — buffer and deliver after delayTicks
            insertDelayed({fireTick, coh, fromNetwork, 1, r.name, r.action});
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
enum class HomeControllerMode { Ubcc, HaVi };
static HomeControllerMode g_homeControllerMode = HomeControllerMode::Ubcc;
static uint64_t g_haExactBase = 0;
static uint64_t g_haExactBytes = 64ULL * 1024 * 1024;
static size_t g_haMaxActive = 256;
static size_t g_haQueueDepth = 8;
static inline uint32_t gidOf(int node, int socket) {
    return static_cast<uint32_t>(node * g_numSockets + socket);
}

bool
sendCoh(Port *port, uint64_t tick, uint32_t srcModule, uint32_t dstModule,
        const CoherenceMessage &msg, bool toNetwork = false)
{
    const bool traceReadPath =
        (msg.h.type == CoherenceMessageType::ReadReq) ||
        (msg.h.type == CoherenceMessageType::ReadResp);
    if (g_debugUbioPerf && (msg.h.type == CoherenceMessageType::ClearReq ||
        msg.h.type == CoherenceMessageType::ClearResp)) {
        LogDebug("UBIO",
                     "[DEBUG-UBIO-CLEAR] send type={} reqId={} pa=0x{:x} srcNode={} dstNode={} routeModule={} tick={}",
                     coherenceMsgTypeName(msg.h.type),
                     msg.h.reqId, msg.h.homeLinePa,
                     msg.h.srcNode, msg.h.dstNode,
                     dstModule,  tick);
    }
    if (!port) {
        if (g_debugUbioPerf && traceReadPath) {
            LogDebug("UBIO",
                         "[DEBUG-UBIO-RR-SEND] type={} sendCoh ret=false reason=no_port reqId={} srcNode={} dstNode={} dstModule={} tick={}",
                         coherenceMsgTypeName(msg.h.type),
                         msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                         dstModule,  tick);
        }
        return false;
    }
    Message *buf = AllocateSendMessage(port, tick);
    if (g_debugUbioPerf && traceReadPath) {
        LogDebug("UBIO",
                     "[DEBUG-UBIO-RR-SEND] type={} alloc ptr={} reqId={} srcNode={} dstNode={} dstModule={} tick={}",
                     coherenceMsgTypeName(msg.h.type),
                     static_cast<void*>(buf),
                     msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                     dstModule,  tick);
    }
    if (!buf) {
        if (g_debugUbioPerf && traceReadPath) {
            LogDebug("UBIO",
                         "[DEBUG-UBIO-RR-SEND] type={} sendCoh ret=false reason=sendAllocateBuffer_null reqId={} srcNode={} dstNode={} dstModule={} tick={}",
                         coherenceMsgTypeName(msg.h.type),
                         msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                         dstModule,  tick);
        }
        return false;
    }
    SetMessageSourceId(buf, srcModule);
    SetMessageTargetId(buf, dstModule);
    SetMessageRequestId(buf, msg.h.reqId);
    if (sizeof(msg) > GetMaxPayloadSize()) {
        if (g_debugUbioPerf && traceReadPath) {
            LogDebug("UBIO",
                         "[DEBUG-UBIO-RR-SEND] type={} sendCoh ret=false reason=payload_too_large reqId={} srcNode={} dstNode={} dstModule={} tick={}",
                         coherenceMsgTypeName(msg.h.type),
                         msg.h.reqId, msg.h.srcNode, msg.h.dstNode,
                         dstModule,  tick);
        }
        ReleaseMessage(buf);
        return false;
    }
    SetMessagePayload(buf, &msg, sizeof(msg));
    uint64_t sendTs = GetMessageTimestamp(buf);
    bool ok = SendMessage(port, buf);
    if (msg.h.type == CoherenceMessageType::UpgradeReq ||
        msg.h.type == CoherenceMessageType::UpgradeResp) {
        LogInfo("UBIO", "[UPGRADE-FORENSIC] stage={} routeSrcGid={} "
                "pa=0x{:x} type={} reqId={} epoch={} payloadSrc={}:{} "
                "dst={}:{} targetGid={} simTs={} sendOk={}",
                toNetwork ? "UBIO_NET_SEND" : "UBIO_GEM5_SEND",
                srcModule, msg.h.homeLinePa, coherenceMsgTypeName(msg.h.type),
                msg.h.reqId, msg.h.epoch, msg.h.srcNode, msg.h.srcSocket,
                msg.h.dstNode, msg.h.dstSocket, dstModule, sendTs, ok ? 1 : 0);
    }
    if (ok && TracePerfPolicy::get().shouldEmit("ubio")) {
        LogInfo("UBIO", "[TRACE-PERF] {}|{}|ubio|{}|0x{:x}|{}|{}",
                     sendTs, dstModule, msg.h.reqId, msg.h.homeLinePa,
                     toNetwork ? "SEND_NET" : "SEND_GEM5",
                     coherenceMsgTypeName(msg.h.type));
    }
    if (g_debugUbioPerf && traceReadPath) {
        LogDebug("UBIO",
                     "[DEBUG-UBIO-RR-SEND] type={} sendCoh ret={} reason={} reqId={} srcNode={} dstNode={} dstModule={} tick={}",
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
        const uint32_t gid = gidOf(_nodeId, _socketId);
        sendCoh(_gem5Port, _tickRef, gid, gid, req);
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
        const uint32_t gid = gidOf(_nodeId, _socketId);
        sendCoh(_gem5Port, _tickRef, gid, gid, req);
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
        const uint32_t gid = gidOf(_nodeId, _socketId);
        return sendCoh(_gem5Port, _tickRef, gid, gid, req);
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
        const uint32_t gid = gidOf(_nodeId, _socketId);
        bool sent = sendCoh(_gem5Port, _tickRef, gid, gid, req);
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

    int deferredLineCount(bool writes) const {
        int count = 0;
        for (int i = 0; i < _deferredCount; ++i)
            count += _deferredOps[i].isWrite == writes;
        return count;
    }

    void drainDeferred() {
        int drained = 0;
        while (_deferredCount > 0 && drained < kMaxDeferredLineOps) {
            int idx = -1;
            for (int i = 0; i < _deferredCount; ++i) {
                const bool hasCredit = _deferredOps[i].isWrite
                    ? _pendingLineWrites.size() < kMaxLinePendingWrites
                    : _pendingLineReads.size() < kMaxLinePendingReads;
                if (hasCredit) {
                    idx = i;
                    break;
                }
            }
            if (idx < 0)
                break;
            auto op = _deferredOps[idx];
            for (int i = idx + 1; i < _deferredCount; ++i)
                _deferredOps[i - 1] = _deferredOps[i];
            _deferredCount--;
            drained++;
            if (_debugH64Pdes) LogDebug("UBIO", "[DEBUG-H64-PDES-DRAIN-OP] n={} op={} off={} reqId=0x{:x} remain={}",
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
                const uint32_t gid = gidOf(_nodeId, _socketId);
                sent = sendCoh(_gem5Port, _tickRef, gid, gid, req);
                if (!sent) {
                    if (_debugH64Pdes) LogDebug("UBIO", "[DEBUG-H64-PDES-DRAIN-FAIL] n={} write off={}",
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
                const uint32_t gid = gidOf(_nodeId, _socketId);
                sent = sendCoh(_gem5Port, _tickRef, gid, gid, req);
                if (!sent) {
                    if (_debugH64Pdes) LogDebug("UBIO", "[DEBUG-H64-PDES-DRAIN-FAIL] n={} read off={}",
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
        int combined = static_cast<int>(_pendingLineReads.size()) +
            deferredLineCount(false);
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
            if (_debugH64Pdes) LogDebug("UBIO", "[DEBUG-H64-PDES-DEFER] n={} read off={} depth={} cnt={}",
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
        const uint32_t gid = gidOf(_nodeId, _socketId);
        if (!sendCoh(_gem5Port, _tickRef, gid, gid, req)) {
            auto it = _pendingLineReads.find(rid);
            if (it != _pendingLineReads.end()) {
                auto cb2 = std::move(it->second.callback);
                _pendingLineReads.erase(it);
                if (cb2) cb2(MetaRNFLineStatus::IoError, nullptr);
            }
        }
    }

    bool retryReadLine(uint64_t bucketOffset,
                       std::function<void(MetaRNFLineStatus,
                                          const uint8_t* data64)> cb) override {
        // Outer-loop retries are older than deferred new work. Reserve the
        // next available in-flight read credit for them.
        if (_pendingLineReads.size() >= kMaxLinePendingReads) {
            if (cb) cb(MetaRNFLineStatus::RetryableBusy, nullptr);
            return false;
        }
        const uint64_t rid = _nextLineReadReqId++;
        _pendingLineReads[rid] = {rid, bucketOffset, std::move(cb)};
        CoherenceMessage req;
        req.h.type = CoherenceMessageType::MetaRNFLineReadReq;
        req.h.srcNode = _nodeId; req.h.srcSocket = _socketId;
        req.h.dstNode = _nodeId; req.h.dstSocket = _socketId;
        req.h.reqId = rid;
        req.b.metaRNFLineReadReq.bucketOffset = bucketOffset;
        const uint32_t gid = gidOf(_nodeId, _socketId);
        const bool sent = sendCoh(_gem5Port, _tickRef, gid, gid, req);
        if (!sent) {
            auto it = _pendingLineReads.find(rid);
            if (it != _pendingLineReads.end()) {
                auto cb2 = std::move(it->second.callback);
                _pendingLineReads.erase(it);
                if (cb2) cb2(MetaRNFLineStatus::IoError, nullptr);
            }
        }
        return sent;
    }

    void handleLineReadResp(const CoherenceMessage &msg) {
        uint64_t rid = msg.h.reqId;
        auto it = _pendingLineReads.find(rid);
        if (it == _pendingLineReads.end()) {
            if (_debugH64Pdes) LogDebug("UBIO", "[DEBUG-H64-PDES-RESP-DROP] n={} readResp reqId={} (not in pending)",
                         _nodeId, rid);
            return;
        }
        auto cb = std::move(it->second.callback);
        MetaRNFLineStatus st = msg.b.metaRNFLineReadResp.status;
        uint8_t data[64];
        memcpy(data, msg.b.metaRNFLineReadResp.data, 64);
        _pendingLineReads.erase(it);
        if (_debugH64Pdes) LogDebug("UBIO", "[DEBUG-H64-PDES-RESP-CB] n={} readResp reqId={} st={} depth={}",
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
        int combined = static_cast<int>(_pendingLineWrites.size()) +
            deferredLineCount(true);
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
            if (_debugH64Pdes) LogDebug("UBIO", "[DEBUG-H64-PDES-DEFER] n={} write off={} depth={} cnt={}",
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
        const uint32_t gid = gidOf(_nodeId, _socketId);
        if (!sendCoh(_gem5Port, _tickRef, gid, gid, req)) {
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
            if (_debugH64Pdes) LogDebug("UBIO", "[DEBUG-H64-PDES-WRESP-DROP] n={} writeResp reqId={} (not in pending)",
                         _nodeId, rid);
            return;
        }
        auto cb = std::move(it->second.callback);
        MetaRNFLineStatus st = msg.b.metaRNFLineWriteResp.status;
        _pendingLineWrites.erase(it);
        if (_debugH64Pdes) LogDebug("UBIO", "[DEBUG-H64-PDES-WRESP-CB] n={} writeResp reqId={} st={} depth={}",
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
            LogWarn("UBIO", "[MetaRNF] WARN: no pending read for reqId={}", rid);
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

    // Fixed admission table for push grants that need an authoritative home
    // read. This is not PA-keyed state: each entry owns one in-flight response
    // and is released by its typed completion.
    struct PendingGrantRead {
        bool active = false;
        bool readInFlight = false;
        bool dataReady = false;
        uint64_t retryTick = 0;
        uint8_t retryCount = 0;
        CoherenceMessage push;
    };
    static constexpr size_t kMaxPendingGrantReads = 32;
    std::array<PendingGrantRead, kMaxPendingGrantReads> pendingGrantReads{};

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
            LogInfo("UBIO", "[UBIO-H64-HOST] node={} socket={} "
                    "H64 host initialized: {} groups x {} buckets/group, "
                    "logical_lines={} table_start={}",
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
            // Use gidOf to compute the correct global module id for the target
            // adapter. Previously only nodeId (bare node number) was passed as
            // dstModule, which set the transport targetId to the wrong value. For
            // point-to-point ZMQ this is cosmetic, but the correct GID must be
            // in the header: the gem5 UBAdapter matches srcSocket<<dstSocket
            // and future topology-aware receivers may filter by targetId.
            uint32_t dstGid = gidOf(nodeId, socketId);
            const uint32_t srcGid = gidOf(nodeId, socketId);
            bool ok = sendCoh(gem5Port, tickRef, srcGid, dstGid, msg);
            if (g_debugUbioPerf) {
                LogDebug("UBIO",
                             "[DEBUG-CTRL-ROUTE] node={} sock={} local type={} reqId={} pa=0x{:x} ok={} tick={} dstGid={}",
                             nodeId, socketId, coherenceMsgTypeName(msg.h.type),
                             msg.h.reqId, msg.h.homeLinePa, ok ? 1 : 0, tickRef, dstGid);
            }
            return ok;
        }
        uint32_t dstGid = gidOf(msg.h.dstNode, msg.h.dstSocket);
        const uint32_t srcGid = gidOf(nodeId, socketId);
        bool ok = sendCoh(netPort, tickRef, srcGid, dstGid, msg, true);
        if (g_debugUbioPerf) {
            LogDebug("UBIO",
                         "[DEBUG-CTRL-ROUTE] node={} sock={} net type={} reqId={} pa=0x{:x} dst={}:{} dstGid={} ok={} tick={}",
                         nodeId, socketId, coherenceMsgTypeName(msg.h.type),
                         msg.h.reqId, msg.h.homeLinePa, msg.h.dstNode,
                         msg.h.dstSocket, dstGid, ok ? 1 : 0, tickRef);
        }
        return ok;
    }
    bool sendRecallReq(const CoherenceMessage &msg) override { return routeControlToTarget(msg); }
    bool sendInvalidateReq(const CoherenceMessage &msg) override { return routeControlToTarget(msg); }
    bool sendUpgradeAckNotify(const CoherenceMessage &msg) override { return routeControlToTarget(msg); }
    bool sendUpgradeResp(const CoherenceMessage &msg) override { return routeControlToTarget(msg); }
    bool queryLineMetaFromBackstore(const CoherenceMessage &request) {
        if (!_useH64 || !_h64Host) {
            return false;
        }
        _h64Host->lookup(
            request.h.homeLinePa,
            [this, request](const BackstoreCompletion &comp) {
                CoherenceMessage response;
                response.h.type = CoherenceMessageType::QueryLineMetaResp;
                response.h.srcNode = nodeId;
                response.h.srcSocket = socketId;
                response.h.dstNode = request.h.srcNode;
                response.h.dstSocket = request.h.srcSocket;
                response.h.homeLinePa = request.h.homeLinePa;
                response.h.reqId = request.h.reqId;
                const bool ok = comp.status == BackstoreStatus::Ok;
                response.b.queryLineMetaResp.found = ok && comp.found;
                response.b.queryLineMetaResp.epoch =
                    response.b.queryLineMetaResp.found ? comp.epoch : 0;
                if (response.b.queryLineMetaResp.found) {
                    UBCCDirEntry entry;
                    entry.state = comp.state;
                    entry.sharersMask = comp.sharersMask;
                    response.b.queryLineMetaResp.ownerNode =
                        UBCCDirEntry::ownerFromSharers(entry);
                } else {
                    response.b.queryLineMetaResp.ownerNode = -1;
                }
                LogInfo("UBIO",
                    "[QLM-H64-DONE] home={} pa=0x{:x} reqId={} status={} "
                    "found={} epoch={} owner={}",
                    nodeId, request.h.homeLinePa, request.h.reqId,
                    backstoreStatusName(comp.status),
                    response.b.queryLineMetaResp.found ? 1 : 0,
                    response.b.queryLineMetaResp.epoch,
                    response.b.queryLineMetaResp.ownerNode);
                routeControlToTarget(response);
            });
        return true;
    }
    bool sendGrantPush(const CoherenceMessage &msg) override {
        CoherenceMessage push = msg;
        for (const PendingGrantRead &slot : pendingGrantReads) {
            if (slot.active &&
                slot.push.h.homeLinePa == push.h.homeLinePa &&
                slot.push.h.requesterNode == push.h.requesterNode &&
                slot.push.h.dstSocket == push.h.dstSocket &&
                slot.push.h.reqId == push.h.reqId) {
                return true;
            }
        }
        // UBCC constructs replay pushes without direct access to the physical
        // home DSM backing. Match the pull-path HomeMemory fallback here so a
        // clean line restored from metadata after its owner wrote back carries
        // the durable 64B payload, not an implicit zero line.
        if (push.h.type == CoherenceMessageType::ReadResp &&
            !(push.h.flags & static_cast<uint32_t>(CFLAG_HAS_DATA)) &&
            dsmData.copyData(push.h.homeLinePa, push.b.readResp.grantData)) {
            push.h.flags |= static_cast<uint32_t>(CFLAG_HAS_DATA);
        }

        if (push.h.type != CoherenceMessageType::ReadResp ||
            (push.h.flags & static_cast<uint32_t>(CFLAG_HAS_DATA))) {
            if (routeControlToTarget(push))
                return true;
            return reserveGrantSlot(push, true);
        }

        // The synchronous copy missed. Do not push a fabricated zero line:
        // reserve a bounded slot and resolve the direct-indexed home read.
        return reserveGrantSlot(push, false);
    }

    bool reserveGrantSlot(const CoherenceMessage &push, bool dataReady) {
        for (size_t i = 0; i < pendingGrantReads.size(); ++i) {
            PendingGrantRead &slot = pendingGrantReads[i];
            if (slot.active)
                continue;
            slot.active = true;
            slot.dataReady = dataReady;
            slot.retryTick = tickRef;
            slot.push = push;
            if (!dataReady)
                issuePendingGrantRead(i);
            return true;
        }
        return false;
    }

    void issuePendingGrantRead(size_t slotIdx) {
        PendingGrantRead &pending = pendingGrantReads[slotIdx];
        if (!pending.active || pending.readInFlight || pending.dataReady)
            return;
        pending.readInFlight = true;
        readDsmDataAsync(pending.push.h.homeLinePa,
            [this, slotIdx](DsmDataStatus status, const uint8_t *data) {
                PendingGrantRead &slot = pendingGrantReads[slotIdx];
                if (!slot.active)
                    return;
                slot.readInFlight = false;
                if (!ubcc.grantTupleLive(slot.push.h.homeLinePa,
                                         slot.push.h.requesterNode,
                                         slot.push.h.reqId)) {
                    slot = PendingGrantRead{};
                    return;
                }
                if (status == DsmDataStatus::RetryableBusy) {
                    // The DSM fixed table is temporarily full. Keep this
                    // bounded response slot and retry after virtual time moves.
                    slot.retryTick = tickRef + dsmData._dsmDramDelayPs;
                    return;
                }
                if (status == DsmDataStatus::Ok && data) {
                    std::memcpy(slot.push.b.readResp.grantData, data, 64);
                    slot.push.h.flags |= static_cast<uint32_t>(CFLAG_HAS_DATA);
                } else if (status == DsmDataStatus::NotWritten) {
                    // An unwritten direct-indexed home line is the sole valid
                    // no-data case; EPBackend may initialize it as zero.
                } else {
                    slot.retryTick = tickRef + dsmData._dsmDramDelayPs;
                    if (slot.retryCount != UINT8_MAX)
                        ++slot.retryCount;
                    return;
                }
                slot.dataReady = true;
                trySendPendingGrant(slot);
            });
    }

    void trySendPendingGrant(PendingGrantRead &slot) {
        if (!slot.active || !slot.dataReady)
            return;
        if (!ubcc.grantTupleLive(slot.push.h.homeLinePa,
                                 slot.push.h.requesterNode,
                                 slot.push.h.reqId)) {
            slot = PendingGrantRead{};
            return;
        }
        if (routeControlToTarget(slot.push)) {
            slot = PendingGrantRead{};
            return;
        }
        slot.retryTick = tickRef + dsmData._dsmDramDelayPs;
        if (slot.retryCount != UINT8_MAX)
            ++slot.retryCount;
    }

    void advancePendingGrantReads(uint64_t tick) {
        for (size_t i = 0; i < pendingGrantReads.size(); ++i) {
            PendingGrantRead &slot = pendingGrantReads[i];
            if (!slot.active || slot.readInFlight || tick < slot.retryTick)
                continue;
            if (slot.dataReady)
                trySendPendingGrant(slot);
            else
                issuePendingGrantRead(i);
        }
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
            LogError("UBIO", "[BACKSTORE-READ-ERR] pa=0x{:x} H64 host not initialized", pa);
            _pendingFills.push_back({tickRef + 1, pa, false, UBCCController::BackstoreEntry{}});
            return;
        }

        // Legacy Schema A path below (unchanged)
        UBCCController::BackstoreEntry e{};
        bool found = false;
        int g = _schema.groupForPa(pa);
        std::vector<uint64_t> pages = _schema.candidatePagesForLookup(pa, _groupIdx[g]);
        LogInfo("UBIO", "[BACKSTORE-READ] pa=0x{:x} group={} candidates={} head=0x{:x} tail=0x{:x}",
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
            LogInfo("UBIO", "[BACKSTORE-READ-DONE] pa=0x{:x} found={} local=1",
                         pa, found ? 1 : 0);
            return;
        }

        // Phase D8: if any candidate page is dirty (MetaRNF write in-flight),
        // defer this fill until the durable callback fires.
        for (auto pagePa : pages) {
            if (_pagesDirty.count(pagePa)) {
                LogInfo("UBIO",
                    "[BACKSTORE-READ-WAIT-DURABLE] pa=0x{:x} group={} page=0x{:x}",
                    pa, g, pagePa);
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
        LogInfo("UBIO", "[BACKSTORE-CHAIN-READ] pa=0x{:x} group={} page=0x{:x} idx=0/{}",
                     pa, g, firstPage, pages.size());
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

    void applyH64CoverageMutation(const BackstoreCompletion &comp) {
        if (!_useH64 || !_h64Host || comp.status != BackstoreStatus::Ok)
            return;
        const size_t group = BackstoreSchemaH64::groupForPaStatic(
            comp.linePa, _h64Host->config().num_groups, _h64Host->config().hash_seed);
        if (group >= h64GroupCovered.size() || !h64GroupCovered[group])
            return;
        if (comp.op == BackstoreOp::Upsert && !comp.existed) {
            ++h64GroupLive[group];
        } else if (comp.op == BackstoreOp::Erase && comp.existed &&
                   h64GroupLive[group] > 0) {
            --h64GroupLive[group];
        }
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
            LogInfo("UBIO",
                "[BACKSTORE-READ-REPLAY-DURABLE] pa=0x{:x} page=0x{:x}",
                pa, pagePa);
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
                LogInfo("UBIO", "[BACKSTORE-CHAIN-FOUND] pa=0x{:x} page=0x{:x}", pa, pagePa);
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
            LogInfo("UBIO", "[BACKSTORE-CHAIN-READ] pa=0x{:x} group={} page=0x{:x} idx={}/{}",
                         pa, g, nextPage, ctx->idx, pages.size());
            ctx->idx++;
            _metaRNF.readPage(nextPage, [this, pa](const uint8_t* d) { chainReadCallback(pa, d); });
        } else {
            LogInfo("UBIO", "[BACKSTORE-CHAIN-MISS] pa=0x{:x} group={} candidates={}", pa, g, pages.size());
            _chainCtx.erase(pa); _chainPages.erase(pa); _chainGroup.erase(pa);
            UBCCController::BackstoreEntry eMiss{};
            _pendingFills.push_back({tickRef + 1, pa, false, eMiss});
        }
    }

    void hostIssueBackstoreWrite(uint64_t pa) override {
        UBCCController::BackstoreEntry e{};
        if (!ubcc.snapshotResidentForBackstore(pa, e)) {
            LogInfo("UBIO", "[BACKSTORE-WRITE] pa=0x{:x} snapshot=0", pa);
            _pendingBackstoreAcks.push_back({tickRef + 1, pa, false, false});
            return;
        }

        // Phase 3: dispatch to H64 when active
        if (_useH64 && _h64Host) {
            _h64Host->upsert(pa, e.state, e.sharersMask, e.epoch,
                [this](const BackstoreCompletion &comp) {
                    applyH64CoverageMutation(comp);
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
        LogInfo("UBIO", "[BACKSTORE-WRITE] pa=0x{:x} group={} page=0x{:x} new={} state={} sharers=0x{:x} epoch={}",
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
            LogError("UBIO", "[BACKSTORE-WRITE-FAIL] pa=0x{:x} page=0x{:x} reason=no_page", pa, plan.target_page_pa);
            _pendingBackstoreAcks.push_back({tickRef + 1, pa, false, false});
            return;
        }

        // D1 overflow allocation
        if (!plan.needs_new_page && p->isFull()) {
            uint64_t oldPagePa = plan.target_page_pa;
            uint64_t newPagePa = _nextPageId++;
            p->hdr.next_page_ptr = newPagePa;
            LogInfo("UBIO", "[BACKSTORE-OVERFLOW-ALLOC] pa=0x{:x} group={} oldPage=0x{:x} newPage=0x{:x} entries={}",
                         pa, g, oldPagePa, newPagePa, p->hdr.entry_count);
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
            LogInfo("UBIO", "[BACKSTORE-WRITE-PENDING] pa=0x{:x} page=0x{:x}", pa, newPagePa);
            _pagesDirty.insert(newPagePa);
            uint64_t capPa = pa; uint64_t capPage = newPagePa;
            _metaRNF.writePageD2(capPage, *newP, [this, capPa, capPage](bool durable) {
                if (durable) {
                    LogInfo("UBIO", "[BACKSTORE-WRITE-DURABLE] pa=0x{:x} page=0x{:x}", capPa, capPage);
                    _pagesDirty.erase(capPage);
                    replayDeferredReads(capPage);
                    _pendingBackstoreAcks.push_back({tickRef + 1, capPa, false, true});
                } else {
                    LogError("UBIO", "[BACKSTORE-WRITE-FAIL] pa=0x{:x} page=0x{:x} reason=remote", capPa, capPage);
                }
            });
            return;
        }

        _schema.applyUpsert(*p, pa, schemaEntry, plan);
        _schema.updateIndexAfterWrite(_groupIdx[g], plan, plan.target_page_pa);
        // D6: durable write via callback
        LogInfo("UBIO", "[BACKSTORE-WRITE-PENDING] pa=0x{:x} page=0x{:x}", pa, plan.target_page_pa);
        _pagesDirty.insert(plan.target_page_pa);
        uint64_t capPa2 = pa; uint64_t capPage2 = plan.target_page_pa;
        _metaRNF.writePageD2(capPage2, *p, [this, capPa2, capPage2](bool durable) {
            if (durable) {
                LogInfo("UBIO", "[BACKSTORE-WRITE-DURABLE] pa=0x{:x} page=0x{:x}", capPa2, capPage2);
                _pagesDirty.erase(capPage2);
                replayDeferredReads(capPage2);
                _pendingBackstoreAcks.push_back({tickRef + 1, capPa2, false, true});
            } else {
                LogError("UBIO", "[BACKSTORE-WRITE-FAIL] pa=0x{:x} page=0x{:x} reason=remote", capPa2, capPage2);
            }
        });
    }


    void hostIssueBackstoreDelete(uint64_t pa) override {
        // Phase 3: dispatch to H64 when active
        if (_useH64 && _h64Host) {
            // Use a dummy epoch for now; the UBCC will pass the correct one
            // in the full integration.
            uint64_t deleteEpoch = ubcc.getEpochForLine(pa);
            _h64Host->erase(pa, deleteEpoch,
                [this](const BackstoreCompletion &comp) {
                    applyH64CoverageMutation(comp);
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

// Wire/transport adapter for the VI home controller.  The HA core deliberately
// knows neither CoherenceMessage nor Port; this class owns those translations
// and the DsmDataStore persistence completions.
struct HomeVIHost {
    Port *gem5Port;
    Port *netPort;
    int nodeId;
    int socketId;
    uint64_t &tickRef;
    DsmDataStore dsmData;

    HomeVIHost(Port *gem5, Port *net, int nid, int sid, uint64_t &tick)
        : gem5Port(gem5), netPort(net), nodeId(nid), socketId(sid), tickRef(tick)
    {}

    bool routeControlToTarget(const CoherenceMessage &msg) {
        if (msg.h.dstNode == nodeId && msg.h.dstSocket == socketId)
            return sendCoh(gem5Port, tickRef,
                           gidOf(nodeId, socketId),
                           gidOf(nodeId, socketId), msg);
        return sendCoh(netPort, tickRef,
                       gidOf(nodeId, socketId),
                       gidOf(msg.h.dstNode, msg.h.dstSocket), msg, true);
    }
};

struct HomeVIAdapter {
    cc::ha::HAController &ha;
    HomeVIHost &host;
    int nodeId;
    int socketId;
    uint64_t &tickRef;
    size_t maxActive;
    NodeAddressMap addressMap;

    struct RequestContext {
        uint16_t requesterNode = 0;
        uint16_t requesterSocket = 0;
        uint64_t address = 0;
        uint64_t wireReqId = 0;
        HAOperation operation = HAOperation::Read;
        uint64_t epoch = 0;
        std::array<uint8_t, 64> data{};
        bool hasData = false;
    };
    struct WireRequestKey {
        uint16_t node = 0;
        uint16_t socket = 0;
        uint64_t address = 0;
        uint64_t reqId = 0;
        HAOperation operation = HAOperation::Read;
        uint64_t epoch = 0;
        bool operator<(const WireRequestKey &o) const {
            return std::tie(node, socket, address, reqId, operation, epoch) <
                   std::tie(o.node, o.socket, o.address, o.reqId, o.operation, o.epoch);
        }
    };
    struct ResponseKey {
        CoherenceMessageType type = CoherenceMessageType::ReadReq;
        uint16_t node = 0;
        uint16_t socket = 0;
        uint64_t address = 0;
        uint64_t reqId = 0;
        bool operator<(const ResponseKey &o) const {
            return std::tie(type, node, socket, address, reqId) <
                   std::tie(o.type, o.node, o.socket, o.address, o.reqId);
        }
    };
    struct WritebackContext {
        uint16_t node = 0;
        uint16_t socket = 0;
        uint64_t address = 0;
        uint64_t wireReqId = 0;
    };
    std::map<uint64_t, RequestContext> requests;
    std::map<WireRequestKey, uint64_t> wireRequests;
    std::map<ResponseKey, uint64_t> expectedResponses;
    std::map<uint64_t, WritebackContext> writebacks;
    uint64_t nextInternalReqId = 1;
    uint64_t nextControlSeq = 1;

    HomeVIAdapter(cc::ha::HAController &controller, HomeVIHost &backing,
                  int nid, int sid, uint64_t &tick, size_t activeLimit)
        : ha(controller), host(backing), nodeId(nid), socketId(sid),
          tickRef(tick), maxActive(activeLimit),
          addressMap(g_numNodes, g_numSockets)
    {}

    uint32_t participant(uint32_t node, uint32_t socket) const {
        return node * static_cast<uint32_t>(g_numSockets) + socket;
    }

    uint32_t participantNode(uint32_t plane) const {
        return plane / static_cast<uint32_t>(g_numSockets);
    }

    uint16_t participantSocket(uint32_t plane) const {
        return static_cast<uint16_t>(plane % static_cast<uint32_t>(g_numSockets));
    }

    uint64_t allocInternalReqId() {
        while (!nextInternalReqId || requests.count(nextInternalReqId) ||
               writebacks.count(nextInternalReqId))
            ++nextInternalReqId;
        return nextInternalReqId++;
    }

    ResponseKey responseKey(CoherenceMessageType type,
                            const CoherenceMessage &msg) const {
        return {type, msg.h.srcNode, msg.h.srcSocket,
                msg.h.homeLinePa, msg.h.reqId};
    }

    bool takeExpected(CoherenceMessageType type, const CoherenceMessage &msg,
                      uint64_t &internalId) {
        auto it = expectedResponses.find(responseKey(type, msg));
        if (it == expectedResponses.end()) return false;
        internalId = it->second;
        expectedResponses.erase(it);
        return true;
    }

    void eraseRequest(uint64_t internalId) {
        auto it = requests.find(internalId);
        if (it == requests.end()) return;
        wireRequests.erase({it->second.requesterNode, it->second.requesterSocket,
                            it->second.address, it->second.wireReqId,
                            it->second.operation, it->second.epoch});
        for (auto expected = expectedResponses.begin();
             expected != expectedResponses.end();) {
            if (expected->second == internalId) expected = expectedResponses.erase(expected);
            else ++expected;
        }
        requests.erase(it);
    }

    void sendPermissionStatus(const CoherenceMessage &request, HAStatus status) {
        CoherenceMessage response;
        response.h.type = CoherenceMessageType::HAPermissionResp;
        response.h.srcNode = nodeId;
        response.h.srcSocket = socketId;
        response.h.dstNode = request.h.srcNode;
        response.h.dstSocket = request.h.srcSocket;
        response.h.homeNode = nodeId;
        response.h.homeSocket = socketId;
        response.h.homeLinePa = request.h.homeLinePa;
        response.h.reqId = request.h.reqId;
        response.b.haPermissionResp.operation = request.b.haPermissionReq.operation;
        response.b.haPermissionResp.status = status;
        response.b.haPermissionResp.permissionEpoch =
            request.b.haPermissionReq.permissionEpoch;
        panic_if(!host.routeControlToTarget(response),
                 "HA permission status send failed reqId={}", request.h.reqId);
    }

    bool handle(const CoherenceMessage &msg) {
        using EventKind = cc::ha::HAController::EventKind;
        const uint32_t sourceParticipant = participant(msg.h.srcNode, msg.h.srcSocket);
        switch (msg.h.type) {
          case CoherenceMessageType::HAPermissionReq: {
            if (msg.b.haPermissionReq.operation != HAOperation::Read &&
                msg.b.haPermissionReq.operation != HAOperation::Write) {
                sendPermissionStatus(msg, HAStatus::InvalidArgument);
                return true;
            }
            const WireRequestKey wireKey{msg.h.srcNode, msg.h.srcSocket,
                msg.h.homeLinePa, msg.h.reqId, msg.b.haPermissionReq.operation,
                msg.b.haPermissionReq.permissionEpoch};
            if (wireRequests.find(wireKey) != wireRequests.end())
                return true; // exact duplicate of a still-pending request
            if (requests.size() >= maxActive) {
                sendPermissionStatus(msg, HAStatus::RetryableBusy);
                return true;
            }
            const uint64_t internalId = allocInternalReqId();
            RequestContext context;
            context.requesterNode = msg.h.srcNode;
            context.requesterSocket = msg.h.srcSocket;
            context.address = msg.h.homeLinePa;
            context.wireReqId = msg.h.reqId;
            context.operation = msg.b.haPermissionReq.operation;
            context.epoch = msg.b.haPermissionReq.permissionEpoch;
            if (context.operation == HAOperation::Write) {
                std::memcpy(context.data.data(), msg.b.haPermissionReq.data, 64);
                context.hasData = true;
            }
            requests[internalId] = context;
            wireRequests[wireKey] = internalId;
            if (TracePerfPolicy::get().shouldEmit("ubio-ha-phase"))
                LogInfo("UBIO", "[HA-PHASE] phase=home_receive tick={} internalId={} "
                    "wireReqId={} requester={}:{} home={}:{} pa=0x{:x} op={}",
                    tickRef, internalId, context.wireReqId,
                    context.requesterNode, context.requesterSocket, nodeId,
                    socketId, context.address,
                    context.operation == HAOperation::Read ? "read" : "write");
            const bool accepted = ha.submit({
                msg.h.homeLinePa, sourceParticipant,
                context.operation == HAOperation::Read
                    ? cc::ha::HAController::RequestKind::Read
                    : cc::ha::HAController::RequestKind::Write,
                internalId,
                context.hasData
                    ? cc::ha::HAController::Payload{context.data, true}
                    : cc::ha::HAController::Payload{}});
            (void)accepted; // a false submit emits Reject, which still needs its context
            drainActions();
            return true;
          }
          case CoherenceMessageType::HAPermissionAck: {
            uint64_t internalId = 0;
            auto expected = expectedResponses.find(responseKey(
                CoherenceMessageType::HAPermissionAck, msg));
            if (expected == expectedResponses.end()) return true;
            auto request = requests.find(expected->second);
            if (request == requests.end() ||
                msg.h.srcNode != request->second.requesterNode ||
                msg.h.srcSocket != request->second.requesterSocket ||
                msg.b.haPermissionAck.operation != request->second.operation ||
                msg.b.haPermissionAck.status != HAStatus::Ok ||
                msg.b.haPermissionAck.permissionEpoch != request->second.epoch)
                return true;
            internalId = expected->second;
            expectedResponses.erase(expected);
            if (TracePerfPolicy::get().shouldEmit("ubio-ha-phase"))
                LogInfo("UBIO", "[HA-PHASE] phase=install_ack_receive tick={} "
                    "internalId={} wireReqId={} requester={}:{} home={}:{} "
                    "pa=0x{:x}", tickRef, internalId,
                    request->second.wireReqId, request->second.requesterNode,
                    request->second.requesterSocket, nodeId, socketId,
                    msg.h.homeLinePa);
            ha.accept({EventKind::InstallAck, msg.h.homeLinePa, sourceParticipant,
                       internalId, {}, false, false});
            drainActions();
            return true;
          }
          case CoherenceMessageType::HAPresenceProbeResp: {
            uint64_t internalId = 0;
            auto expected = expectedResponses.find(responseKey(
                CoherenceMessageType::HAPresenceProbeResp, msg));
            if (expected == expectedResponses.end()) return true;
            internalId = expected->second;
            if (msg.b.haPresenceProbeResp.status != HAStatus::Ok ||
                msg.b.haPresenceProbeResp.action != HAProbeAction::Query) {
                expectedResponses.erase(expected);
                ha.accept({EventKind::Unavailable, msg.h.homeLinePa, sourceParticipant,
                           internalId, {}, false, false});
                drainActions();
                return true;
            }
            expectedResponses.erase(expected);
            ha.accept({EventKind::ProbeResponse, msg.h.homeLinePa, sourceParticipant,
                       internalId, {},
                       msg.b.haPresenceProbeResp.present != 0, false});
            drainActions();
            return true;
          }
          case CoherenceMessageType::RecallResp: {
            uint64_t internalId = 0;
            if (!takeExpected(CoherenceMessageType::RecallResp, msg, internalId))
                return true;
            if (TracePerfPolicy::get().shouldEmit("ubio-ha-phase"))
                LogInfo("UBIO", "[HA-PHASE] phase=owner_data_receive tick={} "
                    "internalId={} source={}:{} home={}:{} pa=0x{:x}",
                    tickRef, internalId, msg.h.srcNode, msg.h.srcSocket,
                    nodeId, socketId, msg.h.homeLinePa);
            cc::ha::HAController::Payload payload;
            if (msg.h.flags & static_cast<uint32_t>(CFLAG_HAS_DATA)) {
                auto it = requests.find(internalId);
                if (it != requests.end()) {
                    std::memcpy(it->second.data.data(), msg.b.recallResp.data, 64);
                    it->second.hasData = true;
                }
                std::memcpy(payload.bytes.data(), msg.b.recallResp.data, 64);
                payload.valid = true;
            }
            if (!payload.valid) {
                ha.accept({EventKind::Unavailable, msg.h.homeLinePa, sourceParticipant,
                           internalId, {}, false, false});
                drainActions();
                return true;
            }
            ha.accept({EventKind::OwnerData, msg.h.homeLinePa, sourceParticipant,
                       internalId, payload, true, true});
            drainActions();
            return true;
          }
          case CoherenceMessageType::InvalidateAck: {
            uint64_t internalId = 0;
            if (!takeExpected(CoherenceMessageType::InvalidateAck, msg, internalId))
                return true;
            if (TracePerfPolicy::get().shouldEmit("ubio-ha-phase"))
                LogInfo("UBIO", "[HA-PHASE] phase=invalidate_ack_receive tick={} "
                    "internalId={} source={}:{} home={}:{} pa=0x{:x}",
                    tickRef, internalId, msg.h.srcNode, msg.h.srcSocket,
                    nodeId, socketId, msg.h.homeLinePa);
            ha.accept({EventKind::InvalidateAck, msg.h.homeLinePa, sourceParticipant,
                       internalId, {}, false, false});
            drainActions();
            return true;
          }
          case CoherenceMessageType::WritebackReq: {
            for (const auto &pending : writebacks) {
                if (pending.second.address != msg.h.homeLinePa) continue;
                CoherenceMessage response;
                response.h.type = CoherenceMessageType::WritebackResp;
                response.h.srcNode = nodeId; response.h.srcSocket = socketId;
                response.h.dstNode = msg.h.srcNode; response.h.dstSocket = msg.h.srcSocket;
                response.h.homeLinePa = msg.h.homeLinePa;
                response.h.localLinePa = msg.h.localLinePa;
                response.h.reqId = msg.h.reqId;
                response.b.writebackResp.success = false;
                host.routeControlToTarget(response);
                return true;
            }
            const uint64_t internalId = allocInternalReqId();
            cc::ha::HAController::Payload payload;
            if (msg.b.writebackReq.hasData) {
                std::memcpy(payload.bytes.data(), msg.b.writebackReq.data, 64);
                payload.valid = true;
            }
            if (!payload.valid) {
                CoherenceMessage response;
                response.h.type = CoherenceMessageType::WritebackResp;
                response.h.srcNode = nodeId; response.h.srcSocket = socketId;
                response.h.dstNode = msg.h.srcNode; response.h.dstSocket = msg.h.srcSocket;
                response.h.homeLinePa = msg.h.homeLinePa;
                response.h.localLinePa = msg.h.localLinePa;
                response.h.reqId = msg.h.reqId;
                response.b.writebackResp.success = false;
                host.routeControlToTarget(response);
                return true;
            }
            ha.accept({EventKind::Writeback, msg.h.homeLinePa,
                       sourceParticipant, internalId, payload,
                       (msg.h.flags & static_cast<uint32_t>(CFLAG_KEEP_AS_CLEAN)) != 0,
                       msg.b.writebackReq.hasData});
            writebacks[internalId] = {msg.h.srcNode, msg.h.srcSocket,
                                      msg.h.homeLinePa, msg.h.reqId};
            drainActions();
            return true;
          }
          case CoherenceMessageType::EvictReq: {
            ha.accept({EventKind::Evict, msg.h.homeLinePa, sourceParticipant,
                       msg.h.reqId, {}, false, false});
            CoherenceMessage response;
            response.h.type = CoherenceMessageType::EvictResp;
            response.h.srcNode = nodeId; response.h.srcSocket = socketId;
            response.h.dstNode = msg.h.srcNode; response.h.dstSocket = msg.h.srcSocket;
            response.h.homeLinePa = msg.h.homeLinePa; response.h.reqId = msg.h.reqId;
            response.b.evictResp.success = true;
            panic_if(!host.routeControlToTarget(response), "HA evict response send failed");
            return true;
          }
          case CoherenceMessageType::PeerExit:
            ha.accept({EventKind::PeerExit, ha.directory().config().base,
                       sourceParticipant, 0, {}, false, false});
            drainActions();
            return true;
          default:
            return false;
        }
    }

    void drainActions() {
        using ActionKind = cc::ha::HAController::ActionKind;
        while (ha.hasAction()) {
            const auto action = ha.popAction();
            auto contextIt = requests.find(action.requestId);
            if (action.kind == ActionKind::FetchMemory) {
                const bool queued = host.dsmData.readData(
                    action.address, tickRef,
                    [this, action](DsmDataStatus status, const uint8_t *data) {
                        auto it = requests.find(action.requestId);
                        if (it == requests.end()) return;
                        const bool zeroFill = status == DsmDataStatus::NotWritten;
                        if (status == DsmDataStatus::Ok && data) {
                            std::memcpy(it->second.data.data(), data, 64);
                            it->second.hasData = true;
                        } else if (zeroFill) {
                            it->second.data.fill(0);
                            it->second.hasData = true;
                        }
                        cc::ha::HAController::Payload payload;
                        if (status == DsmDataStatus::Ok && data) {
                            std::memcpy(payload.bytes.data(), data, 64);
                            payload.valid = true;
                        } else if (zeroFill) {
                            payload.bytes.fill(0);
                            payload.valid = true;
                        }
                        ha.accept({payload.valid
                                       ? cc::ha::HAController::EventKind::OwnerData
                                       : cc::ha::HAController::EventKind::Unavailable,
                                   action.address, action.source, action.requestId,
                                   payload, payload.valid, false});
                        drainActions();
                    });
                if (!queued && requests.count(action.requestId)) {
                    ha.accept({cc::ha::HAController::EventKind::Unavailable,
                               action.address, action.source, action.requestId,
                               {}, false, false});
                    drainActions();
                }
                continue;
            }
            if (action.kind == ActionKind::Commit || action.kind == ActionKind::Release) {
                if (action.kind == ActionKind::Commit && contextIt != requests.end()) {
                    const auto &context = contextIt->second;
                    if (TracePerfPolicy::get().shouldEmit("ubio-ha-phase"))
                        LogInfo("UBIO", "[HA-PHASE] phase=home_commit tick={} "
                            "internalId={} wireReqId={} requester={}:{} "
                            "home={}:{} pa=0x{:x}", tickRef, action.requestId,
                            context.wireReqId, context.requesterNode,
                            context.requesterSocket, nodeId, socketId,
                            action.address);
                }
                if (action.kind == ActionKind::Release) eraseRequest(action.requestId);
                continue;
            }
            if (action.kind == ActionKind::PersistMemory) {
                panic_if(!action.data.valid,
                         "HA persistence action lacks data reqId={}", action.requestId);
                const auto payload = action.data;
                if (contextIt != requests.end()) {
                    const auto &context = contextIt->second;
                    if (TracePerfPolicy::get().shouldEmit("ubio-ha-phase"))
                        LogInfo("UBIO", "[HA-PHASE] phase=persist_start tick={} "
                            "internalId={} wireReqId={} requester={}:{} "
                            "home={}:{} pa=0x{:x}", tickRef, action.requestId,
                            context.wireReqId, context.requesterNode,
                            context.requesterSocket, nodeId, socketId,
                            action.address);
                }
                const bool queued = host.dsmData.writeDataAsync(
                    action.address, payload.bytes.data(), tickRef,
                    [this, action](DsmDataStatus status) {
                        const bool isWriteback = writebacks.count(action.requestId) != 0;
                        if (!isWriteback && requests.count(action.requestId) == 0) return;
                        if (status != DsmDataStatus::Ok) {
                            if (isWriteback) {
                                const auto wb = writebacks.at(action.requestId);
                                CoherenceMessage response;
                                response.h.type = CoherenceMessageType::WritebackResp;
                                response.h.srcNode = nodeId; response.h.srcSocket = socketId;
                                response.h.dstNode = wb.node; response.h.dstSocket = wb.socket;
                                response.h.homeLinePa = wb.address;
                                response.h.localLinePa = wb.address;
                                response.h.reqId = wb.wireReqId;
                                response.b.writebackResp.success = false;
                                if (host.routeControlToTarget(response))
                                    writebacks.erase(action.requestId);
                            } else {
                                ha.accept({cc::ha::HAController::EventKind::Unavailable,
                                           action.address, action.source, action.requestId,
                                           {}, false, false});
                                drainActions();
                            }
                            return;
                        }
                        auto requestIt = requests.find(action.requestId);
                        if (requestIt != requests.end()) {
                            const auto &context = requestIt->second;
                            if (TracePerfPolicy::get().shouldEmit("ubio-ha-phase"))
                                LogInfo("UBIO", "[HA-PHASE] phase=persist_complete "
                                    "tick={} internalId={} wireReqId={} "
                                    "requester={}:{} home={}:{} pa=0x{:x}",
                                    tickRef, action.requestId,
                                    context.wireReqId, context.requesterNode,
                                    context.requesterSocket, nodeId, socketId,
                                    action.address);
                        }
                        ha.accept({cc::ha::HAController::EventKind::PersistenceComplete,
                                   action.address, action.source, action.requestId,
                                   {}, false, false});
                        drainActions();
                        auto wbIt = writebacks.find(action.requestId);
                        if (wbIt != writebacks.end()) {
                            CoherenceMessage response;
                            response.h.type = CoherenceMessageType::WritebackResp;
                            response.h.srcNode = nodeId; response.h.srcSocket = socketId;
                            response.h.dstNode = wbIt->second.node;
                            response.h.dstSocket = wbIt->second.socket;
                            response.h.homeLinePa = wbIt->second.address;
                            response.h.localLinePa = wbIt->second.address;
                            response.h.reqId = wbIt->second.wireReqId;
                            response.b.writebackResp.success = true;
                            if (host.routeControlToTarget(response))
                                writebacks.erase(wbIt);
                        }
                    });
                (void)queued; // failure callback applies safe unavailable/negative response
                continue;
            }
            // A failure can reject a transaction while sibling probe/invalidate
            // actions for it are already queued.  Those stale actions are safe
            // to discard; callbacks and queue draining must not dereference a
            // context that has since been released.
            if (contextIt == requests.end()) continue;
            const RequestContext &context = contextIt->second;
            const uint32_t recipient = action.kind == ActionKind::FetchOwner
                ? action.source : action.target;
            const uint32_t recipientNode = participantNode(recipient);
            const uint16_t recipientSocket = participantSocket(recipient);
            CoherenceMessage out;
            out.h.srcNode = nodeId; out.h.srcSocket = socketId;
            out.h.dstNode = recipientNode;
            out.h.dstSocket = recipientSocket;
            out.h.homeNode = nodeId; out.h.homeSocket = socketId;
            out.h.seqNum = nextControlSeq++;
            out.h.requesterNode = context.requesterNode;
            out.h.targetNode = recipientNode;
            out.h.homeLinePa = action.address;
            out.h.localLinePa = addressMap.buildDsmPA(
                recipientNode, nodeId, addressMap.dsmOffset(action.address), socketId);
            out.h.reqId = context.wireReqId;
            if (action.kind == ActionKind::FetchOwner) {
                out.h.type = CoherenceMessageType::RecallReq;
                out.h.flags |= static_cast<uint32_t>(CFLAG_HAS_DATA);
                if (context.operation == HAOperation::Read)
                    out.h.flags |= static_cast<uint32_t>(CFLAG_IS_READ_RECALL);
            } else if (action.kind == ActionKind::Invalidate) {
                out.h.type = CoherenceMessageType::InvalidateReq;
            } else if (action.kind == ActionKind::Probe) {
                out.h.type = CoherenceMessageType::HAPresenceProbeReq;
                out.b.haPresenceProbeReq.action = HAProbeAction::Query;
                out.b.haPresenceProbeReq.expectedEpoch = context.epoch;
            } else if (action.kind == ActionKind::GrantRead ||
                       action.kind == ActionKind::GrantWrite ||
                       action.kind == ActionKind::Reject) {
                out.h.type = CoherenceMessageType::HAPermissionResp;
                out.b.haPermissionResp.operation = context.operation;
                out.b.haPermissionResp.status = action.kind == ActionKind::Reject
                    ? HAStatus::RetryableBusy : HAStatus::Ok;
                out.b.haPermissionResp.permissionEpoch = context.epoch;
                if (context.hasData) {
                    out.b.haPermissionResp.hasData = 1;
                    std::memcpy(out.b.haPermissionResp.data, context.data.data(), 64);
                }
                if (action.data.valid) {
                    out.b.haPermissionResp.hasData = 1;
                    std::memcpy(out.b.haPermissionResp.data,
                                action.data.bytes.data(), 64);
                }
                panic_if(action.kind == ActionKind::GrantRead &&
                         !out.b.haPermissionResp.hasData,
                         "HA GrantRead missing 64-byte data pa=0x%lx requester=%u reqId=%lu",
                         action.address, context.requesterNode, context.wireReqId);
            } else {
                continue;
            }
            CoherenceMessageType responseType = CoherenceMessageType::ReadReq;
            bool expectsResponse = true;
            if (action.kind == ActionKind::FetchOwner)
                responseType = CoherenceMessageType::RecallResp;
            else if (action.kind == ActionKind::Invalidate)
                responseType = CoherenceMessageType::InvalidateAck;
            else if (action.kind == ActionKind::Probe)
                responseType = CoherenceMessageType::HAPresenceProbeResp;
            else if (action.kind == ActionKind::GrantRead || action.kind == ActionKind::GrantWrite)
                responseType = CoherenceMessageType::HAPermissionAck;
            else
                expectsResponse = false;
            if (expectsResponse)
                expectedResponses[{responseType, out.h.dstNode, out.h.dstSocket,
                                   out.h.homeLinePa, out.h.reqId}] = action.requestId;
            const bool sent = host.routeControlToTarget(out);
            if (!sent) {
                if (expectsResponse)
                    expectedResponses.erase({responseType, out.h.dstNode,
                                             out.h.dstSocket, out.h.homeLinePa,
                                             out.h.reqId});
                warn("HA action send unavailable type={} reqId={} target={}",
                     coherenceMsgTypeName(out.h.type), out.h.reqId, out.h.dstNode);
                if (action.kind != ActionKind::Reject &&
                    requests.count(action.requestId)) {
                    ha.accept({cc::ha::HAController::EventKind::Unavailable,
                               action.address, action.source, action.requestId,
                               {}, false, false});
                }
                continue;
            }
            const char *phase = nullptr;
            if (action.kind == ActionKind::FetchOwner) phase = "recall_send";
            else if (action.kind == ActionKind::Invalidate) phase = "invalidate_send";
            else if (action.kind == ActionKind::GrantRead ||
                     action.kind == ActionKind::GrantWrite) phase = "grant_send";
            if (phase && TracePerfPolicy::get().shouldEmit("ubio-ha-phase")) {
                LogInfo("UBIO", "[HA-PHASE] phase={} tick={} internalId={} "
                        "wireReqId={} requester={}:{} home={}:{} target={}:{} "
                        "pa=0x{:x}", phase, tickRef, action.requestId,
                        context.wireReqId, context.requesterNode,
                        context.requesterSocket, nodeId, socketId,
                        out.h.dstNode, out.h.dstSocket, action.address);
            }
            if (action.kind == ActionKind::Reject)
                eraseRequest(action.requestId);
        }
    }
};

bool
handleUbccMessage(UBCCController &ubcc, UbioBackstoreHost &host, int nid, int sid,
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
        uint64_t grantEpoch = 0;

        auto grant = ubcc.processOuterRequest(
            msg.h.homeLinePa, reqType,
            (msg.h.flags & static_cast<uint32_t>(CFLAG_WRITE_INTENT)) != 0,
            msg.h.requesterNode, msg.h.srcSocket,
            msg.h.epoch, msg.h.reqId,
            &grantVisibleTick, &sentinelVisibleTick,
            &recallNeeded, &recallOwnerNode,
            &dataSource, &authEpoch, &grantEpoch);

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
        response.h.srcSocket = sid;               // v4-dual-socket: home socket plane
        response.h.dstNode = msg.h.srcNode;
        response.h.dstSocket = msg.h.srcSocket;
        response.h.homeNode = nid;
        response.h.homeSocket = sid;              // v4-dual-socket: home socket plane
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
        response.b.readResp.grantEpoch = grantEpoch;
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
        response.h.srcSocket = sid;
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
        response.h.srcSocket = sid;
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
        bool deferred = false;
        bool accepted = ubcc.processOuterUpgradeReq(
            msg.h.homeLinePa, msg.h.requesterNode, msg.h.epoch, msg.h.reqId,
            msg.b.upgradeReq.desiredPerm,
            static_cast<UBCC_UpgradeCause>(msg.b.upgradeReq.cause),
            &notSharer, &deferred, msg.h.srcSocket);
        LogInfo("UBIO", "[UPGRADE-FORENSIC] stage=HOME_REQ_RESULT home={}:{} "
                "pa=0x{:x} reqId={} epoch={} requester={}:{} accepted={} "
                "deferred={} notSharer={}", nid, sid, msg.h.homeLinePa,
                msg.h.reqId, msg.h.epoch, msg.h.srcNode, msg.h.srcSocket,
                accepted ? 1 : 0, deferred ? 1 : 0, notSharer ? 1 : 0);
        if (deferred) {
            // Confirm that Home consumed and queued this exact tuple. The
            // requester keeps the same reqId and does not count this as a
            // dropped/no-response retry while resident replay owns the eventual
            // accepted UpgradeResp.
            response.h.type = CoherenceMessageType::UpgradeResp;
            response.h.srcNode = nid;
            response.h.srcSocket = sid;
            response.h.dstNode = msg.h.srcNode;
            response.h.dstSocket = msg.h.srcSocket;
            response.h.homeLinePa = msg.h.homeLinePa;
            response.h.epoch = msg.h.epoch;
            response.h.reqId = msg.h.reqId;
            response.h.flags = static_cast<uint32_t>(CFLAG_DEFERRED);
            response.b.upgradeResp.upgradeTargetMask = 0;
            response.b.upgradeResp.committedEpoch =
                ubcc.getEpochForLine(msg.h.homeLinePa);
            hasResponse = true;
            LogInfo("UBIO", "[UPGRADE-DEFERRED-RESP] home={}:{} requester={}:{} "
                    "pa=0x{:x} epoch={} reqId={}", nid, sid, msg.h.srcNode,
                    msg.h.srcSocket, msg.h.homeLinePa, msg.h.epoch, msg.h.reqId);
            return true;
        }
        response.h.type = CoherenceMessageType::UpgradeResp;
        response.h.srcNode = nid;
        response.h.srcSocket = sid;
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
        response.h.srcSocket = sid;
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
        LogInfo("UBIO", "[HOME-CLEAR-INGRESS] home={}:{} src={}:{} "
                "pa=0x{:x} reqId={}", nid, sid, msg.h.srcNode,
                msg.h.srcSocket, msg.h.homeLinePa, msg.h.reqId);
        if (g_debugUbioPerf) {
            LogDebug("UBIO",
                         "[DEBUG-UBIO-CLEAR] ubcc-enter nid={} type=ClearReq reqId={} pa=0x{:x} srcNode={} dstNode={} epoch={}",
                         nid, msg.h.reqId, msg.h.homeLinePa,
                         msg.h.srcNode, msg.h.dstNode, msg.h.epoch);
        }
        bool accepted = ubcc.processClear(
            msg.h.homeLinePa, msg.h.requesterNode, msg.h.epoch, msg.h.reqId);
        LogInfo("UBIO", "[HOME-CLEAR-RESULT] home={}:{} src={}:{} "
                "pa=0x{:x} epoch={} reqId={} accepted={}", nid, sid,
                msg.h.srcNode, msg.h.srcSocket, msg.h.homeLinePa,
                msg.h.epoch, msg.h.reqId, accepted ? 1 : 0);
        response.h.type = CoherenceMessageType::ClearResp;
        response.h.srcNode = nid;
        response.h.srcSocket = sid;
        response.h.dstNode = msg.h.srcNode;
        response.h.dstSocket = msg.h.srcSocket;
        response.h.homeLinePa = msg.h.homeLinePa;
        response.h.epoch = msg.h.epoch;
        response.h.reqId = msg.h.reqId;
        response.b.clearResp.accepted = accepted;
        if (g_debugUbioPerf) {
            LogDebug("UBIO",
                         "[DEBUG-UBIO-CLEAR] ubcc-exit nid={} type=ClearResp reqId={} pa=0x{:x} accepted={} dstNode={}",
                         nid, msg.h.reqId, msg.h.homeLinePa,
                         accepted ? 1 : 0, response.h.dstNode);
        }
        // Explicit lossless-oneway Clear still runs the exact same UBCC commit,
        // outstanding retirement, tombstone and waiter replay. It simply does
        // not create a ClearResp that the requester intentionally will not
        // consume.
        hasResponse = (msg.b.clearReq.reason != 1);
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
        if (!qFound && host.queryLineMetaFromBackstore(msg)) {
            return true;
        }
        response.h.type = CoherenceMessageType::QueryLineMetaResp;
        response.h.srcNode = nid;
        response.h.srcSocket = sid;
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
                               UBCCController *ubcc, UbioBackstoreHost *host,
                               HomeVIAdapter *haAdapter, uint64_t tick) {
    while (!g_delayedQueue.empty() && g_delayedQueue.front().fireTick <= tick) {
        DelayedMsg dm = g_delayedQueue.front();
        g_delayedQueue.pop_front();
        const CoherenceMessage &coh = dm.coh;
        LogWarn("UBIO", "[UBFAULT-DELIVER] node={} rule='{}' action={} "
                       "type={} src={} dst={} pa=0x{:x} reqId={} "
                       "fireTick={} currentTick={}",
                     nid, dm.ruleName, faultActionName(dm.action),
                     coherenceMsgTypeName(coh.h.type), coh.h.srcNode,
                     coh.h.dstNode, coh.h.homeLinePa, coh.h.reqId,
                     dm.fireTick, tick);
        // Re-inject: if it was from network, process as network message; else as gem5 message.
        // We push through the same handleUbccMessage path.
        for (int rep = 0; rep < dm.faultCopies; ++rep) {
            if (haAdapter) {
                if (haAdapter->handle(coh))
                    continue;
                if (dm.fromNetwork && isGem5Ingress(coh.h.type)) {
                    panic_if(!sendCoh(gem5Port, tick,
                                      gidOf(nid, sid), gidOf(nid, sid), coh),
                             "delayed HA network-to-gem5 send failed type={} reqId={}",
                             coherenceMsgTypeName(coh.h.type), coh.h.reqId);
                    continue;
                }
                panic_if(true, "delayed HA message unsupported type={} reqId={}",
                         coherenceMsgTypeName(coh.h.type), coh.h.reqId);
            }

            panic_if(!ubcc || !host,
                     "delayed legacy message lacks UBCC host type={} reqId={}",
                     coherenceMsgTypeName(coh.h.type), coh.h.reqId);
            CoherenceMessage response;
            bool hasResponse = false;
            bool handled = handleUbccMessage(*ubcc, *host, nid, sid, coh,
                                             response, hasResponse);
            if (dm.fromNetwork) {
                if (handled && hasResponse) {
                    sendCoh(netPort, tick, gidOf(nid, sid),
                            gidOf(coh.h.srcNode, coh.h.srcSocket), response, true);
                } else if (!handled && isGem5Ingress(coh.h.type)) {
                    sendCoh(gem5Port, tick,
                            gidOf(nid, sid), gidOf(nid, sid), coh);
                }
            } else {
                if (handled && hasResponse) {
                    sendCoh(gem5Port, tick,
                            gidOf(nid, sid), gidOf(nid, sid), response, false);
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
    std::vector<std::string> faultRuleArgs;
    int nid = 0;
    int sid = 0;
    bool paBitsExplicit = false;
    bool sharersBitsExplicit = false;

    for (int i = 1; i < argc; ++i) {
        if (!std::strncmp(argv[i], "--node=", 7)) nid = std::atoi(argv[i] + 7);
        if (!std::strncmp(argv[i], "--socket=", 9)) sid = std::atoi(argv[i] + 9);
        if (!std::strncmp(argv[i], "--num-sockets=", 14)) g_numSockets = std::atoi(argv[i] + 14);
        if (!std::strncmp(argv[i], "--num-nodes=", 12)) g_numNodes = std::atoi(argv[i] + 12);
        if (!std::strncmp(argv[i], "--home-controller=", 18)) {
            const char *p = argv[i] + 18;
            if (!std::strcmp(p, "ubcc")) g_homeControllerMode = HomeControllerMode::Ubcc;
            else if (!std::strcmp(p, "ha-vi")) g_homeControllerMode = HomeControllerMode::HaVi;
            else {
                LogError("UBIO", "[UBIO-FATAL] invalid --home-controller={} (valid: ubcc, ha-vi)", p);
                return 1;
            }
        }
        if (!std::strncmp(argv[i], "--ha-exact-base=", 16))
            g_haExactBase = std::strtoull(argv[i] + 16, nullptr, 0);
        if (!std::strncmp(argv[i], "--ha-exact-bytes=", 17))
            g_haExactBytes = std::strtoull(argv[i] + 17, nullptr, 0);
        if (!std::strncmp(argv[i], "--ha-max-active=", 16))
            g_haMaxActive = static_cast<size_t>(std::strtoull(argv[i] + 16, nullptr, 0));
        if (!std::strncmp(argv[i], "--ha-max-queue=", 15))
            g_haQueueDepth = static_cast<size_t>(std::strtoull(argv[i] + 15, nullptr, 0));
        if (!std::strncmp(argv[i], "--fault-rules=", 14)) {
            faultRuleArgs.emplace_back(argv[i] + 14);
        }
        // ResidentDir config (argv override env/defaults, §7.3)
        if (!std::strncmp(argv[i], "--bloom-bytes=", 14))
            g_rdcfg.bloom_bytes = (size_t)std::strtoull(argv[i] + 14, nullptr, 10);
        if (!std::strncmp(argv[i], "--sram-bytes=", 13))
            g_rdcfg.sram_bytes = (size_t)std::strtoull(argv[i] + 13, nullptr, 10);
        if (!std::strncmp(argv[i], "--pa-bits=", 10)) {
            g_rdcfg.pa_bits = std::atoi(argv[i] + 10);
            paBitsExplicit = true;
        }
        if (!std::strncmp(argv[i], "--sharers-bits=", 15)) {
            g_rdcfg.sharers_bits = std::atoi(argv[i] + 15);
            sharersBitsExplicit = true;
        }
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
                LogError("UBIO",
                    "[UBIO-FATAL] --backstore-schema={}: "
                    "Schema C exists in source but is not wired in ubio_main. "
                    "Use --backstore-schema=legacy_schema_a, h64, or disabled.", p);
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
                LogError("UBIO",
                    "[UBIO-FATAL] --backstore-schema={}: unrecognized. "
                    "Valid: legacy_schema_a, h64, disabled, auto.", p);
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

    if (g_homeControllerMode == HomeControllerMode::HaVi) {
        // VI owns coherence state exclusively in FlatBitmapDirectory.  Disable
        // every ResidentDir/H64 budget and metadata path before construction.
        g_schemaMode = BackstoreSchemaMode::Disabled;
        g_overflowPolicy = ResidentOverflowPolicy::NaiveEvict;
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
                LogError("UBIO",
                    "[UBIO-FATAL] group_index_bytes={} must equal {} "
                    "(sizeof(GroupIndex)*BloomGroups). "
                    "Remove --group-index-bytes= override or use "
                    "--sram-bytes < 65536 for tiny test configs.",
                    eff, kRealGroupIndexStorage);
                std::exit(1);
            }
        }
    }

    // ── Debug gates: default-off, opt-in via env vars ─────────────────
    if (const char *env = std::getenv("UBIO_DEBUG_PERF")) {
        g_debugUbioPerf = (std::atoi(env) != 0);
        if (g_debugUbioPerf) LogDebug("UBIO", "[UBIO-DEBUG] perf tracing enabled");
    }
    bool ubccDebugClear = false;
    if (const char *env = std::getenv("UBCC_DEBUG_CLEAR")) {
        ubccDebugClear = (std::atoi(env) != 0);
    }

    const uint64_t totalPlanes = g_numNodes > 0 && g_numSockets > 0
        ? static_cast<uint64_t>(g_numNodes) *
              static_cast<uint64_t>(g_numSockets)
        : 0;
    if (g_numNodes <= 0 || g_numNodes > NodeAddressMap::MAX_NODES ||
        g_numSockets <= 0 || totalPlanes > 32) {
        LogError("UBIO", "[UBIO-FATAL] invalid topology numNodes={} "
                 "numSockets={} totalPlanes={} (expected 1..{} nodes and "
                 "1..32 planes)",
                 g_numNodes, g_numSockets, totalPlanes,
                 NodeAddressMap::MAX_NODES);
        return 1;
    }
    int nodeIdBits = 0;
    for (int maxNodeId = g_numNodes - 1; maxNodeId > 0; maxNodeId >>= 1)
        ++nodeIdBits;
    const int requiredPaBits = NodeAddressMap::NODE_ADDR_SHIFT + nodeIdBits;
    if (!paBitsExplicit)
        g_rdcfg.pa_bits = requiredPaBits;
    if (!sharersBitsExplicit)
        g_rdcfg.sharers_bits = std::max(8, g_numNodes);
    if (g_rdcfg.pa_bits < requiredPaBits || g_rdcfg.pa_bits > 44) {
        LogError("UBIO", "[UBIO-FATAL] pa_bits={} cannot represent {} nodes "
                 "with NODE_ADDR_SHIFT={} (required {}..44)",
                 g_rdcfg.pa_bits, g_numNodes, NodeAddressMap::NODE_ADDR_SHIFT,
                 requiredPaBits);
        return 1;
    }
    if (g_rdcfg.sharers_bits < g_numNodes || g_rdcfg.sharers_bits > 16) {
        LogError("UBIO", "[UBIO-FATAL] sharers_bits={} cannot represent {} "
                 "nodes (required {}..16)", g_rdcfg.sharers_bits, g_numNodes,
                 g_numNodes);
        return 1;
    }
    if (g_schemaMode == BackstoreSchemaMode::LegacySchemaA &&
        g_rdcfg.sharers_bits > 10) {
        LogError("UBIO", "[UBIO-FATAL] legacy_schema_a stores only 10 sharer "
                 "bits; configured sharers_bits={}. Use "
                 "--backstore-schema=h64.",
                 g_rdcfg.sharers_bits);
        return 1;
    }
    if ((g_schemaMode == BackstoreSchemaMode::LegacySchemaA ||
         g_schemaMode == BackstoreSchemaMode::H64) &&
        g_rdcfg.epoch_bits > 24) {
        LogError("UBIO", "[UBIO-FATAL] schema {} stores only 24 epoch bits; "
                 "configured epoch_bits={}",
                 backstoreSchemaModeName(g_schemaMode), g_rdcfg.epoch_bits);
        return 1;
    }
    if (nid < 0 || nid >= g_numNodes) {
        LogError("UBIO", "[UBIO-FATAL] --node={} out of range [0,{})",
                 nid, g_numNodes);
        return 1;
    }
    if (sid < 0 || sid >= g_numSockets) {
        LogError("UBIO", "[UBIO-FATAL] --socket={} out of range [0,{})",
                 sid, g_numSockets);
        return 1;
    }
    for (const auto &rules : faultRuleArgs) parseFaultRules(rules, nid);

    // Socket-plane model: this ubio process is the home directory + router for
    // exactly one (node, socket) plane. num_sockets from --num-sockets arg.
    int gid = static_cast<int>(gidOf(nid, sid));

    LogInfo("UBIO", "[UBIO-START] node={} socket={} gid={} creating ports...",
            nid, sid, gid);
    PortConfig gem5Config;
    gem5Config.selfRole = "ubio";
    gem5Config.peerRole = "gem5";
    gem5Config.channelName = "coherence";
    gem5Config.nodeId = nid;
    gem5Config.socketId = sid;
    gem5Config.numNodes = g_numNodes;
    gem5Config.numSockets = g_numSockets;
    PortConfig netConfig;
    netConfig.selfRole = "ubio";
    netConfig.peerRole = "networksim";
    netConfig.channelName = "network";
    netConfig.nodeId = nid;
    netConfig.socketId = sid;
    netConfig.numNodes = g_numNodes;
    netConfig.numSockets = g_numSockets;
    Port *gem5Port = CreatePort(gem5Config);
    Port *netPort = CreatePort(netConfig);
    if (!gem5Port || !netPort) {
        LogError("UBIO", "[ubio:{}] port init failed", nid);
        if (gem5Port) DestroyPort(gem5Port);
        if (netPort) DestroyPort(netPort);
        return 1;
    }
    LogInfo("UBIO",
                 "[UBIO-IPC] nid={} sid={} coherence=ubio/gem5 network=ubio/networksim",
                 nid, sid);

    uint64_t tick = 0;
    cc::setUbioTickSource(&tick);

    std::unique_ptr<UBCCController> ubcc;
    if (g_homeControllerMode == HomeControllerMode::Ubcc) {
        ubcc.reset(new UBCCController(nid, sid, nullptr, 64,
                                      g_rdcfg.bloom_bytes, 0, g_numSockets,
                                      g_numNodes, &g_rdcfg));
        ubcc->setBatchRsEnabled(g_batchRs);
        ubcc->setResidentOverflowPolicy(g_overflowPolicy);
        if (ubccDebugClear) ubcc->setDebugClearTrace(true);
    }
    // Phase 3: H64 mode disables Bloom-negative shortcut
    if (ubcc && g_schemaMode == BackstoreSchemaMode::H64) {
        ubcc->setH64BloomAllMisses(true);
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

    std::unique_ptr<UbioBackstoreHost> host;
    std::unique_ptr<HomeVIHost> haHost;
    std::unique_ptr<cc::ha::HAController> haController;
    std::unique_ptr<HomeVIAdapter> haAdapter;
    if (g_homeControllerMode == HomeControllerMode::Ubcc) {
        host.reset(new UbioBackstoreHost(*ubcc, gem5Port, netPort, nid, sid, tick,
                                        useH64, useH64 ? &h64cfg : nullptr));
    } else {
        if (g_haExactBase == 0) {
            constexpr uint64_t kSegSize = 128ULL * 1024 * 1024;
            g_haExactBase = (static_cast<uint64_t>(nid) << 40) + 2 * kSegSize
                + static_cast<uint64_t>(nid * g_numSockets + sid) * kSegSize;
        }
        cc::ha::HAController::Config config;
        config.directory = {
            g_haExactBase, g_haExactBytes, 64,
            static_cast<uint32_t>(g_numNodes * g_numSockets)};
        config.perAddressQueueDepth = g_haQueueDepth;
        haController.reset(new cc::ha::HAController(config));
        haHost.reset(new HomeVIHost(gem5Port, netPort, nid, sid, tick));
        haAdapter.reset(new HomeVIAdapter(*haController, *haHost, nid, sid, tick,
                                         g_haMaxActive));
    }
    // T_ubio_dram: argv --dram-delay-ps= has priority (no env fallback)
    if (host) {
        host->_ubioDramDelayPs = g_dramDelayPs;
        ubcc->setHost(host.get());
        ubcc->setOutbound(host.get());
    }

    // ── Phase 3: Startup manifest & diagnostics ──────────────────────
    {
        if (g_homeControllerMode == HomeControllerMode::HaVi) {
            const auto &directory = haController->directory();
            LogInfo("UBIO",
                "[UBIO-HA-MANIFEST] controller=ha-vi node={} socket={} exact_base=0x{:x} "
                "exact_bytes={} line_bytes={} nodes={} line_count={} payload_bits={} "
                "payload_bytes_exact={} payload_bytes_allocated={} budget_bytes={} "
                "max_active={} per_address_queue={} resident_dir_bytes=0 h64_bytes=0",
                nid, sid, directory.config().base, directory.config().bytes,
                directory.config().lineBytes, directory.config().nodeCount,
                directory.lineCount(), directory.payloadBits(),
                directory.exactPayloadBytes(), directory.payloadBytes(),
                cc::ha::FlatBitmapDirectory::MaxPayloadBytes,
                g_haMaxActive, g_haQueueDepth);
        } else {
        const auto &layout = ubcc->directory().layout();
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
        LogInfo("UBIO",
            "[UBIO-MANIFEST] node={} socket={} num_nodes={} num_sockets={}\n"
            "[UBIO-MANIFEST] resident_pa_bits={} resident_sharers_bits={}\n"
            "[UBIO-MANIFEST] schema_mode={} overflow_policy={}\n"
            "[UBIO-MANIFEST] metadata_dram_configured={} MiB per_socket={} MiB "
                "(authoritative range: see [EPBACKEND-MANIFEST])\n"
            "[UBIO-MANIFEST] resident_capacity={} entries ({}-way x {}-set)\n"
            "[UBIO-MANIFEST] on_chip_budget_total={} KiB (limit=512 KiB)\n"
            "[UBIO-MANIFEST] on_chip_breakdown: dir={} KiB bloom={} KiB "
                "residentGroupIndex={} KiB hostLegacyGroupIndex={} KiB "
                "blc_reserved={} KiB desc_reserved={} KiB",
            nid, sid, g_numNodes, g_numSockets,
            layout.pa_bits, layout.sharers_bits,
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
            LogInfo("UBIO",
                "[UBIO-MANIFEST] H64_ACTIVE: bounded_txn_max={} active_rmw_max={} "
                "num_groups={} buckets_per_group={} "
                "metadata_lines={} table_start_offset={} "
                "(logical offsets only, clean [DEBUG-H64-*] gating)",
                h64cfg.max_pending_ops, h64cfg.max_active_rmw,
                h64cfg.num_groups, h64cfg.buckets_per_group,
                h64cfg.metadata_socket_lines, h64cfg.tableDataStartOffset());
        } else if (g_schemaMode == BackstoreSchemaMode::LegacySchemaA) {
            LogInfo("UBIO",
                "[UBIO-MANIFEST] H64_INACTIVE: using legacy_schema_a "
                "(page-chain with unbounded page cache)");
        }

        // Hard budget assertion (includes host duplicate)
        if (total_on_chip > 512 * 1024) {
            LogError("UBIO",
                "[UBIO-FATAL] total on-chip budget {} KiB exceeds 512 KiB "
                "limit. Reduce bloom/blc/desc or increase sram.",
                total_on_chip / 1024);
            std::exit(1);
        }
        }
    }
    // ── End Phase 0 manifest ─────────────────────────────────────────

    bool gem5Done = false, netDone = false, peerExitFailed = false;
    bool peerExitStarted = false;
    bool peerExitComplete = false;
    bool networkExitStarted = false;
    uint64_t networkExitAttempts = 0;
    uint64_t networkExitLastSendMs = 0;
    bool peerExitQuiesceLogged = false;
    std::set<ubiocc::PeerExitCoordinator::PeerId> peerExitMarked;
    using SteadyClock = std::chrono::steady_clock;
    const auto peerExitClockOrigin = SteadyClock::now();
    const uint64_t localPeerExitId = peerExitNonce(nid, sid);
    const uint64_t peerExitRetryMs = peerExitIntervalFromEnv(
        "UBIO_PEER_EXIT_RETRY_MS", 100, 1, 5000);
    const uint64_t peerExitQuiesceMs = peerExitIntervalFromEnv(
        "UBIO_PEER_EXIT_QUIESCE_MS", 2000, 1, 10000);
    const uint64_t peerExitDeliveryBudgetMs = peerExitIntervalFromEnv(
        "UBIO_PEER_EXIT_DELIVERY_BUDGET_MS", 1000, 1, 5000);
    if (peerExitRetryMs > std::numeric_limits<uint64_t>::max() -
            peerExitDeliveryBudgetMs ||
        peerExitQuiesceMs <= peerExitRetryMs + peerExitDeliveryBudgetMs) {
        LogError("UBIO", "[UBIO-FATAL] PeerExit quiesceMs={} must exceed "
                 "retryMs={} + deliveryBudgetMs={}", peerExitQuiesceMs,
                 peerExitRetryMs, peerExitDeliveryBudgetMs);
        return 1;
    }
    auto peerExitNowMs = [&]() -> uint64_t {
        return static_cast<uint64_t>(std::chrono::duration_cast<
            std::chrono::milliseconds>(SteadyClock::now() - peerExitClockOrigin)
                                         .count());
    };
    ubiocc::PeerExitCoordinator peerExitCoordinator(
        static_cast<uint32_t>(g_numNodes), static_cast<uint32_t>(g_numSockets),
        {static_cast<uint32_t>(nid), static_cast<uint32_t>(sid)},
        {peerExitRetryMs, peerExitQuiesceMs},
        [&]() {
            LogInfo("UBIO", "[PEER-EXIT-CLOSE] local={}:{} exitId={}",
                    nid, sid, localPeerExitId);
            peerExitComplete = true;
        }, localPeerExitId);
    std::map<std::tuple<ubiocc::PeerExitCoordinator::PeerId, bool, uint64_t>,
             uint64_t>
        peerExitSendAttempts;
    auto sendPeerExitActions =
        [&](const std::vector<ubiocc::PeerExitCoordinator::Action> &actions) {
            for (const auto &action : actions) {
                if (!netPort || netDone)
                    continue;
                const bool isAck = action.kind ==
                    ubiocc::PeerExitCoordinator::ActionKind::Ack;
                auto attemptKey =
                    std::make_tuple(action.peer, isAck, action.exitId);
                const uint64_t attempt = ++peerExitSendAttempts[attemptKey];
                CoherenceMessage exit;
                exit.h.type = CoherenceMessageType::PeerExit;
                exit.h.srcNode = static_cast<uint16_t>(nid);
                exit.h.srcSocket = static_cast<uint16_t>(sid);
                exit.h.dstNode = static_cast<uint16_t>(action.peer.node);
                exit.h.dstSocket = static_cast<uint16_t>(action.peer.socket);
                exit.h.reqId = action.exitId;
                exit.h.seqNum = 1;
                if (isAck)
                    exit.h.flags |= static_cast<uint32_t>(CFLAG_PEER_EXIT_ACK);
                if (logPeerExitAttempt(attempt)) {
                    LogInfo("UBIO", "[PEER-EXIT-{}-SEND] local={}:{} peer={}:{} "
                            "exitId={} attempt={} version={}",
                            isAck ? "ACK" : "NOTIFY", nid, sid,
                            action.peer.node, action.peer.socket, action.exitId,
                            attempt, exit.h.seqNum);
                }
                const bool sent = sendCoh(
                    netPort, tick,
                    gidOf(nid, sid),
                    gidOf(action.peer.node, action.peer.socket), exit, true);
                if (!sent && logPeerExitAttempt(attempt)) {
                    LogWarn("UBIO", "[PEER-EXIT-{}-SEND-FAILED] local={}:{} "
                            "peer={}:{} exitId={} attempt={} "
                            "recovery=sender_notify_retry",
                            isAck ? "ACK" : "NOTIFY", nid, sid,
                            action.peer.node, action.peer.socket, action.exitId,
                            attempt);
                }
            }
        };
    auto logPeerExitQuiesce = [&]() {
        if (!peerExitQuiesceLogged && peerExitCoordinator.state() ==
                ubiocc::PeerExitCoordinator::State::Quiescing) {
            peerExitQuiesceLogged = true;
            LogInfo("UBIO", "[PEER-EXIT-QUIESCE] local={}:{} exitId={} "
                    "acked={}/{}",
                    nid, sid, peerExitCoordinator.exitId(),
                    peerExitCoordinator.ackedPeers().size(),
                    peerExitCoordinator.requiredPeers().size());
        }
    };
    auto sendNetworkExitRequest = [&]() {
        CoherenceMessage request;
        request.h.type = CoherenceMessageType::NetworkExit;
        request.h.srcNode = static_cast<uint16_t>(nid);
        request.h.srcSocket = static_cast<uint16_t>(sid);
        request.h.dstNode = static_cast<uint16_t>(nid);
        request.h.dstSocket = static_cast<uint16_t>(sid);
        request.h.reqId = localPeerExitId;
        request.h.seqNum = 1;
        const uint32_t localModule = gidOf(nid, sid);
        const bool sent = sendCoh(
            netPort, tick, localModule, localModule, request, true);
        ++networkExitAttempts;
        if (logPeerExitAttempt(networkExitAttempts)) {
            LogInfo("UBIO", "[NETWORK-EXIT-REQUEST-SEND] local={}:{} "
                    "exitId={} attempt={} sent={}", nid, sid,
                    localPeerExitId, networkExitAttempts, sent ? 1 : 0);
        }
        networkExitLastSendMs = peerExitNowMs();
    };
    struct ReliableResponse {
        CoherenceMessage message;
        uint32_t targetGid = 0;
        uint64_t attempts = 0;
    };
    static constexpr size_t kMaxReliableResponses = 8192;
    std::deque<ReliableResponse> reliableResponses;
    auto sendNetworkResponse = [&](const CoherenceMessage &response,
                                   uint32_t targetGid) {
        if (!reliableResponses.empty()) {
            panic_if(reliableResponses.size() >= kMaxReliableResponses,
                     "reliable response queue full type={} reqId={}",
                     coherenceMsgTypeName(response.h.type), response.h.reqId);
            reliableResponses.push_back({response, targetGid, 0});
            return;
        }
        if (sendCoh(netPort, tick, gidOf(nid, sid), targetGid,
                    response, true)) {
            return;
        }
        panic_if(reliableResponses.size() >= kMaxReliableResponses,
                 "reliable response queue full type={} reqId={}",
                 coherenceMsgTypeName(response.h.type), response.h.reqId);
        reliableResponses.push_back({response, targetGid, 1});
        LogWarn("UBIO", "[UBIO-RESP-QUEUED] local={}:{} type={} reqId={} "
                "target={} depth={} reason=send_failed", nid, sid,
                coherenceMsgTypeName(response.h.type), response.h.reqId,
                targetGid, reliableResponses.size());
    };
    auto drainReliableResponses = [&]() {
        while (!reliableResponses.empty()) {
            ReliableResponse &pending = reliableResponses.front();
            ++pending.attempts;
            if (!sendCoh(netPort, tick, gidOf(nid, sid), pending.targetGid,
                         pending.message, true)) {
                if (logPeerExitAttempt(pending.attempts)) {
                    LogWarn("UBIO", "[UBIO-RESP-RETRY] local={}:{} type={} "
                            "reqId={} target={} attempt={} depth={}", nid, sid,
                            coherenceMsgTypeName(pending.message.h.type),
                            pending.message.h.reqId, pending.targetGid,
                            pending.attempts, reliableResponses.size());
                }
                break;
            }
            LogInfo("UBIO", "[UBIO-RESP-SENT] local={}:{} type={} reqId={} "
                    "target={} attempts={} remaining={}", nid, sid,
                    coherenceMsgTypeName(pending.message.h.type),
                    pending.message.h.reqId, pending.targetGid,
                    pending.attempts, reliableResponses.size() - 1);
            reliableResponses.pop_front();
        }
    };
    using BarrierKey = std::pair<uint32_t, uint32_t>;
    static constexpr size_t kMaxBarrierPlanes = 32;
    static constexpr size_t kMaxQueuedBarrierGenerations = 4;
    struct BarrierArrivals {
        std::array<std::array<uint32_t, kMaxQueuedBarrierGenerations>,
                   kMaxBarrierPlanes> seqs{};
        std::array<uint8_t, kMaxBarrierPlanes> head{};
        std::array<uint8_t, kMaxBarrierPlanes> count{};
    };
    std::map<BarrierKey, BarrierArrivals> barrierArrivals;
    auto barrierReady = [&](const BarrierKey &bk, const BarrierArrivals &arrivals) {
        for (size_t plane = 0; plane < kMaxBarrierPlanes; ++plane) {
            if ((bk.first & (1U << plane)) == 0)
                continue;
            if (arrivals.count[plane] == 0)
                return false;
        }
        return true;
    };
    auto releaseBarrier = [&](const BarrierKey &bk) {
        auto it = barrierArrivals.find(bk);
        if (it == barrierArrivals.end() || !barrierReady(bk, it->second))
            return;

        for (int targetPlane = 0; targetPlane < static_cast<int>(kMaxBarrierPlanes); ++targetPlane) {
            if ((bk.first & (1U << targetPlane)) == 0)
                continue;
            const int targetNode = targetPlane / g_numSockets;
            if (targetNode >= 32)
                continue;
            const size_t plane = static_cast<size_t>(targetPlane);
            const uint32_t seq = it->second.seqs[plane][it->second.head[plane]];
            const int targetSocket = targetPlane % g_numSockets;
            Port *deliveryPort = (targetNode == nid && targetSocket == sid)
                ? gem5Port : netPort;
            Message *rel = AllocateSendMessage(deliveryPort, tick);
            panic_if(!rel, "barrier release allocation failed mask=0x{:x} plane={}",
                     bk.first, targetPlane);
            SetMessageSourceId(rel, gidOf(nid, sid));
            SetMessageTargetId(rel, gidOf(targetNode, targetSocket));
            CoherenceMessage rmsg;
            rmsg.h.type = CoherenceMessageType::BarrierRelease;
            rmsg.b.barrier.mask = bk.first;
            // Each isolated gem5 process may have an independently observed
            // generation; release exactly the generation it reported.
            rmsg.b.barrier.seq = seq;
            panic_if(sizeof(rmsg) > GetMaxPayloadSize(),
                     "barrier release payload too large mask=0x{:x} plane={}",
                     bk.first, targetPlane);
            SetMessagePayload(rel, &rmsg, sizeof(rmsg));
            if (g_debugUbioPerf) {
                LogDebug("UBIO",
                    "[DEBUG-UBIO-BARRIER] release mask=0x{:x} plane={} seq={} route={} tick={}",
                    bk.first, targetPlane, seq,
                    deliveryPort == gem5Port ? "gem5" : "net", tick);
            }
            panic_if(!SendMessage(deliveryPort, rel),
                     "barrier release send failed mask=0x{:x} plane={}",
                     bk.first, targetPlane);
        }
        bool empty = true;
        for (size_t plane = 0; plane < kMaxBarrierPlanes; ++plane) {
            if ((bk.first & (1U << plane)) == 0)
                continue;
            it->second.head[plane] = static_cast<uint8_t>(
                (it->second.head[plane] + 1) % kMaxQueuedBarrierGenerations);
            --it->second.count[plane];
            empty = empty && it->second.count[plane] == 0;
        }
        if (empty)
            barrierArrivals.erase(it);
    };

    auto pollAndProcess = [&](Port *port, Port *replyPort, bool fromNetwork, bool *doneFlag) {
        (void)replyPort;
        if (!port) return;
        ReceiveStatus st;
        const Message *m = ReceiveMessage(port, tick, &st);
        int drain_cnt = 0;
        while (m && st == ReceiveStatus::Message) {
            if (++drain_cnt > 200) break;  // prevent starvation of other ports
            if (GetMessageType(m) == MessageType::Terminate) {
                LogInfo("UBIO", "[ubio:{}] recv TERMINATE ts={} from_net={}",
                             nid, GetMessageTimestamp(m), fromNetwork);
                if (!fromNetwork) {
                    if (!gem5Done) {
                        if (ubcc) {
                        LogInfo("UBIO", "[UBCC-STATS-PHASE] gem5_terminated");
                        ubcc->directory().dumpStatsJson();
                        LogInfo("UBIO", "[UBCC-STATS] {}", ubcc->dumpStatsJson());
                        }
                    }
                    gem5Done = true;
                    *doneFlag = true;
                    if (!peerExitStarted) {
                        peerExitStarted = true;
                        const uint64_t nowMs = peerExitNowMs();
                        const auto actions =
                            peerExitCoordinator.startLocalExit(nowMs);
                        LogInfo("UBIO", "[PEER-EXIT-START] local={}:{} exitId={} "
                                "version=1 required={} seenNotify={} retryMs={} "
                                "quiesceMs={} deliveryBudgetMs={} "
                                "contract=bounded_transient_loss_delay",
                                nid, sid, peerExitCoordinator.exitId(),
                                peerExitCoordinator.requiredPeers().size(),
                                peerExitCoordinator.seenNotifyPeers().size(),
                                peerExitRetryMs, peerExitQuiesceMs,
                                peerExitDeliveryBudgetMs);
                        sendPeerExitActions(actions);
                        logPeerExitQuiesce();
                    }
                } else {
                    if (peerExitCoordinator.state() !=
                            ubiocc::PeerExitCoordinator::State::Closed) {
                        peerExitFailed = true;
                        LogError("UBIO", "[PEER-EXIT-WARN] local={}:{} "
                                 "network terminated before handshake state={}",
                                 nid, sid, static_cast<unsigned>(
                                     peerExitCoordinator.state()));
                    }
                    *doneFlag = true;
                }
                if (*doneFlag) break;
                m = ReceiveMessage(port, tick, &st);
                continue;
            }
            if (GetMessageType(m) == MessageType::ControlSync) {
                m = ReceiveMessage(port, tick, &st);
                continue;
            }
            if (GetMessageType(m) != MessageType::Payload) {
                LogWarn("UBIO", "[ubio:{}] drop Message type={} ts={} size={}",
                             nid, static_cast<unsigned>(GetMessageType(m)),
                             GetMessageTimestamp(m), GetMessagePayloadSize(m));
                m = ReceiveMessage(port, tick, &st);
                continue;
            }

            const CoherenceMessage *coh =
                GetMessagePayloadSize(m) == sizeof(CoherenceMessage)
                    ? static_cast<const CoherenceMessage *>(GetMessagePayloadData(m))
                    : nullptr;
            if (!coh) {
                LogWarn("UBIO", "[ubio:{}] bad payload size={} req_id={}",
                             nid, GetMessagePayloadSize(m), GetMessageRequestId(m));
                m = ReceiveMessage(port, tick, &st);
                continue;
            }
            if (coh->h.type == CoherenceMessageType::UpgradeReq ||
                coh->h.type == CoherenceMessageType::UpgradeResp) {
                LogInfo("UBIO", "[UPGRADE-FORENSIC] stage={} local={}:{} "
                        "pa=0x{:x} type={} reqId={} epoch={} src={}:{} dst={}:{} "
                        "envelope={}:{} msgTs={} tick={}",
                        fromNetwork ? "UBIO_NET_RECV" : "UBIO_GEM5_RECV",
                        nid, sid, coh->h.homeLinePa,
                        coherenceMsgTypeName(coh->h.type), coh->h.reqId,
                        coh->h.epoch, coh->h.srcNode, coh->h.srcSocket,
                        coh->h.dstNode, coh->h.dstSocket,
                        GetMessageSourceId(m), GetMessageTargetId(m),
                        GetMessageTimestamp(m), tick);
            }

            if (gem5Done && coh->h.type != CoherenceMessageType::PeerExit &&
                coh->h.type != CoherenceMessageType::NetworkExit) {
                static uint64_t postExitDrops = 0;
                if (++postExitDrops <= 8 || logPeerExitAttempt(postExitDrops)) {
                    LogWarn("UBIO", "[POST-EXIT-DROP] local={}:{} type={} "
                            "src={}:{} reqId={} reason=local_cache_plane_exited",
                            nid, sid, coherenceMsgTypeName(coh->h.type),
                            coh->h.srcNode, coh->h.srcSocket, coh->h.reqId);
                }
                m = ReceiveMessage(port, tick, &st);
                continue;
            }

            // Forward BarrierRelease from network to local gem5 (per-socket barrier).
            if (coh->h.type == CoherenceMessageType::BarrierRelease) {
                if (fromNetwork) {
                    // Arrived from a peer ubio (another local socket) — forward
                    // to local gem5's UBAdapter via gem5Port.
                    Message *rel = AllocateSendMessage(gem5Port, tick);
                    if (rel) {
                        CopyMessage(rel, m);
                        SetMessageSourceId(rel, gidOf(nid, sid));
                        SetMessageTargetId(rel, gidOf(nid, sid));
                        SendMessage(gem5Port, rel);
                        if (g_debugUbioPerf)
                            LogDebug("UBIO", "[DEBUG-UBIO-BARRIER] n{} release fwd mask=0x{:x}",
                                         nid, coh->b.barrier.mask);
                    }
                }
                // Already handled via gem5Port send above; skip further processing.
                m = ReceiveMessage(port, tick, &st);
                continue;
            }

            // Cross-node barrier (now a PAYLOAD CoherenceMessage). Each set
                // mask bit identifies one
                // (node,socket) plane. Once all masked planes arrive, reply
                // BarrierRelease to those same planes.
                // TC90 fix: key by (mask, seq) to distinguish successive barriers
                // sharing the same mask. Without this, interleaved BarrierReached
                // messages from different generations pollute the set and get
                // cleared together, causing later barriers to never complete.
                if (coh->h.type == CoherenceMessageType::BarrierReached) {
                    uint32_t mask = coh->b.barrier.mask;
                    uint32_t seq  = coh->b.barrier.seq;
                    int src = static_cast<int>(GetMessageSourceId(m));
                    // Generations are local to isolated gem5 processes and
                    // therefore cannot form a distributed key. Aggregate one
                    // in-flight generation per mask and return each plane's
                    // local sequence in its own release message.
                    BarrierKey bk{mask, 0};
                    panic_if(mask == 0, "barrier mask must not be empty");
                    const int leaderPlane = __builtin_ctz(mask);
                    const int leaderNode = leaderPlane / g_numSockets;
                    const int leaderSocket = leaderPlane % g_numSockets;
                    if (nid == leaderNode && sid == leaderSocket) {
                        if (src < 0 || src >= static_cast<int>(kMaxBarrierPlanes) ||
                            (mask & (1U << src)) == 0) {
                            LogWarn("UBIO",
                                         "[UBIO-BARRIER-WARN] n{} ignored source={} mask=0x{:x}",
                                         nid, src, mask);
                            m = ReceiveMessage(port, tick, &st);
                            continue;
                        }
                        BarrierArrivals &arrivals = barrierArrivals[bk];
                        const size_t plane = static_cast<size_t>(src);
                        panic_if(arrivals.count[plane] == kMaxQueuedBarrierGenerations,
                                 "barrier FIFO full mask=0x{:x} plane={}", mask, src);
                        const size_t tail = (arrivals.head[plane] + arrivals.count[plane]) %
                                            kMaxQueuedBarrierGenerations;
                        arrivals.seqs[plane][tail] = seq;
                        ++arrivals.count[plane];
                        if (g_debugUbioPerf) {
                            LogDebug("UBIO",
                                "[DEBUG-UBIO-BARRIER] enqueue mask=0x{:x} plane={} seq={} depth={} tick={}",
                                mask, src, seq, arrivals.count[plane], tick);
                        }
                        releaseBarrier(bk);
                    } else if (!fromNetwork && netPort) {
                        // A single deterministic leader aggregates arrivals.
                        // Broadcast coordination allowed different nodes to
                        // independently release incompatible generations.
                        Message *fwd = AllocateSendMessage(netPort, tick);
                        if (fwd) {
                            CopyMessage(fwd, m);
                            // CopyMessage preserves the gem5-local envelope.
                            // This UBIO plane is the real network source and
                            // the computed leader plane is the real target.
                            SetMessageSourceId(fwd, gidOf(nid, sid));
                            SetMessageTargetId(
                                fwd, gidOf(leaderNode, leaderSocket));
                            SendMessage(netPort, fwd);
                        }
                    }
                m = ReceiveMessage(port, tick, &st);
                continue;
            }

            LogDebug("UBIO", "[ubio:{}] {} recv {} reqId={} src={} dst={}",
                         nid, fromNetwork ? "net" : "gem5",
                         coherenceMsgTypeName(coh->h.type), coh->h.reqId,
                         GetMessageSourceId(m), GetMessageTargetId(m));
            if (TracePerfPolicy::get().shouldEmit("ubio")) {
                LogInfo("UBIO", "[TRACE-PERF] {}|{}|ubio|{}|0x{:x}|{}|{}",
                             GetMessageTimestamp(m), nid, coh->h.reqId, coh->h.homeLinePa,
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
                faultCopies = applyUbioFault(*coh, nid, tick, fromNetwork);
                if (faultCopies == 0) {
                    // Dropped — neither processed nor forwarded.
                    m = ReceiveMessage(port, tick, &st);
                    continue;
                }
            }

            // PeerExit is deliberately processed only after fault injection so
            // drop/duplicate rules exercise the reliable wall-clock handshake.
            if (coh->h.type == CoherenceMessageType::PeerExit) {
                if (!fromNetwork) {
                    LogWarn("UBIO", "[PEER-EXIT-WARN] local={}:{} ignored "
                            "non-network PeerExit src={}:{} exitId={}",
                            nid, sid, coh->h.srcNode, coh->h.srcSocket,
                            coh->h.reqId);
                    m = ReceiveMessage(port, tick, &st);
                    continue;
                }
                constexpr uint32_t kPeerExitFlags =
                    static_cast<uint32_t>(CFLAG_PEER_EXIT_ACK);
                const uint32_t sourceModule = gidOf(
                    static_cast<int>(coh->h.srcNode),
                    static_cast<int>(coh->h.srcSocket));
                const uint32_t localModule = gidOf(nid, sid);
                if (coh->h.seqNum != 1 || coh->h.reqId == 0 ||
                    (coh->h.flags & ~kPeerExitFlags) != 0 ||
                    GetMessageRequestId(m) != coh->h.reqId ||
                    GetMessageSourceId(m) != sourceModule ||
                    GetMessageTargetId(m) != localModule) {
                    LogWarn("UBIO", "[PEER-EXIT-WARN] local={}:{} peer={}:{} "
                            "exitId={} version={} flags=0x{:x} envelope={}:{} "
                            "reqEnvelope={} ignored=invalid_protocol",
                            nid, sid, coh->h.srcNode, coh->h.srcSocket,
                            coh->h.reqId, coh->h.seqNum, coh->h.flags,
                            GetMessageSourceId(m), GetMessageTargetId(m),
                            GetMessageRequestId(m));
                    m = ReceiveMessage(port, tick, &st);
                    continue;
                }
                const bool isAck = (coh->h.flags &
                    static_cast<uint32_t>(CFLAG_PEER_EXIT_ACK)) != 0;
                const ubiocc::PeerExitCoordinator::PeerId peer{
                    coh->h.srcNode, coh->h.srcSocket};
                if (coh->h.dstNode != static_cast<uint16_t>(nid) ||
                    coh->h.dstSocket != static_cast<uint16_t>(sid) ||
                    peer.node >= static_cast<uint32_t>(g_numNodes) ||
                    peer.socket >= static_cast<uint32_t>(g_numSockets) ||
                    (peer.node == static_cast<uint32_t>(nid) &&
                     peer.socket == static_cast<uint32_t>(sid))) {
                    LogWarn("UBIO", "[PEER-EXIT-WARN] local={}:{} peer={}:{} "
                            "dst={}:{} exitId={} ignored=invalid_peer_or_route",
                            nid, sid, peer.node, peer.socket, coh->h.dstNode,
                            coh->h.dstSocket, coh->h.reqId);
                    m = ReceiveMessage(port, tick, &st);
                    continue;
                }
                for (int rep = 0; rep < faultCopies; ++rep) {
                    LogInfo("UBIO", "[PEER-EXIT-{}-RECV] local={}:{} peer={}:{} "
                            "exitId={} version={} copy={}/{}",
                            isAck ? "ACK" : "NOTIFY", nid, sid,
                            peer.node, peer.socket, coh->h.reqId, coh->h.seqNum,
                            rep + 1, faultCopies);
                    if (isAck) {
                        sendPeerExitActions(peerExitCoordinator.receiveAck(
                            peer, coh->h.reqId, peerExitNowMs()));
                    } else {
                        if (peerExitMarked.insert(peer).second) {
                            if (ubcc)
                                ubcc->markPeerPlaneExited(peer.node, peer.socket);
                            if (haAdapter)
                                haAdapter->handle(*coh);
                        }
                        sendPeerExitActions(peerExitCoordinator.receiveNotify(
                            peer, coh->h.reqId, peerExitNowMs()));
                    }
                    logPeerExitQuiesce();
                }
                m = ReceiveMessage(port, tick, &st);
                continue;
            }

            if (coh->h.type == CoherenceMessageType::NetworkExit) {
                const uint32_t localModule = gidOf(nid, sid);
                constexpr uint32_t kAckFlag =
                    static_cast<uint32_t>(CFLAG_NETWORK_EXIT_ACK);
                if (!fromNetwork || coh->h.seqNum != 1 ||
                    coh->h.reqId != localPeerExitId ||
                    coh->h.flags != kAckFlag ||
                    coh->h.srcNode != static_cast<uint16_t>(nid) ||
                    coh->h.srcSocket != static_cast<uint16_t>(sid) ||
                    coh->h.dstNode != static_cast<uint16_t>(nid) ||
                    coh->h.dstSocket != static_cast<uint16_t>(sid) ||
                    GetMessageRequestId(m) != localPeerExitId ||
                    GetMessageSourceId(m) != localModule ||
                    GetMessageTargetId(m) != localModule) {
                    LogWarn("UBIO", "[NETWORK-EXIT-WARN] local={}:{} exitId={} "
                            "ignored=invalid_ack", nid, sid, coh->h.reqId);
                    m = ReceiveMessage(port, tick, &st);
                    continue;
                }
                LogInfo("UBIO", "[NETWORK-EXIT-ACK-RECV] local={}:{} exitId={} "
                        "attempts={}", nid, sid, localPeerExitId,
                        networkExitAttempts);
                netDone = true;
                *doneFlag = true;
                break;
            }

            if (g_debugUbioPerf && (coh->h.type == CoherenceMessageType::ClearReq ||
                coh->h.type == CoherenceMessageType::ClearResp)) {
                LogDebug("UBIO",
                             "[DEBUG-UBIO-CLEAR] recv nid={} from={} type={} reqId={} pa=0x{:x} srcNode={} dstNode={} requester={} epoch={}",
                             nid, fromNetwork ? "net" : "gem5",
                             coherenceMsgTypeName(coh->h.type),
                             coh->h.reqId, coh->h.homeLinePa,
                             coh->h.srcNode, coh->h.dstNode,
                             coh->h.requesterNode, coh->h.epoch);
            }

            if (g_debugUbioPerf && (coh->h.type == CoherenceMessageType::RecallReq ||
                coh->h.type == CoherenceMessageType::RecallResp)) {
                LogDebug("UBIO", "[DEBUG-RECALL-TRACE-C] ubio:{} {} {} reqId={} cohDst={}",
                             nid, fromNetwork ? "net" : "gem5",
                             coherenceMsgTypeName(coh->h.type), coh->h.reqId, coh->h.dstNode);
            }

            if (g_debugUbioPerf && coh->h.type == CoherenceMessageType::ReadReq) {
                LogDebug("UBIO",
                             "[DEBUG-UBIO-RR-PATH] reqId={} from={} srcNode={} dstNode={} nid={} enter_dstNode_check={} homeLinePa=0x{:x}",
                             coh->h.reqId, fromNetwork ? "net" : "gem5",
                             coh->h.srcNode, coh->h.dstNode, nid,
                             (coh->h.dstNode != nid) ? "true" : "false",
                             coh->h.homeLinePa);
            }

            if (coh->h.dstNode != nid || coh->h.dstSocket != sid) {
                // If this PA belongs to our local DSM plane, force local processing
                bool isDsm = ubcc ? ubcc->isDsmAddr(coh->h.homeLinePa)
                                  : (haController &&
                                     haController->directory().contains(coh->h.homeLinePa));
                if (g_debugUbioPerf && coh->h.type == CoherenceMessageType::ReadReq) {
                    LogDebug("UBIO",
                                 "[DEBUG-UBIO-RR-PATH] reqId={} dstNode!=nid true, isDsmAddr={} -> pass_non_dsm_check={} homeLinePa=0x{:x}",
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
                            LogDebug("UBIO", "[DEBUG-TRACE-2] n{} FWD {} dst={}:{} via net",
                                         nid, coherenceMsgTypeName(coh->h.type),
                                         coh->h.dstNode, coh->h.dstSocket);
                        }
                        bool sent = sendCoh(netPort, tick,
                                            gidOf(nid, sid),
                                            gidOf(coh->h.dstNode, coh->h.dstSocket),
                                            *coh, true);
                        if (g_debugUbioPerf && coh->h.type == CoherenceMessageType::ReadReq) {
                            LogDebug("UBIO",
                                         "[DEBUG-UBIO-RR-PATH] reqId={} forward_sendCoh_called=true sendCoh_ret={} dstNode={}",
                                         coh->h.reqId, sent ? "true" : "false", coh->h.dstNode);
                        }
                    } else {
                        if (g_debugUbioPerf) {
                            LogDebug("UBIO", "[ubio:{}] DROP cross-node {} (no net)",
                                         nid, coherenceMsgTypeName(coh->h.type));
                        }
                        if (g_debugUbioPerf && coh->h.type == CoherenceMessageType::ReadReq) {
                            LogDebug("UBIO",
                                         "[DEBUG-UBIO-RR-PATH] reqId={} forward_sendCoh_called=false reason=no_netPort",
                                         coh->h.reqId);
                        }
                    }
                    m = ReceiveMessage(port, tick, &st);
                    continue;
                }
            }

            if (fromNetwork) {
                for (int rep = 0; rep < faultCopies; ++rep) {
                    if (haAdapter) {
                        // Home-directed HA wire traffic (permission requests and
                        // acknowledgements, recall responses, invalidate acks,
                        // probes, writebacks, and evictions) terminates here.
                        if (haAdapter->handle(*coh)) continue;

                        // Point-to-point HA control traffic terminates in the
                        // local gem5 participant, not in the home controller.
                        if (isGem5Ingress(coh->h.type)) {
                            if (gem5Done) {
                                LogError("UBIO",
                                    "[UBIO-HA-FATAL] cannot deliver network {} to exited "
                                    "gem5 node={} socket={} reqId={} pa=0x{:x}",
                                    coherenceMsgTypeName(coh->h.type), nid, sid,
                                    coh->h.reqId, coh->h.homeLinePa);
                            } else {
                                panic_if(!sendCoh(gem5Port, tick,
                                                  gidOf(nid, sid), gidOf(nid, sid),
                                                  *coh),
                                    "HA network-to-gem5 send failed type={} reqId={}",
                                    coherenceMsgTypeName(coh->h.type), coh->h.reqId);
                            }
                            continue;
                        }

                        CoherenceMessage reject;
                        if (buildHaLegacyReject(*coh, nid, sid, reject)) {
                            LogError("UBIO",
                                "[UBIO-HA-REJECT] unsupported legacy network request "
                                "type={} reqId={} pa=0x{:x}",
                                coherenceMsgTypeName(coh->h.type), coh->h.reqId,
                                coh->h.homeLinePa);
                            panic_if(!sendCoh(netPort, tick, gidOf(nid, sid),
                                      gidOf(coh->h.srcNode, coh->h.srcSocket),
                                      reject, true),
                                "HA legacy reject send failed type={} reqId={}",
                                coherenceMsgTypeName(reject.h.type), reject.h.reqId);
                        } else {
                            LogError("UBIO",
                                "[UBIO-HA-FATAL] unsupported network message type={} "
                                "reqId={} pa=0x{:x}; no legacy UBCC/MetaRNF path exists",
                                coherenceMsgTypeName(coh->h.type), coh->h.reqId,
                                coh->h.homeLinePa);
                            panic_if(true,
                                "HA mode unsupported network message type={} reqId={} pa=0x{:x}",
                                coherenceMsgTypeName(coh->h.type), coh->h.reqId,
                                coh->h.homeLinePa);
                        }
                        continue;
                    }

                    panic_if(!ubcc || !host,
                             "UBCC network dispatch lacks controller/host type={} reqId={}",
                             coherenceMsgTypeName(coh->h.type), coh->h.reqId);
                    CoherenceMessage response;
                    bool hasResponse = false;
                    host->_metaRNF.enterReentrant();
                    bool handled = handleUbccMessage(*ubcc, *host, nid, sid, *coh, response, hasResponse);
                    host->_metaRNF.leaveReentrant();
                    if (handled && coh->h.type == CoherenceMessageType::RecallResp) {
                        // RECALL.DONE only flips state inside the home UBCC; there is
                        // no normal response packet back to gem5. Mirror the RecallResp
                        // to the local UBAdapter as a wake-only notification so the
                        // requester's EP-SNF retries immediately instead of waiting for
                        // the 20k-cycle fallback timer.
                        bool sentToGem5 = sendCoh(gem5Port, tick,
                            gidOf(nid, sid), gidOf(nid, sid), *coh);
                        if (g_debugUbioPerf) {
                            LogDebug("UBIO",
                                         "[DEBUG-TRACE-4-RECALL] n{} net->gem5 recall-done sendCoh_ret={} reqId={} dstSocket={}",
                                         nid, sentToGem5 ? "true" : "false",
                                         coh->h.reqId, sid);
                        }
                    }
                    if (handled && hasResponse) {
                        if (g_debugUbioPerf) {
                            LogDebug("UBIO", "[DEBUG-TRACE-3] n{} net->UBCC grant, sending {} back to {}:{}",
                                         nid, coherenceMsgTypeName(response.h.type),
                                         coh->h.srcNode, coh->h.srcSocket);
                        }
                        // Response returns to the requester's (node, socket) plane.
                        sendNetworkResponse(
                            response, gidOf(coh->h.srcNode, coh->h.srcSocket));
                    } else if (!handled && isGem5Ingress(coh->h.type)) {
                        if (gem5Done && coh->h.type == CoherenceMessageType::RecallReq) {
                            // gem5 已退出，无法处理 RECALL。合成 RecallResp 返回给 home。
                            // 注意：此时 gem5 的 L1/L2 可能有未写回的 dirty 数据，
                            // 但 barrier 设计保证 verify 在 gem5 退出前完成，
                            // 此路径仅作防御性兜底。
                            LogWarn("UBIO",
                                "[RECALL-PROXY] n{} gem5Done=true, synthesizing RecallResp "
                                "for PA=0x{:x} reqId={} homeNode={}",
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
                                if (host->dsmData.copyData(coh->h.homeLinePa,
                                                          resp.b.recallResp.data)) {
                                    LogWarn("UBIO",
                                        "[RECALL-PROXY] n{} using DsmDataStore data for PA=0x{:x}",
                                        nid, coh->h.homeLinePa);
                                } else {
                                    panic_if(true,
                                        "gem5 exited with no authoritative recall data "
                                        "node={} socket={} PA=0x{:x} reqId={}",
                                        nid, sid, coh->h.homeLinePa, coh->h.reqId);
                                }
                            }
                            resp.h.flags |= static_cast<uint32_t>(CFLAG_HAS_DATA);
                            sendCoh(netPort, tick, gidOf(nid, sid),
                                    gidOf(coh->h.homeNode, coh->h.homeSocket),
                                    resp, true);
                        } else if (gem5Done) {
                            // gem5 已退出，其他 gem5Ingress 消息无法处理，记录告警
                            LogWarn("UBIO",
                                "[WARN-GEM5DONE] n{} gem5Done=true, dropping {} "
                                "reqId={} PA=0x{:x}",
                                nid, coherenceMsgTypeName(coh->h.type),
                                coh->h.reqId, coh->h.homeLinePa);
                        } else {
                            // 正常路径：转发给 gem5
                            if (g_debugUbioPerf) {
                                LogDebug("UBIO", "[DEBUG-TRACE-4] n{} net->gem5 fwd {} reqId={}",
                                             nid, coherenceMsgTypeName(coh->h.type), coh->h.reqId);
                            }
                            bool sentToGem5 = sendCoh(gem5Port, tick,
                                gidOf(nid, sid), gidOf(nid, sid), *coh);
                            if (g_debugUbioPerf) {
                                LogDebug("UBIO",
                                             "[DEBUG-TRACE-4-SEND] n{} net->gem5 sendCoh_ret={} type={} reqId={} src={}:{} dst={}:{}",
                                             nid, sentToGem5 ? "true" : "false",
                                             coherenceMsgTypeName(coh->h.type), coh->h.reqId,
                                             coh->h.srcNode, coh->h.srcSocket,
                                             coh->h.dstNode, coh->h.dstSocket);
                            }
                        }
                    }
                }
                m = ReceiveMessage(port, tick, &st);
                continue;
            }

            if (haAdapter) {
                for (int rep = 0; rep < faultCopies; ++rep) {
                    if (haAdapter->handle(*coh)) continue;

                    CoherenceMessage reject;
                    if (buildHaLegacyReject(*coh, nid, sid, reject)) {
                        LogError("UBIO",
                            "[UBIO-HA-REJECT] unsupported legacy local request "
                            "type={} reqId={} pa=0x{:x}",
                            coherenceMsgTypeName(coh->h.type), coh->h.reqId,
                            coh->h.homeLinePa);
                        panic_if(!sendCoh(gem5Port, tick,
                                          gidOf(nid, sid), gidOf(nid, sid), reject),
                            "HA local legacy reject send failed type={} reqId={}",
                            coherenceMsgTypeName(reject.h.type), reject.h.reqId);
                    } else {
                        LogError("UBIO",
                            "[UBIO-HA-FATAL] unsupported local message type={} reqId={} "
                            "pa=0x{:x}; no legacy UBCC/MetaRNF path exists",
                            coherenceMsgTypeName(coh->h.type), coh->h.reqId,
                            coh->h.homeLinePa);
                        panic_if(true,
                            "HA mode unsupported local message type={} reqId={} pa=0x{:x}",
                            coherenceMsgTypeName(coh->h.type), coh->h.reqId,
                            coh->h.homeLinePa);
                    }
                }
                m = ReceiveMessage(port, tick, &st);
                continue;
            }

            panic_if(!ubcc || !host,
                     "UBCC local dispatch lacks controller/host type={} reqId={}",
                     coherenceMsgTypeName(coh->h.type), coh->h.reqId);

            // MetaRNFReadResp: response from gem5 MetaRNFController (Phase 3)
            if (coh->h.type == CoherenceMessageType::MetaRNFReadResp) {
                host->_metaRNF.enterReentrant();
                host->_metaRNF.handleResp(*coh);
                host->_metaRNF.leaveReentrant();
                // Deferred ops drained at outer loop boundary (after pollAndProcess)
                m = ReceiveMessage(port, tick, &st);
                continue;
            }
            // MetaRNFWriteResp: durable write ack from gem5 (Phase D2)
            if (coh->h.type == CoherenceMessageType::MetaRNFWriteResp) {
                host->_metaRNF.enterReentrant();
                host->_metaRNF.handleWriteResp(*coh);
                host->_metaRNF.leaveReentrant();
                m = ReceiveMessage(port, tick, &st);
                continue;
            }
            // MetaRNFLineReadResp: typed 64B line read response (Phase 2)
            if (coh->h.type == CoherenceMessageType::MetaRNFLineReadResp) {
                host->_metaRNF.enterReentrant();
                host->_metaRNF.handleLineReadResp(*coh);
                host->_metaRNF.leaveReentrant();
                m = ReceiveMessage(port, tick, &st);
                continue;
            }
            // MetaRNFLineWriteResp: typed 64B line write ack (Phase 2)
            if (coh->h.type == CoherenceMessageType::MetaRNFLineWriteResp) {
                host->_metaRNF.enterReentrant();
                host->_metaRNF.handleLineWriteResp(*coh);
                host->_metaRNF.leaveReentrant();
                m = ReceiveMessage(port, tick, &st);
                continue;
            }

            if (!isUbccIngress(coh->h.type)) {
                LogWarn("UBIO", "[ubio:{}] drop unsupported local type={}",
                             nid, coherenceMsgTypeName(coh->h.type));
                m = ReceiveMessage(port, tick, &st);
                continue;
            }

            for (int rep = 0; rep < faultCopies; ++rep) {
                CoherenceMessage response;
                bool hasResponse = false;
                // Wrap UBCC ingress with reentrant guard: any H64 backstore
                // operations triggered during handleUbccMessage will defer their
                // MetaRNF sends, avoiding reentrant sendCoh during port->recv.
                host->_metaRNF.enterReentrant();
                bool handled = handleUbccMessage(*ubcc, *host, nid, sid, *coh, response, hasResponse);
                host->_metaRNF.leaveReentrant();
                if (!handled) {
                    LogWarn("UBIO", "[ubio:{}] UBCC unhandled type={}",
                                 nid, coherenceMsgTypeName(coh->h.type));
                    break;
                }
                if (hasResponse) {
                    Port *out = fromNetwork ? netPort : gem5Port;
                    const uint32_t targetGid = fromNetwork
                        ? gidOf(coh->h.srcNode, coh->h.srcSocket)
                        : gidOf(nid, sid);
                    if (fromNetwork) {
                        sendNetworkResponse(response, targetGid);
                    } else {
                        panic_if(!sendCoh(out, tick, gidOf(nid, sid),
                                          targetGid, response, false),
                                  "response send failed type={} reqId={} targetGid={}",
                                  coherenceMsgTypeName(response.h.type),
                                  response.h.reqId, targetGid);
                    }
                }
            }

            m = ReceiveMessage(port, tick, &st);
        }
    };

    uint64_t loop_count = 0;
    while (!(gem5Done && (netPort == nullptr || netDone))) {
        loop_count++;
        // 1. Heartbeat: emitSync for all ports (even silent ones)
        if (loop_count <= 5) { LogDebug("UBIO", "[UBIO-PRE-EMIT] tick={}", tick); }
        if (!gem5Done) EmitSync(gem5Port, tick);
        if (loop_count <= 5) { LogDebug("UBIO", "[UBIO-POST-EMIT] tick={}", tick); }
        if (netPort && !netDone) EmitSync(netPort, tick);
        if (netPort && !netDone) drainReliableResponses();

        // 2. Drain all ready messages from each port
        if (!gem5Done) pollAndProcess(gem5Port, gem5Port, false, &gem5Done);
        if (netPort && !netDone) pollAndProcess(netPort, netPort, true, &netDone);

        // Reliable exit retry/quiesce is wall-clock driven even when PDES tick
        // is frozen. Keep pumping it on every host loop iteration.
        if (!netDone) {
            sendPeerExitActions(peerExitCoordinator.pump(peerExitNowMs()));
            logPeerExitQuiesce();
            // pump() only marks close-ready. Close after every returned action
            // has been offered to the transport, so callback teardown cannot
            // suppress an ACK generated in the same dispatch cycle.
            peerExitCoordinator.finalizeClose();
            if (peerExitComplete) {
                const uint64_t nowMs = peerExitNowMs();
                if (!networkExitStarted ||
                    nowMs - networkExitLastSendMs >= peerExitRetryMs) {
                    networkExitStarted = true;
                    sendNetworkExitRequest();
                }
            }
        }

        // 2.5 Drain deferred H64 MetaRNF operations.  These were enqueued
        // during port message dispatch (reentrantDepth > 0) and must be sent
        // OUTSIDE the port receive/message-dispatch stack to avoid PDES
        // reentrant-send deadlocks.  One deferred send may trigger a callback
        // that creates MORE deferred ops; these are drained in the NEXT outer
        // loop iteration (bounded to avoid starvation).
        // Call stack: main() → while(!done) → drainDeferred() → sendCoh().
        const bool dataPlaneActive = !gem5Done;
        if (dataPlaneActive && host && host->_h64Host)
            host->_h64Host->pumpRetries();
        if (dataPlaneActive && host && host->_metaRNF.hasDeferred()) {
            static int dd_cnt = 0;
            if (host->_metaRNF._debugH64Pdes && (++dd_cnt <= 5 || dd_cnt % 1000 == 0))
                LogDebug("UBIO", "[DEBUG-H64-PDES-DRAIN] n={} cnt={} deferred={} tick={}",
                             nid, dd_cnt, host->_metaRNF._deferredCount, tick);
            host->_metaRNF.drainDeferred();
        }

        // A release send can temporarily backpressure after all arrivals have
        // been observed. Retry only those already-satisfied generations.
        std::vector<BarrierKey> readyBarriers;
        if (dataPlaneActive) {
            for (const auto &kv : barrierArrivals) {
                if (barrierReady(kv.first, kv.second))
                    readyBarriers.push_back(kv.first);
            }
        }
        for (const BarrierKey &bk : readyBarriers)
            releaseBarrier(bk);

        // 3. Advance tick via safeTs
        uint64_t minTs = UINT64_MAX;
        if (!gem5Done) minTs = SafeTimestamp(gem5Port, tick);
        if (netPort && !netDone) {
            uint64_t netSafe = SafeTimestamp(netPort, tick);
            if (netSafe < minTs) minTs = netSafe;
        }
        if (minTs > tick) {
            tick = minTs;
            // Run controller maintenance only when virtual time advances.
            // This expires tombstones and recalls without repeatedly scanning
            // state while PDES is parked at the same timestamp.
            if (dataPlaneActive) {
                if (ubcc) ubcc->wakeup();
                if (host) {
                    host->advanceH64Coverage();
                    host->advancePendingGrantReads(tick);
                }
            }
        } else {
            // Bounded by a peer: do NOT drift forward with ++tick (that let the
            // native side crawl billions of ticks ahead of gem5, skewing message
            // timestamps into gem5's far future). Yield and re-poll instead, so
            // we stay clock-locked to the slowest peer.
            std::this_thread::yield();
        }
        // 3.3/4.6: Drain delayed fault-injection queue (reorder/delay)
        if (dataPlaneActive && ((ubcc && host) || haAdapter))
            drainDelayedQueue(gem5Port, netPort, nid, sid, ubcc.get(),
                              host.get(), haAdapter.get(), tick);

        // Fire any expired backstore fills (T_ubio_dram).  Tick-gated deferred
        // callbacks simulate real DRAM read latency.
        if (dataPlaneActive && host) {
            host->drainPendingFills(tick);
            host->drainPendingBackstoreAcks(tick);
            host->dsmData.drain(tick);
        } else if (dataPlaneActive && haHost) {
            haHost->dsmData.drain(tick);
        }
    }

    // 3.4: Dump ResidentDir performance counters
    if (ubcc && host) {
        LogInfo("UBIO", "[UBCC-STATS-PHASE] ubio_final");
        ubcc->directory().dumpStatsJson();
        LogInfo("UBIO", "[UBCC-STATS] {}", ubcc->dumpStatsJson());
        LogInfo("UBIO", "[UBCC-STATS] {{\"asyncWbCount\":{}}}",
                ubcc->getAsyncWbCount());
        LogInfo("UBIO", "[UBCC-STATS] {{\"h64ExactLiveKnown\":{},\"h64ExactLiveCount\":{}}}",
                host->h64ExactCoverageKnown() ? 1 : 0, host->h64ExactLiveCount());
    }

    TerminatePort(gem5Port);
    DestroyPort(gem5Port);
    DestroyPort(netPort);
    return peerExitFailed ? 1 : 0;
}
