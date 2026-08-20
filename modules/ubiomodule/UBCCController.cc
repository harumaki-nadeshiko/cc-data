#include "UBCCController.hh"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdarg>
#include <sstream>

#include "framework/iface/Log.hh"
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
    _numSockets(num_sockets),
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
        fatal("UBCC node_id={} socket={}: epoch_bits={} out of range (1..64)",
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
            "UBCC node_id={} socket={}: initialized with epoch_bits={} "
            "dsmBase=0x{:x} dsmSize=0x{:x} numSockets={}",
            _nodeId, _socketId, _epochBits, _dsmLocalBase, _dsmSegSize,
            kNumSockets);

    framework::LogInfo("UBCC", "UBCC node_id={} socket={}: C3 batch RS {}",
            _nodeId, _socketId, _batchRsEnabled ? "ENABLED" : "DISABLED");

    registerInstance(node_id, socket_id, this);
}

void
UBCCController::markPeerPlaneExited(int node_id, int socket_id)
{
    if (node_id < 0 || node_id >= 64 || socket_id < 0 ||
        socket_id >= _numSockets || socket_id >= 64) {
        return;
    }

    _exitedPeerSocketMasks[node_id] |= (1ULL << socket_id);
    const uint64_t expected = _numSockets == 64
        ? ~0ULL : ((1ULL << _numSockets) - 1);
    if ((_exitedPeerSocketMasks[node_id] & expected) == expected) {
        const uint64_t nodeBit = 1ULL << node_id;
        if (_exitedPeerNodesMask & nodeBit) {
            return;
        }
        _exitedPeerNodesMask |= nodeBit;
        framework::LogInfo("UBCC",
                     "[UBCC-PEER-EXIT] home={} peer={} all_socket_planes_exited",
                     _nodeId, node_id);

        // A networksim-observed normal termination proves every cache copy on
        // this peer was destroyed. Retire only clean-sharer invalidate waits
        // through the normal ack state machine so its existing directory and
        // grant-continuation rules remain authoritative. Dirty owners still
        // use recall and are never completed by peer exit.
        struct PeerExitAck {
            uint64_t linePa;
            uint64_t epoch;
            uint64_t reqId;
        };
        std::vector<PeerExitAck> completedAcks;
        for (const auto &kv : _outstandingReqs) {
            const OutstandingRequest &ost = kv.second;
            DirEntry entry;
            if (!_directory.lookup(kv.first, entry) ||
                DirEntry::protoDirty(entry)) {
                continue;
            }
            const bool invalidating =
                ost.stage == OpStage::WAITING_ALL_ACKS &&
                (ost.opType == OpType::INVALIDATE ||
                 ost.opType == OpType::NAIVE_EVICT_INVALIDATE ||
                 ost.opType == OpType::UPGRADE_PENDING);
            if (!invalidating)
                continue;
            const uint64_t targets = ost.opType == OpType::UPGRADE_PENDING
                ? ost.upgradeTargetMask : ost.totalMask;
            if (targets & nodeBit)
                completedAcks.push_back({kv.first, entry.epoch, ost.reqId});
        }
        for (const auto &ack : completedAcks)
            processInvalidationAck(ack.linePa, node_id, ack.epoch, ack.reqId);
    }
}

UBCCController::~UBCCController()
{
    _instances.erase({_nodeId, _socketId});
}

void
UBCCController::publishBloomLive(uint64_t linePa)
{
    _directory.bloomInsert(linePa);
    if (_h64BloomRebuildActive) {
        _directory.bloomScratchInsert(_h64BloomRebuildSlice, linePa,
                                      _h64BloomScratch.data(),
                                      _h64BloomScratch.size());
    }
}

void
UBCCController::advanceH64BloomRebuild()
{
    if (!_host || _directory.bloomSliceBytes() == 0)
        return;
    if (!_h64BloomRebuildActive) {
        for (int slice = 0; slice < ResidentDir::BloomGroups; ++slice) {
            if (_directory.bloomSliceControl(slice).state ==
                ResidentDir::BloomSliceState::Valid) {
                continue;
            }
            _h64BloomRebuildActive = true;
            _h64BloomH64ScanIssued = false;
            _h64BloomRebuildSlice = slice;
            _h64BloomResidentCursor = 0;
            _h64BloomScratch.assign(_directory.bloomSliceBytes(), 0);
            _directory.setBloomSliceRebuilding(slice, 16);
            break;
        }
        if (!_h64BloomRebuildActive)
            return;
    }
    if (_h64BloomH64ScanIssued)
        return;
    _h64BloomResidentCursor = _directory.scanResidentBloomSlice(
        _h64BloomRebuildSlice, _h64BloomResidentCursor,
        kBloomRebuildEntriesPerWake, _h64BloomScratch.data(),
        _h64BloomScratch.size());
    if (_h64BloomResidentCursor < _directory.capacity())
        return;

    _h64BloomH64ScanIssued = true;
    const int slice = _h64BloomRebuildSlice;
    _host->hostScanH64BloomSlice(
        slice,
        [this, slice](uint64_t linePa) {
            if (_h64BloomRebuildActive && _h64BloomRebuildSlice == slice) {
                _directory.bloomScratchInsert(slice, linePa,
                                              _h64BloomScratch.data(),
                                              _h64BloomScratch.size());
            }
        },
        [this, slice](bool ok) {
            if (!_h64BloomRebuildActive || _h64BloomRebuildSlice != slice)
                return;
            finishH64BloomRebuild(ok);
        });
}

void
UBCCController::finishH64BloomRebuild(bool ok)
{
    const int slice = _h64BloomRebuildSlice;
    if (ok) {
        _directory.publishBloomSlice(slice, _h64BloomScratch.data(),
                                     _h64BloomScratch.size());
        framework::LogInfo("UBCC", "[H64-BLOOM-REBUILD] home={} slice={} result=valid",
                     _nodeId, slice);
    } else {
        _directory.invalidateBloomSlice(slice);
        framework::LogWarn("UBCC", "[H64-BLOOM-REBUILD] home={} slice={} result=invalid",
                     _nodeId, slice);
    }
    _h64BloomScratch.clear();
    _h64BloomResidentCursor = 0;
    _h64BloomRebuildSlice = -1;
    _h64BloomH64ScanIssued = false;
    _h64BloomRebuildActive = false;
}

void
UBCCController::wakeup()
{
    cleanupTombstones();
    cleanupExpiredRecalls();
    cleanupExpiredInvalidations();
    retryPendingGrantPushes();
    retryPendingH64Lookups();
    const Tick now = curTick();
    if (now >= _lastStateLogTick + 100000000) {
        size_t tombstoneCount = 0;
        for (const auto &kv : _tombstones)
            tombstoneCount += kv.second.size();
        size_t residentWaiterCount = 0;
        for (const auto &kv : _residentWaiters)
            residentWaiterCount += kv.second.size();
        size_t pendingRequesterCount = 0;
        for (const auto &kv : _pendingRequesters)
            pendingRequesterCount += kv.second.size();
        framework::LogInfo("UBCC",
                     "[UBCC-STATE] tick={} dir={} outstanding={} tombstones={} "
                      "resident_waiters={} pending_requesters={} "
                      "capacity={} policy={}",
                     now, _directory.count(), _outstandingReqs.size(),
                      tombstoneCount, residentWaiterCount, pendingRequesterCount,
                      _directory.capacity(),
                       _overflowPolicy == ResidentOverflowPolicy::NaiveEvict
                           ? "naive" : "spill");
        const bool sameState =
            _diagLastResidentCount == _directory.count() &&
            _diagLastOutstandingCount == _outstandingReqs.size() &&
            _diagLastResidentWaiterCount == residentWaiterCount &&
            _diagLastPendingRequesterCount == pendingRequesterCount;
        if (sameState) {
            ++_diagStableSamples;
        } else {
            _diagLastResidentCount = _directory.count();
            _diagLastOutstandingCount = _outstandingReqs.size();
            _diagLastResidentWaiterCount = residentWaiterCount;
            _diagLastPendingRequesterCount = pendingRequesterCount;
            _diagStableSamples = 1;
            _diagStableDumped = false;
        }
        if (!_diagStableDumped && _diagStableSamples >= 20 &&
            _directory.count() == _directory.capacity() &&
            residentWaiterCount != 0) {
            dumpStableCapacityBlockDiagnostics(now);
            _diagStableDumped = true;
        }
        _lastStateLogTick = now;
    }
    if (_h64BloomAllMisses)
        advanceH64BloomRebuild();
    else if (++_bloomReconstructCounter >= _bloomReconstructInterval) {
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

void
UBCCController::scheduleH64LookupRetry(uint64_t linePa)
{
    for (const auto &slot : _pendingH64LookupRetries) {
        if (slot.active && slot.linePa == linePa) {
            return;
        }
    }
    for (auto &slot : _pendingH64LookupRetries) {
        if (slot.active) {
            continue;
        }
        slot.active = true;
        slot.linePa = linePa;
        framework::LogInfo("UBCC",
            "[RESIDENT-FILL-RETRY-QUEUED] tick={} home={} pa=0x{:x}",
            _host ? _host->hostCurrentTick() : 0, _nodeId, linePa);
        return;
    }
    fatal("UBCC node_id={}: H64 lookup retry table full PA=0x{:x}",
          _nodeId, linePa);
}

void
UBCCController::cancelH64LookupRetry(uint64_t linePa)
{
    for (auto &slot : _pendingH64LookupRetries) {
        if (slot.active && slot.linePa == linePa) {
            slot = PendingH64LookupRetry{};
        }
    }
}

void
UBCCController::retryPendingH64Lookups()
{
    if (!_host) {
        return;
    }

    std::array<uint64_t, kMaxH64LookupRetriesPerWake> retryPas{};
    size_t retryCount = 0;
    size_t scanned = 0;
    while (scanned++ < _pendingH64LookupRetries.size() &&
           retryCount < retryPas.size()) {
        const size_t index = _h64LookupRetryCursor;
        _h64LookupRetryCursor =
            (_h64LookupRetryCursor + 1) % _pendingH64LookupRetries.size();
        auto &slot = _pendingH64LookupRetries[index];
        if (!slot.active) {
            continue;
        }
        const uint64_t linePa = slot.linePa;
        slot = PendingH64LookupRetry{};

        auto waiter = _residentWaiters.find(linePa);
        if (!_directory.fillPending(linePa)) {
            continue;
        }
        if (waiter == _residentWaiters.end() || waiter->second.empty()) {
            // The retained operation was cancelled while admission was Busy.
            // Release the orphan placeholder instead of leaving an unowned
            // fill pin behind forever.
            _directory.setFillPending(linePa, false);
            refreshPinnedBit(linePa);
            continue;
        }
        retryPas[retryCount++] = linePa;
    }

    // Issue only after taking the bounded snapshot. A synchronous Busy
    // callback can requeue the PA, but it will not recurse in this wakeup.
    for (size_t i = 0; i < retryCount; ++i) {
        framework::LogInfo("UBCC",
            "[RESIDENT-FILL-RETRY-ISSUED] tick={} home={} pa=0x{:x}",
            _host->hostCurrentTick(), _nodeId, retryPas[i]);
        _host->hostIssueBackstoreRead(retryPas[i]);
    }
}

void
UBCCController::dumpStableCapacityBlockDiagnostics(Tick now) const
{
    auto opTypeName = [](OpType type) {
        switch (type) {
          case OpType::RECALL: return "RECALL";
          case OpType::INVALIDATE: return "INVALIDATE";
          case OpType::NAIVE_EVICT_INVALIDATE: return "NAIVE_EVICT_INVALIDATE";
          case OpType::GRANT_HANDSHAKE: return "GRANT_HANDSHAKE";
          case OpType::UPGRADE_PENDING: return "UPGRADE_PENDING";
        }
        return "UNKNOWN";
    };
    auto stageName = [](OpStage stage) {
        switch (stage) {
          case OpStage::CREATED: return "CREATED";
          case OpStage::WAITING_TARGET_RESP: return "WAITING_TARGET_RESP";
          case OpStage::WAITING_ALL_ACKS: return "WAITING_ALL_ACKS";
          case OpStage::WAITING_LOCAL_DONE: return "WAITING_LOCAL_DONE";
          case OpStage::WAITING_CLEAR: return "WAITING_CLEAR";
          case OpStage::DONE: return "DONE";
          case OpStage::CANCELLED: return "CANCELLED";
          case OpStage::TIMED_OUT: return "TIMED_OUT";
          case OpStage::PERSISTENT_BUSY: return "PERSISTENT_BUSY";
        }
        return "UNKNOWN";
    };

    framework::LogInfo("UBCC",
        "[UBCC-STABLE-BLOCK-BEGIN] tick={} samples={} dir={}/{} "
        "outstanding={} waiter_pas={}",
        now, _diagStableSamples, _directory.count(), _directory.capacity(),
        _outstandingReqs.size(), _residentWaiters.size());

    for (const auto &kv : _outstandingReqs) {
        const OutstandingRequest &ost = kv.second;
        framework::LogInfo("UBCC",
            "[UBCC-STABLE-OUTSTANDING] pa=0x{:x} op={} stage={} requester={} "
            "socket={} target={} targetMask=0x{:x} reqId={} baseEpoch={} "
            "reservedEpoch={} pendingAcks={} createTick={} age={} "
            "deadline={} retries={} dataValid={} replayArmed={}",
            kv.first, opTypeName(ost.opType), stageName(ost.stage),
            ost.requesterNode, ost.requesterSocket, ost.targetNode,
            ost.targetMask, ost.reqId, ost.baseEpoch, ost.reservedEpoch,
            ost.pendingAckCount, ost.createTick,
            now >= ost.createTick ? now - ost.createTick : 0,
            ost.deadlineTick, static_cast<unsigned>(ost.recallRetries),
            ost.dataValid ? 1 : 0, ost.replayArmed ? 1 : 0);
    }

    std::array<uint64_t, MAX_RESIDENT_WAITERS_TOTAL> setRepresentatives{};
    size_t setCount = 0;
    for (const auto &kv : _residentWaiters) {
        if (kv.second.empty() ||
            kv.second.front().waitReason != ResidentWaitReason::Capacity) {
            continue;
        }
        bool seen = false;
        for (size_t i = 0; i < setCount; ++i) {
            if (_directory.sameSet(kv.first, setRepresentatives[i])) {
                seen = true;
                break;
            }
        }
        if (!seen && setCount < setRepresentatives.size()) {
            setRepresentatives[setCount++] = kv.first;
        }
    }

    for (size_t setIdx = 0; setIdx < setCount; ++setIdx) {
        const uint64_t waiterPa = setRepresentatives[setIdx];
        size_t setWaiters = 0;
        for (const auto &kv : _residentWaiters) {
            if (_directory.sameSet(kv.first, waiterPa)) {
                setWaiters += kv.second.size();
            }
        }
        framework::LogInfo("UBCC",
            "[UBCC-STABLE-SET] representative=0x{:x} waiters={} free={}",
            waiterPa, setWaiters,
            _directory.hasFreeSlotForPa(waiterPa) ? 1 : 0);

        for (int set = 0; set < _directory.numSets(); ++set) {
            for (int way = 0; way < _directory.numWays(); ++way) {
                if (!_directory.getValid(set, way)) {
                    continue;
                }
                const uint64_t pa = _directory.rebuildPA(set, way);
                if (!_directory.sameSet(pa, waiterPa)) {
                    continue;
                }
                DirEntry entry;
                _directory.lookup(pa, entry);
                auto pendingIt = _pendingRequesters.find(pa);
                auto waiterIt = _residentWaiters.find(pa);
                const bool outstanding = _outstandingReqs.count(pa) != 0;
                const bool pending = pendingIt != _pendingRequesters.end() &&
                    !pendingIt->second.empty();
                const bool waiters = waiterIt != _residentWaiters.end() &&
                    !waiterIt->second.empty();
                const bool snapshot = _asyncWbSnapshots.count(pa) != 0;
                const bool fill = _directory.fillPending(pa);
                const bool wb = _directory.wbPending(pa);
                const bool dirtyTombstone =
                    _overflowPolicy == ResidentOverflowPolicy::Spill &&
                    entry.state == MESIState::G_I && entry.residentDirty;
                framework::LogInfo("UBCC",
                    "[UBCC-STABLE-WAY] representative=0x{:x} set={} way={} "
                    "pa=0x{:x} state={} sharers=0x{:x} epoch={} dirty={} "
                    "pinned={} reasons=outstanding:{},pending:{},waiters:{},"
                    "snapshot:{},fill:{},wb:{},dirty_tombstone:{}",
                    waiterPa, set, way, pa, mesiStateName(entry.state),
                    entry.sharersMask, entry.epoch,
                    entry.residentDirty ? 1 : 0,
                    _directory.pinned(pa) ? 1 : 0,
                    outstanding ? 1 : 0, pending ? 1 : 0, waiters ? 1 : 0,
                    snapshot ? 1 : 0, fill ? 1 : 0, wb ? 1 : 0,
                    dirtyTombstone ? 1 : 0);
            }
        }
    }
    framework::LogInfo("UBCC", "[UBCC-STABLE-BLOCK-END] tick={}", now);
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
    uint64_t line_pa, const PendingRequester &pr, DirEntry &entry)
{
    size_t slot = 0;
    if (_directory.lookupWithSlot(line_pa, entry, slot)) {
        _directory.touch(line_pa);
        if (_directory.fillPending(line_pa) || _directory.wbPending(line_pa)) {
            PendingRequester pr2 = pr;  // copy caller's envelope
            pr2.waitReason = _directory.fillPending(line_pa)
                ? ResidentWaitReason::BackstoreFill
                : ResidentWaitReason::MetadataWriteback;
            enqueueResidentWaiter(line_pa, pr2);
            refreshPinnedBit(line_pa);
            return _directory.fillPending(line_pa)
                ? ResidentAccessResult::Queued
                : ResidentAccessResult::Busy;
        }
        refreshPinnedBit(line_pa);
        return ResidentAccessResult::Ready;
    }

    return handleResidentMiss(line_pa, pr, entry);
}

UBCCController::ResidentAccessResult
UBCCController::handleResidentMiss(
    uint64_t line_pa, const PendingRequester &pr, DirEntry &entry)
{
    // H64 accepts a negative only after the corresponding jointly rebuilt
    // ResidentDir/H64 slice is valid. Invalid and rebuilding slices retain the
    // conservative metadata lookup path.
    const bool mayContain = _directory.bloomMayContain(line_pa);
    const bool h64NegativeAuthoritative = _h64BloomAllMisses &&
        _directory.bloomNegativeAuthoritative(line_pa);
    const bool shouldFill =
        _overflowPolicy == ResidentOverflowPolicy::Spill &&
        (mayContain || (_h64BloomAllMisses && !h64NegativeAuthoritative));

    framework::LogInfo("UBCC", "[RESIDENT-MISS] home={} pa=0x{:x} opKind={} req={} requester={} "
            "mayContain={} h64BloomAll={} count={} capacity={} freeForPa={} policy={} reqId={}",
            _nodeId, line_pa, static_cast<int>(pr.opKind),
            static_cast<int>(pr.reqType), pr.node,
            mayContain ? 1 : 0,
            _h64BloomAllMisses ? 1 : 0,
            _directory.count(), _directory.capacity(),
            _directory.hasFreeSlotForPa(line_pa) ? 1 : 0,
            _overflowPolicy == ResidentOverflowPolicy::NaiveEvict ? 1 : 0,
            pr.reqId);
    if (!_directory.hasFreeSlotForPa(line_pa)) {
        PendingRequester pr2 = pr;  // copy caller's envelope
        pr2.waitReason = ResidentWaitReason::Capacity;
        const ResidentWaiterEnqueueResult enqueueResult =
            enqueueResidentWaiterIfNew(line_pa, pr2);
        // A duplicate means the operation is already retained, not that
        // capacity progress can stop.  Likewise, a new request rejected by the
        // bounded queue can still drive older waiters in this set.
        const ResidentEvictResult evictResult = evictOneVictim(line_pa);
        if (evictResult == ResidentEvictResult::Removed) {
            replayResidentWaiters(line_pa);
        }
        auto wit = _residentWaiters.find(line_pa);
        size_t waiterDepth = (wit == _residentWaiters.end()) ? 0 : wit->second.size();
        if (_verboseLog) {
            framework::LogDebug("UBCC", "[RESIDENT-MISS-BUSY] home={} pa=0x{:x} reason=capacity_wait "
                    "evictResult={} count={} capacity={} waiterDepth={} opKind={} enqueueResult={}",
                    _nodeId, line_pa, static_cast<int>(evictResult),
                    _directory.count(), _directory.capacity(), waiterDepth,
                    static_cast<int>(pr.opKind), static_cast<int>(enqueueResult));
        }
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

    if (!shouldFill) {
        entry = placeholder;
        if (_verboseLog) {
            framework::LogDebug("UBCC", "[RESIDENT-MISS-READY] home={} pa=0x{:x} reason={} opKind={}",
                   _nodeId, line_pa,
                   h64NegativeAuthoritative ? "bloom_negative_h64_authoritative" :
                                              "bloom_negative",
                   static_cast<int>(pr.opKind));
        }
        refreshPinnedBit(line_pa);
        return ResidentAccessResult::Ready;
    }

    // 3.4: Bloom reported positive but directory missed → false positive count
    _directory.incrementBloomFp();

    _directory.setFillPending(line_pa, true);
    _directory.setPinned(line_pa, true);
    PendingRequester pr2 = pr;  // copy caller's envelope
    pr2.waitReason = ResidentWaitReason::BackstoreFill;
    enqueueResidentWaiter(line_pa, pr2);

    if (_host) {
        _host->hostIssueBackstoreRead(line_pa);
    }
    framework::LogInfo("UBCC", "[RESIDENT-FILL-ISSUED] tick={} home={} pa=0x{:x} waiterDepth={} opKind={}",
            _host ? _host->hostCurrentTick() : 0,
            _nodeId, line_pa, _residentWaiters[line_pa].size(),
            static_cast<int>(pr.opKind));
    return ResidentAccessResult::Queued;
}

void
UBCCController::enqueueResidentWaiter(uint64_t linePa, const PendingRequester &pr)
{
    enqueueResidentWaiterIfNew(linePa, pr);
}

UBCCController::ResidentWaiterEnqueueResult
UBCCController::enqueueResidentWaiterIfNew(uint64_t linePa, const PendingRequester &pr)
{
    auto existing = _residentWaiters.find(linePa);
    size_t total = 0;
    for (const auto &kv : _residentWaiters) total += kv.second.size();
    if (existing == _residentWaiters.end() && total >= MAX_RESIDENT_WAITERS_TOTAL) {
        if (_verboseLog) {
            framework::LogDebug("UBCC", "[RESIDENT-WAITER-DROP] home={} pa=0x{:x} reason=global_queue_full total={}",
                    _nodeId, linePa, total);
        }
        return ResidentWaiterEnqueueResult::Full;
    }
    auto &q = _residentWaiters[linePa];
    if (q.size() >= MAX_PENDING_PER_PA) {
        if (_verboseLog) {
            framework::LogDebug("UBCC", "[RESIDENT-WAITER-DROP] home={} pa=0x{:x} opKind={} node={} "
                   "reqId={} reason=queue_full depth={}",
                   _nodeId, linePa, static_cast<int>(pr.opKind), pr.node,
                   pr.reqId, q.size());
        }
        return ResidentWaiterEnqueueResult::Full;
    }
    // Dedup by (opKind, node, socket, reqId). For writeback/evict with reqId==0,
    // also compare opKind+node+epoch to prevent repeated enqueue of the same
    // legacy operation tuple.
    for (const auto &e : q) {
        if (e.opKind == pr.opKind && e.node == pr.node &&
            e.socket == pr.socket) {
            if (pr.reqId != 0 && e.reqId == pr.reqId) {
                if (_verboseLog) {
                    framework::LogDebug("UBCC", "[RESIDENT-WAITER-DEDUP] home={} pa=0x{:x} opKind={} "
                           "node={} reqId={} reason=exact_dup_reqId",
                           _nodeId, linePa, static_cast<int>(pr.opKind), pr.node, pr.reqId);
                }
                return ResidentWaiterEnqueueResult::Duplicate;
            }
            if (pr.reqId == 0 && e.reqId == 0 && e.epoch == pr.epoch) {
                if (_verboseLog) {
                    framework::LogDebug("UBCC", "[RESIDENT-WAITER-DEDUP] home={} pa=0x{:x} opKind={} "
                           "node={} epoch={} reason=legacy_dup_no_reqId",
                           _nodeId, linePa, static_cast<int>(pr.opKind), pr.node, pr.epoch);
                }
                return ResidentWaiterEnqueueResult::Duplicate;
            }
        }
    }
    q.push_back(pr);
    framework::LogInfo("UBCC", "[RESIDENT-WAITER-ENQ] home={} pa=0x{:x} opKind={} node={} "
            "socket={} reqId={} epoch={} depth={}",
            _nodeId, linePa, static_cast<int>(pr.opKind), pr.node,
            pr.socket, pr.reqId, pr.epoch, q.size());
    return ResidentWaiterEnqueueResult::Enqueued;
}

size_t
UBCCController::retireCommittedResidentWaiters(const OutstandingRequest &ost)
{
    auto it = _residentWaiters.find(ost.linePa);
    if (it == _residentWaiters.end()) {
        return 0;
    }

    auto &q = it->second;
    const size_t oldSize = q.size();
    q.erase(std::remove_if(q.begin(), q.end(), [&](const PendingRequester &pr) {
        if (pr.opKind != ResidentOpKind::Read ||
            pr.node != ost.requesterNode ||
            pr.socket != ost.requesterSocket ||
            pr.reqId != ost.reqId) {
            return false;
        }
        return ost.reqId != 0 || normalizeEpoch(pr.epoch) == ost.baseEpoch;
    }), q.end());

    const size_t retired = oldSize - q.size();
    if (q.empty()) {
        _residentWaiters.erase(it);
    }
    if (retired != 0) {
        framework::LogInfo("UBCC",
                     "[RESIDENT-WAITER-RETIRE-COMMITTED] home={} pa=0x{:x} "
                     "node={} socket={} reqId={} count={}",
                     _nodeId, ost.linePa, ost.requesterNode,
                     ost.requesterSocket, ost.reqId, retired);
    }
    return retired;
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
    // A capacity waiter normally targets a non-resident PA, where there is no
    // entry to pin. If that PA becomes resident before the retained operation
    // completes, however, the waiter owns real replay state (including a
    // possible writeback payload) and must protect the entry from victim
    // removal, which erases resident waiters for the victim PA.
    pin = pin || (rit != _residentWaiters.end() && !rit->second.empty());
    // An async metadata snapshot is in flight. Avoid racing a second eviction
    // or persistence operation for the same resident entry.
    pin = pin || (_asyncWbSnapshots.count(linePa) != 0);
    pin = pin || _directory.fillPending(linePa);
    pin = pin || _directory.wbPending(linePa);
    // Spill must retain a dirty invalid entry until it is persisted. Pure
    // naive has no metadata backstore, so such metadata is disposable and
    // must remain evictable under set-local capacity pressure.
    pin = pin || (_overflowPolicy == ResidentOverflowPolicy::Spill &&
                  e.state == MESIState::G_I && e.residentDirty);
    _directory.setPinned(linePa, pin);
}

UBCCController::ResidentEvictResult
UBCCController::evictOneVictim(uint64_t avoidPa)
{
    uint64_t victimPa = 0;
    DirEntry victim;
    if (!_directory.pickVictim(avoidPa, victimPa, victim)) {
        if (_verboseLog) {
            framework::LogDebug("UBCC", "[RESIDENT-EVICT-PICK-FAIL] home={} avoid=0x{:x} count={} capacity={}",
                    _nodeId, avoidPa, _directory.count(), _directory.capacity());
        }
        return ResidentEvictResult::Blocked;
    }

    if (_verboseLog) {
        framework::LogDebug("UBCC", "[RESIDENT-EVICT-PICK] home={} avoid=0x{:x} victim=0x{:x} "
               "state={} sharers=0x{:x} dirty={} residentDirty={} policy={}",
               _nodeId, avoidPa, victimPa, mesiStateName(victim.state),
               victim.sharersMask, DirEntry::protoDirty(victim) ? 1 : 0,
               victim.residentDirty ? 1 : 0,
               _overflowPolicy == ResidentOverflowPolicy::NaiveEvict ? 1 : 0);
    }

    if (_overflowPolicy == ResidentOverflowPolicy::NaiveEvict) {
        return evictOneVictimNaive(victimPa, victim);
    }

    // Phase A4: residentDirty means resident metadata dirtiness (needs
    // backstore flush), NOT home dirty-data authority.  A non-G_I entry
    // must never be force-removed solely because residentDirty is false;
    // its backstore durability must be confirmed first.
    if (victim.state == MESIState::G_I && !victim.residentDirty) {
        _directory.forceRemove(victimPa);
        _residentWaiters.erase(victimPa);
        _pendingRequesters.erase(victimPa);
        return ResidentEvictResult::Removed;
    }

    // Non-G_I or residentDirty=true: must persist metadata before removal.
    if (!victim.residentDirty && victim.state != MESIState::G_I) {
        // residentDirty=false but entry is non-G_I → backstore write was acked
        // previously.  Bloom should still contain this PA from the prior upsert.
        // If Bloom is negative (reconstructed without this PA), fall through to
        // re-persist.  No _backstoreMetadataPAs to check.
        if (_directory.bloomMayContain(victimPa)) {
            framework::LogInfo("UBCC",
                    "[UBCC-SPILL-DIRTY-PERSIST] home={} pa=0x{:x} state={} "
                    "residentDirty=0 bloomPositive=1 — safe force-remove",
                    _nodeId, victimPa, mesiStateName(victim.state));
            _directory.forceRemove(victimPa);
            _residentWaiters.erase(victimPa);
            _pendingRequesters.erase(victimPa);
            return ResidentEvictResult::Removed;
        }
        // Bloom missing — metadata may have been lost.  Fall
        // through to schedule a fresh backstore write to ensure durability.
        framework::LogInfo("UBCC",
                "[UBCC-SPILL-DIRTY-PERSIST] home={} pa=0x{:x} state={} "
                "residentDirty=0 bloomPositive=0 — re-persisting",
                _nodeId, victimPa, mesiStateName(victim.state));
    }

    _directory.setWbPending(victimPa, true);
    _directory.setPinned(victimPa, true);
    _evictionPendingRemoval.insert(victimPa);
    framework::LogInfo("UBCC", "[RESIDENT-SPILL-START] tick={} home={} victim=0x{:x} state={} residentDirty={}",
            _host ? _host->hostCurrentTick() : 0,
            _nodeId, victimPa, mesiStateName(victim.state), victim.residentDirty ? 1 : 0);
    if (victim.state == MESIState::G_I) {
        scheduleBackstoreDelete(victimPa);
    } else {
        framework::LogInfo("UBCC",
                     "[UBCC-SPILL-DIRTY-PERSIST] home={} pa=0x{:x} state={} "
                     "sharers=0x{:x} epoch={} — scheduling backstore write",
                     _nodeId, victimPa, mesiStateName(victim.state),
                     victim.sharersMask, victim.epoch);
        scheduleBackstoreWrite(victimPa);
    }
    return ResidentEvictResult::Armed;
}

UBCCController::ResidentEvictResult
UBCCController::evictOneVictimNaive(uint64_t victimPa, const DirEntry &victim)
{
    if (isLineBusy(victimPa)) {
        return ResidentEvictResult::Blocked;
    }

    uint64_t targetMask = victim.sharersMask;
    const int owner = DirEntry::ownerFromSharers(victim);
    if (owner >= 0 && owner < 64) {
        targetMask |= (1ULL << owner);
    }

    // A clean sharer vanished only after networksim observed TERM from every
    // plane of that peer node. It cannot retain a cache line, so do not wait
    // for an invalidate ack that can never arrive. A dirty owner still takes
    // the recall path below and is never elided by this cleanup.
    if (!DirEntry::protoDirty(victim)) {
        targetMask &= ~_exitedPeerNodesMask;
    }

    _naiveDirEvictions++;
    _naiveForcedInvalidations += __builtin_popcountll(targetMask);
    if (DirEntry::protoDirty(victim)) {
        _naiveDirtyVictims++;
        _naiveForcedWritebacks++;
    }

    framework::LogInfo("UBCC","[UBCC-NAIVE-EVICT] home={} pa=0x{:x} state={} sharers=0x{:x} "
           "targets=0x{:x} dirty={} epoch={}",
           _nodeId, victimPa, mesiStateName(victim.state), victim.sharersMask,
           targetMask, DirEntry::protoDirty(victim) ? 1 : 0, victim.epoch);

    if (DirEntry::protoDirty(victim) && owner >= 0) {
        OutstandingRequest *recallOreq = createOutstanding(
            victimPa, OpType::RECALL, -1, owner, _socketId);
        if (!recallOreq) {
            return ResidentEvictResult::Blocked;
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
        framework::LogInfo("UBCC",
                     "[UBCC-NAIVE-DIRTY-RECALL-CREATE] home={} socket={} "
                     "pa=0x{:x} owner={} state={} sharers=0x{:x} "
                     "residentDirty={} reqId={} baseEpoch={} "
                     "reservedEpoch={} stage={}",
                     _nodeId, _socketId, victimPa, owner,
                     mesiStateName(victim.state), victim.sharersMask,
                     victim.residentDirty ? 1 : 0, recallOreq->reqId,
                     recallOreq->baseEpoch, recallOreq->reservedEpoch,
                     static_cast<int>(recallOreq->stage));
        if (!initiateRecall(victimPa, victim, *recallOreq)) {
            removeOutstanding(victimPa);
            return ResidentEvictResult::Blocked;
        }
        framework::LogInfo("UBCC",
                     "[UBCC-NAIVE-DIRTY-RECALL-HOLD] home={} pa=0x{:x} owner={} "
                     "state={} epoch={}",
                     _nodeId, victimPa, owner, mesiStateName(victim.state),
                     victim.epoch);
        return ResidentEvictResult::Armed;
    }

    if (targetMask == 0) {
        _directory.forceRemove(victimPa);
        _residentWaiters.erase(victimPa);
        _pendingRequesters.erase(victimPa);
        _evictionPendingRemoval.erase(victimPa);
        return ResidentEvictResult::Removed;
    }

    // Keep the victim resident until every invalidation acknowledges.  Removing
    // it before the acks arrive makes processInvalidationAck reject them and
    // exposes its set before the eviction has actually completed.
    OutstandingRequest *evictOreq = createOutstanding(
        victimPa, OpType::NAIVE_EVICT_INVALIDATE, -1, -1, _socketId);
    if (!evictOreq) {
        return ResidentEvictResult::Blocked;
    }
    _directory.setPinned(victimPa, true);

    uint64_t effectiveMask = targetMask;
    if (!fanoutInvalidateTargets(victimPa, targetMask, victim.epoch,
                                 victim.epoch,
                                 -1, UBCC_OuterReqType::GlobalInvalidate,
                                 DirEntry::protoDirty(victim), &effectiveMask)) {
        removeOutstanding(victimPa);
        refreshPinnedBit(victimPa);
        return ResidentEvictResult::Blocked;
    }

    if (effectiveMask == 0) {
        removeOutstanding(victimPa);
        _directory.forceRemove(victimPa);
        _residentWaiters.erase(victimPa);
        _pendingRequesters.erase(victimPa);
        _evictionPendingRemoval.erase(victimPa);
        return ResidentEvictResult::Removed;
    }

    evictOreq->baseEpoch = victim.epoch;
    evictOreq->reqId = victim.epoch;
    evictOreq->stage = OpStage::WAITING_ALL_ACKS;
    evictOreq->targetMask = effectiveMask;
    evictOreq->totalMask = effectiveMask;
    evictOreq->pendingAckCount = __builtin_popcountll(effectiveMask);
    evictOreq->ackMask = 0;
    return ResidentEvictResult::Armed;
}

void
UBCCController::scheduleBackstoreWrite(uint64_t linePa)
{
    if (_overflowPolicy != ResidentOverflowPolicy::Spill)
        return;
    if (_host) {
        _host->hostIssueBackstoreWrite(linePa);
    } else {
        onBackstoreWriteAck(linePa);
    }
}

void
UBCCController::scheduleBackstoreDelete(uint64_t linePa)
{
    if (_overflowPolicy != ResidentOverflowPolicy::Spill)
        return;
    if (_host) {
        _host->hostIssueBackstoreDelete(linePa);
    } else {
        onBackstoreDeleteAck(linePa, true);
    }
}

void
UBCCController::doAsyncWriteback()
{
    if (_overflowPolicy != ResidentOverflowPolicy::Spill)
        return;

    const int maxPerRound = 16;
    int count = 0;
    int numSets = _directory.numSets();
    int numWays = _directory.numWays();

    for (int set = 0; set < numSets && count < maxPerRound; ++set) {
        for (int way = 0; way < numWays && count < maxPerRound; ++way) {
            if (_asyncWbSnapshots.size() >= kMaxAsyncWbSnapshots) {
                return;
            }
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
            // The snapshot owns this entry until its ack. Materialize the
            // derived pin before issuing the asynchronous metadata write.
            refreshPinnedBit(pa);

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
        // A spill-policy G_I tombstone is pinned solely while its metadata is
        // dirty.  Once the async persistence snapshot is durable, recalculate
        // the derived pin and wake capacity waiters in this set.  Without this
        // transition, an entire set can remain permanently non-evictable even
        // though every tombstone is now safe to reclaim.
        refreshPinnedBit(linePa);
        replayResidentWaitersForCapacity(linePa);
        framework::LogInfo("UBCC","[UBCC-ASYNC-WB] home={} pa=0x{:x} epoch={} — dirty cleared (snapshot matched)",
               _nodeId, linePa, snapshotEpoch);
    } else {
        // The completed snapshot no longer owns the entry. Keep the newer
        // metadata dirty, but release the stale snapshot pin and wake capacity
        // waiters in this set.
        refreshPinnedBit(linePa);
        replayResidentWaitersForCapacity(linePa);
        framework::LogInfo("UBCC","[UBCC-ASYNC-WB] home={} pa=0x{:x} snapshotEpoch={} currentEpoch={} "
               "— dirty kept (entry modified)",
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
        << "\"invalidationCount\":" << _invalidationCount << ","
        << "\"residentCount\":" << _directory.count() << ","
        << "\"residentCapacity\":" << _directory.capacity()
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

    // A replay may cause a capacity miss to re-enqueue the same request.  Only
    // process waiters that existed when this pass began so a failed retry
    // cannot consume its own freshly queued copy forever at one simulation tick.
    size_t replayBudget = it->second.size();
    while (!it->second.empty() && replayBudget-- != 0) {
        PendingRequester pr = it->second.front();
        it->second.pop_front();

        framework::LogInfo("UBCC", "[RESIDENT-WAITER-REPLAY] tick={} home={} pa=0x{:x} opKind={} "
                "node={} socket={} reqId={} epoch={}",
                _host ? _host->hostCurrentTick() : 0,
                _nodeId, linePa, static_cast<int>(pr.opKind), pr.node,
                pr.socket, pr.reqId, pr.epoch);

        bool stop = false;
        bool restore = false;  // whether to push the waiter back

        switch (pr.opKind) {
        case ResidentOpKind::Writeback: {
            bool ok = processWriteback(linePa, pr.node, pr.epoch,
                                       pr.wbKeepAsClean,
                                       pr.hasData ? pr.data.data() : nullptr);
            if (!ok) {
                // If fill/wb is now pending, the operation re-enqueued itself
                // behind a resident fill/writeback.  Do NOT restore the old
                // popped copy — it would duplicate the waiter and corrupt the
                // queue on later replay.
                restore = (!_directory.fillPending(linePa) &&
                           !_directory.wbPending(linePa));
                DirEntry current;
                if (restore && _directory.lookup(linePa, current) &&
                    normalizeEpoch(pr.epoch) != current.epoch) {
                    restore = false;
                    stop = false;
                    framework::LogWarn("UBCC",
                            "[RESIDENT-WAITER-WB-DROP-STALE] home={} pa=0x{:x} "
                            "node={} msgEpoch={} currentEpoch={}",
                            _nodeId, linePa, pr.node, normalizeEpoch(pr.epoch),
                            current.epoch);
                    break;
                }
            }
            stop = !ok;
            break;
        }
        case ResidentOpKind::Evict: {
            bool ok = processEvict(linePa, pr.node, pr.epoch);
            if (!ok) {
                restore = (!_directory.fillPending(linePa) &&
                           !_directory.wbPending(linePa));
            }
            stop = !ok;
            break;
        }
        case ResidentOpKind::Upgrade: {
            bool notSharer = false;
            bool deferred = false;
            bool ok = processOuterUpgradeReq(
                linePa, pr.node, pr.epoch, pr.reqId,
                pr.upgradeDesiredPerm, pr.upgradeCause, &notSharer, &deferred,
                pr.socket);
            if (!ok) {
                if (notSharer) {
                    // The requester permanently lost sharer status. It already
                    // received (or will receive on retry) a NotSharer response
                    // and must fall back to ReadUnique. Never retain this stale
                    // waiter: with no fill/WB/outstanding left it cannot become
                    // valid again and would otherwise leak forever.
                    restore = false;
                    stop = false;
                    framework::LogWarn("UBCC",
                            "[RESIDENT-WAITER-UPGRADE-DROP-NOT-SHARER] home={} "
                            "pa=0x{:x} node={} reqId={}",
                            _nodeId, linePa, pr.node, pr.reqId);
                    break;
                }
                // fillPending/wbPending guard: if the upgrade re-enqueued
                // itself behind a resident fill/wb, do NOT push the stale
                // popped copy back — the new copy (from handleResidentMiss)
                // is already in the queue and will replay after the fill.
                restore = (!_directory.fillPending(linePa) &&
                           !_directory.wbPending(linePa));
                if (restore) {
                    framework::LogWarn("UBCC", "[RESIDENT-WAITER-REPLAY-UPGRADE-REJECT] home={} "
                           "pa=0x{:x} node={} reqId={}",
                           _nodeId, linePa, pr.node, pr.reqId);
                } else {
                    framework::LogWarn("UBCC", "[RESIDENT-WAITER-REPLAY-UPGRADE-QUEUED] home={} "
                           "pa=0x{:x} node={} reqId={} — fill/wb pending, waiter "
                           "already re-enqueued, discarding stale copy",
                           _nodeId, linePa, pr.node, pr.reqId);
                }
            }
            stop = !ok;
            // On success, if an outstanding was created, stop further replay
            // (the outstanding blocks subsequent operations).
            if (ok && findOutstanding(linePa)) {
                OutstandingRequest *upgrade = findOutstanding(linePa);
                if (_outbound && upgrade && upgrade->reqId == pr.reqId) {
                    CoherenceMessage response;
                    response.h.type = CoherenceMessageType::UpgradeResp;
                    response.h.srcNode = _nodeId;
                    response.h.srcSocket = _socketId;
                    response.h.dstNode = pr.node;
                    response.h.dstSocket = pr.socket >= 0 ? pr.socket : 0;
                    response.h.homeLinePa = linePa;
                    response.h.epoch = pr.epoch;
                    response.h.reqId = pr.reqId;
                    response.h.flags = static_cast<uint32_t>(CFLAG_ACCEPTED);
                    response.b.upgradeResp.upgradeTargetMask =
                        upgrade->upgradeTargetMask;
                    response.b.upgradeResp.committedEpoch =
                        getEpochForLine(linePa);
                    if (!_outbound->sendUpgradeResp(response)) {
                        fatal("UBCC node_id={}: deferred UpgradeResp send failed "
                              "PA=0x{:x} requester={} socket={} reqId={}",
                              _nodeId, linePa, pr.node, response.h.dstSocket,
                              pr.reqId);
                    }
                    framework::LogInfo("UBCC",
                            "[RESIDENT-REPLAY-UPGRADE-RESP] home={} pa=0x{:x} "
                            "node={} reqId={} targetMask=0x{:x}",
                            _nodeId, linePa, pr.node, pr.reqId,
                            upgrade->upgradeTargetMask);
                }
                stop = true;
            }
            break;
        }
        case ResidentOpKind::Read:
        default: {
            auto g = processOuterRequest(linePa, pr.reqType, pr.writeIntent,
                                          pr.node, pr.socket, pr.epoch, pr.reqId,
                                          nullptr, nullptr, nullptr, nullptr,
                                          nullptr, nullptr);
            OutstandingRequest *ost = findOutstanding(linePa);
            const bool grantCreated = ost &&
                ost->opType == OpType::GRANT_HANDSHAKE &&
                ost->requesterNode == pr.node && ost->reqId == pr.reqId &&
                ost->stage == OpStage::WAITING_CLEAR;
            // A local requester can receive ReadResp and synchronously Clear it
            // before processOuterRequest returns.  The API still returns its
            // legacy busy sentinel, but a resident entry with no in-flight state
            // proves this capacity waiter was fully processed.
            DirEntry resolvedEntry;
            const bool residentResolved =
                _directory.lookup(linePa, resolvedEntry) &&
                !_directory.fillPending(linePa) &&
                !_directory.wbPending(linePa) &&
                !findOutstanding(linePa);
            // A local Clear can synchronously retire the outstanding request
            // before processOuterRequest returns. This is completion for every
            // waiter reason, not only capacity: retaining a stale fill waiter
            // pins its resident entry forever and can make a later full set
            // unevictable.
            const bool replaySucceeded = grantCreated || residentResolved;
            if (static_cast<int>(g) == -1 && grantCreated) {
                // evictOneVictim can recursively replay this same queue. That
                // nested pass owns the grant push and may drain/erase this map
                // entry, invalidating `it` in the outer pass. Do not touch it.
                return;
            }
            if (static_cast<int>(g) == -1 && !replaySucceeded) {
                // A capacity miss re-enqueues the waiter itself before it
                // returns busy. Do not restore this popped copy, or a request
                // that completed synchronously can remain pinned forever.
                restore = pr.waitReason != ResidentWaitReason::Capacity &&
                    (!_directory.fillPending(linePa) && !_directory.wbPending(linePa));
            // A capacity eviction can recursively replay this waiter. If that
            // nested pass created the grant, this outer call returns Busy but
            // sees its outstanding request. The nested pass already pushed it.
            } else if (grantCreated && static_cast<int>(g) != -1) {
                const bool pushOk = tryPushGrant(*ost, "resident-replay");
                framework::LogInfo("UBCC", "[RESIDENT-REPLAY-PUSH] tick={} home={} pa=0x{:x} "
                        "requester={} reqId={} pushOk={}",
                        _host ? _host->hostCurrentTick() : 0,
                        _nodeId, linePa, pr.node, pr.reqId, pushOk ? 1 : 0);
            }
            stop = (static_cast<int>(g) == -1 && !replaySucceeded);
            break;
        }
        } // switch

        // A synchronous local Clear can retire the matching waiter and erase
        // this PA's queue while the replayed operation is still on the stack.
        // Reacquire the iterator before touching the queue again.
        it = _residentWaiters.find(linePa);
        if (restore) {
            if (it == _residentWaiters.end()) {
                it = _residentWaiters.emplace(
                    linePa, std::deque<PendingRequester>{}).first;
            }
            it->second.push_front(pr);
        }
        if (stop) {
            // Don't erase the queue — remaining waiters are still valid
            refreshPinnedBit(linePa);
            return;
        }
        if (_directory.fillPending(linePa) || _directory.wbPending(linePa)) {
            // A fill/wb was started mid-replay; stop iterating.
            refreshPinnedBit(linePa);
            return;
        }
    }

    // A replay may have queued fresh work after the bounded pass began.  Keep
    // it for the next concrete capacity/state-change event.
    it = _residentWaiters.find(linePa);
    if (it == _residentWaiters.end()) {
        refreshPinnedBit(linePa);
        return;
    }
    if (!it->second.empty()) {
        refreshPinnedBit(linePa);
        return;
    }

    // Queue drained
    _residentWaiters.erase(it);
    refreshPinnedBit(linePa);
}

void
UBCCController::replayResidentWaitersForCapacity(uint64_t triggerPa)
{
    if (_capacityReplayActive) {
        return;
    }

    _capacityReplayActive = true;
    std::array<uint64_t, MAX_RESIDENT_WAITERS_TOTAL> keys{};
    size_t keyCount = 0;
    for (const auto &kv : _residentWaiters) {
        if (keyCount == keys.size()) {
            break;
        }
        if (!_directory.sameSet(kv.first, triggerPa) || kv.second.empty()) {
            continue;
        }
        if (kv.second.front().waitReason == ResidentWaitReason::Capacity &&
            !_directory.fillPending(kv.first) &&
            !_directory.wbPending(kv.first)) {
            keys[keyCount++] = kv.first;
        }
    }
    for (size_t i = 0; i < keyCount; ++i) {
        const uint64_t pa = keys[i];
        framework::LogInfo("UBCC", "[RESIDENT-CAPACITY-REPLAY] home={} pa=0x{:x}",
                _nodeId, pa);
        replayResidentWaiters(pa);
    }
    _capacityReplayActive = false;
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
    uint64_t *outAuthEpoch, uint64_t *outGrantEpoch)
{
    baseEpoch = normalizeEpoch(baseEpoch);

    framework::LogInfo("UBCC",
            "UBCC node_id={}: processOuterRequest PA=0x{:x} req={} write={} "
            "requesterNode={} requesterSocket={} baseEpoch={} reqId={}",
            _nodeId, line_pa, static_cast<int>(reqType), writeIntent,
            requesterNode, requesterSocket, baseEpoch, reqId);
    framework::LogInfo("UBCC","[UBCC-OUTER-REQ] home={} pa=0x{:x} req={} write={} requester={} "
           "sock={} baseEpoch={} reqId={}",
           _nodeId, line_pa, static_cast<int>(reqType), writeIntent,
           requesterNode, requesterSocket, baseEpoch, reqId);

    // Initialize M6 recall outputs and F3 dataSource output
    if (outRecallNeeded)   *outRecallNeeded = false;
    if (outRecallOwnerNode) *outRecallOwnerNode = -1;
    if (outDataSource) *outDataSource = GrantDataSource::HomeMemory;
    if (outAuthEpoch) *outAuthEpoch = 0;
    if (outGrantEpoch) *outGrantEpoch = 0;

    // Validate: only DSM addresses for this home node
    if (!isDsmAddr(line_pa)) {
        fatal("UBCC node_id={}: non-home-DSM address PA=0x{:x} in outer request",
              _nodeId, line_pa);
    }

    // Validate: Shared + true is illegal
    if (reqType == UBCC_OuterReqType::GlobalReadShared && writeIntent) {
        fatal("UBCC node_id={}: illegal Shared+writeIntent=true for PA=0x{:x}",
              _nodeId, line_pa);
    }

    // Validate requesterNode
    if (requesterNode < -1 || requesterNode >= 64) {
        fatal("UBCC node_id={}: requesterNode={} out of range",
              _nodeId, requesterNode);
    }

    // H64: if DSM persistence is pending for this PA, queue the requester.
    // The grant callback must not read stale HomeMemory before data is written.
    // HARD cap checks: per-PA limit, total limit, DSM pending set limit.
    if (_h64BloomAllMisses && _h64DsmPending.count(line_pa)) {
        auto& q = _h64PersistenceWaiters[line_pa];
        // Per-PA hard cap
        if ((int)q.size() >= kMaxH64PersistenceWaitersPerPA) {
            if (_debugLog) framework::LogDebug("UBCC",
                "[DEBUG-H64-DSM-FULL-PA] home={} pa=0x{:x} perPA={}",
                _nodeId, line_pa, (int)q.size());
            return static_cast<UBCC_OuterGrantType>(-1); // BUSY
        }
        // Total hard cap
        if (_h64PersistenceWaitersTotal >= kMaxH64PersistenceWaitersTotal) {
            if (_debugLog) framework::LogDebug("UBCC",
                "[DEBUG-H64-DSM-FULL-TOTAL] home={} total={}",
                _nodeId, _h64PersistenceWaitersTotal);
            return static_cast<UBCC_OuterGrantType>(-1); // BUSY
        }
        PendingRequester pr;
        pr.node = requesterNode;
        pr.socket = requesterSocket;
        pr.opKind = ResidentOpKind::Read;
        pr.reqType = reqType;
        pr.writeIntent = writeIntent;
        pr.epoch = baseEpoch;
        pr.reqId = reqId;
        pr.waitReason = ResidentWaitReason::Capacity;
        q.push_back(pr);
        _h64PersistenceWaitersTotal++;
        if (_debugLog) framework::LogDebug("UBCC",
            "[DEBUG-H64-DSM-WAIT] home={} pa=0x{:x} requester={} perPA={} total={}",
            _nodeId, line_pa, requesterNode, (int)q.size(), _h64PersistenceWaitersTotal);
        return static_cast<UBCC_OuterGrantType>(-1); // BUSY
    }

    DirEntry entry;
    PendingRequester prCtx;
    prCtx.opKind = ResidentOpKind::Read;
    prCtx.node = requesterNode;
    prCtx.socket = requesterSocket;
    prCtx.reqType = reqType;
    prCtx.writeIntent = writeIntent;
    prCtx.epoch = baseEpoch;
    prCtx.reqId = reqId;
    ResidentAccessResult r = ensureResidentForAccess(
        line_pa, prCtx, entry);
    framework::LogInfo("UBCC","[UBCC-OUTER-REQ] home={} pa=0x{:x} residentResult={} state={} "
           "sharers=0x{:x} epoch={}",
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
                // Only the exact original tuple is an idempotent grant retry.
                // A different socket or reqId from the same node is a distinct
                // transaction and must not receive the old outstanding's grant:
                // doing so creates a mixed ReadResp tuple whose Clear can never
                // match the retained GRANT_HANDSHAKE.
                const bool exactGrantRetry =
                    existing->stage == OpStage::WAITING_CLEAR &&
                    existing->requesterSocket == requesterSocket &&
                    existing->reqId == reqId;
                if (exactGrantRetry) {
                    framework::LogInfo("UBCC",
                            "UBCC node_id={}: grant hit PA=0x{:x} "
                            "requester={} socket={} reqId={} intended={} — granting",
                            _nodeId, line_pa, requesterNode, requesterSocket, reqId,
                             mesiStateName(existing->intendedState));
                    if (outDataSource) *outDataSource = existing->dataSource;
                    if (outGrantVisibleTick) *outGrantVisibleTick = curTick();
                    if (outSentinelVisibleTick) *outSentinelVisibleTick = curTick();
                    if (outRecallNeeded) *outRecallNeeded = false;
                    if (outRecallOwnerNode) *outRecallOwnerNode = -1;
                    if (outAuthEpoch) *outAuthEpoch = existing->baseEpoch;
                    if (outGrantEpoch) *outGrantEpoch = existing->reservedEpoch;
                    return grantTypeFromIntended(existing->intendedState);
                }
                if (existing->stage == OpStage::WAITING_CLEAR) {
                    framework::LogWarn("UBCC",
                            "[UBCC-GRANT-RETRY-TUPLE-MISMATCH] home={} pa=0x{:x} "
                            "requester={} incomingSocket={} incomingReqId={} "
                            "outstandingSocket={} outstandingReqId={} — BUSY",
                            _nodeId, line_pa, requesterNode, requesterSocket, reqId,
                            existing->requesterSocket, existing->reqId);
                }
                // TC98 fix: rate-limit high-frequency BUSY log
                { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
                framework::LogDebug("UBCC",
                        "UBCC node_id={}: existing outstanding PA=0x{:x} "
                        "same requester={} opType={} stage={} — BUSY (n={})",
                        _nodeId, line_pa, requesterNode,
                        static_cast<int>(existing->opType),
                        static_cast<int>(existing->stage), _cnt); }
                return static_cast<UBCC_OuterGrantType>(-1);
            }
            // recall_done_fix.md §4.2 Case C: different requester — enqueue or drop
            size_t pendingTotal = 0;
            for (const auto &kv : _pendingRequesters) pendingTotal += kv.second.size();
            auto pendingIt = _pendingRequesters.find(line_pa);
            if (pendingIt == _pendingRequesters.end() &&
                pendingTotal >= MAX_PENDING_REQUESTERS_TOTAL) {
                return static_cast<UBCC_OuterGrantType>(-1);
            }
            auto &q = _pendingRequesters[line_pa];
            bool isRS = (reqType == UBCC_OuterReqType::GlobalReadShared);

            // §4.4: Duplicate retry — same (requester, reqId) already queued → BUSY
            for (auto &pr : q) {
                if (pr.node == requesterNode && pr.reqId == reqId) {
                    // TC98 fix: rate-limit dup_retry log
                    { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
                    framework::LogDebug("UBCC","[UBCC-QUEUE] pa=0x{:x} action=dup_retry "
                           "requester={} reqType={} writeIntent={} reqId={} depth={} (n={})",
                           line_pa, requesterNode,
                           isRS ? "RS" : "RU", writeIntent, reqId, q.size(), _cnt); }
                    return static_cast<UBCC_OuterGrantType>(-1);
                }
            }

            // C3: RS merge dedup removed — batch RS grant handles all RS in one shot
            // §6 Q3=C was: RS merge RS — if incoming is RS and queue already has RS, skip
            // Removed to let all RS requests accumulate for batch grant in replayPendingRequesters.

            if (q.size() < MAX_PENDING_PER_PA &&
                pendingTotal < MAX_PENDING_REQUESTERS_TOTAL) {
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
                framework::LogDebug("UBCC","[UBCC-QUEUE] pa=0x{:x} action=enqueue "
                       "requester={} reqType={} writeIntent={} reqId={} depth={} (n={})",
                       line_pa, requesterNode,
                       isRS ? "RS" : "RU", writeIntent, reqId, q.size(), _cnt); }
                // TC98: Log recall wait state for hot-contention diagnostics
                if (existing->stage == OpStage::WAITING_TARGET_RESP) {
                    static uint64_t _rcnt = 0; if (++_rcnt <= 3 || _rcnt % 1000 == 0)
                    framework::LogDebug("UBCC", "[UBCC-RECALL-WAIT] pa=0x{:x} recall_target={} "
                                 "new_requester={} queue_depth={} existing_requester={} (n={})",
                                 line_pa, existing->targetNode, requesterNode,
                                 q.size(), existing->requesterNode, _rcnt);
                }
            } else {
                // TC98 fix: rate-limit drop_full log
                { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
                framework::LogDebug("UBCC","[UBCC-QUEUE] pa=0x{:x} action=drop_full "
                       "requester={} reqType={} writeIntent={} reqId={} depth={} (n={})",
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
                "UBCC node_id={}: tombstone HIT for PA=0x{:x} — idempotent grant",
                _nodeId, line_pa);
        Tick now = curTick();
        if (outGrantVisibleTick) *outGrantVisibleTick = now;
        if (outSentinelVisibleTick) *outSentinelVisibleTick = now;
        if (outDataSource) *outDataSource = GrantDataSource::HomeMemory; // F3: conservative
        if (outAuthEpoch) *outAuthEpoch = baseEpoch;
        if (outGrantEpoch) *outGrantEpoch = entry.epoch;
        return UBCC_OuterGrantType::GlobalGrantShared; // conservative
    }
    if (hasAcceptedGrantReqIdTombstone(line_pa, reqId)) {
        // A queued request is replayed against the newly committed epoch, but
        // delayed wire duplicates still carry its original pre-queue epoch.
        // The stable reqId proves this ReadReq already completed. Suppress it
        // instead of creating a second GRANT_HANDSHAKE that the requester will
        // never Clear.
        framework::LogInfo("UBCC",
                "[UBCC-READ-TOMBSTONE-REQID-HIT] home={} pa=0x{:x} "
                "requester={}:{} incomingEpoch={} reqId={} action=drop_completed_duplicate",
                _nodeId, line_pa, requesterNode, requesterSocket,
                baseEpoch, reqId);
        return static_cast<UBCC_OuterGrantType>(-1);
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

                framework::LogInfo("UBCC","[UBCC-GSRS-FAST] pa=0x{:x} requester={} sharers=0x{:x}",
                       line_pa, requesterNode, entry.sharersMask);
            } else {
                // Unique request — invalidation needed for non-requester sharers
                uint64_t otherSharers = entry.sharersMask;
                if (requesterNode >= 0)
                    otherSharers &= ~(1ULL << requesterNode);
                otherSharers &= ~_exitedPeerNodesMask;

                // F6: If requester is an existing sharer, this is a local
                // upgrade. Defer to the UPGRADE_PENDING path (§4.1.3 G_S row).
                // Do NOT create INVALIDATE here — let processOuterUpgradeReq
                // handle it via the EP-RNF upgrade handshake.
                bool isExistingSharer = (requesterNode >= 0) &&
                    (entry.sharersMask & (1ULL << requesterNode));
                if (isExistingSharer) {
                    framework::LogInfo("UBCC","[UBCC-SHARER-UPGRADE] pa=0x{:x} requester={} "
                           "is existing sharer — deferring to UPGRADE_PENDING",
                           line_pa, requesterNode);
                    return static_cast<UBCC_OuterGrantType>(-1);
                }

                if (otherSharers != 0) {
                    framework::LogWarn("UBCC","[UBCC-INVALIDATE-CREATE] home={} pa=0x{:x} requester={} "
                           "otherSharers=0x{:x} reservedEpoch={} writeIntent={}",
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
                            framework::LogWarn("UBCC","[UBCC-INVALIDATE-EMPTY] home={} pa=0x{:x} "
                                   "requester={} — no live sharers, converting "
                                   "to GRANT_HANDSHAKE immediately",
                                   _nodeId, line_pa, requesterNode);
                            invOreq->invalidateBarrierDone = true;
                            invOreq->opType = OpType::GRANT_HANDSHAKE;
                            invOreq->stage = OpStage::WAITING_CLEAR;
                            invOreq->replayArmed = true;
                            invOreq->recallBarrierDone = false;
                            if (_outbound) {
                                const bool pushOk = tryPushGrant(
                                    *invOreq, "invalidate-empty");
                                framework::LogWarn("UBCC","[PUSH-GRANT] INVALIDATE-EMPTY home={} "
                                        "pa=0x{:x} requester={} sock={} reqId={} "
                                        "grantType={} pushOk={}",
                                        _nodeId, line_pa, invOreq->requesterNode,
                                        invOreq->requesterSocket, invOreq->reqId,
                                        static_cast<int>(
                                           grantTypeFromIntended(invOreq->intendedState)),
                                        pushOk ? 1 : 0);
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
                    if (outGrantEpoch) *outGrantEpoch = grantOreq->reservedEpoch;
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
                    fatal("UBCC node_id={}: failed to create GRANT_HANDSHAKE "
                          "after removing DONE RECALL PA=0x{:x}",
                          _nodeId, line_pa);
                }
                framework::LogInfo("UBCC",
                        "UBCC node_id={}: RECALL→GRANT_HANDSHAKE transition "
                        "PA=0x{:x} requester={} intended={} dataSource=RecallBuffer (NEW object)",
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
                size_t pendingTotal = 0;
                for (const auto &kv : _pendingRequesters) pendingTotal += kv.second.size();
                auto pendingIt = _pendingRequesters.find(line_pa);
                if (pendingIt == _pendingRequesters.end() &&
                    pendingTotal >= MAX_PENDING_REQUESTERS_TOTAL) {
                    return static_cast<UBCC_OuterGrantType>(-1);
                }
                auto &q = _pendingRequesters[line_pa];
                bool isRS = (reqType == UBCC_OuterReqType::GlobalReadShared);

                // §4.4: Duplicate retry check
                for (auto &pr : q) {
                    if (pr.node == requesterNode && pr.reqId == reqId) {
                        // TC98 fix: rate-limit dup_retry log (RECALL.DONE path)
                        { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
                        framework::LogDebug("UBCC","[UBCC-QUEUE] pa=0x{:x} action=dup_retry "
                               "requester={} reqType={} writeIntent={} reqId={} depth={} (n={})",
                               line_pa, requesterNode,
                               isRS ? "RS" : "RU", writeIntent, reqId, q.size(), _cnt); }
                        return static_cast<UBCC_OuterGrantType>(-1);
                    }
                }

                // C3: RS merge dedup removed — see comment at the other location (~L503)

                if (q.size() < MAX_PENDING_PER_PA &&
                    pendingTotal < MAX_PENDING_REQUESTERS_TOTAL) {
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
                    framework::LogDebug("UBCC","[UBCC-QUEUE] pa=0x{:x} action=enqueue "
                           "requester={} reqType={} writeIntent={} reqId={} depth={} (n={})",
                           line_pa, requesterNode,
                           isRS ? "RS" : "RU", writeIntent, reqId, q.size(), _cnt); }
                } else {
                    // TC98 fix: rate-limit drop_full log (RECALL.DONE path)
                    { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
                    framework::LogDebug("UBCC","[UBCC-QUEUE] pa=0x{:x} action=drop_full "
                           "requester={} reqType={} writeIntent={} reqId={} depth={} (n={})",
                           line_pa, requesterNode,
                           isRS ? "RS" : "RU", writeIntent, reqId, q.size(), _cnt); }
                }
                return static_cast<UBCC_OuterGrantType>(-1);
            }

            if (existingOwner >= 0 && existingOwner != requesterNode
                && !recallAlreadyDone) {
                // v4: Recall needed — create outstanding FIRST, then send RecallReq
                framework::LogInfo("UBCC","[RECALL-CREATE] UBCC node={} PA=0x{:x} existingOwner={} requester={}",
                       _nodeId, line_pa, existingOwner, requesterNode);

                OutstandingRequest *recallOreq = createOutstanding(
                    line_pa, OpType::RECALL, requesterNode, existingOwner,
                    requesterSocket);
                if (!recallOreq) {
                    warn("UBCC node_id={}: failed to create RECALL outstanding "
                         "PA=0x{:x} requester={} owner={}",
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

                // Phase A4: spill-mode guard — G_M + different-owner Recall
                // path must use RecallBuffer, never HomeMemory.
                if (entry.state == MESIState::G_M &&
                    recallOreq->dataSource != GrantDataSource::RecallBuffer) {
                    framework::LogInfo("UBCC",
                                 "[UBCC-SPILL-DIRTY-GRANT-GUARD] home={} pa=0x{:x} "
                                 "G_M+owner recall dataSource={} (expected={}) "
                                 "policy={} owner={} requester={}",
                                 _nodeId, line_pa,
                                 static_cast<int>(recallOreq->dataSource),
                                 static_cast<int>(GrantDataSource::RecallBuffer),
                                 (_overflowPolicy == ResidentOverflowPolicy::NaiveEvict)
                                     ? "naive" : "spill",
                                 existingOwner, requesterNode);
                    fatal("UBCC node_id={}: {}-mode G_M+owner recall dataSource "
                          "mismatch PA=0x{:x}",
                          _nodeId,
                          (_overflowPolicy == ResidentOverflowPolicy::NaiveEvict)
                              ? "naive" : "spill",
                          line_pa);
                }

                if (!initiateRecall(line_pa, entry, *recallOreq)) {
                    warn("UBCC node_id={}: initiateRecall failed PA=0x{:x} - "
                         "removing outstanding",
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
            "UBCC node_id={}: v4 grant decision PA=0x{:x} "
            "prev={} intended_state={} grant={} reservedEpoch={} "
            "(committed DirEntry NOT modified)",
            _nodeId, line_pa,
            mesiStateName(prevState),
            oreq ? mesiStateName(oreq->intendedState) : "none",
            static_cast<int>(grant), reservedEpoch);
    framework::LogInfo("UBCC","[UBCC-GRANT-READY] home={} pa=0x{:x} requester={} grant={} prev={} "
           "intended={} baseEpoch={} reservedEpoch={} reqId={} dataSource={}",
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
    if (oreq && outGrantEpoch)
        *outGrantEpoch = oreq->reservedEpoch;

    // v4-latency: log OUTSTANDING state change
    if (oreq) {
        framework::LogInfo("UBCC-latency",
                "[UBST] tick={} home={},{} pa=0x{:x} old={} new={} epoch={} sharers=0x{:x} action=OUTSTANDING",
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
        << "\"naiveDirtyVictims\":" << _naiveDirtyVictims << ","
        << "\"residentCount\":" << _directory.count() << ","
        << "\"residentCapacity\":" << _directory.capacity();
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
        warn("UBCC node_id={}: initiateRecall called with no outbound sender",
             _nodeId);
        return false;
    }

    const int ownerNode = (recallOreq.targetNode >= 0)
        ? recallOreq.targetNode
        : DirEntry::ownerFromSharers(entry);
    if (ownerNode < 0 || ownerNode >= 64) {
        warn("UBCC node_id={}: initiateRecall invalid ownerNode={} PA=0x{:x}",
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

    framework::LogInfo("UBCC","[RECALL-TRACE-A] UBCC n={} initiateRecall PA=0x{:x} owner={} requester={}",
           _nodeId, line_pa, ownerNode, recallOreq.requesterNode);

    if (!_outbound->sendRecallReq(msg)) {
        warn("UBCC node_id={}: sendRecallReq failed PA=0x{:x} owner={} requester={}",
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

    framework::LogInfo("UBCC","[RECALL-DIAG] UBCC node_id={} processRecallResponse PA=0x{:x} "
           "owner={} epoch={} reqId={}",
           _nodeId, line_pa, ownerNode, responseEpoch, reqId);
    DirEntry entry;
    if (!_directory.lookup(line_pa, entry)) {
        framework::LogDebug("UBCC",
                "UBCC node_id={}: processRecallResponse PA=0x{:x} entry not found",
                _nodeId, line_pa);
        return false;
    }

    if (!checkEpochForLine(line_pa, responseEpoch)) {
        framework::LogWarn("UBCC",
                "UBCC node_id={}: processRecallResponse PA=0x{:x} "
                "STALE epoch — REJECTED",
                _nodeId, line_pa);
        _staleRejectedCount++;
        return false;
    }

    OutstandingRequest *ost = findOutstanding(line_pa);
    if (!ost || ost->opType != OpType::RECALL) {
        framework::LogInfo("UBCC","[RECALL-DIAG] UBCC node_id={} PA=0x{:x} no RECALL outstanding",
               _nodeId, line_pa);
        return false;
    }

    if (ost->targetNode >= 0 && ost->targetNode != ownerNode) {
        warn("UBCC node_id={}: recall owner mismatch PA=0x{:x} expected={} got={}",
             _nodeId, line_pa, ost->targetNode, ownerNode);
        return false;
    }

    if (ost->reqId != 0 && ost->reqId != reqId) {
        warn("UBCC node_id={}: recall reqId mismatch PA=0x{:x} expected={} got={}",
             _nodeId, line_pa, ost->reqId, reqId);
        return false;
    }

    if (ost->recallBarrierDone)
        return true;

    // A clean owner may already have dropped its local line, so an accepted
    // no-data recall can release directory capacity. A dirty owner cannot:
    // its payload remains authoritative until a RecallResp supplies it.
    if (ost->reqType == UBCC_OuterReqType::GlobalInvalidate &&
        DirEntry::protoDirty(entry) && !dataReceived) {
        constexpr uint8_t kMaxRecallRetries = 3;
        if (ost->recallRetries >= kMaxRecallRetries) {
            framework::LogInfo("UBCC",
                         "[UBCC-NAIVE-DIRTY-RECALL-EXHAUSTED] home={} socket={} "
                         "pa=0x{:x} responseOwner={} target={} requester={} "
                         "state={} sharers=0x{:x} residentDirty={} "
                         "responseEpoch={} entryEpoch={} reqId={} "
                         "baseEpoch={} reservedEpoch={} stage={} retries={} "
                         "dataReceived={}",
                         _nodeId, _socketId, line_pa, ownerNode,
                         ost->targetNode, ost->requesterNode,
                         mesiStateName(entry.state), entry.sharersMask,
                         entry.residentDirty ? 1 : 0, responseEpoch,
                         entry.epoch, ost->reqId, ost->baseEpoch,
                         ost->reservedEpoch, static_cast<int>(ost->stage),
                         static_cast<unsigned>(ost->recallRetries),
                         dataReceived ? 1 : 0);
            fatal("UBCC node_id={}: dirty capacity recall exhausted retries "
                  "PA=0x{:x} owner={} reqId={}",
                  _nodeId, line_pa, ownerNode, ost->reqId);
        }
        ++ost->recallRetries;
        ost->createTick = curTick();
        framework::LogInfo("UBCC",
                     "[UBCC-NAIVE-DIRTY-RECALL-NODATA] home={} socket={} "
                     "pa=0x{:x} responseOwner={} target={} requester={} "
                     "state={} sharers=0x{:x} residentDirty={} "
                     "responseEpoch={} entryEpoch={} reqId={} "
                     "baseEpoch={} reservedEpoch={} stage={} "
                     "attempt={}/{}",
                     _nodeId, _socketId, line_pa, ownerNode,
                     ost->targetNode, ost->requesterNode,
                     mesiStateName(entry.state), entry.sharersMask,
                     entry.residentDirty ? 1 : 0, responseEpoch,
                     entry.epoch, ost->reqId, ost->baseEpoch,
                     ost->reservedEpoch, static_cast<int>(ost->stage),
                     ost->recallRetries, kMaxRecallRetries);
        framework::LogInfo("UBCC",
                "UBCC node_id={}: retrying dirty no-data recall PA=0x{:x} "
                "owner={} reqId={} attempt={}",
                _nodeId, line_pa, ownerNode, ost->reqId, ost->recallRetries);
        if (!initiateRecall(line_pa, entry, *ost)) {
            fatal("UBCC node_id={}: dirty capacity recall resend failed "
                  "PA=0x{:x} owner={} reqId={}",
                  _nodeId, line_pa, ownerNode, ost->reqId);
        }
        return false;
    }

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
            _host->writeDsmData(line_pa, recallDone.dataBuf);
            framework::LogInfo("UBCC",
                         "[UBCC-NAIVE-DIRTY-RECALL-PAYLOAD] home={} pa=0x{:x} "
                         "owner={} epoch={}",
                         _nodeId, line_pa, ownerNode,
                         recallDone.reservedEpoch);
        } else {
            framework::LogInfo("UBCC",
                         "[UBCC-NAIVE-DIRTY-RECALL-PAYLOAD] home={} pa=0x{:x} "
                         "owner={} data=0",
                         _nodeId, line_pa, ownerNode);
        }
        entry.state = MESIState::G_I;
        entry.sharersMask = 0;
        entry.residentDirty = false;
        _directory.update(line_pa, entry);
        _directory.forceRemove(line_pa);
        _residentWaiters.erase(line_pa);
        _pendingRequesters.erase(line_pa);
        _evictionPendingRemoval.erase(line_pa);
        replayResidentWaitersForCapacity(line_pa);
        _recallResponseCount++;
        framework::LogInfo("UBCC",
                     "[UBCC-NAIVE-EVICT-DONE] home={} pa=0x{:x} owner={} data={}",
                     _nodeId, line_pa, ownerNode, recallDone.dataValid ? 1 : 0);
        return true;
    }

    OutstandingRequest *grantOst = createOutstanding(
        line_pa, OpType::GRANT_HANDSHAKE, requesterNode, -1,
        recallDone.requesterSocket);
    if (!grantOst) {
        fatal("UBCC node_id={}: failed to create GRANT_HANDSHAKE after "
              "RecallResp PA=0x{:x} requester={}",
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
        bool pushOk = tryPushGrant(*grantOst, "recall");
        framework::LogInfo("UBCC","[PUSH-GRANT] RECALL home={} pa=0x{:x} requester={} sock={} "
               "reqId={} grantType={} dataSource={} pushOk={}",
               _nodeId, line_pa, grantOst->requesterNode,
               grantOst->requesterSocket, grantOst->reqId,
               static_cast<int>(grantTypeFromIntended(grantOst->intendedState)),
               static_cast<int>(grantOst->dataSource),
               pushOk ? 1 : 0);
        if (!pushOk) {
            framework::LogError("UBCC",
                "[PUSH-GRANT-FAIL] home={} pa=0x{:x} requester={} sock={} "
                "reqId={}",
                _nodeId, line_pa, grantOst->requesterNode,
                grantOst->requesterSocket, grantOst->reqId);
        }
    }

    _recallResponseCount++;
    framework::LogInfo("UBCC","[RECALL-TO-GRANT] home={} pa=0x{:x} requester={} owner={} intended={} "
           "baseEpoch={} reservedEpoch={} reqId={} dataSource={}",
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
        warn("UBCC node_id={}: processInvalidationAck PA=0x{:x} "
             "ackNode={} out of range - REJECTED",
             _nodeId, line_pa, ackNode);
        return false;
    }

    DirEntry entry;
    if (!_directory.lookup(line_pa, entry)) {
        framework::LogWarn("UBCC",
                "UBCC node_id={}: processInvalidationAck PA=0x{:x} "
                "entry not found", _nodeId, line_pa);
        return false;
    }

    // v4: Half-range epoch check
    if (!checkEpochForLine(line_pa, responseEpoch)) {
        framework::LogWarn("UBCC",
                "UBCC node_id={}: processInvalidationAck PA=0x{:x} "
                "STALE epoch: response={} directory={} — REJECTED",
                _nodeId, line_pa, responseEpoch, entry.epoch);
        _staleRejectedCount++;
        return false;
    }

    // v4: Verify pending invalidation via OutstandingRequest
    OutstandingRequest *ost = findOutstanding(line_pa);
    if (!ost) {
        // Already completed — idempotent
        framework::LogWarn("UBCC",
                "UBCC node_id={}: processInvalidationAck PA=0x{:x} "
                "no outstanding — idempotent",
                _nodeId, line_pa);
        return true;
    }
    if (ost->reqId != reqId) {
        framework::LogWarn("UBCC",
                "UBCC node_id={}: processInvalidationAck PA=0x{:x} "
                "reqId mismatch: active={} incoming={} - dropped",
                _nodeId, line_pa, ost->reqId, reqId);
        return false;
    }

    // upgrade_invalidate_fix: accept both INVALIDATE and UPGRADE_PENDING (WAITING_ALL_ACKS)
    bool isUpgradePath = (ost->opType == OpType::UPGRADE_PENDING &&
                          ost->stage == OpStage::WAITING_ALL_ACKS);
    bool isInvalidatePath = (ost->opType == OpType::INVALIDATE);
    bool isNaiveEvictPath = (ost->opType == OpType::NAIVE_EVICT_INVALIDATE &&
                             ost->stage == OpStage::WAITING_ALL_ACKS);

    if (!isInvalidatePath && !isNaiveEvictPath && !isUpgradePath) {
        // Wrong op type or stage — idempotent
        framework::LogWarn("UBCC",
                "UBCC node_id={}: processInvalidationAck PA=0x{:x} "
                "opType={} stage={} — not applicable, idempotent",
                _nodeId, line_pa,
                static_cast<int>(ost->opType), static_cast<int>(ost->stage));
        return true;
    }

    // Check for duplicate ack — use upgrade fields or standard fields
    uint64_t nodeBit = (1ULL << ackNode);
    uint64_t &effTargetMask = isUpgradePath ? ost->upgradeTargetMask : ost->totalMask;
    uint64_t &effAckMask = isUpgradePath ? ost->upgradeAckMask : ost->ackMask;

    if (!(effTargetMask & nodeBit)) {
        framework::LogWarn("UBCC",
                "UBCC node_id={}: processInvalidationAck PA=0x{:x} "
                "ackNode={} not in targetMask=0x{:x}",
                _nodeId, line_pa, ackNode, effTargetMask);
        return false;
    }

    if (effAckMask & nodeBit) {
        framework::LogWarn("UBCC",
                "UBCC node_id={}: processInvalidationAck PA=0x{:x} "
                "duplicate ack from node {} — ignoring",
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
    if (isInvalidatePath || isNaiveEvictPath) {
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

    framework::LogWarn("UBCC",
            "UBCC node_id={}: invalidation ack PA=0x{:x} ackNode={} op={} "
            "remaining={} ackMask=0x{:x} targetMask=0x{:x}",
            _nodeId, line_pa, ackNode,
            isUpgradePath ? "UPGRADE" :
            (isNaiveEvictPath ? "NAIVE_EVICT" : "INVALIDATE"),
            isUpgradePath ? ost->upgradePendingAckCount : ost->pendingAckCount,
            effAckMask, effTargetMask);
    framework::LogInfo("UBCC","[UBCC-INV-ACK] home={} pa=0x{:x} ackNode={} op={} remaining={} "
           "ackMask=0x{:x} targetMask=0x{:x} dirState={} dirSharers=0x{:x}",
           _nodeId, line_pa, ackNode,
           isUpgradePath ? "UPGRADE" :
           (isNaiveEvictPath ? "NAIVE_EVICT" : "INVALIDATE"),
           isUpgradePath ? ost->upgradePendingAckCount : ost->pendingAckCount,
           effAckMask, effTargetMask, mesiStateName(entry.state),
           entry.sharersMask);

    _invalidationAckCount++;

    // Check if all invalidations are complete
    bool allAcksDone = isUpgradePath ? (ost->upgradePendingAckCount == 0)
                                     : (ost->pendingAckCount == 0);

    if (allAcksDone) {
        framework::LogWarn("UBCC",
                "UBCC node_id={}: all invalidations complete PA=0x{:x}",
                _nodeId, line_pa);
        framework::LogInfo("UBCC","[UBCC-INV-DONE] home={} pa=0x{:x} op={} requester={} "
               "intended={} baseEpoch={} reservedEpoch={} reqId={}",
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

            framework::LogInfo("UBCC","[UBCC-UPGRADE-ACK] pa=0x{:x} requester={} accepted=1 "
                   "ackMask=0x{:x} targetMask=0x{:x}",
                   line_pa, ost->requesterNode, ost->upgradeAckMask, ost->upgradeTargetMask);

            // Notify the requester that OuterUpgradeAck(true) is ready.
            // Route through the outbound interface (message-based) — the ubio
            // process wires this to UbioBackstoreHost which forwards to the
            // requester via the network/gem5 port. (Previously this used a
            // separate _router pointer that was never set in ubio_main, causing
            // a PANIC and leaving the upgrade pending forever → TC3/8/10/11
            // deadlock.)
            if (!_outbound) {
                fatal("UBCC node_id={}: outbound required for UpgradeAckNotify "
                      "PA=0x{:x} requester={}",
                      _nodeId, line_pa, ost->requesterNode);
            }
            CoherenceMessage notifyMsg;
            notifyMsg.h.type = CoherenceMessageType::UpgradeAckNotify;
            notifyMsg.h.srcNode = _nodeId;
            notifyMsg.h.srcSocket = _socketId;
            notifyMsg.h.dstNode = ost->requesterNode;
            notifyMsg.h.dstSocket = ost->requesterSocket;
            notifyMsg.h.homeNode = _nodeId;
            notifyMsg.h.homeSocket = _socketId;
            notifyMsg.h.requesterNode = ost->requesterNode;
            notifyMsg.h.homeLinePa = line_pa;
            notifyMsg.h.epoch = ost->reservedEpoch;
            notifyMsg.h.reqId = ost->reqId;
            notifyMsg.h.flags =
                static_cast<uint32_t>(cc::glob::CFLAG_ACCEPTED);
            notifyMsg.h.seqNum = 0;
            notifyMsg.h.enqueueTick = curTick();
            notifyMsg.h.readyTick = curTick();

            const int requesterNode = ost->requesterNode;
            const int requesterSocket = ost->requesterSocket;
            const uint64_t baseEpoch = ost->baseEpoch;
            const uint64_t reservedEpoch = ost->reservedEpoch;
            const uint64_t activeReqId = ost->reqId;
            const bool cachedDone = ost->upgradeDoneArrived;
            _outbound->sendUpgradeAckNotify(notifyMsg);

            // The notification may synchronously re-enter UpgradeDone and
            // retire this outstanding. Reacquire and validate the tuple before
            // acting on a cached early Done.
            OutstandingRequest *current = findOutstanding(line_pa);
            if (!current)
                return true;
            if (current->opType != OpType::UPGRADE_PENDING ||
                current->stage != OpStage::WAITING_LOCAL_DONE ||
                current->requesterNode != requesterNode ||
                current->requesterSocket != requesterSocket ||
                current->baseEpoch != baseEpoch ||
                current->reservedEpoch != reservedEpoch ||
                current->reqId != activeReqId) {
                framework::LogWarn("UBCC",
                        "UpgradeAckNotify callback changed active tuple "
                        "PA=0x{:x}; old reqId={} new reqId={}",
                        line_pa, activeReqId, current->reqId);
                return true;
            }

            if (cachedDone) {
                panic_if(current->upgradeDoneEpoch != current->baseEpoch ||
                         current->upgradeDoneReqId != current->reqId,
                         "cached UpgradeDone tuple changed PA=0x{:x} "
                         "cachedEpoch={} baseEpoch={} cachedReqId={} reqId={}",
                         line_pa, current->upgradeDoneEpoch,
                         current->baseEpoch, current->upgradeDoneReqId,
                         current->reqId);
                framework::LogInfo("UBCC","[UPGRADE-TENTATIVE-DONE-CACHED] pa=0x{:x} requester={} "
                       "committing after acks complete (Done was cached)",
                       line_pa, current->requesterNode);
                int intendedOwner = current->intendedOwnerNode;
                uint64_t reservedEp = current->reservedEpoch;
                DirEntry currentEntry;
                panic_if(!_directory.lookup(line_pa, currentEntry),
                         "cached UpgradeDone lost directory entry PA=0x{:x}",
                         line_pa);
                if (!commitIntendedResult(
                        currentEntry, *current, "CachedUpgradeDone")) {
                    retireToTombstone(*current, false);
                    removeOutstanding(line_pa);
                    refreshPinnedBit(line_pa);
                    replayPendingRequesters(line_pa);
                    return true;
                }
                _directory.update(line_pa, currentEntry);
                current->stage = OpStage::DONE;
                current->respTick = curTick();
                removeOutstanding(line_pa);
                refreshPinnedBit(line_pa);

                framework::LogInfo("UBCC","[UBCC-UPGRADE-COMMIT] pa=0x{:x} owner={} reservedEpoch={}",
                       line_pa, intendedOwner, reservedEp);

                // Replay queued requesters after commit
                replayPendingRequesters(line_pa);
                replayResidentWaiters(line_pa);
                replayResidentWaitersForCapacity(line_pa);
            }
            return true;
        } else if (isNaiveEvictPath) {
            removeOutstanding(line_pa);
            _directory.forceRemove(line_pa);
            _residentWaiters.erase(line_pa);
            _pendingRequesters.erase(line_pa);
            _evictionPendingRemoval.erase(line_pa);
            replayResidentWaitersForCapacity(line_pa);
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
                const bool pushOk = tryPushGrant(*ost, "invalidate");
                framework::LogWarn("UBCC","[PUSH-GRANT] INVALIDATE home={} pa=0x{:x} requester={} "
                       "sock={} reqId={} grantType={} pushOk={}",
                       _nodeId, line_pa, ost->requesterNode,
                       ost->requesterSocket, ost->reqId,
                       static_cast<int>(grantTypeFromIntended(ost->intendedState)),
                       pushOk ? 1 : 0);
            }

            framework::LogInfo("UBCC","[UBCC-INV-TO-GRANT] home={} pa=0x{:x} requester={} stage={} "
                   "intended={} baseEpoch={} reservedEpoch={} reqId={}",
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
        oit->second.opType == OpType::UPGRADE_PENDING) {
        // Once all acks have arrived, an idempotent UpgradeReq replay acts as
        // the lost AckNotify replacement. Returning zero tells the requester
        // that the deferred ack is now ready.
        if (oit->second.stage == OpStage::WAITING_LOCAL_DONE &&
            oit->second.accepted)
            return 0;
        return oit->second.upgradeTargetMask;
    }
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
                                  uint64_t epochVal, bool keepAsClean,
                                  const uint8_t *data)
{
    epochVal = normalizeEpoch(epochVal);

    framework::LogInfo("UBCC",
            "UBCC node_id={}: processWriteback PA=0x{:x} "
            "requesterNode={} epoch={} keepAsClean={}",
            _nodeId, line_pa, requesterNode, epochVal, keepAsClean);
    framework::LogInfo("UBCC","[UBCC-WB-ENTER] home={} pa=0x{:x} node={} keepAsClean={} epoch={}",
           _nodeId, line_pa, requesterNode, keepAsClean, epochVal);
    framework::LogInfo("UBCC", "[UBCC-WB-REQ] home={} pa=0x{:x} type=WritebackReq node={} keepAsClean={}",
            _nodeId, line_pa, requesterNode, keepAsClean);

    DirEntry entry;
    PendingRequester prCtx;
    prCtx.opKind = ResidentOpKind::Writeback;
    prCtx.node = requesterNode;
    prCtx.socket = -1;
    prCtx.reqType = UBCC_OuterReqType::GlobalWriteback;
    prCtx.writeIntent = false;
    prCtx.epoch = epochVal;
    prCtx.reqId = 0;
    prCtx.wbKeepAsClean = keepAsClean;
    if (data) {
        std::memcpy(prCtx.data.data(), data, 64);
        prCtx.hasData = true;
    }
    ResidentAccessResult rr = ensureResidentForAccess(
        line_pa, prCtx, entry);
    if (rr != ResidentAccessResult::Ready) {
        OutstandingRequest *blocked = findOutstanding(line_pa);
        if (blocked && blocked->opType == OpType::RECALL &&
            blocked->reqType == UBCC_OuterReqType::GlobalInvalidate) {
            framework::LogInfo("UBCC",
                         "[UBCC-NAIVE-DIRTY-RECALL-WB-RESIDENT-BLOCK] "
                         "home={} socket={} pa=0x{:x} requester={} "
                         "wbEpoch={} keepAsClean={} hasData={} "
                         "residentResult={} target={} reqId={} "
                         "baseEpoch={} reservedEpoch={} stage={}",
                         _nodeId, _socketId, line_pa, requesterNode,
                         epochVal, keepAsClean ? 1 : 0, data ? 1 : 0,
                         static_cast<int>(rr), blocked->targetNode,
                         blocked->reqId, blocked->baseEpoch,
                         blocked->reservedEpoch,
                         static_cast<int>(blocked->stage));
        }
        return false;
    }

    // A dirty owner may begin its normal writeback just before a naive
    // capacity recall reaches it. If the writeback carries the exact owner,
    // epoch, and payload required by that recall, it is the authoritative
    // recall completion. Rejecting it as BUSY leaves an already-invalidated
    // owner able to answer subsequent recalls only with no data.
    OutstandingRequest *active = findOutstanding(line_pa);
    if (active && active->opType == OpType::RECALL &&
        active->reqType == UBCC_OuterReqType::GlobalInvalidate) {
        const bool stageMatch = active->stage == OpStage::WAITING_TARGET_RESP;
        const bool ownerMatch = active->targetNode == requesterNode;
        const bool epochMatch = normalizeEpoch(active->baseEpoch) == epochVal;
        const bool payloadMatch = data != nullptr;
        const bool dirtyRelease = !keepAsClean;
        framework::LogInfo("UBCC",
                     "[UBCC-NAIVE-DIRTY-RECALL-WB-CHECK] home={} socket={} "
                     "pa=0x{:x} requester={} target={} state={} "
                     "sharers=0x{:x} residentDirty={} wbEpoch={} "
                     "entryEpoch={} baseEpoch={} reservedEpoch={} "
                     "reqId={} stage={} keepAsClean={} hasData={} "
                     "stageMatch={} ownerMatch={} epochMatch={} "
                     "payloadMatch={} dirtyRelease={}",
                     _nodeId, _socketId, line_pa, requesterNode,
                     active->targetNode, mesiStateName(entry.state),
                     entry.sharersMask, entry.residentDirty ? 1 : 0,
                     epochVal, entry.epoch, active->baseEpoch,
                     active->reservedEpoch, active->reqId,
                     static_cast<int>(active->stage), keepAsClean ? 1 : 0,
                     data ? 1 : 0, stageMatch ? 1 : 0,
                     ownerMatch ? 1 : 0, epochMatch ? 1 : 0,
                     payloadMatch ? 1 : 0, dirtyRelease ? 1 : 0);
    }
    if (active && active->opType == OpType::RECALL &&
        active->reqType == UBCC_OuterReqType::GlobalInvalidate &&
        active->stage == OpStage::WAITING_TARGET_RESP &&
        active->targetNode == requesterNode &&
        normalizeEpoch(active->baseEpoch) == epochVal &&
        data && !keepAsClean) {
        DataBlock payload(64);
        payload.setData(data, 0, 64);
        const bool accepted = processRecallResponse(
            line_pa, requesterNode, true, epochVal, active->reqId, &payload);
        if (accepted) {
            ++_writebackCount;
            framework::LogInfo("UBCC",
                         "[UBCC-NAIVE-DIRTY-RECALL-WB-MERGE] home={} "
                         "pa=0x{:x} owner={} epoch={}",
                         _nodeId, line_pa, requesterNode, epochVal);
        }
        return accepted;
    }

    // v4: Outstanding-aware BUSY check (§4.6.2)
    if (isLineBusy(line_pa)) {
        // TC98 fix: rate-limit writeback BUSY log
        { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
        framework::LogWarn("UBCC",
                "UBCC node_id={}: processWriteback PA=0x{:x} "
                "line busy (outstanding active) — BUSY/RETRY (n={})",
                _nodeId, line_pa, _cnt); }
        return false;
    }

    // A recalled owner can issue its dirty release after the recall commits
    // G_M -> G_S and advances the directory epoch. Accept that stale tuple
    // only while the old owner remains a current sharer.
    const uint64_t requesterBit = requesterNode >= 0 && requesterNode < 64
        ? (1ULL << requesterNode) : 0;
    const bool delayedSharedRelease = entry.state == MESIState::G_S &&
        requesterBit && (entry.sharersMask & requesterBit) && !keepAsClean;

    // ---- M7: Stale epoch check ----
    if (!delayedSharedRelease && !checkEpochForLine(line_pa, epochVal)) {
        framework::LogWarn("UBCC",
                "UBCC node_id={}: processWriteback PA=0x{:x} "
                "STALE epoch: msg={} directory={} — REJECTED",
                _nodeId, line_pa, epochVal, entry.epoch);
        _staleRejectedCount++;
        return false;
    }

    const bool hasData = data != nullptr;

    // A clean shared release removes only the releasing sharer. A data-bearing
    // request in G_S is a delayed dirty-owner payload that arrived after a
    // recall converted the line to shared; persist its bytes, but still remove
    // only that requester's shared copy. Clearing the whole mask here loses
    // live sharers and makes their next Upgrade permanently fail.
    if (entry.state == MESIState::G_S) {
        if (!requesterBit || !(entry.sharersMask & requesterBit) || keepAsClean) {
            framework::LogWarn("UBCC",
                    "UBCC node_id={}: shared release rejected PA=0x{:x} "
                    "requesterNode={} sharersMask=0x{:x} keepAsClean={}",
                    _nodeId, line_pa, requesterNode, entry.sharersMask,
                    keepAsClean ? 1 : 0);
            return false;
        }
        entry.sharersMask &= ~requesterBit;
        entry.state = entry.sharersMask ? MESIState::G_S : MESIState::G_I;
        framework::LogInfo("UBCC",
                "[UBCC-SHARED-RELEASE] home={} pa=0x{:x} node={} hasData={} "
                "remainingSharers=0x{:x}",
                _nodeId, line_pa, requesterNode, hasData ? 1 : 0,
                entry.sharersMask);
    } else if (entry.state == MESIState::G_E) {
        // G_E is clean: its normal release is EvictReq. Accepting WritebackReq
        // would blur clean eviction and dirty-data persistence contracts.
        framework::LogWarn("UBCC",
                "UBCC node_id={}: G_E WritebackReq rejected PA=0x{:x} "
                "requesterNode={} hasData={}; use EvictReq",
                _nodeId, line_pa, requesterNode, hasData ? 1 : 0);
        return false;
    } else {
    // ---- M7: Owner match check ----
    // G_M writeback must come from the current owner. G_I accepts an
    // idempotent duplicate carrying the already-issued dirty payload.
    int ownerNode = DirEntry::ownerFromSharers(entry);
    if (ownerNode >= 0 && ownerNode != requesterNode) {
        framework::LogError("UBCC",
                "UBCC node_id={}: processWriteback PA=0x{:x} "
                "OWNER MISMATCH: requesterNode={} != ownerNode={} — REJECTED",
                _nodeId, line_pa, requesterNode, ownerNode);
        _ownerMismatchRejectedCount++;
        return false;
    }

    if (entry.state == MESIState::G_M && !hasData) {
        framework::LogWarn("UBCC",
                "UBCC node_id={}: G_M WritebackReq rejected PA=0x{:x} "
                "requesterNode={} missing dirty payload",
                _nodeId, line_pa, requesterNode);
        return false;
    }

    // G_M dirty data is written back. keepAsClean downgrades the owner to G_E;
    // otherwise it drops the line. G_I handling is an idempotent duplicate.
    if (entry.state == MESIState::G_M && keepAsClean && requesterNode >= 0) {
        // Owner writes back but retains clean exclusive
        entry.state = MESIState::G_E;
        entry.sharersMask = (1ULL << requesterNode);
    } else {
        // Owner drops the line, or duplicate writeback remains invalid.
        entry.state = MESIState::G_I;
        entry.sharersMask = 0;
    }
    }
    // Phase A4: residentDirty tracks resident-metadata dirtiness (whether
    // the directory entry needs a backstore flush), NOT home dirty-data
    // authority.  The home may or may not have the actual data bytes.
    entry.residentDirty = true;
    // v4: DirEntry.pendingOp removed — no-op here

    _writebackCount++;

    framework::LogInfo("UBCC",
            "UBCC node_id={}: processWriteback PA=0x{:x} complete "
             "newState={} ownerNode={} dirty={}",
             _nodeId, line_pa, mesiStateName(entry.state),
             DirEntry::ownerFromSharers(entry), DirEntry::protoDirty(entry));

    // Make home data authoritative before publishing the owner release.
    if (data && _host) {
        _host->writeDsmData(line_pa, data);
        framework::LogInfo("UBCC",
                     "[WB-DATA-PERSIST] home={} pa=0x{:x} node={} source=writeback",
                     _nodeId, line_pa, requesterNode);
        // ── Phase C4 trace point 5: persisted word ──
        {
            uint64_t off = line_pa & 0x1FFFULL;
            uint64_t ckOff = line_pa & 0xFFFFFULL;
            if (ckOff < 0x80000ULL && (off % 64 == 0)) {
                uint64_t w0;
                std::memcpy(&w0, data, 8);
                framework::LogInfo("UBCC",
                    "[C4-PERSIST] home={} pa=0x{:x} off=0x{:x} w0=0x{:x}",
                    _nodeId, line_pa, off, w0);
            }
        }
    }

    _directory.update(line_pa, entry);
    _directory.touch(line_pa);
    refreshPinnedBit(line_pa);
    // UBInvariant: validate canonical form after writeback
    validateSharersCanonical(line_pa);

    // Only spill policy persists resident metadata. Naive policy reclaims
    // capacity through recalls and must not create a backstore copy.
    if (_overflowPolicy == ResidentOverflowPolicy::Spill) {
        _directory.setWbPending(line_pa, true);
        _directory.setPinned(line_pa, true);
        scheduleBackstoreWrite(line_pa);
    }

    return true;
}

bool
UBCCController::processWritebackWithData(uint64_t line_pa, int requesterNode,
                                         uint64_t epochVal, bool keepAsClean,
                                         const uint8_t *data)
{
    return processWriteback(line_pa, requesterNode, epochVal, keepAsClean, data);
}

// ---- v4: Home Writeback Completion (HN-F→EP-SNF→DRAM) ----

void
UBCCController::notifyHomeWritebackComplete(uint64_t homePa)
{
    framework::LogInfo("UBCC","[UBCC-HOME-WB] home={} pa=0x{:x}", _nodeId, homePa);
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
        framework::LogDebug("UBCC","[UBCC-HOME-WB] home={} pa=0x{:x} BUSY — deferred (n={})",
               _nodeId, homePa, _cnt); }
        return;
    }

    int oldOwner = DirEntry::ownerFromSharers(entry);
    framework::LogInfo("UBCC","[UBCC-HOME-WB] home={} pa=0x{:x} oldState={} owner={} epoch={}",
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
            "UBCC node_id={}: processEvict PA=0x{:x} "
            "evictingNode={} epoch={}",
            _nodeId, line_pa, evictingNode, epochVal);

    DirEntry entry;
    PendingRequester prCtx;
    prCtx.opKind = ResidentOpKind::Evict;
    prCtx.node = evictingNode;
    prCtx.socket = -1;
    prCtx.reqType = UBCC_OuterReqType::GlobalEvict;
    prCtx.writeIntent = false;
    prCtx.epoch = epochVal;
    prCtx.reqId = 0;
    ResidentAccessResult rr = ensureResidentForAccess(
        line_pa, prCtx, entry);
    if (rr != ResidentAccessResult::Ready) {
        return false;
    }

    // ---- M7: Stale epoch check ----
    if (!checkEpochForLine(line_pa, epochVal)) {
        framework::LogDebug("UBCC",
                "UBCC node_id={}: processEvict PA=0x{:x} "
                "STALE epoch: msg={} directory={} — REJECTED",
                _nodeId, line_pa, epochVal, entry.epoch);
        _staleRejectedCount++;
        return false;
    }

    // Phase 2: Line busy check unified to OutstandingRequest-aware
    if (isLineBusy(line_pa)) {
        // TC98 fix: rate-limit evict BUSY log
        { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
        framework::LogWarn("UBCC",
                "UBCC node_id={}: processEvict PA=0x{:x} "
                "line busy — rejected (n={})",
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
                    "UBCC node_id={}: processEvict PA=0x{:x} "
                    "dirty owner evict not allowed — must writeback first",
                    _nodeId, line_pa);
            return false;
        }
        entry.sharersMask = 0; // Exclusive owner has no sharers
        removedFromOwner = true;
    }

    // ---- M7 P0-3: Reject evict if node is neither owner nor sharer ----
    if (!removedFromSharer && !removedFromOwner) {
        framework::LogWarn("UBCC",
                "UBCC node_id={}: processEvict PA=0x{:x} "
                "evictingNode={} is neither owner (ownerNode={}) nor sharer "
                "(sharersMask=0x{:x}) — REJECTED",
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
            "UBCC node_id={}: processEvict PA=0x{:x} complete "
            "removedSharer={} removedOwner={} newState={} "
             "sharersMask=0x{:x} ownerNode={}",
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
    bool* outNotSharer, bool* outDeferred, int requesterSocket)
{
    epoch = normalizeEpoch(epoch);
    if (outNotSharer)
        *outNotSharer = false;
    if (outDeferred)
        *outDeferred = false;

    framework::LogInfo("UBCC",
            "UBCC node_id={}: processOuterUpgradeReq PA=0x{:x} "
            "requesterNode={} epoch={} reqId={} desiredPerm={}",
            _nodeId, line_pa, requesterNode, epoch, reqId, desiredPerm);

    DirEntry entry;
    PendingRequester prCtx;
    prCtx.opKind = ResidentOpKind::Upgrade;
    prCtx.node = requesterNode;
    prCtx.socket = requesterSocket;
    prCtx.reqType = UBCC_OuterReqType::GlobalReadUnique;
    prCtx.writeIntent = true;
    prCtx.epoch = epoch;
    prCtx.reqId = reqId;
    prCtx.upgradeDesiredPerm = desiredPerm;
    prCtx.upgradeCause = cause;
    ResidentAccessResult rr = ensureResidentForAccess(
        line_pa, prCtx, entry);
    if (rr != ResidentAccessResult::Ready) {
        if (outDeferred)
            *outDeferred = true;
        return false;
    }

    // Check if requester is a committed sharer
    if (requesterNode >= 0) {
        uint64_t reqBit = (1ULL << requesterNode);
        if (!(entry.sharersMask & reqBit)) {
            framework::LogWarn("UBCC",
                    "UBCC node_id={}: upgrade rejected — "
                    "PA=0x{:x} requesterNode={} not in sharersMask=0x{:x}",
                    _nodeId, line_pa, requesterNode, entry.sharersMask);
            // PERMANENT reject: requester was invalidated (lost the race). It
            // must abandon and re-fetch via ReadUnique instead of retrying.
            if (outNotSharer)
                *outNotSharer = true;
            return false;
        }
    }

    // Exact retransmission of an accepted upgrade is idempotent. This covers
    // both a duplicated initial request and the watchdog replay used when the
    // asynchronous UpgradeAckNotify is lost. The response builder reports the
    // outstanding's current target mask; once it reaches WAITING_LOCAL_DONE,
    // getUpgradePendingTargetMask() returns zero to complete the requester.
    OutstandingRequest *existing = findOutstanding(line_pa);
    if (existing && existing->opType == OpType::UPGRADE_PENDING &&
        existing->requesterNode == requesterNode &&
        existing->requesterSocket == requesterSocket &&
        existing->baseEpoch == epoch && existing->reqId == reqId) {
        framework::LogInfo("UBCC",
                "UBCC node_id={}: replaying exact UpgradeReq PA=0x{:x} "
                "requesterNode={} requesterSocket={} epoch={} reqId={} "
                "stage={}",
                _nodeId, line_pa, requesterNode, requesterSocket, epoch, reqId,
                static_cast<int>(existing->stage));
        return true;
    }

    // Any non-matching outstanding still conflicts with this upgrade.
    if (existing) {
        framework::LogWarn("UBCC",
                "UBCC node_id={}: upgrade rejected — "
                "existing outstanding for PA=0x{:x}",
                _nodeId, line_pa);
        return false;
    }

    // v4: Allocate reserved epoch (committed epoch + 1)
    uint64_t reservedEpoch = allocateReservedEpoch(entry);

    // upgrade_invalidate_fix D3: freeze targetMask at acceptance time
    uint64_t reqBit = (1ULL << requesterNode);
    uint64_t targetMask = entry.sharersMask & ~reqBit;
    targetMask &= ~_exitedPeerNodesMask;

    // Create UPGRADE_PENDING outstanding
    OutstandingRequest *oreq = createOutstanding(
        line_pa, OpType::UPGRADE_PENDING, requesterNode, -1,
        requesterSocket);
    if (!oreq) {
        framework::LogError("UBCC",
                "UBCC node_id={}: upgrade rejected — "
                "failed to create outstanding for PA=0x{:x}",
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

        framework::LogInfo("UBCC","[UBCC-UPGRADE] pa=0x{:x} requester={} stage=WAITING_ALL_ACKS "
               "targetMask=0x{:x} pendingAckCount={}",
               line_pa, requesterNode, effectiveMask, oreq->upgradePendingAckCount);

        framework::LogWarn("UBCC",
                "UBCC node_id={}: upgrade accepted pending PA=0x{:x} "
                "reservedEpoch={} reqId={} targetMask=0x{:x} — "
                "waiting for invalidation acks before Ack(true)",
                _nodeId, line_pa, reservedEpoch, reqId, effectiveMask);
    } else {
        // upgrade_invalidate_fix: no other sharers — fast path
        oreq->stage = OpStage::WAITING_LOCAL_DONE;
        oreq->accepted = true;
        oreq->upgradeTargetMask = 0;
        oreq->upgradePendingAckCount = 0;
        oreq->upgradeAckMask = 0;

        framework::LogInfo("UBCC","[UBCC-UPGRADE] pa=0x{:x} requester={} stage=WAITING_LOCAL_DONE "
               "targetMask=0 (no other sharers)",
               line_pa, requesterNode);

        framework::LogInfo("UBCC",
                "UBCC node_id={}: upgrade accepted immediate PA=0x{:x} "
                "reservedEpoch={} reqId={} — no other sharers, Ack(true) now",
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
            "UBCC node_id={}: processOuterUpgradeDone PA=0x{:x} "
            "requesterNode={} epoch={} reqId={}",
            _nodeId, line_pa, requesterNode, epoch, reqId);

    DirEntry entry;
    if (!_directory.lookup(line_pa, entry)) {
        framework::LogWarn("UBCC",
                "UBCC node_id={}: processOuterUpgradeDone PA=0x{:x} "
                "entry not found", _nodeId, line_pa);
        return false;
    }

    // Verify UPGRADE_PENDING outstanding
    OutstandingRequest *ost = findOutstanding(line_pa);
    if (!ost || ost->opType != OpType::UPGRADE_PENDING) {
        framework::LogInfo("UBCC",
                "UBCC node_id={}: processOuterUpgradeDone PA=0x{:x} "
                "no UPGRADE_PENDING outstanding", _nodeId, line_pa);
        return false;
    }

    // Verify matching tuple
    if (ost->requesterNode != requesterNode ||
        normalizeEpoch(ost->baseEpoch) != epoch || ost->reqId != reqId) {
        warn("UBCC node_id={}: UpgradeDone tuple mismatch PA=0x{:x} "
             "incoming requester={} epoch={} reqId={} active requester={} "
             "baseEpoch={} reqId={} - dropped",
             _nodeId, line_pa, requesterNode, epoch, reqId,
             ost->requesterNode, normalizeEpoch(ost->baseEpoch), ost->reqId);
        return false;
    }

    // upgrade_invalidate_fix D4 (TENTATIVE): Done may arrive before acks complete
    if (ost->stage == OpStage::WAITING_ALL_ACKS) {
        // TENTATIVE: cache the Done tuple, do NOT commit yet
        ost->upgradeDoneArrived = true;
        ost->upgradeDoneEpoch = epoch;
        ost->upgradeDoneReqId = reqId;
        ost->upgradeSavedStage = ost->stage;

        framework::LogInfo("UBCC","[UPGRADE-TENTATIVE-DONE-CACHED] pa=0x{:x} requester={} "
               "stage=WAITING_ALL_ACKS (Done arrived before all acks) "
               "cachedEpoch={} cachedReqId={}",
               line_pa, requesterNode, epoch, reqId);

        framework::LogInfo("UBCC",
                "UBCC node_id={}: UpgradeDone TENTATIVE cached PA=0x{:x} "
                "requester={} — waiting for remaining acks",
                _nodeId, line_pa, requesterNode);

        return true; // accepted but not committed
    }

    if (ost->stage != OpStage::WAITING_LOCAL_DONE) {
        framework::LogWarn("UBCC",
                "UBCC node_id={}: processOuterUpgradeDone PA=0x{:x} "
                "wrong stage={} — rejecting",
                _nodeId, line_pa, static_cast<int>(ost->stage));
        return false;
    }

    // upgrade_invalidate_fix: only commit when WAITING_LOCAL_DONE and accepted
    if (!ost->accepted) {
        framework::LogWarn("UBCC",
                "UBCC node_id={}: processOuterUpgradeDone PA=0x{:x} "
                "not yet accepted — rejecting",
                _nodeId, line_pa);
        return false;
    }

    // v4: §4.1.4 step 5 — commit intended result to DirEntry
    int intendedOwner = ost->intendedOwnerNode;
    uint64_t reservedEp = ost->reservedEpoch;
    if (!commitIntendedResult(entry, *ost, "UpgradeDone")) {
        retireToTombstone(*ost, false);
        removeOutstanding(line_pa);
        refreshPinnedBit(line_pa);
        replayPendingRequesters(line_pa);
        return false;
    }
    _directory.update(line_pa, entry);
    // UBInvariant: validate canonical form after commit
    validateSharersCanonical(line_pa);

    // Retire UPGRADE_PENDING
    ost->stage = OpStage::DONE;
    ost->respTick = curTick();
    removeOutstanding(line_pa);
    refreshPinnedBit(line_pa);

    framework::LogInfo("UBCC","[UBCC-UPGRADE-COMMIT] pa=0x{:x} owner={} reservedEpoch={}",
           line_pa, intendedOwner, reservedEp);

    framework::LogInfo("UBCC",
            "UBCC node_id={}: upgrade committed PA=0x{:x} "
            "newState={} owner={} epoch={}",
            _nodeId, line_pa, mesiStateName(entry.state),
            DirEntry::ownerFromSharers(entry), entry.epoch);

    // Replay queued requesters after commit
    replayPendingRequesters(line_pa);
    replayResidentWaiters(line_pa);
    replayResidentWaitersForCapacity(line_pa);

    return true;
}

// ---- v4: Clear / ClearAck (§3.5) ----

bool
UBCCController::processClear(
    uint64_t line_pa, int srcNode,
    uint64_t epoch, uint64_t reqId)
{
    if (_debugClearTrace) {
        framework::LogInfo("UBCC",
                     "[DEBUG-UBCC-CLEAR] enter home={} pa=0x{:x} srcNode={} epoch={} reqId={}",
                     _nodeId, line_pa, srcNode, epoch, reqId);
    }
    epoch = normalizeEpoch(epoch);
    appendTmpLog(
        "ubcc_clear.log",
        "[CLEAR] pa=0x%lx epoch=%lu reqId=%lu\n",
        line_pa, epoch, reqId);

    framework::LogInfo("UBCC",
            "UBCC node_id={}: processClear PA=0x{:x} "
            "srcNode={} epoch={} reqId={}",
            _nodeId, line_pa, srcNode, epoch, reqId);

    // Check tombstone first (duplicate Clear within window W)
    bool tsAccepted = false;
    if (checkTombstone(line_pa, epoch, reqId, tsAccepted)) {
        // UBInvariant: log tombstone replay (warning-level)
        _tombstoneReplayCount++;
        framework::LogInfo("UBCC-invariant",
                "[UBINV-INFO] tombstone replay #{} PA=0x{:x} "
                "epoch={} reqId={} accepted={}",
                _tombstoneReplayCount, line_pa, epoch, reqId, tsAccepted);
        framework::LogInfo("UBCC",
                "UBCC node_id={}: tombstone replay PA=0x{:x} "
                "epoch={} reqId={} accepted={}",
                _nodeId, line_pa, epoch, reqId, tsAccepted);
        if (_debugClearTrace) {
            framework::LogInfo("UBCC",
                         "[DEBUG-UBCC-CLEAR] tombstone-replay home={} pa=0x{:x} epoch={} reqId={} accepted={}",
                         _nodeId, line_pa, epoch, reqId, tsAccepted ? 1 : 0);
        }
        return tsAccepted;
    }

    DirEntry entry;
    if (!_directory.lookup(line_pa, entry)) {
        // Stale Clear for unknown line — log and drop (§3.5)
        if (_debugClearTrace) {
            framework::LogWarn("UBCC",
                         "[DEBUG-UBCC-CLEAR] drop home={} pa=0x{:x} reason=unknown_line epoch={} reqId={}",
                         _nodeId, line_pa, epoch, reqId);
        }
        warn("UBCC node_id={}: stale Clear for unknown PA=0x{:x} - dropped",
             _nodeId, line_pa);
        return false;
    }

    // Verify GRANT_HANDSHAKE outstanding
    OutstandingRequest *ost = findOutstanding(line_pa);
    if (_debugClearTrace) {
        framework::LogInfo("UBCC",
                     "[DEBUG-TC5-CLEAR-TRACE] processClearEnter home={} pa=0x{:x} src={} "
                     "epoch={} reqId={} hasOutstanding={}",
                     _nodeId, line_pa, srcNode, epoch, reqId, ost ? 1 : 0);
        if (ost) {
            framework::LogInfo("UBCC",
                         " opType={} stage={} ostRequester={} ostBase={} ostReserved={} ostReqId={}",
                         static_cast<int>(ost->opType), static_cast<int>(ost->stage),
                         ost->requesterNode, ost->baseEpoch, ost->reservedEpoch,
                         ost->reqId);
        }
        framework::LogInfo("UBCC", "");
    }

    if (!ost || ost->opType != OpType::GRANT_HANDSHAKE) {
        // No active GRANT_HANDSHAKE — check for already-completed
        // (might be tombstone already cleaned up)
        if (_debugClearTrace) {
            framework::LogWarn("UBCC",
                         "[DEBUG-TC5-CLEAR-TRACE] processClearDrop home={} pa=0x{:x} src={} "
                         "reason=no_grant_handshake",
                         _nodeId, line_pa, srcNode);
            framework::LogWarn("UBCC",
                         "[DEBUG-UBCC-CLEAR] drop home={} pa=0x{:x} reason=no_grant_handshake epoch={} reqId={}",
                         _nodeId, line_pa, epoch, reqId);
        }
        warn("UBCC node_id={}: processClear PA=0x{:x} "
             "no GRANT_HANDSHAKE outstanding - dropped",
             _nodeId, line_pa);
        return false;
    }

    // Verify reqId match
    if (ost->reqId != reqId) {
        if (_debugClearTrace) {
            framework::LogError("UBCC",
                         "[DEBUG-TC5-CLEAR-TRACE] processClearDrop home={} pa=0x{:x} src={} "
                         "reason=reqid_mismatch ostReqId={} clearReqId={}",
                         _nodeId, line_pa, srcNode, ost->reqId, reqId);
            framework::LogError("UBCC",
                         "[DEBUG-UBCC-CLEAR] drop home={} pa=0x{:x} reason=reqid_mismatch ostReqId={} clearReqId={}",
                         _nodeId, line_pa, ost->reqId, reqId);
        }
        warn("UBCC node_id={}: processClear PA=0x{:x} "
             "reqId mismatch: ost={} clear={} - dropped",
             _nodeId, line_pa, ost->reqId, reqId);
        return false;
    }

    // F2: Strong validation — requesterNode must match srcNode
    if (ost->requesterNode >= 0 && ost->requesterNode != srcNode) {
        if (_debugClearTrace) {
            framework::LogError("UBCC",
                         "[DEBUG-TC5-CLEAR-TRACE] processClearDrop home={} pa=0x{:x} src={} "
                         "reason=requester_mismatch ostRequester={}",
                         _nodeId, line_pa, srcNode, ost->requesterNode);
            framework::LogError("UBCC",
                         "[DEBUG-UBCC-CLEAR] drop home={} pa=0x{:x} reason=requester_mismatch ostRequester={} srcNode={} reqId={}",
                         _nodeId, line_pa, ost->requesterNode, srcNode, reqId);
        }
        warn("UBCC node_id={}: processClear PA=0x{:x} "
               "requesterNode mismatch: ost={} clear={} - dropped",
              _nodeId, line_pa, ost->requesterNode, srcNode);
        return false;
    }

    // F2: Stage must be WAITING_CLEAR — only accept Clear for an active
    // GRANT_HANDSHAKE that is actually expecting a Clear commit.
    if (ost->stage != OpStage::WAITING_CLEAR) {
        if (_debugClearTrace) {
            framework::LogError("UBCC",
                         "[DEBUG-TC5-CLEAR-TRACE] processClearDrop home={} pa=0x{:x} src={} "
                         "reason=stage_mismatch stage={}",
                         _nodeId, line_pa, srcNode, static_cast<int>(ost->stage));
            framework::LogError("UBCC",
                         "[DEBUG-UBCC-CLEAR] drop home={} pa=0x{:x} reason=stage_mismatch stage={} reqId={}",
                         _nodeId, line_pa, static_cast<int>(ost->stage), reqId);
        }
        warn("UBCC node_id={}: processClear PA=0x{:x} "
               "stage mismatch: expected WAITING_CLEAR got {} - dropped",
              _nodeId, line_pa, static_cast<int>(ost->stage));
        return false;
    }

    // The message now matches the active requester/reqId/stage tuple. An epoch
    // mismatch belongs to this transaction rather than an unrelated stale
    // Clear, so retire the failed grant to preserve the existing recovery path.
    if (normalizeEpoch(ost->baseEpoch) != epoch) {
        warn("UBCC node_id={}: processClear PA=0x{:x} "
             "epoch mismatch for active tuple: ost_base={} clear={} - retiring",
             _nodeId, line_pa, normalizeEpoch(ost->baseEpoch), epoch);
        retireToTombstone(*ost, false);
        removeOutstanding(line_pa);
        return false;
    }

    // v4: GRANT_HANDSHAKE existence + correct stage implies prerequisites DONE.
    // The upstream processOuterRequest / processInvalidationAck only creates
    // GRANT_HANDSHAKE after all barriers (RECALL/INVALIDATE) have completed.

    // v4: §3.3, §3.5 — commit intended result to committed DirEntry
    MESIState oldState = entry.state;
    if (!commitIntendedResult(entry, *ost, "Clear")) {
        retireToTombstone(*ost, false);
        removeOutstanding(line_pa);
        refreshPinnedBit(line_pa);
        replayPendingRequesters(line_pa);
        return false;
    }
    _directory.update(line_pa, entry);
    // UBInvariant: validate canonical form after commit
    validateSharersCanonical(line_pa);

    // v4-latency: log COMMIT state change
    framework::LogInfo("UBCC-latency",
            "[UBST] tick={} home={},{} pa=0x{:x} old={} new={} epoch={} sharers=0x{:x} action=COMMIT",
            curTick(), _nodeId, _socketId, line_pa,
            mesiStateName(oldState),
            mesiStateName(entry.state),
            entry.epoch,
            entry.sharersMask);

    // Retire GRANT_HANDSHAKE to tombstone(W) for duplicate Clear replay
    retireCommittedResidentWaiters(*ost);
    retireToTombstone(*ost, true);
    removeOutstanding(line_pa);
    refreshPinnedBit(line_pa);

    // recall_done_fix.md §5: Replay queued pending requesters using the
    // newly committed state (just committed by this Clear).
    replayPendingRequesters(line_pa);
    replayResidentWaiters(line_pa);
    // Clear removes the grant-handshake pin. A different PA waiting on the
    // same full set may now evict this entry and must not lose that wakeup.
    replayResidentWaitersForCapacity(line_pa);

    // Order log audit (§3.6)
    if (_debugClearTrace) {
        framework::LogInfo("UBCC",
                     "[DEBUG-TC5-CLEAR-TRACE] processClearAccept home={} pa=0x{:x} src={} "
                     "epoch={} reqId={} newState={}",
           _nodeId, line_pa, srcNode, epoch, reqId,
           mesiStateName(entry.state));
        framework::LogInfo("UBCC",
                     "[DEBUG-UBCC-ORDER] pa=0x{:x} epoch={} reqId={} op=ClearGrantHandshake "
                     "requester={} state={}",
                     line_pa, epoch, reqId, srcNode,
                     mesiStateName(entry.state));
        framework::LogInfo("UBCC",
                     "[DEBUG-UBCC-CLEAR] accept home={} pa=0x{:x} srcNode={} epoch={} reqId={} newState={}",
                     _nodeId, line_pa, srcNode, epoch, reqId,
                     mesiStateName(entry.state));
    }

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

// ---- H64 async DSM persistence completion ----
void
UBCCController::onDsmPersistComplete(uint64_t linePa)
{
    _h64DsmPending.erase(linePa);
    if (_debugLog) {
        framework::LogInfo("UBCC", "[DEBUG-H64-DSM-DONE] home={} pa=0x{:x}", _nodeId, linePa);
    }
    // Replay queued waiters — each dequeued item decrements total counter
    auto wit = _h64PersistenceWaiters.find(linePa);
    if (wit != _h64PersistenceWaiters.end()) {
        auto waiters = std::move(wit->second);
        _h64PersistenceWaiters.erase(wit);
        // Decrement total for ALL moved waiters
        _h64PersistenceWaitersTotal -= (int)waiters.size();
        for (auto& pr : waiters) {
            DirEntry entry;
            if (!_directory.lookup(linePa, entry)) continue;
            const auto grant = processOuterRequest(
                linePa, pr.reqType, pr.writeIntent,
                pr.node, pr.socket, pr.epoch, pr.reqId);
            // The deferred request bypassed handleUbccMessage(), which normally
            // serializes a successful pull response. Deliver a newly-created
            // grant here; otherwise the requester remains blocked behind the
            // DSM persistence gate despite the data becoming visible.
            OutstandingRequest *ost = findOutstanding(linePa);
            if (static_cast<int>(grant) >= 0 && ost &&
                ost->opType == OpType::GRANT_HANDSHAKE &&
                ost->requesterNode == pr.node && ost->reqId == pr.reqId &&
                ost->stage == OpStage::WAITING_CLEAR) {
                tryPushGrant(*ost, "dsm-persist");
            }
        }
    }
    // A Clear can have queued requesters behind the recall-derived grant that
    // initiated this DSM write. Replaying them before the write is visible
    // would let the batch shared fast path emit a no-data HomeMemory grant.
    replayPendingRequesters(linePa);
}

// ---- H64 async DSM persistence FAILURE ----
void
UBCCController::onDsmPersistFailed(uint64_t linePa)
{
    _h64DsmPending.erase(linePa);
    if (_debugLog) {
        framework::LogError("UBCC", "[DEBUG-H64-DSM-FAIL] home={} pa=0x{:x} — completing waiters with failure",
                     _nodeId, linePa);
    }
    // DSM write failed: data is NOT in DsmDataStore.
    // Drain waiters with explicit failure — do NOT strand them.
    // Each waiter gets BUSY, preserving source OutstandingRequest/pin.
    auto wit = _h64PersistenceWaiters.find(linePa);
    if (wit != _h64PersistenceWaiters.end()) {
        auto waiters = std::move(wit->second);
        _h64PersistenceWaiters.erase(wit);
        _h64PersistenceWaitersTotal -= (int)waiters.size();
        // All waiters get BUSY — they'll retry and may succeed if
        // another source provides the data (RecallBuffer, fresh grant).
        // No HomeMemory fallback because data was lost.
        for (auto& pr : waiters) {
            // Return BUSY via same path as capacity overflow
            // Grant type -1 signals BUSY to the caller
            if (_debugLog) framework::LogDebug("UBCC",
                "[DEBUG-H64-DSM-FAIL-REPLAY] home={} pa=0x{:x} requester={} — BUSY",
                _nodeId, linePa, pr.node);
        }
    }
}

bool
UBCCController::commitIntendedResult(
    DirEntry &entry, const OutstandingRequest &ost, const char *path)
{
    const uint64_t predecessor = normalizeEpoch(ost.reservedEpoch - 1);
    if (normalizeEpoch(entry.epoch) != predecessor) {
        framework::LogError("UBCC",
                "[UBCC-RESERVATION-SUPERSEDED] path={} home={}:{} "
                "pa=0x{:x} entryEpoch={} predecessor={} requesterBaseEpoch={} "
                "reservedEpoch={} requester={}:{} reqId={} op={} stage={}",
                path, _nodeId, _socketId, ost.linePa, entry.epoch,
                predecessor, ost.baseEpoch, ost.reservedEpoch,
                ost.requesterNode, ost.requesterSocket, ost.reqId,
                static_cast<int>(ost.opType), static_cast<int>(ost.stage));
        return false;
    }

    // This is a per-PA commit ordinal, not a duplicate transaction count.
    int &cnt = _commitCount[ost.linePa];
    cnt++;
    framework::LogDebug("UBCC-invariant",
            "[UBINV-COMMIT-ORDINAL] path={} PA=0x{:x} ordinal={} "
            "entryEpoch={} reservedEpoch={} reqId={}",
            path, ost.linePa, cnt, entry.epoch, ost.reservedEpoch, ost.reqId);

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

    if (ost.dataValid) {
        if (_h64BloomAllMisses) {
            // H64: async DSM write with a bounded visibility gate.
            // HARD cap on concurrent DSM writes.
            if ((int)_h64DsmPending.size() >= kMaxH64DsmPending) {
                // Too many in-flight DSM writes — log and fall through.
                // The data remains in dataBuf for immediate grant; later
                // HomeMemory grants will be BUSY until some complete.
                if (_debugLog) framework::LogDebug("UBCC",
                    "[DEBUG-H64-DSM-PENDING-FULL] home={} pa=0x{:x} size={}",
                    _nodeId, ost.linePa, (int)_h64DsmPending.size());
            } else {
                _h64DsmPending.insert(ost.linePa);
                uint8_t dbuf[64];
                std::memcpy(dbuf, ost.dataBuf, 64);
                uint64_t pa = ost.linePa;
                if (_host) {
                    _host->writeDsmDataAsync(pa, dbuf,
                        [this, pa](bool ok) {
                            if (ok) onDsmPersistComplete(pa);
                            else onDsmPersistFailed(pa);
                        });
                }
            }
        } else if (_host) {
            _host->writeDsmData(ost.linePa, ost.dataBuf);
        }
    }

    panic_if((entry.state == MESIState::G_E || entry.state == MESIState::G_M) &&
             __builtin_popcountll(entry.sharersMask) != 1,
              "UBCC canonical assert failed PA=0x{:x} state={} sharers=0x{:x}",
             ost.linePa, static_cast<int>(entry.state), entry.sharersMask);

    framework::LogInfo("UBCC",
            "UBCC node_id={}: commitIntendedResult PA=0x{:x} "
            "path={} state={} owner={} sharers=0x{:x} dirty={} epoch={} "
            "baseEpoch={} reservedEpoch={} requester={}:{} reqId={}",
            _nodeId, ost.linePa, path,
            mesiStateName(entry.state), DirEntry::ownerFromSharers(entry),
            entry.sharersMask, DirEntry::protoDirty(entry), entry.epoch,
            ost.baseEpoch, ost.reservedEpoch, ost.requesterNode,
            ost.requesterSocket, ost.reqId);

    if (entry.state != MESIState::G_I) {
        publishBloomLive(ost.linePa);
    }
    // v4-A3: Don't force-delete G_I — let ResidentDir eviction handle cleanup
    return true;
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
            "UBCC node_id={}: retireToTombstone PA=0x{:x} "
            "baseEpoch={} reservedEpoch={} reqId={} expireTick={} depth={}",
            _nodeId, ost.linePa, ost.baseEpoch, ost.reservedEpoch, ost.reqId,
            ts.expireTick,
            _tombstones[ost.linePa].size());

    // v4-latency: log RETIRE state change
    framework::LogInfo("UBCC-latency",
            "[UBST] tick={} home={},{} pa=0x{:x} old={} new={} epoch={} sharers=0x{:x} action=RETIRE",
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
                    "UBCC node_id={}: checkTombstone HIT PA=0x{:x} "
                    "epoch={} reqId={} accepted={}",
                    _nodeId, linePa, epoch, reqId, ts.accepted);
            return true;
        }
    }
    return false;
}

bool
UBCCController::hasAcceptedGrantReqIdTombstone(uint64_t linePa, uint64_t reqId)
{
    cleanupTombstones();
    auto it = _tombstones.find(linePa);
    if (it == _tombstones.end())
        return false;
    for (const auto &ts : it->second) {
        if (ts.opType == OpType::GRANT_HANDSHAKE &&
            ts.reqId == reqId && ts.accepted) {
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
                    "UBCC node_id={}: tombstone expired PA=0x{:x} "
                    "epoch={} reqId={}",
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
    (void)replayWaiters;
    OutstandingRequest *ost = findOutstanding(linePa);
    if (!ost || !isExpiredRecall(*ost))
        return false;

    // Never discard a live recall. A delayed response carries authoritative
    // owner data; deleting the outstanding lets the response become orphaned
    // and strands the requester. Retry the exact tuple with a fixed bound.
    constexpr uint8_t kMaxRecallRetries = 3;
    if (ost->recallRetries >= kMaxRecallRetries) {
        fatal("UBCC node_id={}: recall timed out after retries "
              "PA=0x{:x} owner={} requester={} reqId={}",
              _nodeId, linePa, ost->targetNode, ost->requesterNode, ost->reqId);
    }
    DirEntry entry;
    if (!_directory.lookup(linePa, entry)) {
        fatal("UBCC node_id={}: recall lost directory entry PA=0x{:x}",
              _nodeId, linePa);
    }
    ++ost->recallRetries;
    ost->createTick = curTick();
    framework::LogInfo("UBCC",
            "UBCC node_id={}: retrying timed-out recall PA=0x{:x} owner={} "
            "requester={} reqId={} attempt={}",
            _nodeId, linePa, ost->targetNode, ost->requesterNode, ost->reqId,
            ost->recallRetries);
    if (!initiateRecall(linePa, entry, *ost)) {
        fatal("UBCC node_id={}: recall resend failed PA=0x{:x}",
              _nodeId, linePa);
    }
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

void
UBCCController::cleanupExpiredInvalidations()
{
    constexpr uint8_t kMaxInvalidateRetries = 8;
    const Tick now = curTick();
    std::vector<uint64_t> expired;
    for (const auto &kv : _outstandingReqs) {
        const OutstandingRequest &ost = kv.second;
        const bool invalidating = ost.stage == OpStage::WAITING_ALL_ACKS &&
            (ost.opType == OpType::INVALIDATE ||
             ost.opType == OpType::NAIVE_EVICT_INVALIDATE ||
             ost.opType == OpType::UPGRADE_PENDING);
        if (invalidating && now >= ost.createTick + _recallTimeout)
            expired.push_back(kv.first);
    }

    for (uint64_t linePa : expired) {
        OutstandingRequest *ost = findOutstanding(linePa);
        if (!ost || ost->stage != OpStage::WAITING_ALL_ACKS)
            continue;
        const uint64_t totalMask = ost->opType == OpType::UPGRADE_PENDING
            ? ost->upgradeTargetMask : ost->totalMask;
        const uint64_t ackMask = ost->opType == OpType::UPGRADE_PENDING
            ? ost->upgradeAckMask : ost->ackMask;
        const uint64_t pendingMask = totalMask & ~ackMask;
        if (pendingMask == 0)
            continue;
        if (ost->recallRetries >= kMaxInvalidateRetries) {
            fatal("UBCC node_id={}: invalidation timed out after retries "
                  "PA=0x{:x} reqId={} pendingMask=0x{:x}",
                  _nodeId, linePa, ost->reqId, pendingMask);
        }

        DirEntry entry;
        if (!_directory.lookup(linePa, entry)) {
            fatal("UBCC node_id={}: invalidation lost directory entry PA=0x{:x}",
                  _nodeId, linePa);
        }
        ++ost->recallRetries;
        ost->createTick = now;
        uint64_t effectiveMask = pendingMask;
        if (!fanoutInvalidateTargets(
                linePa, pendingMask, entry.epoch, ost->reqId,
                ost->requesterNode, ost->reqType, ost->writeIntent,
                &effectiveMask)) {
            fatal("UBCC node_id={}: invalidation resend failed PA=0x{:x} "
                  "reqId={}", _nodeId, linePa, ost->reqId);
        }
        framework::LogWarn("UBCC",
                     "[UBCC-INVALIDATE-RETRY] home={} pa=0x{:x} reqId={} "
                     "pendingMask=0x{:x} attempt={}/{}",
                     _nodeId, linePa, ost->reqId, effectiveMask,
                     static_cast<unsigned>(ost->recallRetries),
                     static_cast<unsigned>(kMaxInvalidateRetries));
    }
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
    cancelH64LookupRetry(linePa);
    framework::LogInfo("UBCC", "[RESIDENT-FILL-DONE] tick={} home={} pa=0x{:x} found={} waiters={}",
            _host ? _host->hostCurrentTick() : 0,
            _nodeId, linePa, found ? 1 : 0,
           _residentWaiters.count(linePa) ? _residentWaiters[linePa].size() : 0);
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
        // Phase A4: Validate restored G_M owner is one-hot and epoch is valid.
        // Malformed backstore metadata must not silently become G_I.
        if (entry.state == MESIState::G_M) {
            int ownerPopcount = __builtin_popcountll(entry.sharersMask);
            if (ownerPopcount != 1 || entry.epoch == 0) {
                framework::LogInfo("UBCC",
                             "[UBCC-SPILL-DIRTY-CORRUPT] home={} pa=0x{:x} "
                             "restored G_M with sharersMask=0x{:x} (popcount={}) "
                             "epoch={} — fatal",
                             _nodeId, linePa, entry.sharersMask,
                             ownerPopcount, entry.epoch);
                fatal("UBCC node_id={}: backstore returned corrupt G_M "
                      "metadata PA=0x{:x} sharersMask=0x{:x} epoch={}",
                      _nodeId, linePa, entry.sharersMask, entry.epoch);
            }
            int restoredOwner = (ownerPopcount == 1)
                ? __builtin_ctzll(entry.sharersMask) : -1;
            framework::LogInfo("UBCC",
                         "[UBCC-SPILL-DIRTY-FILL] home={} pa=0x{:x} "
                         "restored G_M owner={} sharersMask=0x{:x} epoch={}",
                         _nodeId, linePa, restoredOwner,
                         entry.sharersMask, entry.epoch);
        }
        e.state = entry.state;
        e.sharersMask = entry.sharersMask;
        e.epoch = entry.epoch;
        e.residentDirty = false;
        // Phase 3: Bloom insert after verified Fill from H64 (upsert ack inserts)
        publishBloomLive(linePa);
    } else {
        e.state = MESIState::G_I;
        e.sharersMask = 0;
        e.residentDirty = false;
        // Phase 3: no exact-PA shadow; Bloom retains old bit until group rebuild
    }
    appendTmpLog(
        "ubcc_fill_complete.log",
        "[FILL-COMPLETE] pa=0x%lx found=%d state=%d sharers=0x%lx\n",
        linePa, found ? 1 : 0, static_cast<int>(e.state), e.sharersMask);
    framework::LogInfo("UBCC","[UBCC-FILL-DONE] home={} pa=0x{:x} found={} state={} sharers=0x{:x} "
           "epoch={}",
           _nodeId, linePa, found ? 1 : 0, mesiStateName(e.state),
           e.sharersMask, e.epoch);
    _directory.update(linePa, e);
    _directory.setFillPending(linePa, false);
    _directory.touch(linePa);
    refreshPinnedBit(linePa);
    replayResidentWaiters(linePa);
    // A completed fill may make this set's entry evictable again.
    replayResidentWaitersForCapacity(linePa);
}

void
UBCCController::onBackstoreWriteAck(uint64_t linePa)
{
    framework::LogInfo("UBCC", "[RESIDENT-SPILL-DONE] tick={} home={} pa=0x{:x} evictionPending={} async={}",
            _host ? _host->hostCurrentTick() : 0,
            _nodeId, linePa,
            _evictionPendingRemoval.count(linePa) ? 1 : 0,
           _asyncWbSnapshots.count(linePa) ? 1 : 0);

    // Phase 3: successful backstore write — Bloom already inserted by caller.
    // No exact-PA shadow set.

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
        publishBloomLive(linePa);
    }
    e.residentDirty = false;
    _directory.update(linePa, e);
    _directory.setWbPending(linePa, false);

    if (_evictionPendingRemoval.erase(linePa) != 0) {
        _directory.forceRemove(linePa);
    }
    refreshPinnedBit(linePa);
    replayResidentWaiters(linePa);
    // A clean resident entry is now a legal victim for waiters in this set.
    replayResidentWaitersForCapacity(linePa);
}

void
UBCCController::onBackstoreDeleteAck(uint64_t linePa, bool existed)
{
    // Phase 3: Bloom retains stale bit after delete ack (group rebuild corrects)
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
    replayResidentWaitersForCapacity(linePa);
    (void)existed;
}

// ---- Phase 3: Typed H64 completion handler ----
void
UBCCController::onBackstoreH64Complete(const BackstoreCompletion &comp)
{
    uint64_t linePa = comp.linePa;

    if (_debugLog) framework::LogDebug("UBCC", "[BACKSTORE-H64-COMPLETE] home={} pa=0x{:x} op={} status={} found={} epoch={} snapshotEpoch={}",
            _nodeId, linePa,
            backstoreOpName(comp.op),
            backstoreStatusName(comp.status),
            comp.found ? 1 : 0,
            comp.epoch,
            comp.snapshotEpoch);

    // Map H64 status to existing callback semantics
    switch (comp.op) {
      case BackstoreOp::Lookup: {
        if (comp.status == BackstoreStatus::Ok) {
            BackstoreEntry entry;
            if (comp.found) {
                entry.state   = comp.state;
                entry.sharersMask = comp.sharersMask;
                entry.epoch   = comp.epoch;
            } else {
                entry.state   = MESIState::G_I;
                entry.sharersMask = 0;
                entry.epoch   = 0;
            }
            onBackstoreFillComplete(linePa, comp.found, entry);
        } else if (comp.status == BackstoreStatus::RetryableBusy) {
            // Initial H64 admission can fail synchronously. Retain the exact
            // fill placeholder and waiter, then retry from the outer wakeup
            // loop with a fixed per-wakeup budget. Never fabricate G_I and
            // never recursively resubmit from this callback.
            scheduleH64LookupRetry(linePa);
        } else {
            // There is no safe fallback for an I/O/corruption failure: treating
            // it as NotFound would lose metadata, while retaining the pin would
            // silently deadlock the set.
            fatal("UBCC node_id={}: H64 lookup failed PA=0x{:x} status={}",
                  _nodeId, linePa, backstoreStatusName(comp.status));
        }
        break;
      }
      case BackstoreOp::Upsert: {
        if (comp.status == BackstoreStatus::Ok) {
            // Bloom: upsert ack inserts
            publishBloomLive(linePa);
            onBackstoreWriteAck(linePa);
        } else if (comp.status == BackstoreStatus::StaleEpoch) {
            // An async snapshot may legitimately become stale. Drop only its
            // admission marker; residentDirty remains set for the next bounded
            // sweep to persist the current epoch.
            if (_asyncWbSnapshots.erase(linePa) != 0) {
                refreshPinnedBit(linePa);
                return;
            }
            // A non-async stale write must never be counted as durable.
            if (_debugLog) framework::LogDebug("UBCC", "[BACKSTORE-H64-UPSERT-STALE] home={} pa=0x{:x} "
                    "snapshotEpoch={} — write rejected",
                    _nodeId, linePa, comp.snapshotEpoch);
            fatal("UBCC node_id={}: non-async H64 upsert stale PA=0x{:x}",
                  _nodeId, linePa);
        } else if (comp.status == BackstoreStatus::RetryableBusy) {
            // The Host rejected admission before a durable transaction began.
            // Keeping an async snapshot or eviction pin in that state strands
            // capacity waiters forever. Retain dirty metadata and retry only
            // through the existing bounded async sweep after a slot frees.
            if (_asyncWbSnapshots.erase(linePa) != 0) {
                refreshPinnedBit(linePa);
                return;
            }
            _directory.setWbPending(linePa, false);
            _evictionPendingRemoval.erase(linePa);
            refreshPinnedBit(linePa);
        } else {
            if (_debugLog) framework::LogDebug("UBCC", "[BACKSTORE-H64-UPSERT-ERR] home={} pa=0x{:x} status={} — "
                    "fatal",
                    _nodeId, linePa, backstoreStatusName(comp.status));
            fatal("UBCC node_id={}: H64 upsert failed PA=0x{:x} status={}",
                  _nodeId, linePa, backstoreStatusName(comp.status));
        }
        break;
      }
      case BackstoreOp::Erase: {
        if (comp.status == BackstoreStatus::Ok ||
            comp.status == BackstoreStatus::AlreadyAbsent) {
            // Delete ack: may retain stale Bloom bit
            onBackstoreDeleteAck(linePa, comp.existed);
        } else {
            if (_debugLog) framework::LogDebug("UBCC", "[BACKSTORE-H64-ERASE-ERR] home={} pa=0x{:x} status={} — "
                    "delete pin retained",
                    _nodeId, linePa, backstoreStatusName(comp.status));
        }
        break;
      }
    }
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
    publishBloomLive(linePa);
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
        publishBloomLive(linePa);
    }
    refreshPinnedBit(linePa);
    return true;
}

bool
UBCCController::debugEnqueueResidentWaiterForTest(
    uint64_t linePa, int waitReason)
{
    if (waitReason < static_cast<int>(ResidentWaitReason::Capacity) ||
        waitReason > static_cast<int>(ResidentWaitReason::MetadataWriteback)) {
        return false;
    }
    DirEntry entry;
    if (!_directory.lookup(linePa, entry)) {
        return false;
    }
    PendingRequester pr;
    pr.node = 0;
    pr.socket = 0;
    pr.opKind = ResidentOpKind::Read;
    pr.reqType = UBCC_OuterReqType::GlobalReadShared;
    pr.reqId = entry.epoch + 1;
    pr.epoch = entry.epoch;
    pr.waitReason = static_cast<ResidentWaitReason>(waitReason);
    const ResidentWaiterEnqueueResult result =
        enqueueResidentWaiterIfNew(linePa, pr);
    refreshPinnedBit(linePa);
    return result == ResidentWaiterEnqueueResult::Enqueued;
}

bool
UBCCController::debugEnqueueResidentWaiterTupleForTest(
    uint64_t linePa, ResidentOpKind opKind, int requesterNode,
    int requesterSocket, uint64_t epoch, uint64_t reqId, int waitReason)
{
    if (waitReason < static_cast<int>(ResidentWaitReason::Capacity) ||
        waitReason > static_cast<int>(ResidentWaitReason::MetadataWriteback)) {
        return false;
    }
    PendingRequester pr;
    pr.node = requesterNode;
    pr.socket = requesterSocket;
    pr.opKind = opKind;
    pr.reqType = UBCC_OuterReqType::GlobalReadUnique;
    pr.writeIntent = true;
    pr.epoch = normalizeEpoch(epoch);
    pr.reqId = reqId;
    pr.waitReason = static_cast<ResidentWaitReason>(waitReason);
    const ResidentWaiterEnqueueResult result =
        enqueueResidentWaiterIfNew(linePa, pr);
    refreshPinnedBit(linePa);
    return result == ResidentWaiterEnqueueResult::Enqueued;
}

bool
UBCCController::debugClearResidentWaitersForTest(uint64_t linePa)
{
    if (_residentWaiters.erase(linePa) == 0) {
        return false;
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

bool
UBCCController::debugEnqueuePendingRequesterForTest(
    uint64_t linePa, int node, int socket, bool shared,
    uint64_t epoch, uint64_t reqId)
{
    auto &queue = _pendingRequesters[linePa];
    if (queue.size() >= MAX_PENDING_PER_PA)
        return false;
    PendingRequester pr;
    pr.node = node;
    pr.socket = socket;
    pr.reqType = shared ? UBCC_OuterReqType::GlobalReadShared :
                          UBCC_OuterReqType::GlobalReadUnique;
    pr.writeIntent = !shared;
    pr.epoch = normalizeEpoch(epoch);
    pr.reqId = reqId;
    queue.push_back(pr);
    return true;
}

// ---- recall_done_fix.md §5: Replay queued pending requesters ----
void
UBCCController::replayPendingRequesters(uint64_t linePa)
{
    // HomeMemory grants must wait until recall-derived data is authoritative.
    // processOuterRequest applies the same gate to new requests; this replay
    // path bypasses processOuterRequest for the batch shared fast path.
    if (_h64BloomAllMisses && _h64DsmPending.count(linePa))
        return;
    if (findOutstanding(linePa))
        return;
    if (!_pendingReplayActive.insert(linePa).second)
        return;
    struct ReplayGuard {
        std::set<uint64_t> &active;
        uint64_t linePa;
        ~ReplayGuard() { active.erase(linePa); }
    } guard{_pendingReplayActive, linePa};

    // Replay all queued entries one by one, each as a fresh processOuterRequest
    // with rebased epoch against the NEW committed state.
    while (true) {
        if (findOutstanding(linePa))
            break;
        auto qit = _pendingRequesters.find(linePa);
        if (qit == _pendingRequesters.end() || qit->second.empty())
            break;
        DirEntry entry;
        if (!_directory.lookup(linePa, entry))
            break;
        PendingRequester pr = qit->second.front();
        qit->second.pop_front();

        // §5.2: Rebase epoch to newly committed epoch (the Clear just advanced it).
        // upgrade_invalidate_fix D5: this rebaseEpoch becomes the baseEpoch
        // in the new GRANT_HANDSHAKE. The requester's subsequent Clear must
        // use THIS baseEpoch (from the grant envelope/GRANT_HANDSHAKE context),
        // NOT its own stale local entry.epoch. The EPBackend-side fix in
        // handleRemoteMiss/sendClear ensures this by reading getOutstandingBaseEpoch().
        uint64_t rebaseEpoch = entry.epoch;

        framework::LogInfo("UBCC","[UBCC-QUEUE-REPLAY] pa=0x{:x} requester={} reqType={} "
               "writeIntent={} reqId={} originalEpoch={} rebaseEpoch={} "
               "committedState={}",
               linePa, pr.node,
               (pr.reqType == UBCC_OuterReqType::GlobalReadShared) ? "RS" : "RU",
               pr.writeIntent, pr.reqId, pr.epoch, rebaseEpoch,
               mesiStateName(entry.state));

        // ── C3 Batch RS path: G_S + RS → direct grant without outstanding ──
        if (_batchRsEnabled &&
            entry.state == MESIState::G_S &&
            pr.reqType == UBCC_OuterReqType::GlobalReadShared) {

            framework::LogInfo("UBCC","[UBCC-QUEUE-REPLAY-BATCH] pa=0x{:x} requester={} "
                   "reqType=RS rebaseEpoch={} committedState={}",
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

            tempOst.dataSource = GrantDataSource::HomeMemory;

            // Commit directly (no Clear needed for shared). A live
            // reservation or callback-side commit must not be crossed.
            if (!commitIntendedResult(entry, tempOst, "BatchRS")) {
                auto currentQueue = _pendingRequesters.find(linePa);
                if (currentQueue != _pendingRequesters.end())
                    currentQueue->second.push_front(pr);
                break;
            }
            _directory.update(linePa, entry);
            validateSharersCanonical(linePa);
            retireToTombstone(tempOst, true);
            refreshPinnedBit(linePa);

            // Push-grant to requester
            if (_outbound) {
                CoherenceMessage push;
                buildGrantResponse(tempOst, push);
                _outbound->sendGrantPush(push);
                framework::LogInfo("UBCC","[PUSH-GRANT] BATCH-RS home={} pa=0x{:x} "
                       "requester={} sock={} grantType={}",
                       _nodeId, linePa, pr.node, pr.socket,
                       static_cast<int>(grantTypeFromIntended(tempOst.intendedState)));
            }

            if (findOutstanding(linePa))
                break;
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
                    const int requesterNode = ost->requesterNode;
                    const int requesterSocket = ost->requesterSocket;
                    const uint64_t requestId = ost->reqId;
                    const int grantType = static_cast<int>(
                        grantTypeFromIntended(ost->intendedState));
                    const bool pushOk = tryPushGrant(*ost, "queue-replay");
                    framework::LogInfo("UBCC","[PUSH-GRANT] QUEUE-REPLAY home={} pa=0x{:x} "
                           "requester={} sock={} reqId={} grantType={} pushOk={}",
                           _nodeId, linePa, requesterNode,
                           requesterSocket, requestId, grantType,
                           pushOk ? 1 : 0);
                }
            }
            break;
        }
        // No outstanding created (e.g., immediate grant that returned BUSY
        // because the entry was already enqueued elsewhere) — continue to
        // next queued entry.
    }

    // Clean up empty queue to avoid stale entries
    auto qit = _pendingRequesters.find(linePa);
    if (qit != _pendingRequesters.end() && qit->second.empty())
        _pendingRequesters.erase(qit);
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
        warn("UBCC node_id={}: fanoutInvalidateTargets called with no outbound sender",
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
                framework::LogWarn("UBCC","[UBCC-FANOUT-STALE] home={} pa=0x{:x} requested=0x{:x} "
                       "liveSharers=0x{:x} dropped=0x{:x} (not current sharers)",
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

        framework::LogInfo("UBCC","[UBCC-FANOUT] home={} pa=0x{:x} target={} epoch={} reqId={}",
               _nodeId, linePa, target, committedEpoch, reqId);

        if (!_outbound->sendInvalidateReq(msg)) {
            warn("UBCC node_id={}: invalidate fanout failed target={}",
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
    if (_outstandingReqs.size() >= MAX_OUTSTANDING_TOTAL) {
        warn("UBCC node_id={}: outstanding table full ({}), PA=0x{:x} stays BUSY",
             _nodeId, _outstandingReqs.size(), linePa);
        return nullptr;
    }
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

bool
UBCCController::grantTupleLive(uint64_t linePa, int requesterNode,
                               uint64_t reqId) const
{
    auto it = _outstandingReqs.find(linePa);
    if (it != _outstandingReqs.end()) {
        const OutstandingRequest &ost = it->second;
        if (ost.opType == OpType::GRANT_HANDSHAKE &&
            ost.stage == OpStage::WAITING_CLEAR &&
            ost.requesterNode == requesterNode && ost.reqId == reqId) {
            return true;
        }
    }

    // Batch-RS grants commit immediately and retain only a tombstone while the
    // requester may still consume the pushed response.
    auto tombstones = _tombstones.find(linePa);
    if (tombstones == _tombstones.end())
        return false;
    for (const auto &ts : tombstones->second) {
        if (ts.opType == OpType::GRANT_HANDSHAKE && ts.reqId == reqId &&
            curTick() < ts.expireTick) {
            return true;
        }
    }
    return false;
}

bool
UBCCController::tryPushGrant(OutstandingRequest &ost, const char *reason)
{
    if (!_outbound || ost.opType != OpType::GRANT_HANDSHAKE ||
        ost.stage != OpStage::WAITING_CLEAR) {
        return false;
    }

    CoherenceMessage push;
    buildGrantResponse(ost, push);
    ost.respTick = curTick();
    const bool accepted = _outbound->sendGrantPush(push);
    ost.replayArmed = !accepted;
    framework::LogInfo("UBCC",
        "[PUSH-GRANT-TRY] reason={} home={} pa=0x{:x} requester={} "
        "sock={} reqId={} accepted={}",
        reason ? reason : "unknown", _nodeId, ost.linePa,
        ost.requesterNode, ost.requesterSocket, ost.reqId,
        accepted ? 1 : 0);
    return accepted;
}

void
UBCCController::retryPendingGrantPushes()
{
    if (!_outbound || _outstandingReqs.empty())
        return;

    const size_t count = _outstandingReqs.size();
    const size_t start = _grantPushRetryCursor % count;
    auto it = _outstandingReqs.begin();
    std::advance(it, start);
    size_t visited = 0;
    size_t attempted = 0;
    while (visited < count && attempted < kGrantPushAttemptsPerWake) {
        if (it == _outstandingReqs.end())
            it = _outstandingReqs.begin();
        OutstandingRequest &ost = it->second;
        ++it;
        ++visited;
        if (ost.opType != OpType::GRANT_HANDSHAKE ||
            ost.stage != OpStage::WAITING_CLEAR || !ost.replayArmed) {
            continue;
        }
        ++attempted;
        tryPushGrant(ost, "bounded-retry");
    }
    _grantPushRetryCursor = (start + visited) % count;
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
    push.h.srcSocket = _socketId;                // v4-dual-socket: home socket plane
    push.h.dstNode = grantOst.requesterNode;
    push.h.dstSocket = grantOst.requesterSocket >= 0
        ? static_cast<uint16_t>(grantOst.requesterSocket)
        : static_cast<uint16_t>(_socketId);  // fallback: use home socket
    push.h.homeNode = _nodeId;
    push.h.homeSocket = _socketId;               // v4-dual-socket: home socket plane
    push.h.requesterNode = grantOst.requesterNode;
    push.h.homeLinePa = grantOst.linePa;
    push.h.epoch = grantOst.baseEpoch;
    push.h.reqId = grantOst.reqId;
    const bool hasGrantData = grantOst.dataValid;

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
    push.b.readResp.grantEpoch = grantOst.reservedEpoch;
    push.b.readResp.committedEpoch = 0;
    push.b.readResp.pendingInvMask = 0;

    // Push grants only carry transaction-owned data. The router supplies
    // authoritative home data for grants that do not own a payload.
    if (grantOst.dataValid) {
        std::memcpy(push.b.readResp.grantData, grantOst.dataBuf, 64);
    } else {
        std::memset(push.b.readResp.grantData, 0, 64);
    }
    // ── Phase C4 trace point 6: push grant payload word ──
    {
        uint64_t off = grantOst.linePa & 0x1FFFULL;
        uint64_t ckOff = grantOst.linePa & 0xFFFFFULL;
        if (ckOff < 0x80000ULL && (off % 64 == 0)) {
            uint64_t w0;
            std::memcpy(&w0, push.b.readResp.grantData, 8);
            framework::LogInfo("UBCC",
                "[C4-PUSHGRANT] home={} pa=0x{:x} off=0x{:x} hasData={} "
                "dataSource={} w0=0x{:x}",
                _nodeId, grantOst.linePa, off,
                (push.h.flags & static_cast<uint32_t>(CFLAG_HAS_DATA)) ? 1 : 0,
                static_cast<int>(push.b.readResp.dataSource), w0);
        }
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

    framework::LogInfo("UBCC","[UBCC-HOME-WB-NOTIFY] home={} socket={} pa=0x{:x} epoch={}",
           _nodeId, _socketId, homePa, notifyEpoch);

    DirEntry entry;
    if (!_directory.lookup(homePa, entry)) {
        framework::LogInfo("UBCC",
                "UBCC node_id={} socket={}: HomeWritebackNotify PA=0x{:x} "
                "no directory entry — ignored",
                _nodeId, _socketId, homePa);
        return;
    }

    if (entry.state == MESIState::G_I) {
        framework::LogInfo("UBCC",
                "UBCC node_id={} socket={}: HomeWritebackNotify PA=0x{:x} "
                "already G_I — ignored",
                _nodeId, _socketId, homePa);
        return;
    }

    // Guard: if a new request is already in-flight, drop stale notify
    if (isLineBusy(homePa)) {
        // TC98 fix: rate-limit home-WB-NOTIFY BUSY log
        { static uint64_t _cnt = 0; if (++_cnt <= 3 || _cnt % 1000 == 0)
        framework::LogDebug("UBCC","[UBCC-HOME-WB-NOTIFY] home={} socket={} pa=0x{:x} BUSY — deferred (n={})",
               _nodeId, _socketId, homePa, _cnt); }
        return;
    }

    // Optimistic stale epoch check
    if (notifyEpoch != 0 && !checkEpochForLine(homePa, notifyEpoch)) {
        framework::LogWarn("UBCC","[UBCC-HOME-WB-NOTIFY] home={} socket={} pa=0x{:x} "
               "STALE epoch notify={} dir={} — dropped",
               _nodeId, _socketId, homePa, notifyEpoch, entry.epoch);
        return;
    }

    // Release directory ownership
    int oldOwner = DirEntry::ownerFromSharers(entry);
    framework::LogInfo("UBCC","[UBCC-HOME-WB-NOTIFY] home={} socket={} pa=0x{:x} "
           "oldState={} owner={} — releasing to G_I",
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
        panic("[UBInv] PA=0x{:x} epoch DECREASED {} -> {}", pa, oldEpoch, newEpoch);
    }
}

void
UBCCController::validateSharersCanonical(uint64_t pa) const
{
    DirEntry entry;
    if (!_directory.lookup(pa, entry)) return;
    if (entry.state == MESIState::G_I && entry.sharersMask != 0)
        panic("[UBInv] PA=0x{:x} G_I with non-zero sharers 0x{:x}", pa, entry.sharersMask);
    if (entry.state == MESIState::G_S && entry.sharersMask == 0)
        panic("[UBInv] PA=0x{:x} G_S with zero sharers", pa);
    if ((entry.state == MESIState::G_E || entry.state == MESIState::G_M)
        && __builtin_popcountll(entry.sharersMask) != 1)
        panic("[UBInv] PA=0x{:x} G_E/G_M with non-one-hot sharers 0x{:x}", pa, entry.sharersMask);
}

} // namespace glob
} // namespace cc
