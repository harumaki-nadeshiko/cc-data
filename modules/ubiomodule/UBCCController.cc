#include "UBCCController.hh"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>
#include <sstream>

#include "framework/Log.hh"
#include "NodeAddressMap.hh"

namespace cc
{

namespace glob
{

namespace
{

void
appendTmpLog(const char *file, const char *fmt, ...)
{
    char path[256];
    std::snprintf(path, sizeof(path), "/workspace/tmp_logs/%s", file);
    FILE *fp = std::fopen(path, "a");
    if (!fp) {
        return;
    }

    va_list ap;
    va_start(ap, fmt);
    std::vfprintf(fp, fmt, ap);
    va_end(ap);
    std::fclose(fp);
}

} // anonymous namespace

// Static registry for cross-node UBCC routing
std::map<std::pair<int,int>, UBCCController*> UBCCController::_instances;

void
UBCCController::registerInstance(int node_id, int socket_id, UBCCController *ubcc)
{
    _instances[{node_id, socket_id}] = ubcc;
}

UBCCController*
UBCCController::getInstance(int node_id, int socket_id)
{
    auto it = _instances.find({node_id, socket_id});
    return (it != _instances.end()) ? it->second : nullptr;
}

UBCCController::UBCCController(int node_id, int socket_id,
                                RubySystem *ruby_system,
                                uint32_t epoch_bits,
                                uint32_t resident_bf_bytes,
                                uint32_t resident_force_entries,
                                int num_sockets,
                                int num_nodes,
                                const ResidentDirConfig *rdcfg)
  : _nodeId(node_id),
    _socketId(socket_id),
    _interconnectLatency(200),
    _directory(rdcfg ? ResidentDir(*rdcfg)
                     : ResidentDir(resident_bf_bytes, resident_force_entries)),
    _epochBits(epoch_bits),
    _recallCount(0),
    _recallResponseCount(0),
    _writebackCount(0),
    _evictCount(0),
    _staleRejectedCount(0),
    _ownerMismatchRejectedCount(0),
    _invalidationCount(0),
    _invalidationAckCount(0),
    _batchRsEnabled(true),
    _epRnfSnoopCount(0),
    _tombstoneReplayCount(0),
    _invariantWarnCount(0),
    _dsmLocalBase(0),
    _dsmSegSize(0)
{
    if (_epochBits == 0 || _epochBits > 64) {
        fatal("UBCC node_id=%d socket=%d: epoch_bits=%u out of range (1..64)\n",
              _nodeId, _socketId, _epochBits);
    }

    // Precompute DSM local range for isDsmAddr()
    // v4-dual-socket: DSM_(homeNode, homeSocket) layout
    // Hardcoded prototype constants: num_nodes=3, segSize=128MB, NODE_ADDR_SHIFT=40
    constexpr uint64_t kSegSize = 128ULL * 1024 * 1024;
    constexpr int kNodeAddrShift = 40;
    // Socket-plane model: num_sockets from constructor parameter.
    int kNumSockets = num_sockets;
    uint64_t nodeBase = static_cast<uint64_t>(node_id) << kNodeAddrShift;
    // DSM base = phy_base + 2*seg + (node_id * numSockets + socket_id) * seg.
    _dsmLocalBase = nodeBase + 2 * kSegSize
                    + (node_id * kNumSockets + socket_id) * kSegSize;
    _dsmSegSize = kSegSize;
    // v4-dual-socket: rebuild the address map with the actual num_sockets so
    // buildDsmPA()/homeSocket() use the correct per-(node,socket) plane layout.
    // The default member initializer assumes num_sockets=1 (single-socket
    // legacy); for dual-socket we must override it here.
    _addrMap = NodeAddressMap(num_nodes, kNumSockets, kSegSize);
    framework::LogInfo("UBCC",
            "UBCC node_id=%d socket=%d: initialized with epoch_bits=%u "
            "dsmBase=0x%lx dsmSize=0x%lx numSockets=%d\n",
            _nodeId, _socketId, _epochBits, _dsmLocalBase, _dsmSegSize,
            kNumSockets);

    framework::LogInfo("UBCC", "UBCC node_id=%d socket=%d: C3 batch RS %s\n",
            _nodeId, _socketId, _batchRsEnabled ? "ENABLED" : "DISABLED");

    registerInstance(node_id, socket_id, this);
}

UBCCController::~UBCCController()
{
    _instances.erase({_nodeId, _socketId});
}

void
UBCCController::wakeup()
{
    cleanupTombstones();
    cleanupExpiredRecalls();
    if (++_bloomReconstructCounter >= _bloomReconstructInterval) {
        _bloomReconstructCounter = 0;
        for (int g = 0; g < ResidentDir::BloomGroups; ++g) {
            if (_directory.shouldReconstructGroup(g))
                _directory.reconstructGroup(g);
        }
    }
    // Async writeback: periodically scan dirty ResidentDir entries
    if (++_asyncWbCounter >= _asyncWbInterval) {
        _asyncWbCounter = 0;
        doAsyncWriteback();
    }
}

// ---- isDsmAddr (pure computation, no SentinelHelper) ----

bool
UBCCController::isDsmAddr(uint64_t pa) const
{
    return pa >= _dsmLocalBase && pa < _dsmLocalBase + _dsmSegSize;
}

// ---- M5: Home UBCC MESI Grant Decision ----

void
UBCCController::ensureDirEntry(uint64_t line_pa)
{
    DirEntry entry;
    if (!_directory.lookup(line_pa, entry)) {
        DirEntry new_entry;
        new_entry.lineAddr = line_pa;
        _directory.insert(line_pa, new_entry);
        _directory.touch(line_pa);
    }
}

UBCCController::ResidentAccessResult
UBCCController::ensureResidentForAccess(
    uint64_t line_pa, UBCC_OuterReqType reqType, bool writeIntent,
    int requesterNode, int requesterSocket,
    uint64_t baseEpoch, uint64_t reqId, DirEntry &entry)
{
    size_t slot = 0;
    if (_directory.lookupWithSlot(line_pa, entry, slot)) {
        _directory.touch(line_pa);
        if (_directory.fillPending(line_pa) || _directory.wbPending(line_pa)) {
            PendingRequester pr;
            pr.node = requesterNode;
            pr.socket = requesterSocket;
            pr.reqType = reqType;
            pr.writeIntent = writeIntent;
            pr.epoch = baseEpoch;
            pr.reqId = reqId;
            enqueueResidentWaiter(line_pa, pr);
            refreshPinnedBit(line_pa);
            return _directory.fillPending(line_pa)
                ? ResidentAccessResult::Queued
                : ResidentAccessResult::Busy;
        }
        refreshPinnedBit(line_pa);
        return ResidentAccessResult::Ready;
    }

    return handleResidentMiss(line_pa, reqType, writeIntent,
                              requesterNode, requesterSocket,
                              baseEpoch, reqId, entry);
}

UBCCController::ResidentAccessResult
UBCCController::handleResidentMiss(
    uint64_t line_pa, UBCC_OuterReqType reqType, bool writeIntent,
    int requesterNode, int requesterSocket,
    uint64_t baseEpoch, uint64_t reqId, DirEntry &entry)
{
    const bool mayContain = _directory.bloomMayContain(line_pa);
    fprintf(stderr, "[RESIDENT-MISS] home=%d pa=0x%lx req=%d requester=%d "
           "mayContain=%d count=%zu capacity=%zu freeForPa=%d policy=%d\n",
           _nodeId, line_pa, static_cast<int>(reqType), requesterNode,
           mayContain ? 1 : 0, _directory.count(), _directory.capacity(),
           _directory.hasFreeSlotForPa(line_pa) ? 1 : 0,
           _overflowPolicy == ResidentOverflowPolicy::NaiveEvict ? 1 : 0);
    fflush(stderr);
    if (!_directory.hasFreeSlotForPa(line_pa)) {
        PendingRequester pr;
        pr.node = requesterNode;
        pr.socket = requesterSocket;
        pr.reqType = reqType;
        pr.writeIntent = writeIntent;
        pr.epoch = baseEpoch;
        pr.reqId = reqId;
        enqueueResidentWaiter(line_pa, pr);
        bool evictProgress = evictOneVictim(line_pa);
        if (evictProgress) {
            replayResidentWaiters(line_pa);
        }
        auto wit = _residentWaiters.find(line_pa);
        size_t waiterDepth = (wit == _residentWaiters.end()) ? 0 : wit->second.size();
        fprintf(stderr, "[RESIDENT-MISS-BUSY] home=%d pa=0x%lx reason=capacity_wait "
               "evictProgress=%d count=%zu capacity=%zu waiterDepth=%zu\n",
               _nodeId, line_pa, evictProgress ? 1 : 0,
               _directory.count(), _directory.capacity(), waiterDepth);
        fflush(stderr);
        return ResidentAccessResult::Busy;
    }

    DirEntry placeholder;
    placeholder.lineAddr = line_pa;
    placeholder.state = MESIState::G_I;
    placeholder.sharersMask = 0;
    placeholder.epoch = 0;
    placeholder.residentDirty = false;

    if (!_directory.insert(line_pa, placeholder)) {
        if (!_directory.lookup(line_pa, placeholder)) {
            return ResidentAccessResult::Busy;
        }
    }
    _directory.touch(line_pa);

    if (!mayContain) {
        entry = placeholder;
        fprintf(stderr, "[RESIDENT-MISS-READY] home=%d pa=0x%lx reason=bloom_negative\n",
               _nodeId, line_pa);
        fflush(stderr);
        refreshPinnedBit(line_pa);
        return ResidentAccessResult::Ready;
    }

    // 3.4: Bloom reported positive but directory missed → false positive count
    _directory.incrementBloomFp();

    _directory.setFillPending(line_pa, true);
    _directory.setPinned(line_pa, true);
    PendingRequester pr;
    pr.node = requesterNode;
    pr.socket = requesterSocket;
    pr.reqType = reqType;
    pr.writeIntent = writeIntent;
    pr.epoch = baseEpoch;
    pr.reqId = reqId;
    enqueueResidentWaiter(line_pa, pr);

    if (_host) {
        _host->hostIssueBackstoreRead(line_pa);
    }
    fprintf(stderr, "[RESIDENT-FILL-ISSUED] home=%d pa=0x%lx waiterDepth=%zu\n",
           _nodeId, line_pa, _residentWaiters[line_pa].size());
    fflush(stderr);
    return ResidentAccessResult::Queued;
}

void
UBCCController::enqueueResidentWaiter(uint64_t linePa, const PendingRequester &pr)
{
    auto &q = _residentWaiters[linePa];
    if (q.size() >= MAX_PENDING_PER_PA) {
        return;
    }
    for (const auto &e : q) {
        if (e.node == pr.node && e.reqId == pr.reqId && e.reqType == pr.reqType) {
            return;
        }
    }
    q.push_back(pr);
}

void
UBCCController::refreshPinnedBit(uint64_t linePa)
{
    DirEntry e;
    if (!_directory.lookup(linePa, e)) {
        return;
    }
    bool pin = false;
    pin = pin || (_outstandingReqs.find(linePa) != _outstandingReqs.end());
    auto pit = _pendingRequesters.find(linePa);
    pin = pin || (pit != _pendingRequesters.end() && !pit->second.empty());
    auto rit = _residentWaiters.find(linePa);
    pin = pin || (rit != _residentWaiters.end() && !rit->second.empty());
    pin = pin || _directory.fillPending(linePa);
    pin = pin || _directory.wbPending(linePa);
    pin = pin || (e.state == MESIState::G_I && e.residentDirty);
    _directory.setPinned(linePa, pin);
}

bool
UBCCController::evictOneVictim(uint64_t avoidPa)
{
    uint64_t victimPa = 0;
    DirEntry victim;
    if (!_directory.pickVictim(avoidPa, victimPa, victim)) {
        fprintf(stderr, "[RESIDENT-EVICT-PICK-FAIL] home=%d avoid=0x%lx count=%zu capacity=%zu\n",
               _nodeId, avoidPa, _directory.count(), _directory.capacity());
        fflush(stderr);
        return false;
    }

    fprintf(stderr, "[RESIDENT-EVICT-PICK] home=%d avoid=0x%lx victim=0x%lx "
           "state=%s sharers=0x%lx dirty=%d residentDirty=%d policy=%d\n",
           _nodeId, avoidPa, victimPa, mesiStateName(victim.state),
           victim.sharersMask, DirEntry::protoDirty(victim) ? 1 : 0,
           victim.residentDirty ? 1 : 0,
           _overflowPolicy == ResidentOverflowPolicy::NaiveEvict ? 1 : 0);
    fflush(stderr);

    if (_overflowPolicy == ResidentOverflowPolicy::NaiveEvict) {
        return evictOneVictimNaive(victimPa, victim);
    }

    if (!victim.residentDirty) {
        _directory.forceRemove(victimPa);
        _residentWaiters.erase(victimPa);
        _pendingRequesters.erase(victimPa);
        replayResidentWaitersForCapacity();
        return true;
    }

    _directory.setWbPending(victimPa, true);
    _directory.setPinned(victimPa, true);
    _evictionPendingRemoval.insert(victimPa);
    fprintf(stderr, "[RESIDENT-SPILL-START] home=%d victim=0x%lx state=%s residentDirty=%d\n",
           _nodeId, victimPa, mesiStateName(victim.state), victim.residentDirty ? 1 : 0);
    fflush(stderr);
    if (victim.state == MESIState::G_I) {
        scheduleBackstoreDelete(victimPa);
    } else {
        scheduleBackstoreWrite(victimPa);
    }
    return false;
}

bool
UBCCController::evictOneVictimNaive(uint64_t victimPa, const DirEntry &victim)
{
    if (isLineBusy(victimPa)) {
        return false;
    }

    uint64_t targetMask = victim.sharersMask;
    const int owner = DirEntry::ownerFromSharers(victim);
    if (owner >= 0 && owner < 64) {
        targetMask |= (1ULL << owner);
    }

    _naiveDirEvictions++;
    _naiveForcedInvalidations += __builtin_popcountll(targetMask);
    if (DirEntry::protoDirty(victim)) {
        _naiveDirtyVictims++;
        _naiveForcedWritebacks++;
    }

    printf("[UBCC-NAIVE-EVICT] home=%d pa=0x%lx state=%s sharers=0x%lx "
           "targets=0x%lx dirty=%d epoch=%lu\n",
           _nodeId, victimPa, mesiStateName(victim.state), victim.sharersMask,
           targetMask, DirEntry::protoDirty(victim) ? 1 : 0, victim.epoch);

    if (DirEntry::protoDirty(victim) && owner >= 0) {
        OutstandingRequest *recallOreq = createOutstanding(
            victimPa, OpType::RECALL, -1, owner, _socketId);
        if (!recallOreq) {
            return false;
        }
        recallOreq->reservedEpoch = normalizeEpoch(victim.epoch + 1);
        recallOreq->reqId = victim.epoch;
        recallOreq->baseEpoch = victim.epoch;
        recallOreq->reqType = UBCC_OuterReqType::GlobalInvalidate;
        recallOreq->stage = OpStage::WAITING_TARGET_RESP;
        recallOreq->intendedState = MESIState::G_I;
        recallOreq->intendedSharersMask = 0;
        recallOreq->intendedOwnerNode = -1;
        recallOreq->intendedDirty = false;
        if (!initiateRecall(victimPa, victim, *recallOreq)) {
            removeOutstanding(victimPa);
            return false;
        }
        return false;
    }

    if (targetMask != 0 && _outbound) {
        uint64_t effectiveMask = targetMask;
        fanoutInvalidateTargets(victimPa, targetMask, victim.epoch,
                                victim.epoch,
                                -1, UBCC_OuterReqType::GlobalInvalidate,
                                DirEntry::protoDirty(victim), &effectiveMask);
    }

    _directory.forceRemove(victimPa);
    _residentWaiters.erase(victimPa);
    _pendingRequesters.erase(victimPa);
    _evictionPendingRemoval.erase(victimPa);
    replayResidentWaitersForCapacity();
    return true;
}

void
UBCCController::scheduleBackstoreWrite(uint64_t linePa)
{
    if (_host) {
        _host->hostIssueBackstoreWrite(linePa);
    } else {
        onBackstoreWriteAck(linePa);
    }
}

void
UBCCController::scheduleBackstoreDelete(uint64_t linePa)
{
    if (_host) {
        _host->hostIssueBackstoreDelete(linePa);
    } else {
        onBackstoreDeleteAck(linePa, true);
    }
}

void
UBCCController::doAsyncWriteback()
{
    const int maxPerRound = 16;
    int count = 0;
    int numSets = _directory.numSets();
    int numWays = _directory.numWays();

    for (int set = 0; set < numSets && count < maxPerRound; ++set) {
        for (int way = 0; way < numWays && count < maxPerRound; ++way) {
            if (!_directory.getValid(set, way))
                continue;
            if (!_directory.getDirty(set, way))
                continue;

            uint64_t pa = _directory.rebuildPA(set, way);

            // Skip if already pending writeback (eviction in flight)
            if (_directory.wbPending(pa))
                continue;

            // Skip if already in async writeback snapshot map
            if (_asyncWbSnapshots.count(pa) > 0)
                continue;

            uint64_t epoch = _directory.getEpoch(set, way);
            _asyncWbSnapshots[pa] = epoch;

            scheduleBackstoreWrite(pa);
            count++;
        }
    }
}

void
UBCCController::onAsyncWritebackAck(uint64_t linePa)
{
    auto it = _asyncWbSnapshots.find(linePa);
    if (it == _asyncWbSnapshots.end())
        return;

    uint64_t snapshotEpoch = it->second;
    _asyncWbSnapshots.erase(it);

    DirEntry entry;
    if (!_directory.lookup(linePa, entry))
        return;

    // Epoch check: if unchanged, entry was not modified → safe to clear dirty
    if (entry.epoch == snapshotEpoch) {
        entry.residentDirty = false;
        _directory.update(linePa, entry);
        _asyncWbCount++;
        printf("[UBCC-ASYNC-WB] home=%d pa=0x%lx epoch=%lu — dirty cleared (snapshot matched)\n",
               _nodeId, linePa, snapshotEpoch);
    } else {
        printf("[UBCC-ASYNC-WB] home=%d pa=0x%lx snapshotEpoch=%lu currentEpoch=%lu "
               "— dirty kept (entry modified)\n",
               _nodeId, linePa, snapshotEpoch, entry.epoch);
    }
}

std::string
UBCCController::dumpStatsJson() const
{
    std::ostringstream oss;
    oss << "{"
        << "\"residentOverflowPolicy\":"
        << (_overflowPolicy == ResidentOverflowPolicy::NaiveEvict ? 1 : 0) << ","
        << "\"naiveDirEvictions\":" << _naiveDirEvictions << ","
        << "\"naiveForcedInvalidations\":" << _naiveForcedInvalidations << ","
        << "\"naiveForcedWritebacks\":" << _naiveForcedWritebacks << ","
        << "\"naiveDirtyVictims\":" << _naiveDirtyVictims << ","
        << "\"asyncWbCount\":" << _asyncWbCount << ","
        << "\"writebackCount\":" << _writebackCount << ","
        << "\"evictCount\":" << _evictCount << ","
        << "\"invalidationCount\":" << _invalidationCount
        << "}";
    return oss.str();
}

void
UBCCController::replayResidentWaiters(uint64_t linePa)
{
    auto it = _residentWaiters.find(linePa);
    if (it == _residentWaiters.end()) {
        return;
    }
    if (_directory.fillPending(linePa) || _directory.wbPending(linePa)) {
        return;
    }

    while (!it->second.empty()) {
        PendingRequester pr = it->second.front();
        it->second.pop_front();
        if (pr.reqType == UBCC_OuterReqType::GlobalWriteback) {
            if (!processWriteback(linePa, pr.node, pr.epoch, pr.writeIntent)) {
                it->second.push_front(pr);
                break;
            }
            if (pr.hasData && _host) {
                _host->writeDsmData(linePa, pr.data.data());
                updateLineDataCache(linePa, pr.data.data());
                std::fprintf(stderr,
                             "[WB-DATA-PERSIST] home=%d pa=0x%lx node=%d source=resident_replay\n",
                             _nodeId, linePa, pr.node);
                std::fflush(stderr);
            }
        } else if (pr.reqType == UBCC_OuterReqType::GlobalEvict) {
            if (!processEvict(linePa, pr.node, pr.epoch)) {
                it->second.push_front(pr);
                break;
            }
        } else {
            auto g = processOuterRequest(linePa, pr.reqType, pr.writeIntent,
                                          pr.node, pr.socket, pr.epoch, pr.reqId,
                                          nullptr, nullptr, nullptr, nullptr,
                                          nullptr, nullptr);
            if (static_cast<int>(g) == -1) {
                it->second.push_front(pr);
                break;
            }
            OutstandingRequest *ost = findOutstanding(linePa);
            if (ost && ost->opType == OpType::GRANT_HANDSHAKE &&
                ost->requesterNode == pr.node && ost->reqId == pr.reqId &&
                ost->stage == OpStage::WAITING_CLEAR && _outbound) {
                CoherenceMessage push;
                buildGrantResponse(*ost, push);
                _outbound->sendGrantPush(push);
                fprintf(stderr, "[RESIDENT-REPLAY-PUSH] home=%d pa=0x%lx "
                        "requester=%d reqId=%lu\n",
                        _nodeId, linePa, pr.node, pr.reqId);
                fflush(stderr);
            }
        }
        if (_directory.fillPending(linePa) || _directory.wbPending(linePa)) {
            break;
        }
    }

    if (it->second.empty()) {
        _residentWaiters.erase(it);
    }
    refreshPinnedBit(linePa);
}

void
UBCCController::replayResidentWaitersForCapacity()
{
    std::vector<uint64_t> keys;
    keys.reserve(_residentWaiters.size());
    for (const auto &kv : _residentWaiters) {
        if (!_directory.fillPending(kv.first) && !_directory.wbPending(kv.first)) {
            keys.push_back(kv.first);
        }
    }
    for (uint64_t pa : keys) {
        fprintf(stderr, "[RESIDENT-CAPACITY-REPLAY] home=%d pa=0x%lx\n",
                _nodeId, pa);
        fflush(stderr);
        replayResidentWaiters(pa);
    }
}

const char*
UBCCController::mesiStateName(MESIState s) const
{
    switch (s) {
        case MESIState::G_I: return "G_I";
        case MESIState::G_S: return "G_S";
        case MESIState::G_E: return "G_E";
        case MESIState::G_M: return "G_M";
        default: return "UNKNOWN";
    }
}

// F24: Map intended MESI state to outer grant type
static UBCC_OuterGrantType
grantTypeFromIntended(MESIState s)
{
    switch (s) {
        case MESIState::G_S: return UBCC_OuterGrantType::GlobalGrantShared;
        case MESIState::G_E: return UBCC_OuterGrantType::GlobalGrantExclusive;
        case MESIState::G_M: return UBCC_OuterGrantType::GlobalGrantModified;
        default: return UBCC_OuterGrantType::GlobalGrantShared;
    }
}

UBCC_OuterGrantType
UBCCController::processOuterRequest(
    uint64_t line_pa, UBCC_OuterReqType reqType, bool writeIntent,
    int requesterNode, int requesterSocket,
    uint64_t baseEpoch, uint64_t reqId,
    Tick *outGrantVisibleTick, Tick *outSentinelVisibleTick,
    bool *outRecallNeeded, int *outRecallOwnerNode,
    GrantDataSource *outDataSource,
    uint64_t *outAuthEpoch)
{
    baseEpoch = normalizeEpoch(baseEpoch);

    framework::LogInfo("UBCC",
            "UBCC node_id=%d: processOuterRequest PA=0x%lx req=%d write=%d "
            "requesterNode=%d requesterSocket=%d baseEpoch=%lu reqId=%lu\n",
            _nodeId, line_pa, static_cast<int>(reqType), writeIntent,
            requesterNode, requesterSocket, baseEpoch, reqId);
    printf("[UBCC-OUTER-REQ] home=%d pa=0x%lx req=%d write=%d requester=%d "
           "sock=%d baseEpoch=%lu reqId=%lu\n",
           _nodeId, line_pa, static_cast<int>(reqType), writeIntent,
           requesterNode, requesterSocket, baseEpoch, reqId);

    // Initialize M6 recall outputs and F3 dataSource output
    if (outRecallNeeded)   *outRecallNeeded = false;
    if (outRecallOwnerNode) *outRecallOwnerNode = -1;
    if (outDataSource) *outDataSource = GrantDataSource::HomeMemory;
    if (outAuthEpoch) *outAuthEpoch = 0;

    // Validate: only DSM addresses for this home node
    if (!isDsmAddr(line_pa)) {
        fatal("UBCC node_id=%d: non-home-DSM address PA=0x%lx in outer request\n",
              _nodeId, line_pa);
    }

    // Validate: Shared + true is illegal
    if (reqType == UBCC_OuterReqType::GlobalReadShared && writeIntent) {
        fatal("UBCC node_id=%d: illegal Shared+writeIntent=true for PA=0x%lx\n",
              _nodeId, line_pa);
    }

    // Validate requesterNode
    if (requesterNode < -1 || requesterNode >= 64) {
        fatal("UBCC node_id=%d: requesterNode=%d out of range\n",
              _nodeId, requesterNode);
    }

    DirEntry entry;
    ResidentAccessResult r = ensureResidentForAccess(
        line_pa, reqType, writeIntent, requesterNode, requesterSocket,
        baseEpoch, reqId, entry);
    printf("[UBCC-OUTER-REQ] home=%d pa=0x%lx residentResult=%d state=%s "
           "sharers=0x%lx epoch=%lu\n",
           _nodeId, line_pa, static_cast<int>(r), mesiStateName(entry.state),
           entry.sharersMask, entry.epoch);
    if (r != ResidentAccessResult::Ready) {
        return static_cast<UBCC_OuterGrantType>(-1);
    }

    // v4: Lazy cleanup — if there's an expired RECALL, remove it before
    // checking for existing outstanding so that the new request can proceed
    // on the current committed DirEntry.
    cleanupExpiredRecallIfNeeded(line_pa, false);

    // v4: Check for existing outstanding — if active and belongs to a different
    // requester, try to enqueue (§4.2, recall_done_fix.md).
    // Same requester with live outstanding → BUSY (no self-queue).
    OutstandingRequest *existing = findOutstanding(line_pa);
    if (existing) {
        appendTmpLog(
            "ubcc_outer_req.log",
            "[OUTER-REQ] pa=0x%lx req=%d existing_op=%d existing_stage=%d "
            "existing_requester=%d replayArmed=%d\n",
            line_pa, static_cast<int>(reqType), static_cast<int>(existing->opType),
            static_cast<int>(existing->stage), existing->requesterNode,
            existing->replayArmed ? 1 : 0);
        // Non-terminal: still active (RECALL WAITING, INVALIDATE WAITING, etc.)
        if (existing->stage != OpStage::DONE &&
            existing->stage != OpStage::CANCELLED &&
            existing->stage != OpStage::TIMED_OUT) {
            // Same requester already has live outstanding → BUSY
            // F24: Unless this outstanding was created by replay (replayArmed=true)
            // and the retry matches the grant tuple — then return the grant directly.
            if (existing->requesterNode == requesterNode) {
                // Same-requester has live outstanding in WAITING_CLEAR:
                // the grant was already delivered. Return it directly regardless
                // of replayArmed or reqId match. The retry is idempotent.
                if (existing->stage == OpStage::WAITING_CLEAR) {
                    framework::LogInfo("UBCC",
                            "UBCC node_id=%d: grant hit PA=0x%lx "
                            "requester=%d reqId=%lu intended=%s — granting\n",
                            _nodeId, line_pa, requesterNode, reqId,
                             mesiStateName(existing->intendedState));
                    if (outDataSource) *outDataSource = existing->dataSource;
                    if (outGrantVisibleTick) *outGrantVisibleTick = curTick();
                    if (outSentinelVisibleTick) *outSentinelVisibleTick = curTick();
                    if (outRecallNeeded) *outRecallNeeded = false;
                    if (outRecallOwnerNode) *outRecallOwnerNode = -1;
                    if (outAuthEpoch) *outAuthEpoch = existing->baseEpoch;
                    return grantTypeFromIntended(existing->intendedState);
                }
                // TC98 fix: rate-limit high-frequency BUSY log
                { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
                framework::LogInfo("UBCC",
                        "UBCC node_id=%d: existing outstanding PA=0x%lx "
                        "same requester=%d opType=%d stage=%d — BUSY (n=%lu)\n",
                        _nodeId, line_pa, requesterNode,
                        static_cast<int>(existing->opType),
                        static_cast<int>(existing->stage), _cnt); }
                return static_cast<UBCC_OuterGrantType>(-1);
            }
            // recall_done_fix.md §4.2 Case C: different requester — enqueue or drop
            auto &q = _pendingRequesters[line_pa];
            bool isRS = (reqType == UBCC_OuterReqType::GlobalReadShared);

            // §4.4: Duplicate retry — same (requester, reqId) already queued → BUSY
            for (auto &pr : q) {
                if (pr.node == requesterNode && pr.reqId == reqId) {
                    // TC98 fix: rate-limit dup_retry log
                    { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
                    printf("[UBCC-QUEUE] pa=0x%lx action=dup_retry "
                           "requester=%d reqType=%s writeIntent=%d reqId=%lu depth=%zu (n=%lu)\n",
                           line_pa, requesterNode,
                           isRS ? "RS" : "RU", writeIntent, reqId, q.size(), _cnt); }
                    return static_cast<UBCC_OuterGrantType>(-1);
                }
            }

            // C3: RS merge dedup removed — batch RS grant handles all RS in one shot
            // §6 Q3=C was: RS merge RS — if incoming is RS and queue already has RS, skip
            // Removed to let all RS requests accumulate for batch grant in replayPendingRequesters.

            if (q.size() < MAX_PENDING_PER_PA) {
                PendingRequester pr;
                pr.node = requesterNode;
                pr.socket = requesterSocket;
                pr.reqType = reqType;
                pr.writeIntent = writeIntent;
                pr.epoch = baseEpoch;
                pr.reqId = reqId;
                q.push_back(pr);
                // TC98 fix: rate-limit enqueue log
                { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
                printf("[UBCC-QUEUE] pa=0x%lx action=enqueue "
                       "requester=%d reqType=%s writeIntent=%d reqId=%lu depth=%zu (n=%lu)\n",
                       line_pa, requesterNode,
                       isRS ? "RS" : "RU", writeIntent, reqId, q.size(), _cnt); }
                // TC98: Log recall wait state for hot-contention diagnostics
                if (existing->stage == OpStage::WAITING_TARGET_RESP) {
                    static uint64_t _rcnt = 0; if (++_rcnt <= 3 || _rcnt % 1000 == 0)
                    std::fprintf(stderr, "[UBCC-RECALL-WAIT] pa=0x%lx recall_target=%d "
                                 "new_requester=%d queue_depth=%zu existing_requester=%d (n=%lu)\n",
                                 line_pa, existing->targetNode, requesterNode,
                                 q.size(), existing->requesterNode, _rcnt);
                }
            } else {
                // TC98 fix: rate-limit drop_full log
                { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
                printf("[UBCC-QUEUE] pa=0x%lx action=drop_full "
                       "requester=%d reqType=%s writeIntent=%d reqId=%lu depth=%zu (n=%lu)\n",
                       line_pa, requesterNode,
                       isRS ? "RS" : "RU", writeIntent, reqId, q.size(), _cnt); }
            }
            return static_cast<UBCC_OuterGrantType>(-1);
        }
        // RECALL.DONE or other terminal — keep in map, let case blocks handle transition
    }

    // v4: Check tombstone for duplicate Clear within window W
    bool tsAccepted = false;
    if (checkTombstone(line_pa, baseEpoch, reqId, tsAccepted)) {
        // Already committed — return idempotent grant
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: tombstone HIT for PA=0x%lx — idempotent grant\n",
                _nodeId, line_pa);
        Tick now = curTick();
        if (outGrantVisibleTick) *outGrantVisibleTick = now;
        if (outSentinelVisibleTick) *outSentinelVisibleTick = now;
        if (outDataSource) *outDataSource = GrantDataSource::HomeMemory; // F3: conservative
        return UBCC_OuterGrantType::GlobalGrantShared; // conservative
    }

    // Record grant-visible tick
    Tick grantVisibleTick = curTick();
    Tick sentinelVisibleTick = curTick();

    // v4: Allocate reserved epoch (committed epoch + 1, NOT committed yet)
    uint64_t reservedEpoch = allocateReservedEpoch(entry);

    UBCC_OuterGrantType grant = UBCC_OuterGrantType::GlobalGrantShared;
    MESIState prevState = entry.state;

    // Determine intended result based on current committed state + request
    OutstandingRequest *oreq = nullptr;

    switch (entry.state) {
        case MESIState::G_I: {
            if (reqType == UBCC_OuterReqType::GlobalReadShared) {
                grant = UBCC_OuterGrantType::GlobalGrantShared;
                // Intended: G_S, sharers+=req, no owner
                oreq = createOutstanding(line_pa, OpType::GRANT_HANDSHAKE,
                                         requesterNode, -1, requesterSocket);
                if (oreq) {
                    oreq->reservedEpoch = reservedEpoch;
                    oreq->reqId = reqId;
                    oreq->baseEpoch = baseEpoch;
                    oreq->stage = OpStage::WAITING_CLEAR;
                    oreq->intendedState = MESIState::G_S;
                    oreq->intendedSharersMask = (1ULL << requesterNode);
                    oreq->intendedOwnerNode = -1;
                    oreq->intendedDirty = false;
                    oreq->dataSource = GrantDataSource::HomeMemory;
                    if (outDataSource) *outDataSource = GrantDataSource::HomeMemory;
                    if (outAuthEpoch) *outAuthEpoch = oreq->baseEpoch;
                }
            } else { // GlobalReadUnique
                if (!writeIntent) {
                    grant = UBCC_OuterGrantType::GlobalGrantExclusive;
                    oreq = createOutstanding(line_pa, OpType::GRANT_HANDSHAKE,
                                             requesterNode, -1, requesterSocket);
                    if (oreq) {
                        oreq->reservedEpoch = reservedEpoch;
                        oreq->reqId = reqId;
                        oreq->baseEpoch = baseEpoch;
                        oreq->stage = OpStage::WAITING_CLEAR;
                        oreq->intendedState = MESIState::G_E;
                        oreq->intendedSharersMask = 0;
                        oreq->intendedOwnerNode = requesterNode;
                        oreq->intendedDirty = false;
                        oreq->dataSource = GrantDataSource::HomeMemory;
                        if (outDataSource) *outDataSource = GrantDataSource::HomeMemory;
                        if (outAuthEpoch) *outAuthEpoch = oreq->baseEpoch;
                    }
                } else {
                    grant = UBCC_OuterGrantType::GlobalGrantModified;
                    oreq = createOutstanding(line_pa, OpType::GRANT_HANDSHAKE,
                                             requesterNode, -1, requesterSocket);
                    if (oreq) {
                        oreq->reservedEpoch = reservedEpoch;
                        oreq->reqId = reqId;
                        oreq->baseEpoch = baseEpoch;
                        oreq->stage = OpStage::WAITING_CLEAR;
                        oreq->intendedState = MESIState::G_M;
                        oreq->intendedSharersMask = 0;
                        oreq->intendedOwnerNode = requesterNode;
                        oreq->intendedDirty = true;
                        oreq->dataSource = GrantDataSource::HomeMemory;
                        if (outDataSource) *outDataSource = GrantDataSource::HomeMemory;
                        if (outAuthEpoch) *outAuthEpoch = oreq->baseEpoch;
                    }
                }
            }
            break;
        }

        case MESIState::G_S: {
            if (reqType == UBCC_OuterReqType::GlobalReadShared) {
                // C3-bis: G_S+RS fast path — no outstanding, no Clear.
                // Shared reads are clean; if grant is lost, requester retries
                // and gets re-granted from the same G_S state.
                grant = UBCC_OuterGrantType::GlobalGrantShared;
                oreq = createOutstanding(line_pa, OpType::GRANT_HANDSHAKE,
                                         requesterNode, -1, requesterSocket);
                if (oreq) {
                    oreq->reservedEpoch = reservedEpoch;
                    oreq->reqId = reqId;
                    oreq->baseEpoch = baseEpoch;
                    oreq->stage = OpStage::WAITING_CLEAR;
                    oreq->intendedState = MESIState::G_S;
                    oreq->intendedSharersMask = entry.sharersMask | (1ULL << requesterNode);
                    oreq->intendedOwnerNode = -1;
                    oreq->intendedDirty = false;
                    oreq->dataSource = GrantDataSource::HomeMemory;
                    if (outDataSource) *outDataSource = GrantDataSource::HomeMemory;
                    if (outAuthEpoch) *outAuthEpoch = oreq->baseEpoch;
                }

                printf("[UBCC-GSRS-FAST] pa=0x%lx requester=%d sharers=0x%lx\n",
                       line_pa, requesterNode, entry.sharersMask);
            } else {
                // Unique request — invalidation needed for non-requester sharers
                uint64_t otherSharers = entry.sharersMask;
                if (requesterNode >= 0)
                    otherSharers &= ~(1ULL << requesterNode);

                // F6: If requester is an existing sharer, this is a local
                // upgrade. Defer to the UPGRADE_PENDING path (§4.1.3 G_S row).
                // Do NOT create INVALIDATE here — let processOuterUpgradeReq
                // handle it via the EP-RNF upgrade handshake.
                bool isExistingSharer = (requesterNode >= 0) &&
                    (entry.sharersMask & (1ULL << requesterNode));
                if (isExistingSharer) {
                    printf("[UBCC-SHARER-UPGRADE] pa=0x%lx requester=%d "
                           "is existing sharer — deferring to UPGRADE_PENDING\n",
                           line_pa, requesterNode);
                    return static_cast<UBCC_OuterGrantType>(-1);
                }

                if (otherSharers != 0) {
                    printf("[UBCC-INVALIDATE-CREATE] home=%d pa=0x%lx requester=%d "
                           "otherSharers=0x%lx reservedEpoch=%lu writeIntent=%d\n",
                           _nodeId, line_pa, requesterNode, otherSharers,
                           reservedEpoch, writeIntent);
                    // v4: Create INVALIDATE + GRANT_HANDSHAKE
                    // INVALIDATE outstanding
                    OutstandingRequest *invOreq = createOutstanding(
                        line_pa, OpType::INVALIDATE, requesterNode, -1,
                        requesterSocket);
                    // fix2: fan out FIRST so we learn the effective (live) target
                    // set, then size the ack accounting to exactly what we sent.
                    uint64_t effectiveMask = otherSharers;
                    _invalidationCount++;
                    fanoutInvalidateTargets(line_pa, otherSharers,
                                             entry.epoch, reqId,
                                             requesterNode,
                                             reqType, writeIntent,
                                             &effectiveMask);
                    if (invOreq) {
                        invOreq->reservedEpoch = reservedEpoch;
                        invOreq->reqId = reqId;
                        invOreq->baseEpoch = baseEpoch;
                        invOreq->reqType = reqType;
                        invOreq->stage = OpStage::WAITING_ALL_ACKS;
                        // fix2: ack accounting tracks the effective (live) mask
                        invOreq->targetMask = effectiveMask;
                        invOreq->totalMask = effectiveMask;
                        invOreq->pendingAckCount = __builtin_popcountll(effectiveMask);
                        invOreq->ackMask = 0;
                        invOreq->writeIntent = writeIntent;
                        invOreq->intendedState = writeIntent ? MESIState::G_M : MESIState::G_E;
                        invOreq->intendedOwnerNode = requesterNode;
                        invOreq->intendedSharersMask = 0;
                        invOreq->intendedDirty = writeIntent;
                        invOreq->dataSource = GrantDataSource::HomeMemory; // F3
                        if (outAuthEpoch) *outAuthEpoch = invOreq->baseEpoch;

                        // fix2 corner case: if NO sharer remains live, there is
                        // nothing to wait for — the invalidation is already
                        // satisfied. Convert the INVALIDATE straight to a
                        // GRANT_HANDSHAKE (mirroring the all-acks-done path) so
                        // the grant proceeds immediately instead of hanging in
                        // WAITING_ALL_ACKS with pendingAckCount==0 and no ack to
                        // trigger the transition.
                        if (effectiveMask == 0) {
                            printf("[UBCC-INVALIDATE-EMPTY] home=%d pa=0x%lx "
                                   "requester=%d — no live sharers, converting "
                                   "to GRANT_HANDSHAKE immediately\n",
                                   _nodeId, line_pa, requesterNode);
                            invOreq->invalidateBarrierDone = true;
                            invOreq->opType = OpType::GRANT_HANDSHAKE;
                            invOreq->stage = OpStage::WAITING_CLEAR;
                            invOreq->replayArmed = true;
                            invOreq->recallBarrierDone = false;
                            if (_outbound) {
                                CoherenceMessage push;
                                buildGrantResponse(*invOreq, push);
                                _outbound->sendGrantPush(push);
                                printf("[PUSH-GRANT] INVALIDATE-EMPTY home=%d "
                                       "pa=0x%lx requester=%d sock=%d reqId=%lu "
                                       "grantType=%d\n",
                                       _nodeId, line_pa, invOreq->requesterNode,
                                       invOreq->requesterSocket, invOreq->reqId,
                                       static_cast<int>(
                                          grantTypeFromIntended(invOreq->intendedState)));
                            }
                        }
                    }
                    // Return BUSY — invalidation must complete before grant
                    return static_cast<UBCC_OuterGrantType>(-1);
                } else {
                    // No other sharers — immediate upgrade (self-upgrade)
                    // v4: This should use UPGRADE_PENDING path (§4.1.3 G_S row)
                    // For now, create GRANT_HANDSHAKE for the upgrade
                    grant = writeIntent
                        ? UBCC_OuterGrantType::GlobalGrantModified
                        : UBCC_OuterGrantType::GlobalGrantExclusive;
                    oreq = createOutstanding(line_pa, OpType::GRANT_HANDSHAKE,
                                             requesterNode, -1, requesterSocket);
                    if (oreq) {
                        oreq->reservedEpoch = reservedEpoch;
                        oreq->reqId = reqId;
                        oreq->baseEpoch = baseEpoch;
                        oreq->stage = OpStage::WAITING_CLEAR;
                        oreq->intendedState = writeIntent ? MESIState::G_M : MESIState::G_E;
                        oreq->intendedSharersMask = 0;
                        oreq->intendedOwnerNode = requesterNode;
                        oreq->intendedDirty = writeIntent;
                        oreq->dataSource = GrantDataSource::HomeMemory;
                        if (outDataSource) *outDataSource = GrantDataSource::HomeMemory;
                        if (outAuthEpoch) *outAuthEpoch = oreq->baseEpoch;
                    }
                }
            }
            break;
        }

        case MESIState::G_E:
        case MESIState::G_M: {
            int existingOwner = DirEntry::ownerFromSharers(entry);

            // v4: Check if there's an already-completed RECALL for this requester
            bool recallAlreadyDone = false;
            auto rit = _outstandingReqs.find(line_pa);
            if (rit != _outstandingReqs.end() &&
                rit->second.opType == OpType::RECALL &&
                rit->second.requesterNode == requesterNode &&
                rit->second.stage == OpStage::DONE) {
                recallAlreadyDone = true;

                // F2: RECALL and GRANT_HANDSHAKE are two separate lifecycle
                // objects.  Remove the terminal RECALL first, then create a
                // new GRANT_HANDSHAKE.  DO NOT mutate opType in place.
                OutstandingRequest recallData = rit->second;  // capture fields
                removeOutstanding(line_pa);  // free the PA slot

                uint64_t newSharers = (1ULL << requesterNode);
                if (existingOwner >= 0)
                    newSharers |= (1ULL << existingOwner);

                OutstandingRequest *grantOreq = createOutstanding(
                    line_pa, OpType::GRANT_HANDSHAKE,
                    requesterNode, -1, requesterSocket);
                if (grantOreq) {
                    grantOreq->reservedEpoch = recallData.reservedEpoch;
                    grantOreq->reqId = recallData.reqId;
                    grantOreq->baseEpoch = recallData.baseEpoch;
                    grantOreq->stage = OpStage::WAITING_CLEAR;
                    grantOreq->recallBarrierDone = true;
                    // F2: Copy recall data from RECALL → GRANT_HANDSHAKE
                    grantOreq->dataValid = recallData.dataValid;
                    if (recallData.dataValid) {
                        memcpy(grantOreq->dataBuf, recallData.dataBuf, 64);
                    }
                    // F3: Data source is RecallBuffer since data came from recall
                    grantOreq->dataSource = GrantDataSource::RecallBuffer;
                    if (outDataSource) *outDataSource = GrantDataSource::RecallBuffer;
                    if (outAuthEpoch) *outAuthEpoch = grantOreq->baseEpoch;
                    if (reqType == UBCC_OuterReqType::GlobalReadShared) {
                        grant = UBCC_OuterGrantType::GlobalGrantShared;
                        grantOreq->intendedState = MESIState::G_S;
                        grantOreq->intendedSharersMask = newSharers;
                        grantOreq->intendedOwnerNode = -1;
                        grantOreq->intendedDirty = false;
                    } else {
                        grant = writeIntent
                            ? UBCC_OuterGrantType::GlobalGrantModified
                            : UBCC_OuterGrantType::GlobalGrantExclusive;
                        grantOreq->intendedState = writeIntent
                            ? MESIState::G_M : MESIState::G_E;
                        grantOreq->intendedOwnerNode = requesterNode;
                        grantOreq->intendedSharersMask = 0;
                        grantOreq->intendedDirty = writeIntent;
                    }
                } else {
                    // Failed to create GRANT_HANDSHAKE — restore RECALL
                    // (should not happen since PA was freed above)
                    fatal("UBCC node_id=%d: failed to create GRANT_HANDSHAKE "
                          "after removing DONE RECALL PA=0x%lx\n",
                          _nodeId, line_pa);
                }
                framework::LogInfo("UBCC",
                        "UBCC node_id=%d: RECALL→GRANT_HANDSHAKE transition "
                        "PA=0x%lx requester=%d intended=%s dataSource=RecallBuffer (NEW object)\n",
                        _nodeId, line_pa, requesterNode,
                        grantOreq ? mesiStateName(grantOreq->intendedState)
                                  : "none");
                return grant;
            }

            // recall_done_fix.md §4.2 Case B: RECALL.DONE exists but for
            // a DIFFERENT requester.  Do NOT consume it; enqueue instead.
            if (rit != _outstandingReqs.end() &&
                rit->second.opType == OpType::RECALL &&
                rit->second.stage == OpStage::DONE &&
                rit->second.requesterNode != requesterNode) {
                auto &q = _pendingRequesters[line_pa];
                bool isRS = (reqType == UBCC_OuterReqType::GlobalReadShared);

                // §4.4: Duplicate retry check
                for (auto &pr : q) {
                    if (pr.node == requesterNode && pr.reqId == reqId) {
                        // TC98 fix: rate-limit dup_retry log (RECALL.DONE path)
                        { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
                        printf("[UBCC-QUEUE] pa=0x%lx action=dup_retry "
                               "requester=%d reqType=%s writeIntent=%d reqId=%lu depth=%zu (n=%lu)\n",
                               line_pa, requesterNode,
                               isRS ? "RS" : "RU", writeIntent, reqId, q.size(), _cnt); }
                        return static_cast<UBCC_OuterGrantType>(-1);
                    }
                }

                // C3: RS merge dedup removed — see comment at the other location (~L503)

                if (q.size() < MAX_PENDING_PER_PA) {
                    PendingRequester pr;
                    pr.node = requesterNode;
                    pr.socket = requesterSocket;
                    pr.reqType = reqType;
                    pr.writeIntent = writeIntent;
                    pr.epoch = baseEpoch;
                    pr.reqId = reqId;
                    q.push_back(pr);
                    // TC98 fix: rate-limit enqueue log (RECALL.DONE path)
                    { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
                    printf("[UBCC-QUEUE] pa=0x%lx action=enqueue "
                           "requester=%d reqType=%s writeIntent=%d reqId=%lu depth=%zu (n=%lu)\n",
                           line_pa, requesterNode,
                           isRS ? "RS" : "RU", writeIntent, reqId, q.size(), _cnt); }
                } else {
                    // TC98 fix: rate-limit drop_full log (RECALL.DONE path)
                    { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
                    printf("[UBCC-QUEUE] pa=0x%lx action=drop_full "
                           "requester=%d reqType=%s writeIntent=%d reqId=%lu depth=%zu (n=%lu)\n",
                           line_pa, requesterNode,
                           isRS ? "RS" : "RU", writeIntent, reqId, q.size(), _cnt); }
                }
                return static_cast<UBCC_OuterGrantType>(-1);
            }

            if (existingOwner >= 0 && existingOwner != requesterNode
                && !recallAlreadyDone) {
                // v4: Recall needed — create outstanding FIRST, then send RecallReq
                printf("[RECALL-CREATE] UBCC node=%d PA=0x%lx existingOwner=%d requester=%d\n",
                       _nodeId, line_pa, existingOwner, requesterNode);

                OutstandingRequest *recallOreq = createOutstanding(
                    line_pa, OpType::RECALL, requesterNode, existingOwner,
                    requesterSocket);
                if (!recallOreq) {
                    warn("UBCC node_id=%d: failed to create RECALL outstanding "
                         "PA=0x%lx requester=%d owner=%d\n",
                         _nodeId, line_pa, requesterNode, existingOwner);
                    return static_cast<UBCC_OuterGrantType>(-1);
                }

                recallOreq->reservedEpoch = reservedEpoch;
                recallOreq->reqId = reqId;
                recallOreq->baseEpoch = baseEpoch;
                recallOreq->stage = OpStage::WAITING_TARGET_RESP;
                recallOreq->reqType = reqType;
                recallOreq->writeIntent = writeIntent;
                recallOreq->dataSource = GrantDataSource::RecallBuffer;

                if (!initiateRecall(line_pa, entry, *recallOreq)) {
                    warn("UBCC node_id=%d: initiateRecall failed PA=0x%lx — "
                         "removing outstanding\n",
                         _nodeId, line_pa);
                    removeOutstanding(line_pa);
                    return static_cast<UBCC_OuterGrantType>(-1);
                }

                _recallCount++;
                if (outRecallNeeded) *outRecallNeeded = true;
                if (outRecallOwnerNode) *outRecallOwnerNode = existingOwner;
                if (outDataSource) *outDataSource = GrantDataSource::RecallBuffer;
                if (outAuthEpoch) *outAuthEpoch = recallOreq->baseEpoch;

                return static_cast<UBCC_OuterGrantType>(-1);
            }

            // Same owner or no recall — immediate grant
            if (reqType == UBCC_OuterReqType::GlobalReadShared) {
                grant = UBCC_OuterGrantType::GlobalGrantShared;
                oreq = createOutstanding(line_pa, OpType::GRANT_HANDSHAKE,
                                         requesterNode, -1, requesterSocket);
                if (oreq) {
                    oreq->reservedEpoch = reservedEpoch;
                    oreq->reqId = reqId;
                    oreq->baseEpoch = baseEpoch;
                    oreq->stage = OpStage::WAITING_CLEAR;
                    oreq->intendedState = MESIState::G_S;
                    uint64_t newSharers = (1ULL << requesterNode);
                    if (existingOwner >= 0)
                        newSharers |= (1ULL << existingOwner);
                    oreq->intendedSharersMask = newSharers;
                    oreq->intendedOwnerNode = -1;
                    oreq->intendedDirty = false;
                    oreq->dataSource = GrantDataSource::HomeMemory;
                    if (outDataSource) *outDataSource = GrantDataSource::HomeMemory;
                    if (outAuthEpoch) *outAuthEpoch = oreq->baseEpoch;
                }
            } else {
                grant = writeIntent
                    ? UBCC_OuterGrantType::GlobalGrantModified
                    : UBCC_OuterGrantType::GlobalGrantExclusive;
                oreq = createOutstanding(line_pa, OpType::GRANT_HANDSHAKE,
                                         requesterNode, -1, requesterSocket);
                if (oreq) {
                    oreq->reservedEpoch = reservedEpoch;
                    oreq->reqId = reqId;
                    oreq->baseEpoch = baseEpoch;
                    oreq->stage = OpStage::WAITING_CLEAR;
                    oreq->intendedState = writeIntent ? MESIState::G_M : MESIState::G_E;
                    oreq->intendedSharersMask = 0;
                    oreq->intendedOwnerNode = requesterNode;
                    oreq->intendedDirty = writeIntent;
                    oreq->dataSource = GrantDataSource::HomeMemory;
                    if (outDataSource) *outDataSource = GrantDataSource::HomeMemory;
                    if (outAuthEpoch) *outAuthEpoch = oreq->baseEpoch;
                }
            }
            break;
        }
    }

    // v4: §4.1.3 — SHALL NOT modify committed DirEntry here.
    // Committed DirEntry stays as-is until matching Clear is accepted.

    framework::LogInfo("UBCC",
            "UBCC node_id=%d: v4 grant decision PA=0x%lx "
            "prev=%s intended_state=%s grant=%d reservedEpoch=%lu "
            "(committed DirEntry NOT modified)\n",
            _nodeId, line_pa,
            mesiStateName(prevState),
            oreq ? mesiStateName(oreq->intendedState) : "none",
            static_cast<int>(grant), reservedEpoch);
    printf("[UBCC-GRANT-READY] home=%d pa=0x%lx requester=%d grant=%d prev=%s "
           "intended=%s baseEpoch=%lu reservedEpoch=%lu reqId=%lu dataSource=%d\n",
           _nodeId, line_pa, requesterNode, static_cast<int>(grant),
           mesiStateName(prevState),
           oreq ? mesiStateName(oreq->intendedState) : "none",
           oreq ? oreq->baseEpoch : 0,
           oreq ? oreq->reservedEpoch : 0,
           oreq ? oreq->reqId : 0,
           oreq ? static_cast<int>(oreq->dataSource) : -1);

    if (outGrantVisibleTick)
        *outGrantVisibleTick = grantVisibleTick;
    if (outSentinelVisibleTick)
        *outSentinelVisibleTick = sentinelVisibleTick;

    // v4-latency: log OUTSTANDING state change
    if (oreq) {
        framework::LogInfo("UBCC-latency",
                "[UBST] tick=%lu home=%d,%d pa=0x%lx old=%s new=%s epoch=%lu sharers=0x%lx action=OUTSTANDING\n",
                curTick(), _nodeId, _socketId, line_pa,
                mesiStateName(prevState),
                mesiStateName(oreq->intendedState),
                oreq->reservedEpoch,
                oreq->intendedSharersMask);
    }

    return grant;
}

std::string
UBCCController::inspectUbccDirForTest(uint64_t line_pa)
{
    DirEntry e;
    if (!_directory.lookup(line_pa, e)) {
        return "{\"error\": \"entry not found\"}";
    }

    std::ostringstream oss;
    oss << "{"
        << "\"lineAddr\":\"0x" << std::hex << e.lineAddr << std::dec << "\","
        << "\"state\":\"" << mesiStateName(e.state) << "\","
        << "\"sharersMask\":" << e.sharersMask << ","
        << "\"ownerNode\":" << DirEntry::ownerFromSharers(e) << ","
        << "\"dirty\":" << (DirEntry::protoDirty(e) ? "true" : "false") << ","
         << "\"epoch\":" << e.epoch;

    // v4: Outstanding state sourced from OutstandingRequest
    auto oit = _outstandingReqs.find(line_pa);
    if (oit != _outstandingReqs.end()) {
        const auto &ost = oit->second;
        oss << ","
            << "\"ostOpType\":" << static_cast<int>(ost.opType) << ","
            << "\"ostStage\":" << static_cast<int>(ost.stage) << ","
            << "\"ostRequester\":" << ost.requesterNode << ","
            << "\"ostTarget\":" << ost.targetNode << ","
            << "\"ostReservedEpoch\":" << ost.reservedEpoch << ","
            << "\"ostReqId\":" << ost.reqId << ","
            << "\"ostDataSource\":" << static_cast<int>(ost.dataSource);  // F3
        if (ost.opType == OpType::INVALIDATE) {
            oss << ","
                << "\"pendingInvalidationCount\":" << ost.pendingAckCount << ","
                << "\"pendingInvalidationMask\":" << ost.totalMask << ","
                << "\"invalidatedAckMask\":" << ost.ackMask;
        }
    } else {
        oss << ","
            << "\"ostOpType\":-1";
    }

    // Counters for test observation
    oss << ","
        << "\"writebackCount\":" << _writebackCount << ","
        << "\"evictCount\":" << _evictCount << ","
        << "\"staleRejectedCount\":" << _staleRejectedCount << ","
        << "\"ownerMismatchRejectedCount\":" << _ownerMismatchRejectedCount << ","
        << "\"invalidationCount\":" << _invalidationCount << ","
        << "\"invalidationAckCount\":" << _invalidationAckCount << ","
        << "\"asyncWbCount\":" << _asyncWbCount << ","
        << "\"residentOverflowPolicy\":"
        << (_overflowPolicy == ResidentOverflowPolicy::NaiveEvict ? 1 : 0) << ","
        << "\"naiveDirEvictions\":" << _naiveDirEvictions << ","
        << "\"naiveForcedInvalidations\":" << _naiveForcedInvalidations << ","
        << "\"naiveForcedWritebacks\":" << _naiveForcedWritebacks << ","
        << "\"naiveDirtyVictims\":" << _naiveDirtyVictims;
    oss << "}";
    return oss.str();
}

bool
UBCCController::getUbccDirFieldsForTest(uint64_t line_pa,
    MESIState &outState, int &outOwnerNode,
    uint64_t &outSharersMask, bool &outDirty) const
{
    DirEntry e;
    if (!_directory.lookup(line_pa, e)) {
        return false;
    }
    outState = e.state;
    outOwnerNode = DirEntry::ownerFromSharers(e);
    outSharersMask = e.sharersMask;
    outDirty = DirEntry::protoDirty(e);
    return true;
}

// ---- M6: Extended Directory Field Access (includes recall context) ----

bool
UBCCController::getUbccDirFieldsExtendedForTest(uint64_t line_pa,
    MESIState &outState, int &outOwnerNode,
    uint64_t &outSharersMask, bool &outDirty, bool &outBusy,
    int &outPendingRequester, int &outPendingRecallTarget) const
{
    DirEntry e;
    if (!_directory.lookup(line_pa, e)) {
        return false;
    }
    outState = e.state;
    outOwnerNode = DirEntry::ownerFromSharers(e);
    outSharersMask = e.sharersMask;
    outDirty = DirEntry::protoDirty(e);
    outBusy = isLineBusy(line_pa);
    outPendingRequester = getPendingRequester(line_pa);
    outPendingRecallTarget = getPendingRecallTarget(line_pa);
    return true;
}

// ---- M6: Recall Management ----

bool
UBCCController::initiateRecall(uint64_t line_pa, const DirEntry &entry,
                               const OutstandingRequest &recallOreq)
{
    if (!_outbound) {
        warn("UBCC node_id=%d: initiateRecall called with no outbound sender\n",
             _nodeId);
        return false;
    }

    const int ownerNode = (recallOreq.targetNode >= 0)
        ? recallOreq.targetNode
        : DirEntry::ownerFromSharers(entry);
    if (ownerNode < 0 || ownerNode >= 64) {
        warn("UBCC node_id=%d: initiateRecall invalid ownerNode=%d PA=0x%lx\n",
             _nodeId, ownerNode, line_pa);
        return false;
    }

    const uint64_t offset = _addrMap.dsmOffset(line_pa);

    CoherenceMessage msg;
    msg.h.type = CoherenceMessageType::RecallReq;
    msg.h.srcNode = _nodeId;
    msg.h.srcSocket = _socketId;
    msg.h.dstNode = ownerNode;
    msg.h.dstSocket = _socketId;
    msg.h.homeNode = _nodeId;
    msg.h.homeSocket = _socketId;
    msg.h.ingressSocket = _socketId;
    msg.h.requesterNode = recallOreq.requesterNode;
    msg.h.targetNode = ownerNode;
    msg.h.homeLinePa = line_pa;
    msg.h.localLinePa = _addrMap.buildDsmPA(ownerNode, _nodeId, offset, _socketId);
    msg.h.epoch = entry.epoch;
    msg.h.reqId = recallOreq.reqId;
    msg.h.seqNum = 0;
    msg.h.enqueueTick = curTick();
    msg.h.readyTick = curTick();

    if (recallOreq.reqType == UBCC_OuterReqType::GlobalReadShared)
        msg.h.flags |= static_cast<uint32_t>(CFLAG_IS_READ_RECALL);
    msg.h.flags |= static_cast<uint32_t>(CFLAG_HAS_DATA);

    printf("[RECALL-TRACE-A] UBCC n=%d initiateRecall PA=0x%lx owner=%d requester=%d\n",
           _nodeId, line_pa, ownerNode, recallOreq.requesterNode);

    if (!_outbound->sendRecallReq(msg)) {
        warn("UBCC node_id=%d: sendRecallReq failed PA=0x%lx owner=%d requester=%d\n",
             _nodeId, line_pa, ownerNode, recallOreq.requesterNode);
        return false;
    }

    return true;
}

bool
UBCCController::processRecallResponse(uint64_t line_pa, int ownerNode,
                                       bool dataReceived, uint64_t responseEpoch,
                                       uint64_t reqId,
                                       const DataBlock *dataBlk)
{
    responseEpoch = normalizeEpoch(responseEpoch);

    printf("[RECALL-DIAG] UBCC node_id=%d processRecallResponse PA=0x%lx "
           "owner=%d epoch=%lu reqId=%lu\n",
           _nodeId, line_pa, ownerNode, responseEpoch, reqId);
    DirEntry entry;
    if (!_directory.lookup(line_pa, entry)) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processRecallResponse PA=0x%lx entry not found\n",
                _nodeId, line_pa);
        return false;
    }

    if (!checkEpochForLine(line_pa, responseEpoch)) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processRecallResponse PA=0x%lx "
                "STALE epoch — REJECTED\n",
                _nodeId, line_pa);
        _staleRejectedCount++;
        return false;
    }

    OutstandingRequest *ost = findOutstanding(line_pa);
    if (!ost || ost->opType != OpType::RECALL) {
        printf("[RECALL-DIAG] UBCC node_id=%d PA=0x%lx no RECALL outstanding\n",
               _nodeId, line_pa);
        return false;
    }

    if (ost->targetNode >= 0 && ost->targetNode != ownerNode) {
        warn("UBCC node_id=%d: recall owner mismatch PA=0x%lx expected=%d got=%d\n",
             _nodeId, line_pa, ost->targetNode, ownerNode);
        return false;
    }

    if (ost->reqId != 0 && ost->reqId != reqId) {
        warn("UBCC node_id=%d: recall reqId mismatch PA=0x%lx expected=%lu got=%lu\n",
             _nodeId, line_pa, ost->reqId, reqId);
        return false;
    }

    if (ost->recallBarrierDone)
        return true;

    ost->recallBarrierDone = true;
    ost->respTick = curTick();
    ost->dataValid = dataReceived;

    if (dataBlk && dataReceived) {
        memcpy(ost->dataBuf, dataBlk->getData(0, 64), 64);
        ost->dataValid = true;
    }

    const OutstandingRequest recallDone = *ost;
    const int requesterNode = recallDone.requesterNode;
    const UBCC_OuterReqType reqType = recallDone.reqType;
    const bool writeIntent = recallDone.writeIntent;

    removeOutstanding(line_pa);

    if (reqType == UBCC_OuterReqType::GlobalInvalidate) {
        if (recallDone.dataValid && _host) {
            std::array<uint8_t, 64> cached{};
            memcpy(cached.data(), recallDone.dataBuf, 64);
            _lineDataCache[line_pa] = cached;
            _host->writeDsmData(line_pa, recallDone.dataBuf);
            std::fprintf(stderr,
                         "[NAIVE-DATA-PERSIST] home=%d pa=0x%lx owner=%d data=1\n",
                         _nodeId, line_pa, ownerNode);
            std::fflush(stderr);
        } else {
            std::fprintf(stderr,
                         "[NAIVE-DATA-PERSIST] home=%d pa=0x%lx owner=%d data=0\n",
                         _nodeId, line_pa, ownerNode);
            std::fflush(stderr);
        }
        entry.state = MESIState::G_I;
        entry.sharersMask = 0;
        entry.residentDirty = false;
        _directory.update(line_pa, entry);
        _directory.forceRemove(line_pa);
        _residentWaiters.erase(line_pa);
        _pendingRequesters.erase(line_pa);
        _evictionPendingRemoval.erase(line_pa);
        replayResidentWaitersForCapacity();
        _recallResponseCount++;
        printf("[UBCC-NAIVE-EVICT-DONE] home=%d pa=0x%lx owner=%d data=%d\n",
               _nodeId, line_pa, ownerNode, recallDone.dataValid ? 1 : 0);
        return true;
    }

    OutstandingRequest *grantOst = createOutstanding(
        line_pa, OpType::GRANT_HANDSHAKE, requesterNode, -1,
        recallDone.requesterSocket);
    if (!grantOst) {
        fatal("UBCC node_id=%d: failed to create GRANT_HANDSHAKE after "
              "RecallResp PA=0x%lx requester=%d\n",
              _nodeId, line_pa, requesterNode);
    }

    grantOst->reservedEpoch = recallDone.reservedEpoch;
    grantOst->reqId = recallDone.reqId;
    grantOst->baseEpoch = recallDone.baseEpoch;
    grantOst->reqType = recallDone.reqType;
    grantOst->writeIntent = recallDone.writeIntent;
    grantOst->stage = OpStage::WAITING_CLEAR;
    grantOst->recallBarrierDone = true;
    grantOst->replayArmed = true;
    grantOst->dataValid = recallDone.dataValid;
    grantOst->dataSource = recallDone.dataValid
        ? GrantDataSource::RecallBuffer
        : GrantDataSource::HomeMemory;

    if (recallDone.dataValid) {
        memcpy(grantOst->dataBuf, recallDone.dataBuf, 64);
    }

    if (reqType == UBCC_OuterReqType::GlobalReadShared) {
        grantOst->intendedState = MESIState::G_S;
        grantOst->intendedSharersMask = (1ULL << requesterNode);
        if (ownerNode >= 0)
            grantOst->intendedSharersMask |= (1ULL << ownerNode);
        grantOst->intendedOwnerNode = -1;
        grantOst->intendedDirty = false;
    } else {
        grantOst->intendedState = writeIntent ? MESIState::G_M : MESIState::G_E;
        grantOst->intendedSharersMask = 0;
        grantOst->intendedOwnerNode = requesterNode;
        grantOst->intendedDirty = writeIntent;
    }

    // Push-grant: home proactively delivers ReadResp to requester so
    // the EP-SNF retry can hit _readyResponses immediately (~0-cycle gap)
    // instead of waiting for the 20000-cycle retry timer.
    // replayArmed stays true as pull fallback if push fails.
    if (_outbound) {
        CoherenceMessage push;
        buildGrantResponse(*grantOst, push);
        _outbound->sendGrantPush(push);
        printf("[PUSH-GRANT] RECALL home=%d pa=0x%lx requester=%d sock=%d "
               "reqId=%lu grantType=%d dataSource=%d\n",
               _nodeId, line_pa, grantOst->requesterNode,
               grantOst->requesterSocket, grantOst->reqId,
               static_cast<int>(grantTypeFromIntended(grantOst->intendedState)),
               static_cast<int>(grantOst->dataSource));
    }

    _recallResponseCount++;
    printf("[RECALL-TO-GRANT] home=%d pa=0x%lx requester=%d owner=%d intended=%s "
           "baseEpoch=%lu reservedEpoch=%lu reqId=%lu dataSource=%d\n",
           _nodeId, line_pa, requesterNode, ownerNode,
           mesiStateName(grantOst->intendedState),
           grantOst->baseEpoch, grantOst->reservedEpoch, grantOst->reqId,
           static_cast<int>(grantOst->dataSource));

    return true;
}

bool
UBCCController::isLineBusy(uint64_t line_pa) const
{
    // v4: Check outstanding requests for non-terminal stages
    auto oit = _outstandingReqs.find(line_pa);
    if (oit != _outstandingReqs.end()) {
        switch (oit->second.stage) {
            case OpStage::DONE:
            case OpStage::CANCELLED:
            case OpStage::TIMED_OUT:
                break;  // Terminal stages — not busy
            default:
                return true;
        }
    }
    return false;
}

int
UBCCController::getPendingRequester(uint64_t line_pa) const
{
    auto oit = _outstandingReqs.find(line_pa);
    if (oit != _outstandingReqs.end())
        return oit->second.requesterNode;
    return -1;
}

int
UBCCController::getPendingRecallTarget(uint64_t line_pa) const
{
    auto oit = _outstandingReqs.find(line_pa);
    if (oit != _outstandingReqs.end() &&
        oit->second.opType == OpType::RECALL)
        return oit->second.targetNode;
    return -1;
}

// ---- M8: Global Invalidation Management ----

bool
UBCCController::processInvalidationAck(uint64_t line_pa, int ackNode,
                                         uint64_t responseEpoch,
                                         uint64_t reqId)
{
    responseEpoch = normalizeEpoch(responseEpoch);

    // Validate ackNode boundaries
    if (ackNode < 0 || ackNode >= 64) {
        warn("UBCC node_id=%d: processInvalidationAck PA=0x%lx "
             "ackNode=%d out of range — REJECTED\n",
             _nodeId, line_pa, ackNode);
        return false;
    }

    DirEntry entry;
    if (!_directory.lookup(line_pa, entry)) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processInvalidationAck PA=0x%lx "
                "entry not found\n", _nodeId, line_pa);
        return false;
    }

    // v4: Half-range epoch check
    if (!checkEpochForLine(line_pa, responseEpoch)) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processInvalidationAck PA=0x%lx "
                "STALE epoch: response=%lu directory=%lu — REJECTED\n",
                _nodeId, line_pa, responseEpoch, entry.epoch);
        _staleRejectedCount++;
        return false;
    }

    // v4: Verify pending invalidation via OutstandingRequest
    OutstandingRequest *ost = findOutstanding(line_pa);
    if (!ost) {
        // Already completed — idempotent
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processInvalidationAck PA=0x%lx "
                "no outstanding — idempotent\n",
                _nodeId, line_pa);
        return true;
    }

    // upgrade_invalidate_fix: accept both INVALIDATE and UPGRADE_PENDING (WAITING_ALL_ACKS)
    bool isUpgradePath = (ost->opType == OpType::UPGRADE_PENDING &&
                          ost->stage == OpStage::WAITING_ALL_ACKS);
    bool isInvalidatePath = (ost->opType == OpType::INVALIDATE);

    if (!isInvalidatePath && !isUpgradePath) {
        // Wrong op type or stage — idempotent
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processInvalidationAck PA=0x%lx "
                "opType=%d stage=%d — not applicable, idempotent\n",
                _nodeId, line_pa,
                static_cast<int>(ost->opType), static_cast<int>(ost->stage));
        return true;
    }

    // Check for duplicate ack — use upgrade fields or standard fields
    uint64_t nodeBit = (1ULL << ackNode);
    uint64_t &effTargetMask = isUpgradePath ? ost->upgradeTargetMask : ost->totalMask;
    uint64_t &effAckMask = isUpgradePath ? ost->upgradeAckMask : ost->ackMask;

    if (!(effTargetMask & nodeBit)) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processInvalidationAck PA=0x%lx "
                "ackNode=%d not in targetMask=0x%lx\n",
                _nodeId, line_pa, ackNode, effTargetMask);
        return false;
    }

    if (effAckMask & nodeBit) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processInvalidationAck PA=0x%lx "
                "duplicate ack from node %d — ignoring\n",
                _nodeId, line_pa, ackNode);
        return true;
    }

    // Record the ack
    effAckMask |= nodeBit;
    if (isUpgradePath) {
        ost->upgradePendingAckCount--;
    } else {
        ost->pendingAckCount--;
    }

    // INVALIDATE path (not UPGRADE_PENDING): committed sharer set must track
    // acked invalidations so later committed lookups do not observe stale sharers.
    // Keep epoch/intended-result commit deferred to GRANT_HANDSHAKE Clear.
    if (isInvalidatePath) {
        entry.sharersMask &= ~nodeBit;
        if (entry.state == MESIState::G_S && entry.sharersMask == 0) {
            // Canonicalize shared-empty into G_I to satisfy ResidentDir
            // invariant (G_S requires non-empty sharersMask).
            entry.state = MESIState::G_I;
        }
        _directory.update(line_pa, entry);
        // UBInvariant: validate canonical form after sharer eviction
        validateSharersCanonical(line_pa);
        appendTmpLog(
            "ubcc_inv_ack.log",
            "[INV-ACK] pa=0x%lx node=%d remaining=%d ackMask=0x%lx\n",
            line_pa, ackNode, ost->pendingAckCount, ost->ackMask);
    }

    framework::LogInfo("UBCC",
            "UBCC node_id=%d: invalidation ack PA=0x%lx ackNode=%d op=%s "
            "remaining=%d ackMask=0x%lx targetMask=0x%lx\n",
            _nodeId, line_pa, ackNode,
            isUpgradePath ? "UPGRADE" : "INVALIDATE",
            isUpgradePath ? ost->upgradePendingAckCount : ost->pendingAckCount,
            effAckMask, effTargetMask);
    printf("[UBCC-INV-ACK] home=%d pa=0x%lx ackNode=%d op=%s remaining=%d "
           "ackMask=0x%lx targetMask=0x%lx dirState=%s dirSharers=0x%lx\n",
           _nodeId, line_pa, ackNode,
           isUpgradePath ? "UPGRADE" : "INVALIDATE",
           isUpgradePath ? ost->upgradePendingAckCount : ost->pendingAckCount,
           effAckMask, effTargetMask, mesiStateName(entry.state),
           entry.sharersMask);

    _invalidationAckCount++;

    // Check if all invalidations are complete
    bool allAcksDone = isUpgradePath ? (ost->upgradePendingAckCount == 0)
                                     : (ost->pendingAckCount == 0);

    if (allAcksDone) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: all invalidations complete PA=0x%lx\n",
                _nodeId, line_pa);
        printf("[UBCC-INV-DONE] home=%d pa=0x%lx op=%s requester=%d "
               "intended=%s baseEpoch=%lu reservedEpoch=%lu reqId=%lu\n",
               _nodeId, line_pa,
               isUpgradePath ? "UPGRADE" : "INVALIDATE",
               ost->requesterNode, mesiStateName(ost->intendedState),
               ost->baseEpoch, ost->reservedEpoch, ost->reqId);

        if (isUpgradePath) {
            // upgrade_invalidate_fix D2: all acks in → now safe to Ack(true)
            ost->invalidateBarrierDone = true;
            ost->accepted = true;
            ost->stage = OpStage::WAITING_LOCAL_DONE;
            ost->respTick = curTick();

            printf("[UBCC-UPGRADE-ACK] pa=0x%lx requester=%d accepted=1 "
                   "ackMask=0x%lx targetMask=0x%lx\n",
                   line_pa, ost->requesterNode, ost->upgradeAckMask, ost->upgradeTargetMask);

            // Notify the requester that OuterUpgradeAck(true) is ready.
            // Route through the outbound interface (message-based) — the ubio
            // process wires this to UbioBackstoreHost which forwards to the
            // requester via the network/gem5 port. (Previously this used a
            // separate _router pointer that was never set in ubio_main, causing
            // a PANIC and leaving the upgrade pending forever → TC3/8/10/11
            // deadlock.)
            if (!_outbound) {
                fatal("UBCC node_id=%d: outbound required for UpgradeAckNotify "
                      "PA=0x%lx requester=%d\n",
                      _nodeId, line_pa, ost->requesterNode);
            }
            CoherenceMessage notifyMsg;
            notifyMsg.h.type = CoherenceMessageType::UpgradeAckNotify;
            notifyMsg.h.srcNode = _nodeId;
            notifyMsg.h.dstNode = ost->requesterNode;
            notifyMsg.h.homeNode = _nodeId;
            notifyMsg.h.requesterNode = ost->requesterNode;
            notifyMsg.h.homeLinePa = line_pa;
            notifyMsg.h.epoch = ost->reservedEpoch;
            notifyMsg.h.reqId = ost->reqId;
            notifyMsg.h.flags =
                static_cast<uint32_t>(cc::glob::CFLAG_ACCEPTED);
            notifyMsg.h.seqNum = 0;
            notifyMsg.h.enqueueTick = curTick();
            notifyMsg.h.readyTick = curTick();
            _outbound->sendUpgradeAckNotify(notifyMsg);

            // TENTATIVE: if Done arrived early (upgradeDoneArrived), auto-commit now
            if (ost->upgradeDoneArrived) {
                printf("[UPGRADE-TENTATIVE-DONE-CACHED] pa=0x%lx requester=%d "
                       "committing after acks complete (Done was cached)\n",
                       line_pa, ost->requesterNode);
                int intendedOwner = ost->intendedOwnerNode;
                uint64_t reservedEp = ost->reservedEpoch;
                commitIntendedResult(entry, *ost);
                _directory.update(line_pa, entry);
                ost->stage = OpStage::DONE;
                ost->respTick = curTick();
                removeOutstanding(line_pa);
                refreshPinnedBit(line_pa);

                printf("[UBCC-UPGRADE-COMMIT] pa=0x%lx owner=%d reservedEpoch=%lu\n",
                       line_pa, intendedOwner, reservedEp);

                // Replay queued requesters after commit
                replayPendingRequesters(line_pa);
                replayResidentWaiters(line_pa);
            }
        } else {
            // v4: Release invalidate barrier (INVALIDATE path)
            ost->invalidateBarrierDone = true;
            ost->stage = OpStage::DONE;
            ost->respTick = curTick();

            // v4: Create GRANT_HANDSHAKE for the intended result.
            // Convert the INVALIDATE outstanding in-place to GRANT_HANDSHAKE
            // to avoid the create-then-remove race on the same linePa key.
            ost->opType = OpType::GRANT_HANDSHAKE;
            ost->stage = OpStage::WAITING_CLEAR;
            ost->replayArmed = true;  // allow requester retry to match this grant
            // intendedState, intendedOwnerNode, intendedSharersMask, intendedDirty
            // are already set from when the INVALIDATE was created.
            ost->recallBarrierDone = false;
            ost->invalidateBarrierDone = true;  // INVALIDATE is now DONE

            // Push-grant: home proactively delivers ReadResp so requester's
            // retry hits _readyResponses immediately.
            if (_outbound) {
                CoherenceMessage push;
                buildGrantResponse(*ost, push);
                _outbound->sendGrantPush(push);
                printf("[PUSH-GRANT] INVALIDATE home=%d pa=0x%lx requester=%d "
                       "sock=%d reqId=%lu grantType=%d\n",
                       _nodeId, line_pa, ost->requesterNode,
                       ost->requesterSocket, ost->reqId,
                       static_cast<int>(grantTypeFromIntended(ost->intendedState)));
            }

            printf("[UBCC-INV-TO-GRANT] home=%d pa=0x%lx requester=%d stage=%d "
                   "intended=%s baseEpoch=%lu reservedEpoch=%lu reqId=%lu\n",
                   _nodeId, line_pa, ost->requesterNode,
                   static_cast<int>(ost->stage), mesiStateName(ost->intendedState),
                   ost->baseEpoch, ost->reservedEpoch, ost->reqId);
            appendTmpLog(
                "ubcc_inv_ack.log",
                "[INV-DONE] pa=0x%lx converting to GRANT_HANDSHAKE\n",
                line_pa);
        }
    }

    return true;
}

int
UBCCController::getPendingInvalidationCount(uint64_t line_pa) const
{
    auto oit = _outstandingReqs.find(line_pa);
    if (oit != _outstandingReqs.end() &&
        oit->second.opType == OpType::INVALIDATE)
        return oit->second.pendingAckCount;
    return -1;
}

uint64_t
UBCCController::getPendingInvalidationMask(uint64_t line_pa) const
{
    auto oit = _outstandingReqs.find(line_pa);
    if (oit != _outstandingReqs.end() &&
        oit->second.opType == OpType::INVALIDATE)
        return oit->second.totalMask & ~oit->second.ackMask;
    return 0;
}

uint64_t
UBCCController::getUpgradePendingTargetMask(uint64_t line_pa) const
{
    auto oit = _outstandingReqs.find(line_pa);
    if (oit != _outstandingReqs.end() &&
        oit->second.opType == OpType::UPGRADE_PENDING)
        return oit->second.upgradeTargetMask;
    return 0;
}

// ---- M7: Epoch / Stale Protection ----

bool
UBCCController::checkEpochForLine(uint64_t line_pa, uint64_t responseEpoch) const
{
    DirEntry entry;
    if (!_directory.lookup(line_pa, entry))
        return true; // No entry yet — accept (first miss creates entry)

    // v4: Half-range epoch comparison (§3.1.2).
    // Reject if responseEpoch is older than committed epoch.
    // Accept if responseEpoch >= committed epoch (within half-range).
    // This handles wrap-around correctly.
    if (isNewerEpoch(entry.epoch, responseEpoch)) {
        // committed epoch is newer than response → stale
        return false;
    }
    return true;
}

uint64_t
UBCCController::getEpochForLine(uint64_t line_pa) const
{
    DirEntry entry;
    if (!_directory.lookup(line_pa, entry))
        return 0;
    return normalizeEpoch(entry.epoch);
}

int
UBCCController::getOwnerForLine(uint64_t line_pa) const
{
    DirEntry entry;
    if (!_directory.lookup(line_pa, entry))
        return -1;
    return DirEntry::ownerFromSharers(entry);
}

uint64_t
UBCCController::getOutstandingBaseEpoch(uint64_t line_pa) const
{
    auto it = _outstandingReqs.find(line_pa);
    if (it == _outstandingReqs.end())
        return 0;
    return normalizeEpoch(it->second.baseEpoch);
}

// ---- M7: GlobalWriteback ----

bool
UBCCController::processWriteback(uint64_t line_pa, int requesterNode,
                                  uint64_t epochVal, bool keepAsClean)
{
    epochVal = normalizeEpoch(epochVal);

    framework::LogInfo("UBCC",
            "UBCC node_id=%d: processWriteback PA=0x%lx "
            "requesterNode=%d epoch=%lu keepAsClean=%d\n",
            _nodeId, line_pa, requesterNode, epochVal, keepAsClean);
    printf("[UBCC-WB-ENTER] home=%d pa=0x%lx node=%d keepAsClean=%d epoch=%lu\n",
           _nodeId, line_pa, requesterNode, keepAsClean, epochVal);

    DirEntry entry;
    ResidentAccessResult rr = ensureResidentForAccess(
        line_pa, UBCC_OuterReqType::GlobalWriteback, keepAsClean,
        requesterNode, -1, epochVal, 0, entry);
    if (rr != ResidentAccessResult::Ready) {
        return false;
    }

    // v4: Outstanding-aware BUSY check (§4.6.2)
    if (isLineBusy(line_pa)) {
        // TC98 fix: rate-limit writeback BUSY log
        { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processWriteback PA=0x%lx "
                "line busy (outstanding active) — BUSY/RETRY (n=%lu)\n",
                _nodeId, line_pa, _cnt); }
        return false;
    }

    // ---- M7: Stale epoch check ----
    if (!checkEpochForLine(line_pa, epochVal)) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processWriteback PA=0x%lx "
                "STALE epoch: msg=%lu directory=%lu — REJECTED\n",
                _nodeId, line_pa, epochVal, entry.epoch);
        _staleRejectedCount++;
        return false;
    }

    // ---- M7: Owner match check ----
    // Writeback must come from the current owner (or -1 if no entry).
    // Reject if the requesting node is not the current owner.
    int ownerNode = DirEntry::ownerFromSharers(entry);
    if (ownerNode >= 0 && ownerNode != requesterNode) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processWriteback PA=0x%lx "
                "OWNER MISMATCH: requesterNode=%d != ownerNode=%d — REJECTED\n",
                _nodeId, line_pa, requesterNode, ownerNode);
        _ownerMismatchRejectedCount++;
        return false;
    }

    // ---- M7: Process writeback ----
    // Dirty data is written back — clear dirty flag.
    // If keepAsClean, the owner retains exclusive clean ownership (G_E).
    // Otherwise, the owner drops the line entirely (G_I).
    if (keepAsClean && requesterNode >= 0) {
        // Owner writes back but retains clean exclusive
        entry.state = MESIState::G_E;
        entry.sharersMask = (1ULL << requesterNode);
    } else {
        // Owner drops the line completely
        entry.state = MESIState::G_I;
        entry.sharersMask = 0;
    }
    entry.residentDirty = true;
    // v4: DirEntry.pendingOp removed — no-op here

    _writebackCount++;

    framework::LogInfo("UBCC",
            "UBCC node_id=%d: processWriteback PA=0x%lx complete "
             "newState=%s ownerNode=%d dirty=%d\n",
             _nodeId, line_pa, mesiStateName(entry.state),
             DirEntry::ownerFromSharers(entry), DirEntry::protoDirty(entry));

    _directory.update(line_pa, entry);
    _directory.touch(line_pa);
    refreshPinnedBit(line_pa);
    // UBInvariant: validate canonical form after writeback
    validateSharersCanonical(line_pa);
    // v4-A3: Don't force-delete G_I — let ResidentDir eviction handle cleanup

    return true;
}

bool
UBCCController::processWritebackWithData(uint64_t line_pa, int requesterNode,
                                         uint64_t epochVal, bool keepAsClean,
                                         const uint8_t *data)
{
    bool success = processWriteback(line_pa, requesterNode, epochVal, keepAsClean);
    if (success) {
        if (data && _host) {
            _host->writeDsmData(line_pa, data);
            updateLineDataCache(line_pa, data);
            std::fprintf(stderr,
                         "[WB-DATA-PERSIST] home=%d pa=0x%lx node=%d source=resident\n",
                         _nodeId, line_pa, requesterNode);
            std::fflush(stderr);
        }
        return true;
    }

    if (!data) {
        return false;
    }

    auto it = _residentWaiters.find(line_pa);
    if (it == _residentWaiters.end()) {
        return false;
    }
    for (auto &pr : it->second) {
        if (pr.reqType == UBCC_OuterReqType::GlobalWriteback &&
            pr.node == requesterNode && pr.epoch == normalizeEpoch(epochVal)) {
            std::memcpy(pr.data.data(), data, 64);
            pr.hasData = true;
            std::fprintf(stderr,
                         "[WB-DATA-QUEUED] home=%d pa=0x%lx node=%d waiters=%zu\n",
                         _nodeId, line_pa, requesterNode, it->second.size());
            std::fflush(stderr);
            break;
        }
    }
    return false;
}

// ---- v4: Home Writeback Completion (HN-F→EP-SNF→DRAM) ----

void
UBCCController::notifyHomeWritebackComplete(uint64_t homePa)
{
    printf("[UBCC-HOME-WB] home=%d pa=0x%lx\n", _nodeId, homePa);
    DirEntry entry;
    if (!_directory.lookup(homePa, entry)) {
        return;
    }
    if (entry.state == MESIState::G_I) {
        return;
    }

    // Guard: if a new request is already in-flight for this PA,
    // the stale writeback notification must not overwrite the state.
    // The in-flight request will determine the correct final state.
    if (isLineBusy(homePa)) {
        // TC98 fix: rate-limit home-WB BUSY log
        { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
        printf("[UBCC-HOME-WB] home=%d pa=0x%lx BUSY — deferred (n=%lu)\n",
               _nodeId, homePa, _cnt); }
        return;
    }

    int oldOwner = DirEntry::ownerFromSharers(entry);
    printf("[UBCC-HOME-WB] home=%d pa=0x%lx oldState=%s owner=%d epoch=%lu\n",
           _nodeId, homePa, mesiStateName(entry.state), oldOwner, entry.epoch);

    entry.state = MESIState::G_I;
    entry.sharersMask = 0;
    entry.residentDirty = true;
    _writebackCount++;

    _directory.update(homePa, entry);
    _directory.touch(homePa);
    refreshPinnedBit(homePa);
    // UBInvariant: validate canonical form after home WB complete
    validateSharersCanonical(homePa);
}

// ---- M7: GlobalEvict (Clean Evict) ----

bool
UBCCController::processEvict(uint64_t line_pa, int evictingNode,
                              uint64_t epochVal)
{
    epochVal = normalizeEpoch(epochVal);

    framework::LogInfo("UBCC",
            "UBCC node_id=%d: processEvict PA=0x%lx "
            "evictingNode=%d epoch=%lu\n",
            _nodeId, line_pa, evictingNode, epochVal);

    DirEntry entry;
    ResidentAccessResult rr = ensureResidentForAccess(
        line_pa, UBCC_OuterReqType::GlobalEvict, false,
        evictingNode, -1, epochVal, 0, entry);
    if (rr != ResidentAccessResult::Ready) {
        return false;
    }

    // ---- M7: Stale epoch check ----
    if (!checkEpochForLine(line_pa, epochVal)) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processEvict PA=0x%lx "
                "STALE epoch: msg=%lu directory=%lu — REJECTED\n",
                _nodeId, line_pa, epochVal, entry.epoch);
        _staleRejectedCount++;
        return false;
    }

    // Phase 2: Line busy check unified to OutstandingRequest-aware
    if (isLineBusy(line_pa)) {
        // TC98 fix: rate-limit evict BUSY log
        { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processEvict PA=0x%lx "
                "line busy — rejected (n=%lu)\n",
                _nodeId, line_pa, _cnt); }
        return false;
    }

    // ---- M7: Process evict based on current state ----
    bool removedFromSharer = false;
    bool removedFromOwner = false;

    // Remove from sharer mask if present
    if (evictingNode >= 0) {
        uint64_t nodeBit = (1ULL << evictingNode);
        if (entry.sharersMask & nodeBit) {
            entry.sharersMask &= ~nodeBit;
            removedFromSharer = true;
        }
    }

    // If the evicting node is the current owner (clean owner, G_E),
    // clear ownership.
    int ownerNode = DirEntry::ownerFromSharers(entry);
    if (ownerNode >= 0 && ownerNode == evictingNode) {
        // Only clean owners (G_E) can evict without writeback.
        // Dirty owners (G_M) must writeback first.
        if (DirEntry::protoDirty(entry)) {
            framework::LogInfo("UBCC",
                    "UBCC node_id=%d: processEvict PA=0x%lx "
                    "dirty owner evict not allowed — must writeback first\n",
                    _nodeId, line_pa);
            return false;
        }
        entry.sharersMask = 0; // Exclusive owner has no sharers
        removedFromOwner = true;
    }

    // ---- M7 P0-3: Reject evict if node is neither owner nor sharer ----
    if (!removedFromSharer && !removedFromOwner) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processEvict PA=0x%lx "
                "evictingNode=%d is neither owner (ownerNode=%d) nor sharer "
                "(sharersMask=0x%lx) — REJECTED\n",
                _nodeId, line_pa, evictingNode,
                ownerNode, entry.sharersMask);
        return false;
    }

    // ---- Determine new state ----
    ownerNode = DirEntry::ownerFromSharers(entry);
    if (entry.sharersMask == 0 && ownerNode < 0) {
        // No sharers, no owner → G_I
        entry.state = MESIState::G_I;
    } else if (ownerNode >= 0) {
        // Exclusive owner remains (different from evicting node)
        // State stays G_E or G_M — unchanged
    } else {
        // Share-only line
        entry.state = MESIState::G_S;
    }

    // M7 P0-2: Only clear dirty if we removed a clean owner.
    // Sharer-only eviction must not touch dirty (owner's dirty state preserved).
    // Dirty owner eviction was already rejected above.
    (void)removedFromOwner;
    entry.residentDirty = true;
    _evictCount++;

    framework::LogInfo("UBCC",
            "UBCC node_id=%d: processEvict PA=0x%lx complete "
            "removedSharer=%d removedOwner=%d newState=%s "
             "sharersMask=0x%lx ownerNode=%d\n",
             _nodeId, line_pa, removedFromSharer, removedFromOwner,
             mesiStateName(entry.state),
             entry.sharersMask, DirEntry::ownerFromSharers(entry));

    _directory.update(line_pa, entry);
    _directory.touch(line_pa);
    refreshPinnedBit(line_pa);
    // UBInvariant: validate canonical form after evict
    validateSharersCanonical(line_pa);
    // v4-A3: Don't force-delete G_I — let ResidentDir eviction handle cleanup

    return true;
}

// ---- v4: Local Upgrade Management (§4.1.4) ----

bool
UBCCController::processOuterUpgradeReq(
    uint64_t line_pa, int requesterNode,
    uint64_t epoch, uint64_t reqId,
    int desiredPerm, UBCC_UpgradeCause cause,
    bool* outNotSharer)
{
    epoch = normalizeEpoch(epoch);
    if (outNotSharer)
        *outNotSharer = false;

    framework::LogInfo("UBCC",
            "UBCC node_id=%d: processOuterUpgradeReq PA=0x%lx "
            "requesterNode=%d epoch=%lu reqId=%lu desiredPerm=%d\n",
            _nodeId, line_pa, requesterNode, epoch, reqId, desiredPerm);

    DirEntry entry;
    ResidentAccessResult rr = ensureResidentForAccess(
        line_pa, UBCC_OuterReqType::GlobalReadUnique, true,
        requesterNode, -1, epoch, reqId, entry);
    if (rr != ResidentAccessResult::Ready) {
        return false;
    }

    // Check if requester is a committed sharer
    if (requesterNode >= 0) {
        uint64_t reqBit = (1ULL << requesterNode);
        if (!(entry.sharersMask & reqBit)) {
            framework::LogInfo("UBCC",
                    "UBCC node_id=%d: upgrade rejected — "
                    "requesterNode=%d not in sharersMask=0x%lx\n",
                    _nodeId, line_pa, requesterNode, entry.sharersMask);
            // PERMANENT reject: requester was invalidated (lost the race). It
            // must abandon and re-fetch via ReadUnique instead of retrying.
            if (outNotSharer)
                *outNotSharer = true;
            return false;
        }
    }

    // Check existing outstanding — if any, reject
    if (findOutstanding(line_pa)) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: upgrade rejected — "
                "existing outstanding for PA=0x%lx\n",
                _nodeId, line_pa);
        return false;
    }

    // v4: Allocate reserved epoch (committed epoch + 1)
    uint64_t reservedEpoch = allocateReservedEpoch(entry);

    // upgrade_invalidate_fix D3: freeze targetMask at acceptance time
    uint64_t reqBit = (1ULL << requesterNode);
    uint64_t targetMask = entry.sharersMask & ~reqBit;

    // Create UPGRADE_PENDING outstanding
    OutstandingRequest *oreq = createOutstanding(
        line_pa, OpType::UPGRADE_PENDING, requesterNode, -1);
    if (!oreq) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: upgrade rejected — "
                "failed to create outstanding for PA=0x%lx\n",
                _nodeId, line_pa);
        return false;
    }

    oreq->reservedEpoch = reservedEpoch;
    oreq->reqId = reqId;
    oreq->baseEpoch = epoch;
    oreq->upgradeCause = cause;

    // Determine intended state
    bool writeIntent = (desiredPerm == 1);  // Unique
    oreq->intendedState = writeIntent ? MESIState::G_M : MESIState::G_E;
    oreq->intendedOwnerNode = requesterNode;
    // upgrade_invalidate_fix: local upgrade commits to owner-only state.
    // All non-requester sharers in targetMask are being invalidated, and the
    // requester itself becomes the sole owner (G_E/G_M), so committed sharers
    // must be cleared instead of preserving the pre-upgrade snapshot.
    oreq->intendedSharersMask = 0;
    oreq->intendedDirty = writeIntent;

    // fix1 (home-owned invalidation fanout): the home that CREATES the
    // WAITING_ALL_ACKS outstanding MUST also emit the InvalidateReq to each
    // target sharer. Previously the upgrade path left the fanout to the
    // requester's EPBackend (EPBackend.cc notifyLocalWriteUpgrade), which in the
    // hot-line RS/RU + recall + batch-RS-replay interleaving could fail to fire
    // — leaving an orphan outstanding whose acks never arrive (WAITING_ALL_ACKS
    // forever) and blocking all later upgrades (TC98 deadlock at transfer #10).
    // Unifying with the INVALIDATE path guarantees "whoever creates the
    // outstanding owns the fanout".
    //
    // fix2 (send-time directory): fan out FIRST so we learn the effective (live)
    // sharer set, then size the ack accounting to exactly what we sent.
    uint64_t effectiveMask = targetMask;
    if (targetMask != 0) {
        fanoutInvalidateTargets(line_pa, targetMask, entry.epoch, reqId,
                                requesterNode,
                                UBCC_OuterReqType::GlobalReadUnique,
                                writeIntent, &effectiveMask);
    }

    if (effectiveMask != 0) {
        // upgrade_invalidate_fix D1/D2: other sharers exist → must invalidate first
        oreq->stage = OpStage::WAITING_ALL_ACKS;
        oreq->accepted = false;  // not yet ready to Ack(true)
        oreq->upgradeTargetMask = effectiveMask;
        oreq->totalMask = effectiveMask;
        oreq->upgradePendingAckCount = __builtin_popcountll(effectiveMask);
        oreq->upgradeAckMask = 0;
        oreq->invalidateBarrierDone = false;

        printf("[UBCC-UPGRADE] pa=0x%lx requester=%d stage=WAITING_ALL_ACKS "
               "targetMask=0x%lx pendingAckCount=%d\n",
               line_pa, requesterNode, effectiveMask, oreq->upgradePendingAckCount);

        framework::LogInfo("UBCC",
                "UBCC node_id=%d: upgrade accepted pending PA=0x%lx "
                "reservedEpoch=%lu reqId=%lu targetMask=0x%lx — "
                "waiting for invalidation acks before Ack(true)\n",
                _nodeId, line_pa, reservedEpoch, reqId, effectiveMask);
    } else {
        // upgrade_invalidate_fix: no other sharers — fast path
        oreq->stage = OpStage::WAITING_LOCAL_DONE;
        oreq->accepted = true;
        oreq->upgradeTargetMask = 0;
        oreq->upgradePendingAckCount = 0;
        oreq->upgradeAckMask = 0;

        printf("[UBCC-UPGRADE] pa=0x%lx requester=%d stage=WAITING_LOCAL_DONE "
               "targetMask=0 (no other sharers)\n",
               line_pa, requesterNode);

        framework::LogInfo("UBCC",
                "UBCC node_id=%d: upgrade accepted immediate PA=0x%lx "
                "reservedEpoch=%lu reqId=%lu — no other sharers, Ack(true) now\n",
                _nodeId, line_pa, reservedEpoch, reqId);
    }

    // v4: §4.1.4 step 2-3 — DirEntry NOT modified; committed stays as-is.
    // irrevocable-after-ack: once accepted, can only be DONE or PERSISTENT_BUSY.
    return true;
}

bool
UBCCController::processOuterUpgradeDone(
    uint64_t line_pa, int requesterNode,
    uint64_t epoch, uint64_t reqId)
{
    epoch = normalizeEpoch(epoch);

    framework::LogInfo("UBCC",
            "UBCC node_id=%d: processOuterUpgradeDone PA=0x%lx "
            "requesterNode=%d epoch=%lu reqId=%lu\n",
            _nodeId, line_pa, requesterNode, epoch, reqId);

    DirEntry entry;
    if (!_directory.lookup(line_pa, entry)) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processOuterUpgradeDone PA=0x%lx "
                "entry not found\n", _nodeId, line_pa);
        return false;
    }

    // Verify UPGRADE_PENDING outstanding
    OutstandingRequest *ost = findOutstanding(line_pa);
    if (!ost || ost->opType != OpType::UPGRADE_PENDING) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processOuterUpgradeDone PA=0x%lx "
                "no UPGRADE_PENDING outstanding\n", _nodeId, line_pa);
        return false;
    }

    // Verify matching tuple
    if (ost->requesterNode != requesterNode) {
        warn("UBCC node_id=%d: UpgradeDone requester mismatch PA=0x%lx\n",
             _nodeId, line_pa);
        return false;
    }

    // upgrade_invalidate_fix D4 (TENTATIVE): Done may arrive before acks complete
    if (ost->stage == OpStage::WAITING_ALL_ACKS) {
        // TENTATIVE: cache the Done tuple, do NOT commit yet
        ost->upgradeDoneArrived = true;
        ost->upgradeDoneEpoch = epoch;
        ost->upgradeDoneReqId = reqId;
        ost->upgradeSavedStage = ost->stage;

        printf("[UPGRADE-TENTATIVE-DONE-CACHED] pa=0x%lx requester=%d "
               "stage=WAITING_ALL_ACKS (Done arrived before all acks) "
               "cachedEpoch=%lu cachedReqId=%lu\n",
               line_pa, requesterNode, epoch, reqId);

        framework::LogInfo("UBCC",
                "UBCC node_id=%d: UpgradeDone TENTATIVE cached PA=0x%lx "
                "requester=%d — waiting for remaining acks\n",
                _nodeId, line_pa, requesterNode);

        return true; // accepted but not committed
    }

    if (ost->stage != OpStage::WAITING_LOCAL_DONE) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processOuterUpgradeDone PA=0x%lx "
                "wrong stage=%d — rejecting\n",
                _nodeId, line_pa, static_cast<int>(ost->stage));
        return false;
    }

    // upgrade_invalidate_fix: only commit when WAITING_LOCAL_DONE and accepted
    if (!ost->accepted) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: processOuterUpgradeDone PA=0x%lx "
                "not yet accepted — rejecting\n",
                _nodeId, line_pa);
        return false;
    }

    // v4: §4.1.4 step 5 — commit intended result to DirEntry
    int intendedOwner = ost->intendedOwnerNode;
    uint64_t reservedEp = ost->reservedEpoch;
    commitIntendedResult(entry, *ost);
    _directory.update(line_pa, entry);
    // UBInvariant: validate canonical form after commit
    validateSharersCanonical(line_pa);

    // Retire UPGRADE_PENDING
    ost->stage = OpStage::DONE;
    ost->respTick = curTick();
    removeOutstanding(line_pa);
    refreshPinnedBit(line_pa);

    printf("[UBCC-UPGRADE-COMMIT] pa=0x%lx owner=%d reservedEpoch=%lu\n",
           line_pa, intendedOwner, reservedEp);

    framework::LogInfo("UBCC",
            "UBCC node_id=%d: upgrade committed PA=0x%lx "
            "newState=%s owner=%d epoch=%lu\n",
            _nodeId, line_pa, mesiStateName(entry.state),
            DirEntry::ownerFromSharers(entry), entry.epoch);

    // Replay queued requesters after commit
    replayPendingRequesters(line_pa);
    replayResidentWaiters(line_pa);

    return true;
}

// ---- v4: Clear / ClearAck (§3.5) ----

bool
UBCCController::processClear(
    uint64_t line_pa, int srcNode,
    uint64_t epoch, uint64_t reqId)
{
    std::fprintf(stderr,
                 "[UBCC-CLEAR] enter home=%d pa=0x%lx srcNode=%d epoch=%lu reqId=%lu\n",
                 _nodeId, line_pa, srcNode, epoch, reqId);
    epoch = normalizeEpoch(epoch);
    appendTmpLog(
        "ubcc_clear.log",
        "[CLEAR] pa=0x%lx epoch=%lu reqId=%lu\n",
        line_pa, epoch, reqId);

    framework::LogInfo("UBCC",
            "UBCC node_id=%d: processClear PA=0x%lx "
            "srcNode=%d epoch=%lu reqId=%lu\n",
            _nodeId, line_pa, srcNode, epoch, reqId);

    // Check tombstone first (duplicate Clear within window W)
    bool tsAccepted = false;
    if (checkTombstone(line_pa, epoch, reqId, tsAccepted)) {
        // UBInvariant: log tombstone replay (warning-level)
        _tombstoneReplayCount++;
        framework::LogInfo("UBCC-invariant",
                "[UBINV-INFO] tombstone replay #%lu PA=0x%lx "
                "epoch=%lu reqId=%lu accepted=%d\n",
                _tombstoneReplayCount, line_pa, epoch, reqId, tsAccepted);
        framework::LogInfo("UBCC",
                "UBCC node_id=%d: tombstone replay PA=0x%lx "
                "epoch=%lu reqId=%lu accepted=%d\n",
                _nodeId, line_pa, epoch, reqId, tsAccepted);
        std::fprintf(stderr,
                     "[UBCC-CLEAR] tombstone-replay home=%d pa=0x%lx epoch=%lu reqId=%lu accepted=%d\n",
                     _nodeId, line_pa, epoch, reqId, tsAccepted ? 1 : 0);
        return tsAccepted;
    }

    DirEntry entry;
    if (!_directory.lookup(line_pa, entry)) {
        // Stale Clear for unknown line — log and drop (§3.5)
        std::fprintf(stderr,
                     "[UBCC-CLEAR] drop home=%d pa=0x%lx reason=unknown_line epoch=%lu reqId=%lu\n",
                     _nodeId, line_pa, epoch, reqId);
        warn("UBCC node_id=%d: stale Clear for unknown PA=0x%lx — dropped\n",
             _nodeId, line_pa);
        return false;
    }

    // Verify GRANT_HANDSHAKE outstanding
    OutstandingRequest *ost = findOutstanding(line_pa);
    printf("[TC5-CLEAR-TRACE] processClearEnter home=%d pa=0x%lx src=%d "
           "epoch=%lu reqId=%lu hasOutstanding=%d",
           _nodeId, line_pa, srcNode, epoch, reqId, ost ? 1 : 0);
    if (ost) {
        printf(" opType=%d stage=%d ostRequester=%d ostBase=%lu ostReserved=%lu ostReqId=%lu",
               static_cast<int>(ost->opType), static_cast<int>(ost->stage),
               ost->requesterNode, ost->baseEpoch, ost->reservedEpoch,
               ost->reqId);
    }
    printf("\n");

    if (!ost || ost->opType != OpType::GRANT_HANDSHAKE) {
        // No active GRANT_HANDSHAKE — check for already-completed
        // (might be tombstone already cleaned up)
        printf("[TC5-CLEAR-TRACE] processClearDrop home=%d pa=0x%lx src=%d "
               "reason=no_grant_handshake\n",
               _nodeId, line_pa, srcNode);
        std::fprintf(stderr,
                     "[UBCC-CLEAR] drop home=%d pa=0x%lx reason=no_grant_handshake epoch=%lu reqId=%lu\n",
                     _nodeId, line_pa, epoch, reqId);
        warn("UBCC node_id=%d: processClear PA=0x%lx "
             "no GRANT_HANDSHAKE outstanding — dropped\n",
             _nodeId, line_pa);
        return false;
    }

    // v4: Verify epoch — the Clear carries the base epoch observed by
    // requester; the GRANT_HANDSHAKE's reservedEpoch = baseEpoch + 1.
    // Compare against baseEpoch (not reservedEpoch) for matching.
    if (normalizeEpoch(ost->baseEpoch) != epoch) {
        printf("[TC5-CLEAR-TRACE] processClearDrop home=%d pa=0x%lx src=%d "
               "reason=epoch_mismatch ostBase=%lu clear=%lu\n",
               _nodeId, line_pa, srcNode,
               normalizeEpoch(ost->baseEpoch), epoch);
        std::fprintf(stderr,
                     "[UBCC-CLEAR] drop home=%d pa=0x%lx reason=epoch_mismatch ostBase=%lu clear=%lu reqId=%lu\n",
                     _nodeId, line_pa, normalizeEpoch(ost->baseEpoch), epoch, reqId);
        warn("UBCC node_id=%d: processClear PA=0x%lx "
              "epoch mismatch: ost_base=%lu clear=%lu — dropping, "
              "retiring stale GRANT_HANDSHAKE\n",
              _nodeId, line_pa, normalizeEpoch(ost->baseEpoch), epoch);
        // v4 D-18: Retire stale GRANT_HANDSHAKE so it doesn't block
        // future RECALL/INVALIDATE creation for this PA.
        retireToTombstone(*ost, false);
        removeOutstanding(line_pa);
        return false;
    }

    // Verify reqId match
    if (ost->reqId != reqId) {
        printf("[TC5-CLEAR-TRACE] processClearDrop home=%d pa=0x%lx src=%d "
               "reason=reqid_mismatch ostReqId=%lu clearReqId=%lu\n",
               _nodeId, line_pa, srcNode, ost->reqId, reqId);
        std::fprintf(stderr,
                     "[UBCC-CLEAR] drop home=%d pa=0x%lx reason=reqid_mismatch ostReqId=%lu clearReqId=%lu\n",
                     _nodeId, line_pa, ost->reqId, reqId);
        warn("UBCC node_id=%d: processClear PA=0x%lx "
             "reqId mismatch: ost=%lu clear=%lu — dropped\n",
             _nodeId, line_pa, ost->reqId, reqId);
        return false;
    }

    // F2: Strong validation — requesterNode must match srcNode
    if (ost->requesterNode >= 0 && ost->requesterNode != srcNode) {
        printf("[TC5-CLEAR-TRACE] processClearDrop home=%d pa=0x%lx src=%d "
               "reason=requester_mismatch ostRequester=%d\n",
               _nodeId, line_pa, srcNode, ost->requesterNode);
        std::fprintf(stderr,
                     "[UBCC-CLEAR] drop home=%d pa=0x%lx reason=requester_mismatch ostRequester=%d srcNode=%d reqId=%lu\n",
                     _nodeId, line_pa, ost->requesterNode, srcNode, reqId);
        warn("UBCC node_id=%d: processClear PA=0x%lx "
              "requesterNode mismatch: ost=%d clear=%d — dropped\n",
              _nodeId, line_pa, ost->requesterNode, srcNode);
        return false;
    }

    // F2: Stage must be WAITING_CLEAR — only accept Clear for an active
    // GRANT_HANDSHAKE that is actually expecting a Clear commit.
    if (ost->stage != OpStage::WAITING_CLEAR) {
        printf("[TC5-CLEAR-TRACE] processClearDrop home=%d pa=0x%lx src=%d "
               "reason=stage_mismatch stage=%d\n",
               _nodeId, line_pa, srcNode, static_cast<int>(ost->stage));
        std::fprintf(stderr,
                     "[UBCC-CLEAR] drop home=%d pa=0x%lx reason=stage_mismatch stage=%d reqId=%lu\n",
                     _nodeId, line_pa, static_cast<int>(ost->stage), reqId);
        warn("UBCC node_id=%d: processClear PA=0x%lx "
              "stage mismatch: expected WAITING_CLEAR got %d — dropped\n",
              _nodeId, line_pa, static_cast<int>(ost->stage));
        return false;
    }

    // v4: GRANT_HANDSHAKE existence + correct stage implies prerequisites DONE.
    // The upstream processOuterRequest / processInvalidationAck only creates
    // GRANT_HANDSHAKE after all barriers (RECALL/INVALIDATE) have completed.

    // v4: §3.3, §3.5 — commit intended result to committed DirEntry
    MESIState oldState = entry.state;
    commitIntendedResult(entry, *ost);
    _directory.update(line_pa, entry);
    // UBInvariant: validate canonical form after commit
    validateSharersCanonical(line_pa);

    // v4-latency: log COMMIT state change
    framework::LogInfo("UBCC-latency",
            "[UBST] tick=%lu home=%d,%d pa=0x%lx old=%s new=%s epoch=%lu sharers=0x%lx action=COMMIT\n",
            curTick(), _nodeId, _socketId, line_pa,
            mesiStateName(oldState),
            mesiStateName(entry.state),
            entry.epoch,
            entry.sharersMask);

    // Retire GRANT_HANDSHAKE to tombstone(W) for duplicate Clear replay
    retireToTombstone(*ost, true);
    removeOutstanding(line_pa);
    refreshPinnedBit(line_pa);

    // recall_done_fix.md §5: Replay queued pending requesters using the
    // newly committed state (just committed by this Clear).
    replayPendingRequesters(line_pa);
    replayResidentWaiters(line_pa);

    // Order log audit (§3.6)
    printf("[TC5-CLEAR-TRACE] processClearAccept home=%d pa=0x%lx src=%d "
           "epoch=%lu reqId=%lu newState=%s\n",
           _nodeId, line_pa, srcNode, epoch, reqId,
           mesiStateName(entry.state));
    printf("[UBCC-ORDER] pa=0x%lx epoch=%lu reqId=%lu op=ClearGrantHandshake "
           "requester=%d state=%s\n",
           line_pa, epoch, reqId, srcNode,
           mesiStateName(entry.state));
    std::fprintf(stderr,
                 "[UBCC-CLEAR] accept home=%d pa=0x%lx srcNode=%d epoch=%lu reqId=%lu newState=%s\n",
                 _nodeId, line_pa, srcNode, epoch, reqId,
                 mesiStateName(entry.state));

    return true;
}

bool
UBCCController::copyOutstandingGrantData(uint64_t line_pa, DataBlock &outBlk) const
{
    auto it = _outstandingReqs.find(line_pa);
    if (it == _outstandingReqs.end()) {
        return false;
    }

    const OutstandingRequest &ost = it->second;
    if (!ost.dataValid) {
        return false;
    }

    if (ost.opType != OpType::GRANT_HANDSHAKE &&
        ost.opType != OpType::RECALL) {
        return false;
    }

    outBlk.setData(ost.dataBuf, 0, 64);
    return true;
}

bool
UBCCController::copyImmediateGrantData(uint64_t line_pa, DataBlock &outBlk)
{
    auto it = _immediateGrantData.find(line_pa);
    if (it == _immediateGrantData.end()) {
        return false;
    }
    outBlk.setData(it->second.dataBuf, 0, 64);
    _immediateGrantData.erase(it);
    return true;
}

// ---- v4: Private helpers ----

// Half-range epoch comparison (§3.1.2)
bool
UBCCController::isNewerEpoch(uint64_t a, uint64_t b) const
{
    const uint64_t mask = epochMask();
    const uint64_t delta = (normalizeEpoch(a) - normalizeEpoch(b)) & mask;
    const uint64_t half_range = (_epochBits == 64)
        ? (1ULL << 63)
        : (1ULL << (_epochBits - 1));
    return delta != 0 && delta < half_range;
}

uint64_t
UBCCController::normalizeEpoch(uint64_t epoch) const
{
    return epoch & epochMask();
}

uint64_t
UBCCController::epochMask() const
{
    if (_epochBits >= 64) {
        return 0xffffffffffffffffULL;
    }
    return (1ULL << _epochBits) - 1;
}

uint64_t
UBCCController::allocateReservedEpoch(DirEntry &entry)
{
    // reservedEpoch = committed epoch + 1; committed epoch is NOT modified here
    return normalizeEpoch(entry.epoch + 1);
}

void
UBCCController::commitIntendedResult(DirEntry &entry, const OutstandingRequest &ost)
{
    // UBInvariant: warn on double-commit (per-PA counter)
    int &cnt = _commitCount[ost.linePa];
    cnt++;
    if (cnt > 1) {
        _invariantWarnCount++;
        framework::LogInfo("UBCC-invariant",
                "[UBINV-WARN] double-commit #%u PA=0x%lx cnt=%d\n",
                _invariantWarnCount, ost.linePa, cnt);
        warn("[UBINV] double-commit PA=0x%lx cnt=%d (warn #%u)\n",
             ost.linePa, cnt, _invariantWarnCount);
    }

    // UBInvariant: epoch monotonicity check before overwriting entry.epoch
    validateEpochMonotonic(entry.epoch, ost.reservedEpoch, ost.linePa);

    entry.state = ost.intendedState;
    if (ost.intendedState == MESIState::G_E ||
        ost.intendedState == MESIState::G_M) {
        uint64_t mask = ost.intendedSharersMask;
        if (__builtin_popcountll(mask) != 1 && ost.intendedOwnerNode >= 0) {
            mask = (1ULL << ost.intendedOwnerNode);
        }
        entry.sharersMask = mask;
    } else {
        entry.sharersMask = ost.intendedSharersMask;
    }
    entry.epoch = normalizeEpoch(ost.reservedEpoch);
    entry.residentDirty = true;

    // Persist recall-sourced payload at home so later shared grants from G_S
    // can return real line data in split-mode (instead of stale HomeMemory).
    if (ost.dataValid) {
        std::array<uint8_t, 64> cached{};
        memcpy(cached.data(), ost.dataBuf, 64);
        _lineDataCache[ost.linePa] = cached;
        if (_host) _host->writeDsmData(ost.linePa, ost.dataBuf);
    } else if (entry.state == MESIState::G_I) {
        _lineDataCache.erase(ost.linePa);
    }

    panic_if((entry.state == MESIState::G_E || entry.state == MESIState::G_M) &&
             __builtin_popcountll(entry.sharersMask) != 1,
             "UBCC canonical assert failed PA=0x%lx state=%d sharers=0x%lx",
             ost.linePa, static_cast<int>(entry.state), entry.sharersMask);

    framework::LogInfo("UBCC",
            "UBCC node_id=%d: commitIntendedResult PA=0x%lx "
            "state=%s owner=%d sharers=0x%lx dirty=%d epoch=%lu\n",
            _nodeId, ost.linePa,
            mesiStateName(entry.state), DirEntry::ownerFromSharers(entry),
            entry.sharersMask, DirEntry::protoDirty(entry), entry.epoch);

    if (entry.state != MESIState::G_I) {
        _directory.bloomInsert(ost.linePa);
    }
    // v4-A3: Don't force-delete G_I — let ResidentDir eviction handle cleanup
}

void
UBCCController::retireToTombstone(const OutstandingRequest &ost, bool accepted)
{
    GrantHandshakeTombstone ts;
    ts.linePa = ost.linePa;
    ts.epoch = normalizeEpoch(ost.baseEpoch);
    ts.reqId = ost.reqId;
    ts.opType = OpType::GRANT_HANDSHAKE;
    ts.accepted = accepted;
    ts.expireTick = curTick() + _tombstoneWindowW;
    // §7.4 / recall_done_fix.md: per-PA multi-entry deque so queued replay
    // doesn't clobber earlier tombstones within window W.
    _tombstones[ost.linePa].push_back(ts);

    framework::LogInfo("UBCC",
            "UBCC node_id=%d: retireToTombstone PA=0x%lx "
            "baseEpoch=%lu reservedEpoch=%lu reqId=%lu expireTick=%lu depth=%zu\n",
            _nodeId, ost.linePa, ost.baseEpoch, ost.reservedEpoch, ost.reqId,
            ts.expireTick,
            _tombstones[ost.linePa].size());

    // v4-latency: log RETIRE state change
    framework::LogInfo("UBCC-latency",
            "[UBST] tick=%lu home=%d,%d pa=0x%lx old=%s new=%s epoch=%lu sharers=0x%lx action=RETIRE\n",
            curTick(), _nodeId, _socketId, ost.linePa,
            mesiStateName(ost.intendedState),
            "Tombstone",
            ost.baseEpoch,
            ost.intendedSharersMask);
}

bool
UBCCController::checkTombstone(uint64_t linePa, uint64_t epoch, uint64_t reqId,
                                 bool &outAccepted)
{
    epoch = normalizeEpoch(epoch);
    cleanupTombstones();
    auto it = _tombstones.find(linePa);
    if (it == _tombstones.end())
        return false;

    // §7.4 / recall_done_fix.md: scan the deque for matching (epoch, reqId)
    for (auto &ts : it->second) {
        if (ts.epoch == epoch && ts.reqId == reqId) {
            outAccepted = ts.accepted;
            framework::LogInfo("UBCC",
                    "UBCC node_id=%d: checkTombstone HIT PA=0x%lx "
                    "epoch=%lu reqId=%lu accepted=%d\n",
                    _nodeId, linePa, epoch, reqId, ts.accepted);
            return true;
        }
    }
    return false;
}

void
UBCCController::cleanupTombstones()
{
    Tick now = curTick();
    for (auto it = _tombstones.begin(); it != _tombstones.end(); ) {
        auto &deq = it->second;
        // Remove expired entries from the front (FIFO push order)
        while (!deq.empty() && deq.front().expireTick <= now) {
            framework::LogInfo("UBCC",
                    "UBCC node_id=%d: tombstone expired PA=0x%lx "
                    "epoch=%lu reqId=%lu\n",
                    _nodeId, deq.front().linePa,
                    deq.front().epoch, deq.front().reqId);
            deq.pop_front();
        }
        if (deq.empty()) {
            it = _tombstones.erase(it);
        } else {
            ++it;
        }
    }
}

// ---- Recall orphan cleanup (v4) ----

bool
UBCCController::isExpiredRecall(const OutstandingRequest &ost) const
{
    if (ost.opType != OpType::RECALL)
        return false;
    if (ost.stage != OpStage::WAITING_TARGET_RESP &&
        ost.stage != OpStage::DONE)
        return false;
    return curTick() > ost.createTick + _recallTimeout;
}

bool
UBCCController::cleanupExpiredRecallIfNeeded(uint64_t linePa,
                                             bool replayWaiters)
{
    OutstandingRequest *ost = findOutstanding(linePa);
    if (!ost || !isExpiredRecall(*ost))
        return false;

    framework::LogInfo("UBCC",
            "UBCC node_id=%d: expired RECALL cleanup PA=0x%lx stage=%d "
            "age=%lu replayWaiters=%d\n",
            _nodeId, linePa, static_cast<int>(ost->stage),
            curTick() - ost->createTick, replayWaiters ? 1 : 0);

    removeOutstanding(linePa);

    if (replayWaiters)
        replayPendingRequesters(linePa);
    return true;
}

void
UBCCController::cleanupExpiredRecalls()
{
    std::vector<uint64_t> expired;
    for (const auto &kv : _outstandingReqs) {
        if (isExpiredRecall(kv.second))
            expired.push_back(kv.first);
    }
    for (uint64_t linePa : expired)
        cleanupExpiredRecallIfNeeded(linePa, true);
}

bool
UBCCController::snapshotResidentForBackstore(
    uint64_t linePa, BackstoreEntry &entry) const
{
    DirEntry e;
    if (!_directory.lookup(linePa, e)) {
        return false;
    }
    entry.state = e.state;
    entry.sharersMask = e.sharersMask;
    entry.epoch = e.epoch;
    return true;
}

void
UBCCController::onBackstoreFillComplete(
    uint64_t linePa, bool found, const BackstoreEntry &entry)
{
    fprintf(stderr, "[RESIDENT-FILL-DONE] home=%d pa=0x%lx found=%d waiters=%zu\n",
           _nodeId, linePa, found ? 1 : 0,
           _residentWaiters.count(linePa) ? _residentWaiters[linePa].size() : 0);
    fflush(stderr);
    DirEntry e;
    if (!_directory.lookup(linePa, e)) {
        e.lineAddr = linePa;
        e.state = MESIState::G_I;
        e.sharersMask = 0;
        e.epoch = 0;
        e.residentDirty = false;
        _directory.insert(linePa, e);
    }

    if (found) {
        e.state = entry.state;
        e.sharersMask = entry.sharersMask;
        e.epoch = entry.epoch;
        e.residentDirty = false;
    } else {
        e.state = MESIState::G_I;
        e.sharersMask = 0;
        e.residentDirty = false;
    }
    appendTmpLog(
        "ubcc_fill_complete.log",
        "[FILL-COMPLETE] pa=0x%lx found=%d state=%d sharers=0x%lx\n",
        linePa, found ? 1 : 0, static_cast<int>(e.state), e.sharersMask);
    printf("[UBCC-FILL-DONE] home=%d pa=0x%lx found=%d state=%s sharers=0x%lx "
           "epoch=%lu\n",
           _nodeId, linePa, found ? 1 : 0, mesiStateName(e.state),
           e.sharersMask, e.epoch);
    _directory.update(linePa, e);
    _directory.setFillPending(linePa, false);
    _directory.touch(linePa);
    refreshPinnedBit(linePa);
    replayResidentWaiters(linePa);
}

void
UBCCController::onBackstoreWriteAck(uint64_t linePa)
{
    fprintf(stderr, "[RESIDENT-WB-ACK] home=%d pa=0x%lx evictionPending=%d async=%d\n",
           _nodeId, linePa,
           _evictionPendingRemoval.count(linePa) ? 1 : 0,
           _asyncWbSnapshots.count(linePa) ? 1 : 0);
    fflush(stderr);
    // Check if this was an async writeback (not an eviction writeback)
    if (_asyncWbSnapshots.count(linePa) > 0) {
        onAsyncWritebackAck(linePa);
        return;
    }

    DirEntry e;
    if (!_directory.lookup(linePa, e)) {
        return;
    }
    if (e.state != MESIState::G_I) {
        _directory.bloomInsert(linePa);
    }
    e.residentDirty = false;
    _directory.update(linePa, e);
    _directory.setWbPending(linePa, false);

    if (_evictionPendingRemoval.erase(linePa) != 0) {
        _directory.forceRemove(linePa);
        replayResidentWaitersForCapacity();
    }
    refreshPinnedBit(linePa);
    replayResidentWaiters(linePa);
}

void
UBCCController::onBackstoreDeleteAck(uint64_t linePa, bool existed)
{
    _directory.bloomRemove(linePa);

    DirEntry e;
    if (_directory.lookup(linePa, e)) {
        _directory.setFillPending(linePa, false);
        _directory.setWbPending(linePa, false);
        if (e.state == MESIState::G_I) {
            _directory.forceRemove(linePa);
        } else {
            e.residentDirty = false;
            _directory.update(linePa, e);
        }
    }
    _evictionPendingRemoval.erase(linePa);
    refreshPinnedBit(linePa);
    replayResidentWaiters(linePa);
    replayResidentWaitersForCapacity();
    (void)existed;
}

std::string
UBCCController::inspectOffloadLineForTest(uint64_t linePa) const
{
    DirEntry e;
    bool present = _directory.lookup(linePa, e);
    auto wit = _residentWaiters.find(linePa);
    size_t waiterDepth = (wit == _residentWaiters.end()) ? 0 : wit->second.size();

    std::ostringstream oss;
    oss << "{";
    oss << "\"resident_present\":" << (present ? "true" : "false") << ",";
    oss << "\"resident_state\":" << (present ? static_cast<int>(e.state) : -1) << ",";
    oss << "\"resident_sharers_mask\":" << (present ? e.sharersMask : 0) << ",";
    oss << "\"resident_epoch\":" << (present ? e.epoch : 0) << ",";
    oss << "\"resident_dirty\":" << (present && e.residentDirty ? "true" : "false") << ",";
    oss << "\"bf_positive\":" << (_directory.bloomMayContain(linePa) ? "true" : "false") << ",";
    oss << "\"fill_pending\":" << (_directory.fillPending(linePa) ? "true" : "false") << ",";
    oss << "\"wb_pending\":" << (_directory.wbPending(linePa) ? "true" : "false") << ",";
    oss << "\"pinned\":" << (_directory.pinned(linePa) ? "true" : "false") << ",";
    oss << "\"resident_waiter_depth\":" << waiterDepth;
    oss << "}";
    return oss.str();
}

bool
UBCCController::debugSeedBackstoreForTest(
    uint64_t linePa, int mesi, uint64_t sharersMask, uint64_t epoch)
{
    if (mesi < 0 || mesi > 3) {
        return false;
    }
    _directory.bloomInsert(linePa);
    return true;
}

bool
UBCCController::debugSeedResidentForTest(
    uint64_t linePa, int mesi, uint64_t sharersMask, uint64_t epoch,
    bool residentDirty)
{
    if (mesi < 0 || mesi > 3) {
        return false;
    }
    DirEntry e;
    e.lineAddr = linePa;
    e.state = static_cast<MESIState>(mesi);
    e.sharersMask = sharersMask;
    e.epoch = epoch;
    e.residentDirty = residentDirty;
    if (!_directory.lookup(linePa, e)) {
        _directory.insert(linePa, e);
    }
    _directory.update(linePa, e);
    _directory.touch(linePa);
    if (e.state != MESIState::G_I) {
        _directory.bloomInsert(linePa);
    }
    refreshPinnedBit(linePa);
    return true;
}

bool
UBCCController::debugForceResidentEvictForTest(uint64_t linePa)
{
    DirEntry e;
    if (!_directory.lookup(linePa, e)) {
        return false;
    }
    _directory.setPinned(linePa, false);
    if (!e.residentDirty) {
        return _directory.forceRemove(linePa);
    }
    _directory.setWbPending(linePa, true);
    _directory.setPinned(linePa, true);
    _evictionPendingRemoval.insert(linePa);
    if (e.state == MESIState::G_I) {
        scheduleBackstoreDelete(linePa);
    } else {
        scheduleBackstoreWrite(linePa);
    }
    return true;
}

// ---- recall_done_fix.md §5: Replay queued pending requesters ----
void
UBCCController::replayPendingRequesters(uint64_t linePa)
{
    auto qit = _pendingRequesters.find(linePa);
    if (qit == _pendingRequesters.end() || qit->second.empty())
        return;

    // Get current committed entry (just committed by Clear or UpgradeDone)
    DirEntry entry;
    if (!_directory.lookup(linePa, entry))
        return;

    // Replay all queued entries one by one, each as a fresh processOuterRequest
    // with rebased epoch against the NEW committed state.
    while (!qit->second.empty()) {
        PendingRequester pr = qit->second.front();
        qit->second.pop_front();

        // §5.2: Rebase epoch to newly committed epoch (the Clear just advanced it).
        // upgrade_invalidate_fix D5: this rebaseEpoch becomes the baseEpoch
        // in the new GRANT_HANDSHAKE. The requester's subsequent Clear must
        // use THIS baseEpoch (from the grant envelope/GRANT_HANDSHAKE context),
        // NOT its own stale local entry.epoch. The EPBackend-side fix in
        // handleRemoteMiss/sendClear ensures this by reading getOutstandingBaseEpoch().
        uint64_t rebaseEpoch = entry.epoch;

        printf("[UBCC-QUEUE-REPLAY] pa=0x%lx requester=%d reqType=%s "
               "writeIntent=%d reqId=%lu originalEpoch=%lu rebaseEpoch=%lu "
               "committedState=%s\n",
               linePa, pr.node,
               (pr.reqType == UBCC_OuterReqType::GlobalReadShared) ? "RS" : "RU",
               pr.writeIntent, pr.reqId, pr.epoch, rebaseEpoch,
               mesiStateName(entry.state));

        // ── C3 Batch RS path: G_S + RS → direct grant without outstanding ──
        if (_batchRsEnabled &&
            entry.state == MESIState::G_S &&
            pr.reqType == UBCC_OuterReqType::GlobalReadShared) {

            printf("[UBCC-QUEUE-REPLAY-BATCH] pa=0x%lx requester=%d "
                   "reqType=RS rebaseEpoch=%lu committedState=%s\n",
                   linePa, pr.node, rebaseEpoch, mesiStateName(entry.state));

            // Build a temporary outstanding for commit/tombstone/grant construction
            OutstandingRequest tempOst;
            tempOst.linePa = linePa;
            tempOst.baseEpoch = rebaseEpoch;
            tempOst.reservedEpoch = allocateReservedEpoch(entry);
            tempOst.reqId = pr.reqId;
            tempOst.opType = OpType::GRANT_HANDSHAKE;
            tempOst.stage = OpStage::WAITING_CLEAR;
            tempOst.requesterNode = pr.node;
            tempOst.requesterSocket = pr.socket;
            tempOst.intendedState = MESIState::G_S;
            tempOst.intendedSharersMask = entry.sharersMask | (1ULL << pr.node);
            tempOst.intendedOwnerNode = -1;
            tempOst.intendedDirty = false;
            tempOst.reqType = pr.reqType;
            tempOst.writeIntent = false;
            tempOst.replayArmed = true;

            // Data from _lineDataCache (recall-sourced), or HomeMemory
            auto cacheIt = _lineDataCache.find(linePa);
            if (cacheIt != _lineDataCache.end()) {
                tempOst.dataValid = true;
                memcpy(tempOst.dataBuf, cacheIt->second.data(), 64);
                tempOst.dataSource = GrantDataSource::RecallBuffer;
            } else {
                tempOst.dataSource = GrantDataSource::HomeMemory;
            }

            // Commit directly (no Clear needed for shared)
            commitIntendedResult(entry, tempOst);
            _directory.update(linePa, entry);
            validateSharersCanonical(linePa);
            retireToTombstone(tempOst, true);
            refreshPinnedBit(linePa);

            // Push-grant to requester
            if (_outbound) {
                CoherenceMessage push;
                buildGrantResponse(tempOst, push);
                _outbound->sendGrantPush(push);
                printf("[PUSH-GRANT] BATCH-RS home=%d pa=0x%lx "
                       "requester=%d sock=%d grantType=%d\n",
                       _nodeId, linePa, pr.node, pr.socket,
                       static_cast<int>(grantTypeFromIntended(tempOst.intendedState)));
            }

            continue;  // skip processOuterRequest, continue to next queued entry
        }
        // ── End C3 Batch RS ──

        // Replay as fresh processOuterRequest — it sees the NEW committed state.
        // The outcome depends on the current committed state:
        //   G_S + RS → direct GRANT_HANDSHAKE (no recall/invalidate)   -- handled above
        //   G_S + RU → INVALIDATE + GRANT_HANDSHAKE
        //   G_E/G_M + RS/RU → new RECALL + GRANT_HANDSHAKE
        processOuterRequest(linePa, pr.reqType, pr.writeIntent,
                            pr.node, pr.socket, rebaseEpoch, pr.reqId,
                            nullptr, nullptr, nullptr, nullptr, nullptr,
                            nullptr);

        // If the replay created a new live outstanding, stop here.
        // The remainder of the queue will be replayed when that outstanding's
        // Clear commits (chained replay).
        if (findOutstanding(linePa)) {
            // Live outstanding created — break, remaining queue stays
            // F24: Mark the grant as replay-armed so the requester's
            // retry can hit it directly instead of being marked dup_retry.
            OutstandingRequest *ost = findOutstanding(linePa);
            if (ost) {
                ost->replayArmed = true;

                // Push-grant: the queue replay may have created a direct
                // GRANT_HANDSHAKE (G_S+RS case). Push the grant immediately
                // so the requester gets it without waiting for retry timer.
                if (ost->opType == OpType::GRANT_HANDSHAKE && _outbound) {
                    CoherenceMessage push;
                    buildGrantResponse(*ost, push);
                    _outbound->sendGrantPush(push);
                    printf("[PUSH-GRANT] QUEUE-REPLAY home=%d pa=0x%lx "
                           "requester=%d sock=%d reqId=%lu grantType=%d\n",
                           _nodeId, linePa, ost->requesterNode,
                           ost->requesterSocket, ost->reqId,
                           static_cast<int>(
                               grantTypeFromIntended(ost->intendedState)));
                }
            }
            break;
        }
        // No outstanding created (e.g., immediate grant that returned BUSY
        // because the entry was already enqueued elsewhere) — continue to
        // next queued entry.
    }

    // Clean up empty queue to avoid stale entries
    if (qit->second.empty()) {
        _pendingRequesters.erase(qit);
    }
}


// ---- v4: Home UBCC direct fanout ----

bool
UBCCController::fanoutInvalidateTargets(uint64_t linePa, uint64_t targetMask,
                                        uint64_t committedEpoch, uint64_t reqId,
                                        int requesterNode,
                                        UBCC_OuterReqType reqType, bool writeIntent,
                                        uint64_t *outEffectiveMask)
{
    if (!_outbound) {
        warn("UBCC node_id=%d: fanoutInvalidateTargets called with no outbound sender\n",
             _nodeId);
        return false;
    }

    const uint64_t offset = _addrMap.dsmOffset(linePa);

    // fix2 (send-time directory): recompute the effective target set from the
    // CURRENT committed directory sharers, not the mask captured earlier. A
    // sharer that was recalled/evicted between outstanding-creation and this
    // fanout is no longer a real sharer; sending it an InvalidateReq would only
    // rely on the requester-side "no local copy → immediate ack" path. Dropping
    // it here keeps the InvalidateReq set aligned with the live directory. The
    // caller must set pendingAckCount from *outEffectiveMask (see below) so the
    // ack accounting matches exactly what we send.
    uint64_t effectiveMask = targetMask;
    {
        DirEntry curEntry;
        if (_directory.lookup(linePa, curEntry)) {
            uint64_t liveSharers = curEntry.sharersMask;
            uint64_t dropped = targetMask & ~liveSharers;
            if (dropped) {
                printf("[UBCC-FANOUT-STALE] home=%d pa=0x%lx requested=0x%lx "
                       "liveSharers=0x%lx dropped=0x%lx (not current sharers)\n",
                       _nodeId, linePa, targetMask, liveSharers, dropped);
            }
            effectiveMask = targetMask & liveSharers;
        }
    }
    if (outEffectiveMask)
        *outEffectiveMask = effectiveMask;

    uint64_t remaining = effectiveMask;

    while (remaining) {
        int target = __builtin_ctzll(remaining);
        remaining &= (remaining - 1);

        CoherenceMessage msg;
        msg.h.type = CoherenceMessageType::InvalidateReq;
        msg.h.srcNode = _nodeId;
        msg.h.srcSocket = _socketId;
        msg.h.dstNode = target;
        msg.h.dstSocket = _socketId;
        msg.h.homeNode = _nodeId;
        msg.h.homeSocket = _socketId;
        msg.h.ingressSocket = _socketId;
        msg.h.requesterNode = requesterNode;
        msg.h.targetNode = target;
        msg.h.homeLinePa = linePa;
        // Compute the target sharer's LOCAL view of this home line using the
        // same layout gem5 uses (NodeAddressMap::buildDsmPA), so the sharer's
        // EPBackend can match it against its _requesterLines key and L1 line.
        // The old formula (target*_dsmSegSize + offset) omitted nodeBase(target)
        // and the home-segment offset, producing e.g. 0x700000 instead of
        // 0x20700000 — the invalidation then missed the requester's bookkeeping,
        // leaving a stale R_S entry so the next read returned stale data (TC23/41).
        msg.h.localLinePa = _addrMap.buildDsmPA(target, _nodeId, offset, _socketId);
        msg.h.epoch = committedEpoch;
        msg.h.reqId = reqId;
        msg.h.seqNum = 0;

        printf("[UBCC-FANOUT] home=%d pa=0x%lx target=%d epoch=%lu reqId=%lu\n",
               _nodeId, linePa, target, committedEpoch, reqId);

        if (!_outbound->sendInvalidateReq(msg)) {
            warn("UBCC node_id=%d: invalidate fanout failed target=%d\n",
                 _nodeId, target);
            return false;
        }
    }

    return true;
}

// ---- v4: Outstanding request API ----
OutstandingRequest*
UBCCController::findOutstanding(uint64_t linePa)
{
    auto it = _outstandingReqs.find(linePa);
    if (it != _outstandingReqs.end())
        return &it->second;
    return nullptr;
}

OutstandingRequest*
UBCCController::createOutstanding(uint64_t linePa, OpType opType,
                                  int requesterNode, int targetNode,
                                  int requesterSocket)
{
    // v4: Keep single outstanding per line
    if (_outstandingReqs.count(linePa))
        return nullptr;
    OutstandingRequest req;
    req.linePa = linePa;
    req.baseEpoch = getEpochForLine(linePa);
    req.reservedEpoch = 0;   // filled in by caller
    req.reqId = 0;           // filled in by caller
    req.opType = opType;
    req.stage = OpStage::CREATED;
    req.requesterNode = requesterNode;
    req.requesterSocket = requesterSocket;
    req.targetNode = targetNode;
    req.targetMask = 0;
    req.intendedState = MESIState::G_I;
    req.intendedSharersMask = 0;
    req.intendedOwnerNode = -1;
    req.intendedDirty = false;
    req.recallBarrierDone = false;
    req.invalidateBarrierDone = false;
    req.createTick = curTick();
    req.respTick = 0;
    req.deadlineTick = curTick() + _interconnectLatency * 10;
    req.accepted = false;
    req.dataValid = false;
    req.dataSource = GrantDataSource::HomeMemory;  // F3: default
    req.pendingAckCount = 0;
    req.ackMask = 0;
    req.totalMask = 0;
    _outstandingReqs[linePa] = req;
    return &_outstandingReqs[linePa];
}

void
UBCCController::removeOutstanding(uint64_t linePa)
{
    _outstandingReqs.erase(linePa);
}

// ---- Push-Grant: build a complete ReadResp from GRANT_HANDSHAKE outstanding ----

void
UBCCController::buildGrantResponse(const OutstandingRequest &grantOst,
                                    CoherenceMessage &push) const
{
    // Construct a complete ReadResp using fields stored in the grantOst.
    // Aligns with pull-path ReadResp construction in ubio_main.cc:408-424.
    // Differences: field sources come from grantOst, not an inbound msg.

    push.h.type = CoherenceMessageType::ReadResp;
    push.h.srcNode = _nodeId;
    push.h.dstNode = grantOst.requesterNode;
    push.h.dstSocket = grantOst.requesterSocket >= 0
        ? static_cast<uint16_t>(grantOst.requesterSocket)
        : static_cast<uint16_t>(_socketId);  // fallback: use home socket
    push.h.homeNode = _nodeId;
    push.h.requesterNode = grantOst.requesterNode;
    push.h.homeLinePa = grantOst.linePa;
    push.h.epoch = grantOst.baseEpoch;
    push.h.reqId = grantOst.reqId;
    auto cachedData = _lineDataCache.end();
    if (!grantOst.dataValid) {
        cachedData = _lineDataCache.find(grantOst.linePa);
    }
    const bool hasGrantData = grantOst.dataValid || cachedData != _lineDataCache.end();

    push.h.flags = hasGrantData ? static_cast<uint32_t>(CFLAG_HAS_DATA) : 0;

    // Grant type from intended MESI state (reuses existing helper)
    push.b.readResp.grantType =
        static_cast<int8_t>(grantTypeFromIntended(grantOst.intendedState));

    // Data source: RecallBuffer if data came from previous owner, else HomeMemory
    push.b.readResp.dataSource = static_cast<int8_t>(grantOst.dataSource);

    // Set zero/nil for fields not stored in grantOst (pull path fills these
    // from the inbound msg, but requester doesn't need them for the push).
    push.b.readResp.pendingInvCount = 0;
    push.b.readResp.grantVisibleTick = curTick();
    push.b.readResp.sentinelVisibleTick = curTick();
    push.b.readResp.recallNeeded = false;
    push.b.readResp.recallOwnerNode = -1;
    push.b.readResp.authEpoch = grantOst.baseEpoch;
    push.b.readResp.committedEpoch = 0;
    push.b.readResp.pendingInvMask = 0;

    // Grant data: copy from grantOst dataBuf if dataValid, otherwise use the
    // home-side recall/writeback cache populated by naive dirty eviction.
    if (grantOst.dataValid) {
        std::memcpy(push.b.readResp.grantData, grantOst.dataBuf, 64);
    } else if (cachedData != _lineDataCache.end()) {
        std::memcpy(push.b.readResp.grantData, cachedData->second.data(), 64);
        push.b.readResp.dataSource = static_cast<int8_t>(GrantDataSource::RecallBuffer);
        std::fprintf(stderr,
                     "[DATA-CACHE-PUSH] home=%d pa=0x%lx requester=%d hit=1\n",
                     _nodeId, grantOst.linePa, grantOst.requesterNode);
        std::fflush(stderr);
    }

    push.h.seqNum = 0;
    push.h.enqueueTick = curTick();
    push.h.readyTick = curTick();
}

// ---- v4-dual-socket: Query Line Metadata (read-only snapshot) ----

void
UBCCController::queryLineMeta(uint64_t linePa,
                               uint64_t &outEpoch,
                               int &outOwnerNode,
                               MESIState &outState,
                               bool &outFound) const
{
    outFound = false;
    outEpoch = 0;
    outOwnerNode = -1;
    outState = MESIState::G_I;

    DirEntry entry;
    if (_directory.lookup(linePa, entry)) {
        outFound = true;
        outEpoch = normalizeEpoch(entry.epoch);
        outOwnerNode = DirEntry::ownerFromSharers(entry);
        outState = entry.state;
    }
    // Note: Does not check backstore or create resident placeholder.
    // Returns committed snapshot only.
}

// ---- v4-dual-socket: HomeWritebackNotify handler ----

void
UBCCController::processHomeWritebackNotify(uint64_t homePa, uint64_t notifyEpoch)
{
    notifyEpoch = normalizeEpoch(notifyEpoch);

    printf("[UBCC-HOME-WB-NOTIFY] home=%d socket=%d pa=0x%lx epoch=%lu\n",
           _nodeId, _socketId, homePa, notifyEpoch);

    DirEntry entry;
    if (!_directory.lookup(homePa, entry)) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d socket=%d: HomeWritebackNotify PA=0x%lx "
                "no directory entry — ignored\n",
                _nodeId, _socketId, homePa);
        return;
    }

    if (entry.state == MESIState::G_I) {
        framework::LogInfo("UBCC",
                "UBCC node_id=%d socket=%d: HomeWritebackNotify PA=0x%lx "
                "already G_I — ignored\n",
                _nodeId, _socketId, homePa);
        return;
    }

    // Guard: if a new request is already in-flight, drop stale notify
    if (isLineBusy(homePa)) {
        // TC98 fix: rate-limit home-WB-NOTIFY BUSY log
        { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
        printf("[UBCC-HOME-WB-NOTIFY] home=%d socket=%d pa=0x%lx BUSY — deferred (n=%lu)\n",
               _nodeId, _socketId, homePa, _cnt); }
        return;
    }

    // Optimistic stale epoch check
    if (notifyEpoch != 0 && !checkEpochForLine(homePa, notifyEpoch)) {
        printf("[UBCC-HOME-WB-NOTIFY] home=%d socket=%d pa=0x%lx "
               "STALE epoch notify=%lu dir=%lu — dropped\n",
               _nodeId, _socketId, homePa, notifyEpoch, entry.epoch);
        return;
    }

    // Release directory ownership
    int oldOwner = DirEntry::ownerFromSharers(entry);
    printf("[UBCC-HOME-WB-NOTIFY] home=%d socket=%d pa=0x%lx "
           "oldState=%s owner=%d — releasing to G_I\n",
           _nodeId, _socketId, homePa, mesiStateName(entry.state), oldOwner);

    entry.state = MESIState::G_I;
    entry.sharersMask = 0;
    entry.residentDirty = true;
    _writebackCount++;

    _directory.update(homePa, entry);
    _directory.touch(homePa);
    refreshPinnedBit(homePa);
    // UBInvariant: validate canonical form after home WB notify
    validateSharersCanonical(homePa);
}

// ---- UBInvariant: runtime invariant checker (debug-only) ----

void
UBCCController::validateEpochMonotonic(uint64_t oldEpoch, uint64_t newEpoch,
                                        uint64_t pa) const
{
    if (newEpoch < oldEpoch) {
        panic("[UBInv] PA=0x%lx epoch DECREASED %lu -> %lu", pa, oldEpoch, newEpoch);
    }
}

void
UBCCController::validateSharersCanonical(uint64_t pa) const
{
    DirEntry entry;
    if (!_directory.lookup(pa, entry)) return;
    if (entry.state == MESIState::G_I && entry.sharersMask != 0)
        panic("[UBInv] PA=0x%lx G_I with non-zero sharers 0x%lx", pa, entry.sharersMask);
    if (entry.state == MESIState::G_S && entry.sharersMask == 0)
        panic("[UBInv] PA=0x%lx G_S with zero sharers", pa);
    if ((entry.state == MESIState::G_E || entry.state == MESIState::G_M)
        && __builtin_popcountll(entry.sharersMask) != 1)
        panic("[UBInv] PA=0x%lx G_E/G_M with non-one-hot sharers 0x%lx", pa, entry.sharersMask);
}

} // namespace glob
} // namespace cc
