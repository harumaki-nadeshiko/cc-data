#ifndef __MEM_RUBY_PROTOCOL_CHI_EP_BACKSTOREHOSTH64_HH__
#define __MEM_RUBY_PROTOCOL_CHI_EP_BACKSTOREHOSTH64_HH__

#include <cstdint>
#include <cstddef>
#include <cstring>
#include <functional>
#include <memory>

#include "BackstoreTypes.hh"
#include "BackstoreSchemaH64.hh"
#include "CoherenceMessage.hh"

namespace cc { namespace glob {

// ---- Pure translation helper: logical bucketOffset -> physical PA ----
// UBIO uses logical 64B bucketOffset only.  UBAdapter maps:
//   physical = metadataRangeStart + bucketOffset * 64
// This helper is used by tests and UBAdapter; Host never computes physical PA.
inline uint64_t h64BucketOffsetToPhys(uint64_t metadataRangeStart,
                                       uint64_t bucketOffset) {
    return metadataRangeStart + bucketOffset * 64ULL;
}
inline bool h64BucketOffsetInRange(uint64_t bucketOffset, uint64_t metadataBytes) {
    return (bucketOffset * 64ULL + 64ULL) <= metadataBytes;
}

class MetaRNFClientIF {
  public:
    virtual ~MetaRNFClientIF() = default;
    virtual void readLine(uint64_t logicalBucketOffset,
        std::function<void(MetaRNFLineStatus, const uint8_t* data64)> cb) = 0;
    virtual bool retryReadLine(uint64_t logicalBucketOffset,
        std::function<void(MetaRNFLineStatus, const uint8_t* data64)> cb) {
        readLine(logicalBucketOffset, std::move(cb));
        return true;
    }
    virtual void writeLine(uint64_t logicalBucketOffset, const uint8_t* data64,
        std::function<void(MetaRNFLineStatus)> cb) = 0;
};

// ---- Group control record: persisted in metadata DRAM ----
// Each group has one 64B record before its table buckets.
// Logical offset of group g's control record = g (reserved range: 0..num_groups-1).
// Table bucket data starts at offset = num_groups.
struct H64GroupControl {
    static constexpr uint8_t kFmtVersion = 1;
    static constexpr size_t  kRecordBytes = 64;

    uint8_t  format;
    uint8_t  _pad0[3];
    uint32_t active_bucket_count;
    uint64_t salt;
    uint8_t  generation;
    uint8_t  flags;
    uint8_t  _pad1[46];

    H64GroupControl() : format(kFmtVersion), active_bucket_count(0),
                        salt(0), generation(0), flags(0) {
        std::memset(_pad0, 0, sizeof(_pad0));
        std::memset(_pad1, 0, sizeof(_pad1));
    }
    bool valid() const { return format == kFmtVersion && active_bucket_count > 0; }

    void loadFrom(const uint8_t raw[64]) {
        std::memcpy(this, raw, 64);
    }
    void storeTo(uint8_t raw[64]) const {
        std::memcpy(raw, this, 64);
    }
};
static_assert(sizeof(H64GroupControl) == 64, "H64GroupControl must be 64B");

struct H64HostConfig {
    size_t   num_groups;
    size_t   buckets_per_group;     // max active extent per group
    uint64_t hash_seed;

    // Logical metadata capacity: total logical 64B lines available
    // Must be >= (num_groups control records + num_groups * buckets_per_group)
    uint64_t metadata_socket_lines;  // total 64B lines (not bytes) in metadata DRAM

    int      max_active_rmw;
    int      max_pending_ops;
    int      max_waiters_per_bucket;

    H64HostConfig()
        : num_groups(256), buckets_per_group(1024),
          hash_seed(0x9e3779b97f4a7c15ULL),
          metadata_socket_lines(256 + 256*1024),  // control + table
          max_active_rmw(8), max_pending_ops(128), max_waiters_per_bucket(8) {}

    H64Config toSchemaConfig() const {
        H64Config c;
        c.num_groups = num_groups;
        c.buckets_per_group = buckets_per_group;
        c.hash_seed = hash_seed;
        return c;
    }
    size_t totalBuckets() const { return num_groups * buckets_per_group; }
    size_t tableDataStartOffset() const { return num_groups; } // control recs first
    size_t groupControlOffset(size_t g) const { return g; }    // logical offset
    size_t bucketDataOffset(size_t groupIdx, size_t bucketIdx) const {
        return tableDataStartOffset() + groupIdx * buckets_per_group + bucketIdx;
    }
};

/**
 * BackstoreHostH64 — bounded async host for Schema H64 metadata DRAM access.
 *
 * Key properties:
 *   1. Fixed transaction slot table (<=128 slots). No std::map/deque keyed by PA.
 *      Duplicate same-PA requests return RetryableBusy (bounded check).
 *   2. Per-bucket waiter queue (<=8/bucket, circular array). Oldest-selected.
 *      RMW contention waits, rereads fresh bucket, revalidates before write.
 *   3. Logical bucketOffset only — no physical PA knowledge.
 *      Translation helper h64BucketOffsetToPhys used by UBAdapter and tests.
 *   4. Group control records persisted in metadata DRAM (offsets 0..num_groups-1).
 *      Every transaction ensures group control is valid before probing table.
 *   5. Full group scan within active_bucket_count from control record.
 *   6. All [DEBUG-H64-*] diagnostics default off; gated by _debugEnabled.
 */
class BackstoreHostH64
{
  public:
    BackstoreHostH64(const H64HostConfig& cfg, MetaRNFClientIF* metaRNF);
    ~BackstoreHostH64();

    BackstoreHostH64(const BackstoreHostH64&) = delete;
    BackstoreHostH64& operator=(const BackstoreHostH64&) = delete;

    void lookup(uint64_t linePa,
                std::function<void(const BackstoreCompletion&)> completion);
    void upsert(uint64_t linePa, UBCCMESIState state, uint64_t sharersMask,
                uint64_t epoch,
                std::function<void(const BackstoreCompletion&)> completion);
    void erase(uint64_t linePa, uint64_t deleteEpoch,
               std::function<void(const BackstoreCompletion&)> completion);

    // Bounded, asynchronous scan used exclusively by Bloom reconstruction.
    // It reads only the persisted active extent and never mutates metadata.
    void scanGroupLive(size_t groupIdx,
                       std::function<void(const H64SlotEntry&)> onLive,
                       std::function<void(BackstoreStatus)> completion);

    const H64HostConfig& config() const { return _cfg; }

    // ---- Introspection for tests ----
    int  activeSlotCount() const;
    int  activeRmwCount() const;
    int  bucketWaiterCount(size_t flatIdx) const;
    bool isBucketLocked(size_t flatIdx) const;
    bool isPaBusy(uint64_t linePa) const;
    void setDebugEnabled(bool v) { _debugEnabled = v; }
    // Retry metadata probes only from the outer event loop. A synchronous
    // RetryableBusy callback must never recursively resubmit the same read.
    void pumpRetries();

  private:
    static constexpr int kMaxSlots = 128;
    static constexpr int kMaxGroupScans = 1;

    enum class SlotState : uint8_t {
        Free, Probing, ProbeRetryPending, WaitingControl, RmwPending,
        RmwCreditPending
    };

    struct TxnSlot {
        uint64_t         linePa;
        BackstoreOp      op;
        uint64_t         snapshotEpoch;
        SlotState        state;
        // Probe
        size_t           groupIdx;
        size_t           homeBucket;
        size_t           probeIdx;
        size_t           activeBuckets;  // from control record
        bool             tombstoneSeen;
        size_t           tombstoneBucket;
        int              tombstoneSlot;
        bool             emptySeen;
        size_t           emptyBucket;
        int              emptySlot;
        // Upsert
        H64SlotEntry     upsertEntry;
        bool             upsertDataReady;
        // RMW
        H64BucketLine    rmwBucket;
        size_t           rmwBucketIdx;
        size_t           rmwFlatIdx;
        bool             rmwReadDone;
        // Completion
        BackstoreCompletion result;
        std::function<void(const BackstoreCompletion&)> cb;

        TxnSlot() : linePa(0), op(BackstoreOp::Lookup), snapshotEpoch(0),
            state(SlotState::Free), groupIdx(0), homeBucket(0), probeIdx(0),
            activeBuckets(0), tombstoneSeen(false), tombstoneBucket(0),
            tombstoneSlot(-1), emptySeen(false), emptyBucket(0), emptySlot(-1),
            upsertDataReady(false), rmwBucketIdx(0), rmwFlatIdx(0),
            rmwReadDone(false) {}
    };

    TxnSlot _slots[kMaxSlots];
    int     _slotCount = 0;

    struct GroupScan {
        bool active = false;
        size_t groupIdx = 0;
        size_t activeBuckets = 0;
        size_t nextBucket = 0;
        uint32_t seqBefore = 0;
        uint8_t rescans = 0;
        std::function<void(const H64SlotEntry&)> onLive;
        std::function<void(BackstoreStatus)> completion;
    };
    GroupScan _groupScans[kMaxGroupScans];
    void onGroupScanControl(int scanIdx, MetaRNFLineStatus st,
                            const uint8_t *data64);
    void readGroupScanBucket(int scanIdx);
    void onGroupScanBucket(int scanIdx, MetaRNFLineStatus st,
                           const uint8_t *data64);
    void completeGroupScan(int scanIdx, BackstoreStatus status);

    static constexpr size_t kTrackedGroups = 256;
    static constexpr uint8_t kMaxGroupScanRescans = 3;
    uint32_t _mutationSeq[kTrackedGroups]{};
    uint16_t _activeWriters[kTrackedGroups]{};
    void beginGroupMutation(size_t groupIdx);
    void endGroupMutation(size_t groupIdx);

    int allocSlot();
    void freeSlot(int idx);

    // ---- Active-bucket waiter table (fixed, <=128 buckets) ----
    // Only buckets touched by live transactions need serialization state.
    // Allocating this for every metadata-DRAM bucket would make host memory
    // scale with DRAM capacity rather than the fixed transaction bound.
    static constexpr int kMaxWaitersPerBucket = 8;
    static constexpr int kMaxActiveBucketStates = kMaxSlots;
    struct BucketWaiter { int slotIdx; uint64_t arrivalSeq; };
    struct BucketState {
        bool valid;
        size_t flatIdx;
        bool locked;
        BucketWaiter waiters[kMaxWaitersPerBucket];
        int  waiterHead;
        int  waiterCount;
        BucketState() : valid(false), flatIdx(0), locked(false), waiterHead(0), waiterCount(0) {
            for (int i=0;i<kMaxWaitersPerBucket;++i) waiters[i]={-1,0};
        }
    };
    BucketState _bucketStates[kMaxActiveBucketStates];
    BucketState* findBucketState(size_t flatIdx);
    const BucketState* findBucketState(size_t flatIdx) const;
    BucketState* acquireBucketState(size_t flatIdx);
    void releaseBucketStateIfIdle(size_t flatIdx);

    // Transactions that have already completed their probe retain their
    // bounded slot while waiting for an RMW credit. This is admission, not a
    // PA-indexed retry cache: at most kMaxSlots slot indices can be queued.
    int _rmwCreditWaiters[kMaxSlots]{};
    int _rmwCreditHead = 0;
    int _rmwCreditCount = 0;
    void enqueueRmwCredit(int slotIdx);
    void resumeRmwCredits();

    // ---- Group control cache (in-memory, refreshed on first probe) ----
    struct GroupCtrlCache {
        bool     valid;       // has been read from DRAM and is valid
        uint32_t active_bucket_count;
        uint64_t salt;
        uint8_t  generation;
    };
    std::unique_ptr<GroupCtrlCache[]> _groupCtrls;
    size_t _groupCtrlsSize = 0;

    uint64_t _arrivalSeq = 0;
    int      _probeRetryCursor = 0;
    bool     _debugEnabled = false;   // [DEBUG-H64-*] gate

    // ---- Internal methods ----
    void ensureGroupControl(int slotIdx);
    void onGroupControlRead(int slotIdx, MetaRNFLineStatus st, const uint8_t* data64);
    void startProbe(int slotIdx);
    void onProbeBucketRead(int slotIdx, MetaRNFLineStatus st, const uint8_t* data64);
    void startRmwWrite(int slotIdx);
    void onRmwWriteAck(int slotIdx, MetaRNFLineStatus st);
    void completeSlot(int slotIdx);
    void resumeBucketWaiter(size_t flatIdx);
    void rereadForRmw(int slotIdx, size_t flatIdx);

    size_t flatBucketIdx(size_t groupIdx, size_t bucketIdx) const;
    size_t tableBucketOffset(size_t groupIdx, size_t bucketIdx) const;
    size_t groupForPa(uint64_t linePa) const;
    size_t homeBucketForPa(uint64_t linePa) const;

    static BackstoreStatus mapMetaRNFStatus(MetaRNFLineStatus ms);

    H64HostConfig _cfg;
    MetaRNFClientIF* _metaRNF;
};

} }
#endif
