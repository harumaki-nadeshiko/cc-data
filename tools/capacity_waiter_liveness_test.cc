// Regression for TC132: an unrelated writeback completion must not let a
// capacity waiter replay itself forever while its target set remains full.

#include "modules/ubiomodule/UBCCController.hh"

#include <cassert>
#include <cstdio>
#include <cstring>
#include <vector>

using namespace cc::glob;

class HoldBackstoreHost final : public UBCCHostIf
{
  public:
    uint64_t lastWritePa = 0;
    uint8_t lastWriteData[64] = {};

    uint64_t hostCurrentTick() const override { return 1; }
    void hostIssueBackstoreRead(uint64_t) override {}
    void hostIssueBackstoreWrite(uint64_t) override {}
    void hostIssueBackstoreDelete(uint64_t) override {}
    void readDsmData(uint64_t, std::function<void(const uint8_t*)> cb) override
    {
        if (cb) cb(nullptr);
    }
    void writeDsmData(uint64_t pa, const uint8_t *data) override
    {
        lastWritePa = pa;
        if (data) std::memcpy(lastWriteData, data, sizeof(lastWriteData));
    }
};

class CaptureOutbound final : public UBCCOutboundIf
{
  public:
    int recallCount = 0;
    int invalidateCount = 0;
    CoherenceMessage lastRecall;
    CoherenceMessage lastInvalidate;
    std::vector<CoherenceMessage> grants;
    int grantPushAttempts = 0;
    int rejectedGrantPushes = 0;

    bool sendRecallReq(const CoherenceMessage &msg) override
    {
        ++recallCount;
        lastRecall = msg;
        return true;
    }
    bool sendInvalidateReq(const CoherenceMessage &msg) override
    {
        ++invalidateCount;
        lastInvalidate = msg;
        return true;
    }
    bool sendUpgradeAckNotify(const CoherenceMessage&) override { return true; }
    bool sendUpgradeResp(const CoherenceMessage&) override { return true; }
    bool sendGrantPush(const CoherenceMessage &msg) override
    {
        ++grantPushAttempts;
        if (rejectedGrantPushes > 0) {
            --rejectedGrantPushes;
            return false;
        }
        grants.push_back(msg);
        return true;
    }
};

class RetryLookupHost final : public UBCCHostIf
{
  public:
    UBCCController *controller = nullptr;
    int lookupCount = 0;

    uint64_t hostCurrentTick() const override { return lookupCount + 1; }
    void hostIssueBackstoreRead(uint64_t pa) override
    {
        ++lookupCount;
        assert(controller);
        BackstoreCompletion completion;
        completion.linePa = pa;
        completion.op = BackstoreOp::Lookup;
        completion.status = lookupCount == 1
            ? BackstoreStatus::RetryableBusy
            : BackstoreStatus::Ok;
        completion.found = false;
        controller->onBackstoreH64Complete(completion);
    }
    void hostIssueBackstoreWrite(uint64_t) override {}
    void hostIssueBackstoreDelete(uint64_t) override {}
    void readDsmData(uint64_t, std::function<void(const uint8_t*)> cb) override
    {
        if (cb) cb(nullptr);
    }
    void writeDsmData(uint64_t, const uint8_t*) override {}
};

int
main()
{
    // Two one-way sets make same-set capacity and unrelated-set completion
    // deterministic without adding any directory state.
    ResidentDirConfig cfg;
    cfg.sram_bytes = 4352;
    cfg.bloom_bytes = 128;
    cfg.ways = 1;
    cfg.set_bits = 1;

    UBCCController ubcc(0, 0, nullptr, 64, cfg.bloom_bytes, 0, 1, 3, &cfg);
    HoldBackstoreHost host;
    ubcc.setHost(&host);
    ubcc.setResidentOverflowPolicy(ResidentOverflowPolicy::Spill);

    // A Bloom-positive H64 fill whose initial admission returns RetryableBusy
    // must retry from a later wakeup rather than retaining fillPending forever.
    ResidentDirConfig lookupRetryCfg = cfg;
    lookupRetryCfg.ways = 2;
    UBCCController lookupRetryUbcc(
        0, 0, nullptr, 64, lookupRetryCfg.bloom_bytes, 0, 1, 3,
        &lookupRetryCfg);
    RetryLookupHost retryLookupHost;
    retryLookupHost.controller = &lookupRetryUbcc;
    lookupRetryUbcc.setHost(&retryLookupHost);
    lookupRetryUbcc.setResidentOverflowPolicy(ResidentOverflowPolicy::Spill);
    lookupRetryUbcc.setH64BloomAllMisses(true);
    constexpr uint64_t lookupRetryPa = 0x10000000;
    const auto lookupBusy = lookupRetryUbcc.processOuterRequest(
        lookupRetryPa, UBCC_OuterReqType::GlobalReadUnique, true,
        0, 0, 1, 7);
    assert(static_cast<int>(lookupBusy) == -1);
    assert(retryLookupHost.lookupCount == 1);
    const std::string lookupBusyState =
        lookupRetryUbcc.inspectOffloadLineForTest(lookupRetryPa);
    assert(lookupBusyState.find("\"fill_pending\":true") !=
           std::string::npos);
    assert(lookupBusyState.find("\"resident_waiter_depth\":1") !=
           std::string::npos);
    lookupRetryUbcc.wakeup();
    assert(retryLookupHost.lookupCount == 2);
    const std::string lookupProgressed =
        lookupRetryUbcc.inspectOffloadLineForTest(lookupRetryPa);
    assert(lookupProgressed.find("\"fill_pending\":false") !=
           std::string::npos);
    assert(lookupProgressed.find("\"resident_waiter_depth\":0") !=
           std::string::npos);

    constexpr uint64_t victim = 0x10000000; // home-0 DSM, set 0
    constexpr uint64_t target = 0x10000080; // same set as victim
    constexpr uint64_t unrelated = 0x10000040; // set 1

    assert(ubcc.debugSeedResidentForTest(
        victim, static_cast<int>(MESIState::G_M), 1, 1, true));
    assert(ubcc.debugSeedResidentForTest(
        unrelated, static_cast<int>(MESIState::G_E), 1, 1, false));

    // Start a held eviction, keeping victim pinned so target cannot enter.
    assert(ubcc.debugForceResidentEvictForTest(victim));
    const auto busy = ubcc.processOuterRequest(
        target, UBCC_OuterReqType::GlobalReadUnique, true, 0, 0, 1, 42);
    assert(static_cast<int>(busy) == -1);

    // This completion frees no target-set slot. Before P0, its global replay
    // pop/re-enqueued target forever at the same tick.
    ubcc.onBackstoreWriteAck(unrelated);

    const std::string state = ubcc.inspectOffloadLineForTest(target);
    assert(state.find("\"resident_present\":false") != std::string::npos);
    assert(state.find("\"resident_waiter_depth\":1") != std::string::npos);

    // The held victim is in target's set. Its durable completion removes the
    // victim and must wake target exactly through the matching set-local path.
    ubcc.onBackstoreWriteAck(victim);
    const std::string progressed = ubcc.inspectOffloadLineForTest(target);
    assert(progressed.find("\"resident_present\":true") != std::string::npos);
    assert(progressed.find("\"resident_waiter_depth\":0") != std::string::npos);

    // If a capacity-waiting PA becomes resident before its retained operation
    // completes, the waiter must protect that entry. Victim removal erases the
    // PA's waiter queue and could otherwise lose a writeback payload.
    ResidentDirConfig pinCfg = cfg;
    pinCfg.ways = 2;
    UBCCController pinUbcc(
        0, 0, nullptr, 64, pinCfg.bloom_bytes, 0, 1, 3, &pinCfg);
    pinUbcc.setHost(&host);
    pinUbcc.setResidentOverflowPolicy(ResidentOverflowPolicy::Spill);

    constexpr uint64_t capacityOnly = 0x10000100;
    assert(pinUbcc.debugSeedResidentForTest(
        capacityOnly, static_cast<int>(MESIState::G_I), 0, 7, false));
    assert(pinUbcc.debugEnqueueResidentWaiterForTest(
        capacityOnly,
        static_cast<int>(UBCCController::ResidentWaitReason::Capacity)));
    const std::string capacityState =
        pinUbcc.inspectOffloadLineForTest(capacityOnly);
    assert(capacityState.find("\"resident_waiter_depth\":1") !=
           std::string::npos);
    assert(capacityState.find("\"pinned\":true") != std::string::npos);

    constexpr uint64_t fillDependent = 0x10000140;
    assert(pinUbcc.debugSeedResidentForTest(
        fillDependent, static_cast<int>(MESIState::G_I), 0, 9, false));
    assert(pinUbcc.debugEnqueueResidentWaiterForTest(
        fillDependent,
        static_cast<int>(UBCCController::ResidentWaitReason::BackstoreFill)));
    const std::string fillState =
        pinUbcc.inspectOffloadLineForTest(fillDependent);
    assert(fillState.find("\"resident_waiter_depth\":1") != std::string::npos);
    assert(fillState.find("\"pinned\":true") != std::string::npos);

    // A duplicate retry must drive eviction again. First block target admission
    // with a real resident dependency, then remove that dependency without a
    // capacity completion callback. The retry is deduplicated but must still
    // evict the now-eligible victim and admit the retained target operation.
    ResidentDirConfig retryCfg = cfg;
    UBCCController retryUbcc(
        0, 0, nullptr, 64, retryCfg.bloom_bytes, 0, 1, 3, &retryCfg);
    retryUbcc.setHost(&host);
    retryUbcc.setResidentOverflowPolicy(ResidentOverflowPolicy::Spill);
    constexpr uint64_t retryVictim = 0x10000200;
    constexpr uint64_t retryOtherSet = 0x10000240;
    constexpr uint64_t retryTarget = 0x10000280;
    assert(retryUbcc.debugSeedResidentForTest(
        retryVictim, static_cast<int>(MESIState::G_I), 0, 11, false));
    assert(retryUbcc.debugSeedResidentForTest(
        retryOtherSet, static_cast<int>(MESIState::G_I), 0, 13, false));
    assert(retryUbcc.debugEnqueueResidentWaiterForTest(
        retryVictim,
        static_cast<int>(UBCCController::ResidentWaitReason::BackstoreFill)));
    const auto firstBusy = retryUbcc.processOuterRequest(
        retryTarget, UBCC_OuterReqType::GlobalReadUnique, true, 0, 0, 1, 84);
    assert(static_cast<int>(firstBusy) == -1);
    assert(retryUbcc.inspectOffloadLineForTest(retryTarget).find(
        "\"resident_waiter_depth\":1") != std::string::npos);
    assert(retryUbcc.debugClearResidentWaitersForTest(retryVictim));
    const auto retryResult = retryUbcc.processOuterRequest(
        retryTarget, UBCC_OuterReqType::GlobalReadUnique, true, 0, 0, 1, 84);
    assert(static_cast<int>(retryResult) == -1);
    const std::string retryState =
        retryUbcc.inspectOffloadLineForTest(retryTarget);
    assert(retryState.find("\"resident_present\":true") != std::string::npos);
    assert(retryState.find("\"resident_waiter_depth\":0") !=
           std::string::npos);

    // A retained copy of the operation that produced a grant must retire with
    // its successful Clear. Otherwise replay creates a second grant with the
    // same reqId after the first transaction has already committed.
    ResidentDirConfig commitCfg = cfg;
    commitCfg.ways = 2;
    UBCCController commitUbcc(
        0, 0, nullptr, 64, commitCfg.bloom_bytes, 0, 1, 3, &commitCfg);
    commitUbcc.setHost(&host);
    commitUbcc.setResidentOverflowPolicy(ResidentOverflowPolicy::Spill);
    constexpr uint64_t committedPa = 0x10000300;
    constexpr uint64_t committedReqId = 1234;
    constexpr uint64_t laterReqId = 1235;
    constexpr uint64_t requesterEpoch = 420;
    assert(commitUbcc.debugSeedResidentForTest(
        committedPa, static_cast<int>(MESIState::G_I), 0, 0, false));
    uint64_t clearEpoch = 0;
    uint64_t ownerEpoch = 0;
    const auto grant = commitUbcc.processOuterRequest(
        committedPa, UBCC_OuterReqType::GlobalReadUnique, true,
        0, 0, requesterEpoch, committedReqId,
        nullptr, nullptr, nullptr, nullptr, nullptr,
        &clearEpoch, &ownerEpoch);
    assert(static_cast<int>(grant) != -1);
    assert(clearEpoch == requesterEpoch);
    assert(ownerEpoch == 1);
    assert(commitUbcc.debugEnqueueResidentWaiterTupleForTest(
        committedPa, ResidentOpKind::Read, 0, 0, requesterEpoch, committedReqId,
        static_cast<int>(UBCCController::ResidentWaitReason::Capacity)));
    assert(commitUbcc.debugEnqueueResidentWaiterTupleForTest(
        committedPa, ResidentOpKind::Read, 0, 0, requesterEpoch, laterReqId,
        static_cast<int>(UBCCController::ResidentWaitReason::Capacity)));
    assert(commitUbcc.debugEnqueueResidentWaiterTupleForTest(
        committedPa, ResidentOpKind::Read, 1, 0, requesterEpoch, committedReqId,
        static_cast<int>(UBCCController::ResidentWaitReason::Capacity)));
    assert(commitUbcc.debugEnqueueResidentWaiterTupleForTest(
        committedPa, ResidentOpKind::Read, 0, 1, requesterEpoch, committedReqId,
        static_cast<int>(UBCCController::ResidentWaitReason::Capacity)));
    assert(commitUbcc.debugEnqueueResidentWaiterTupleForTest(
        committedPa, ResidentOpKind::Writeback, 0, -1, requesterEpoch, 0,
        static_cast<int>(UBCCController::ResidentWaitReason::Capacity)));
    // Hold replay so the post-Clear queue directly exposes which tuples were
    // retired instead of immediately consuming the valid successors.
    commitUbcc.directory().setFillPending(committedPa, true);
    assert(commitUbcc.processClear(
        committedPa, 0, clearEpoch, committedReqId));

    const std::string committedState =
        commitUbcc.inspectOffloadLineForTest(committedPa);
    assert(committedState.find("\"resident_waiter_depth\":4") !=
           std::string::npos);
    OutstandingRequest *afterClear = commitUbcc.findOutstanding(committedPa);
    assert(!afterClear || afterClear->reqId != committedReqId);

    // TC35: only the exact node/socket/reqId tuple may retry a live Grant.
    UBCCController tupleUbcc(
        0, 1, nullptr, 64, commitCfg.bloom_bytes, 0, 2, 3, &commitCfg);
    tupleUbcc.setHost(&host);
    tupleUbcc.setResidentOverflowPolicy(ResidentOverflowPolicy::Spill);
    constexpr uint64_t tuplePa = 0x18000380;
    constexpr uint64_t tupleReqId = (1ULL << 56) | 0x4f;
    constexpr uint64_t tupleNextReqId = tupleReqId + 1;
    constexpr uint64_t tupleBaseEpoch = 77;
    assert(tupleUbcc.debugSeedResidentForTest(
        tuplePa, static_cast<int>(MESIState::G_I), 0, 0, false));
    uint64_t tupleClearEpoch = 0;
    const auto tupleGrant = tupleUbcc.processOuterRequest(
        tuplePa, UBCC_OuterReqType::GlobalReadShared, false,
        1, 0, tupleBaseEpoch, tupleReqId,
        nullptr, nullptr, nullptr, nullptr, nullptr,
        &tupleClearEpoch, nullptr);
    assert(static_cast<int>(tupleGrant) != -1);
    OutstandingRequest *tupleOutstanding = tupleUbcc.findOutstanding(tuplePa);
    assert(tupleOutstanding);
    assert(tupleOutstanding->stage == OpStage::WAITING_CLEAR);
    assert(tupleOutstanding->requesterSocket == 0);
    assert(tupleOutstanding->reqId == tupleReqId);
    const auto exactTupleRetry = tupleUbcc.processOuterRequest(
        tuplePa, UBCC_OuterReqType::GlobalReadShared, false,
        1, 0, tupleBaseEpoch, tupleReqId);
    assert(static_cast<int>(exactTupleRetry) == static_cast<int>(tupleGrant));
    const auto wrongSocketRetry = tupleUbcc.processOuterRequest(
        tuplePa, UBCC_OuterReqType::GlobalReadShared, false,
        1, 1, tupleBaseEpoch, tupleNextReqId);
    assert(static_cast<int>(wrongSocketRetry) == -1);
    const auto wrongReqIdRetry = tupleUbcc.processOuterRequest(
        tuplePa, UBCC_OuterReqType::GlobalReadShared, false,
        1, 0, tupleBaseEpoch, tupleNextReqId);
    assert(static_cast<int>(wrongReqIdRetry) == -1);
    tupleOutstanding = tupleUbcc.findOutstanding(tuplePa);
    assert(tupleOutstanding);
    assert(tupleOutstanding->requesterSocket == 0);
    assert(tupleOutstanding->reqId == tupleReqId);
    assert(!tupleUbcc.processClear(
        tuplePa, 1, tupleClearEpoch, tupleNextReqId));
    assert(tupleUbcc.processClear(
        tuplePa, 1, tupleClearEpoch, tupleReqId));

    // A normal dirty writeback can race a naive capacity recall after the
    // owner has already dropped its cache line. The matching data-bearing
    // writeback must complete the recall instead of being rejected as BUSY.
    ResidentDirConfig mergeCfg = cfg;
    mergeCfg.bloom_bytes = 0;
    UBCCController mergeUbcc(
        0, 0, nullptr, 64, 0, 0, 1, 3, &mergeCfg);
    HoldBackstoreHost mergeHost;
    CaptureOutbound outbound;
    mergeUbcc.setHost(&mergeHost);
    mergeUbcc.setOutbound(&outbound);
    mergeUbcc.setResidentOverflowPolicy(ResidentOverflowPolicy::NaiveEvict);
    constexpr uint64_t mergePa = 0x10000400;
    constexpr uint64_t mergeTarget = 0x10000480;
    constexpr uint64_t mergeEpoch = 7;
    assert(mergeUbcc.debugSeedResidentForTest(
        mergePa, static_cast<int>(MESIState::G_M), 1ULL << 1,
        mergeEpoch, true));
    const auto mergeBlocked = mergeUbcc.processOuterRequest(
        mergeTarget, UBCC_OuterReqType::GlobalReadUnique, true,
        0, 0, 1, 99);
    assert(static_cast<int>(mergeBlocked) == -1);
    assert(outbound.recallCount == 1);
    uint8_t payload[64];
    std::memset(payload, 0x5a, sizeof(payload));
    assert(mergeUbcc.processWritebackWithData(
        mergePa, 1, mergeEpoch, false, payload));
    assert(mergeHost.lastWritePa == mergePa);
    assert(std::memcmp(mergeHost.lastWriteData, payload, sizeof(payload)) == 0);
    assert(mergeUbcc.findOutstanding(mergePa) == nullptr);
    const std::string mergedState =
        mergeUbcc.inspectOffloadLineForTest(mergePa);
    assert(mergedState.find("\"resident_present\":false") !=
           std::string::npos);

    // A naive dirty-victim recall can release only one way for several
    // different PAs waiting on the same set. The first replayed PA installs a
    // grant-handshake entry and pins that way. Its Clear must wake the next
    // same-set capacity waiter after removing the pin.
    ResidentDirConfig clearWakeCfg = cfg;
    clearWakeCfg.bloom_bytes = 0;
    UBCCController clearWakeUbcc(
        0, 0, nullptr, 64, 0, 0, 1, 3, &clearWakeCfg);
    HoldBackstoreHost clearWakeHost;
    CaptureOutbound clearWakeOutbound;
    clearWakeUbcc.setHost(&clearWakeHost);
    clearWakeUbcc.setOutbound(&clearWakeOutbound);
    clearWakeUbcc.setResidentOverflowPolicy(
        ResidentOverflowPolicy::NaiveEvict);

    constexpr uint64_t clearWakeVictim = 0x10000500;
    constexpr uint64_t clearWakeFirst = 0x10000580;
    constexpr uint64_t clearWakeSecond = 0x10000600;
    constexpr uint64_t clearWakeVictimEpoch = 7;
    constexpr uint64_t clearWakeBaseEpoch = 33;
    constexpr uint64_t clearWakeFirstReqId = 1001;
    constexpr uint64_t clearWakeSecondReqId = 1002;
    assert(clearWakeUbcc.debugSeedResidentForTest(
        clearWakeVictim, static_cast<int>(MESIState::G_M), 1ULL << 2,
        clearWakeVictimEpoch, true));

    assert(static_cast<int>(clearWakeUbcc.processOuterRequest(
        clearWakeFirst, UBCC_OuterReqType::GlobalReadShared, false,
        0, 0, clearWakeBaseEpoch, clearWakeFirstReqId)) == -1);
    assert(static_cast<int>(clearWakeUbcc.processOuterRequest(
        clearWakeSecond, UBCC_OuterReqType::GlobalReadShared, false,
        1, 0, clearWakeBaseEpoch, clearWakeSecondReqId)) == -1);
    assert(clearWakeOutbound.recallCount == 1);
    assert(clearWakeOutbound.lastRecall.h.homeLinePa == clearWakeVictim);

    // The fixed outbound admission can transiently reject a capacity replay
    // push. The existing GRANT_HANDSHAKE must retain the exact tuple and retry
    // from bounded outstanding state, without adding another PA queue.
    clearWakeOutbound.rejectedGrantPushes = 1;
    assert(clearWakeUbcc.processRecallResponse(
        clearWakeVictim, 2, true, clearWakeVictimEpoch,
        clearWakeVictimEpoch));
    assert(clearWakeOutbound.grantPushAttempts == 1);
    assert(clearWakeOutbound.grants.empty());
    OutstandingRequest *retryGrant =
        clearWakeUbcc.findOutstanding(clearWakeFirst);
    assert(retryGrant);
    assert(retryGrant->opType == OpType::GRANT_HANDSHAKE);
    assert(retryGrant->stage == OpStage::WAITING_CLEAR);
    assert(retryGrant->replayArmed);
    clearWakeUbcc.wakeup();
    assert(clearWakeOutbound.grantPushAttempts == 2);
    assert(clearWakeOutbound.grants.size() == 1);
    assert(clearWakeOutbound.grants[0].h.homeLinePa == clearWakeFirst);
    assert(clearWakeOutbound.grants[0].h.reqId == clearWakeFirstReqId);
    assert(clearWakeUbcc.inspectOffloadLineForTest(clearWakeSecond).find(
        "\"resident_waiter_depth\":1") != std::string::npos);

    assert(clearWakeUbcc.processClear(
        clearWakeFirst, 0,
        clearWakeOutbound.grants[0].b.readResp.authEpoch,
        clearWakeFirstReqId));
    assert(clearWakeOutbound.invalidateCount == 1);
    assert(clearWakeOutbound.lastInvalidate.h.homeLinePa == clearWakeFirst);
    assert(clearWakeUbcc.processInvalidationAck(
        clearWakeFirst, 0,
        clearWakeOutbound.lastInvalidate.h.epoch,
        clearWakeOutbound.lastInvalidate.h.reqId));
    assert(clearWakeOutbound.grants.size() == 2);
    assert(clearWakeOutbound.grants[1].h.homeLinePa == clearWakeSecond);
    assert(clearWakeOutbound.grants[1].h.reqId == clearWakeSecondReqId);
    assert(clearWakeUbcc.inspectOffloadLineForTest(clearWakeSecond).find(
        "\"resident_waiter_depth\":0") != std::string::npos);
    assert(clearWakeUbcc.processClear(
        clearWakeSecond, 1,
        clearWakeOutbound.grants[1].b.readResp.authEpoch,
        clearWakeSecondReqId));

    // UpgradeDone also removes an outstanding pin. A different PA waiting on
    // the same one-way set must be retried when that transition completes.
    UBCCController upgradeWakeUbcc(
        0, 0, nullptr, 64, 0, 0, 1, 3, &clearWakeCfg);
    HoldBackstoreHost upgradeWakeHost;
    CaptureOutbound upgradeWakeOutbound;
    upgradeWakeUbcc.setHost(&upgradeWakeHost);
    upgradeWakeUbcc.setOutbound(&upgradeWakeOutbound);
    upgradeWakeUbcc.setResidentOverflowPolicy(
        ResidentOverflowPolicy::NaiveEvict);
    constexpr uint64_t upgradeWakeVictim = 0x10000700;
    constexpr uint64_t upgradeWakeTarget = 0x10000780;
    constexpr uint64_t upgradeWakeEpoch = 7;
    constexpr uint64_t upgradeWakeReqId = 2001;
    constexpr uint64_t upgradeWakeTargetReqId = 2002;
    assert(upgradeWakeUbcc.debugSeedResidentForTest(
        upgradeWakeVictim, static_cast<int>(MESIState::G_S), 1ULL << 2,
        upgradeWakeEpoch, false));
    assert(upgradeWakeUbcc.processOuterUpgradeReq(
        upgradeWakeVictim, 2, upgradeWakeEpoch, upgradeWakeReqId, 1,
        UBCC_UpgradeCause::LocalStoreUpgrade));
    OutstandingRequest *upgradeWakeOutstanding =
        upgradeWakeUbcc.findOutstanding(upgradeWakeVictim);
    assert(upgradeWakeOutstanding);
    const uint64_t upgradeWakeReservedEpoch =
        upgradeWakeOutstanding->reservedEpoch;
    assert(static_cast<int>(upgradeWakeUbcc.processOuterRequest(
        upgradeWakeTarget, UBCC_OuterReqType::GlobalReadShared, false,
        1, 0, 1, upgradeWakeTargetReqId)) == -1);
    assert(upgradeWakeOutbound.recallCount == 0);

    assert(upgradeWakeUbcc.processOuterUpgradeDone(
        upgradeWakeVictim, 2, upgradeWakeReservedEpoch, upgradeWakeReqId));
    assert(upgradeWakeOutbound.recallCount == 1);
    assert(upgradeWakeOutbound.lastRecall.h.homeLinePa == upgradeWakeVictim);
    assert(upgradeWakeUbcc.processRecallResponse(
        upgradeWakeVictim, 2, true,
        upgradeWakeOutbound.lastRecall.h.epoch,
        upgradeWakeOutbound.lastRecall.h.reqId));
    assert(upgradeWakeOutbound.grants.size() == 1);
    assert(upgradeWakeOutbound.grants[0].h.homeLinePa == upgradeWakeTarget);
    assert(upgradeWakeOutbound.grants[0].h.reqId == upgradeWakeTargetReqId);
    assert(upgradeWakeUbcc.processClear(
        upgradeWakeTarget, 1,
        upgradeWakeOutbound.grants[0].b.readResp.authEpoch,
        upgradeWakeTargetReqId));

    std::fprintf(stderr, "capacity waiter liveness regression passed\n");
    return 0;
}
