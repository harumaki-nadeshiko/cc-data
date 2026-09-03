#include "modules/hamodule/HAController.hh"

#include <stdexcept>

namespace cc::ha {

namespace {
unsigned popcount(std::uint64_t value)
{
    unsigned n = 0;
    while (value) { value &= value - 1; ++n; }
    return n;
}
unsigned firstSet(std::uint64_t value)
{
    unsigned n = 0;
    while (!(value & 1)) { value >>= 1; ++n; }
    return n;
}
}

HAController::Payload HAController::Payload::fromU64(std::uint64_t value)
{
    Payload result;
    result.valid = true;
    for (unsigned i = 0; i < 8; ++i)
        result.bytes[i] = static_cast<std::uint8_t>(value >> (i * 8));
    return result;
}

HAController::HAController(const Config &config)
    : directory_(config.directory), queueDepth_(config.perAddressQueueDepth),
      unavailable_(static_cast<std::size_t>(directory_.lineCount()), 0)
{
    if (!queueDepth_)
        throw std::invalid_argument("HAController queue depth must be non-zero");
}

void HAController::validateAddressNode(std::uint64_t address, std::uint32_t node) const
{
    if (address % directory_.config().lineBytes)
        throw std::out_of_range("HAController address is not line aligned");
    if (node >= directory_.config().nodeCount)
        throw std::out_of_range("HAController node is outside configured range");
}

bool HAController::unavailable(std::uint64_t address) const noexcept
{
    return directory_.contains(address) &&
        unavailable_[static_cast<std::size_t>((address - directory_.config().base) /
                                              directory_.config().lineBytes)];
}

bool HAController::submit(const Request &request)
{
    validateAddressNode(request.address, request.requester);
    if (!request.requestId)
        throw std::invalid_argument("HAController requestId zero is reserved");
    if (request.kind == RequestKind::Write &&
        (!request.data.valid || !request.byteMask))
        throw std::invalid_argument("HAController write requires data and a non-zero mask");
    if (request.kind == RequestKind::Read && request.byteMask)
        throw std::invalid_argument("HAController read mask must be zero");
    if (!directory_.contains(request.address)) return submitOverflow(request);
    if (unavailable(request.address)) {
        actions_.push_back({ActionKind::Reject, request.address, request.requester,
                            request.requester, request.requestId, {}});
        actions_.back().permanentReject = true;
        return false;
    }
    LineWork &work = work_[request.address];
    if (work.active) {
        if (work.waiting.size() >= queueDepth_) {
            actions_.push_back({ActionKind::Reject, request.address, request.requester,
                                request.requester, request.requestId, {}});
            return false;
        }
        work.waiting.push_back(request);
        return true;
    }
    start(work, request);
    return true;
}

bool HAController::submitOverflow(const Request &request)
{
    validateAddressNode(request.address, request.requester);
    if (directory_.contains(request.address)) return submit(request);
    if (!request.requestId)
        throw std::invalid_argument("HAController requestId zero is reserved");
    if (request.kind == RequestKind::Write &&
        (!request.data.valid || !request.byteMask))
        throw std::invalid_argument("HAController write requires data and a non-zero mask");
    if (request.kind == RequestKind::Read && request.byteMask)
        throw std::invalid_argument("HAController read mask must be zero");
    LineWork &work = work_[request.address];
    if (work.active) {
        if (work.waiting.size() >= queueDepth_) {
            actions_.push_back({ActionKind::Reject, request.address, request.requester,
                                request.requester, request.requestId, {}});
            return false;
        }
        work.waiting.push_back(request);
        return true;
    }
    Transaction txn;
    txn.request = request;
    txn.phase = Phase::Reconstruct;
    txn.overflow = true;
    txn.probePending = directory_.config().nodeCount == 64
        ? ~std::uint64_t{0} : (std::uint64_t{1} << directory_.config().nodeCount) - 1;
    work.active = txn;
    for (std::uint32_t node = 0; node < directory_.config().nodeCount; ++node)
        emit(ActionKind::Probe, *work.active, node, node);
    return true;
}

void HAController::emit(ActionKind kind, const Transaction &txn, std::uint32_t source,
                        std::uint32_t target, const Payload &data)
{
    actions_.push_back({kind, txn.request.address, source, target,
                        txn.request.requestId, data});
}

void HAController::emit(ActionKind kind, const Transaction &txn, std::uint32_t source,
                        std::uint32_t target)
{
    emit(kind, txn, source, target, Payload{});
}

void HAController::start(LineWork &work, const Request &request)
{
    Transaction txn;
    txn.request = request;
    txn.oldSharers = directory_.sharers(request.address);
    work.active = txn;
    startKnown(work, *work.active);
}

void HAController::startKnown(LineWork &work, Transaction &txn)
{
    txn.phase = Phase::NeedDataAndInvalidates;
    const std::uint64_t requesterBit = std::uint64_t{1} << txn.request.requester;
    if (txn.request.kind == RequestKind::Read) {
        if (popcount(txn.oldSharers) == 1) {
            txn.dataPending = true;
            txn.dataSource = firstSet(txn.oldSharers);
            // A singleton participant may be the dirty/latest owner, including
            // when it is also the requester.  Probe it before consulting Home
            // memory; a successful clean/no-data response explicitly permits
            // the memory fallback below.
            // A data-bearing ReadShared recall downgrades the sole dirty owner
            // into an ordinary shared participant. Persist the latest line
            // before that distinction is lost, even for a self read.
            txn.persistBeforeGrant = true;
            emit(ActionKind::FetchOwner, txn, txn.dataSource,
                 txn.request.requester);
        } else if (txn.oldSharers == 0 || popcount(txn.oldSharers) > 1) {
            txn.dataPending = true;
            txn.dataSource = txn.request.requester;
            txn.fetchingMemory = true;
            emit(ActionKind::FetchMemory, txn, txn.request.requester,
                 txn.request.requester);
        }
    } else {
        txn.partialWrite = txn.request.byteMask != ~std::uint64_t{0};
        txn.data = txn.request.data;
        txn.pendingInvalidates = txn.oldSharers & ~requesterBit;
        if (txn.partialWrite && popcount(txn.oldSharers) == 1)
            txn.pendingInvalidates &= ~txn.oldSharers;
        for (std::uint32_t node = 0; node < directory_.config().nodeCount; ++node)
            if (txn.pendingInvalidates & (std::uint64_t{1} << node))
                emit(ActionKind::Invalidate, txn, txn.request.requester, node);
        if (txn.partialWrite) {
            txn.dataPending = true;
            if (popcount(txn.oldSharers) == 1) {
                txn.dataSource = firstSet(txn.oldSharers);
                emit(ActionKind::FetchOwner, txn, txn.dataSource,
                     txn.request.requester);
            } else {
                txn.dataSource = txn.request.requester;
                txn.fetchingMemory = true;
                emit(ActionKind::FetchMemory, txn, txn.request.requester,
                     txn.request.requester);
            }
        }
    }
    maybeGrant(work);
}

void HAController::maybeGrant(LineWork &work)
{
    Transaction &txn = *work.active;
    if (txn.phase != Phase::NeedDataAndInvalidates || txn.dataPending || txn.pendingInvalidates)
        return;
    if (txn.persistBeforeGrant) {
        emit(ActionKind::PersistMemory, txn, txn.dataSource, txn.request.requester, txn.data);
        txn.phase = Phase::NeedPersistence;
        return;
    }
    emit(txn.request.kind == RequestKind::Read ? ActionKind::GrantRead : ActionKind::GrantWrite,
         txn, txn.request.requester, txn.request.requester, txn.data);
    txn.phase = Phase::NeedInstall;
}

void HAController::rejectUnavailable(LineWork &work, bool permanent)
{
    Transaction &txn = *work.active;
    emit(ActionKind::Reject, txn, txn.request.requester, txn.request.requester);
    actions_.back().permanentReject = permanent;
    finish(work, txn.request.address);
}

void HAController::finish(LineWork &work, std::uint64_t address)
{
    work.active.reset();
    if (!work.waiting.empty()) {
        Request next = work.waiting.front();
        work.waiting.pop_front();
        if (unavailable(next.address)) {
            actions_.push_back({ActionKind::Reject, next.address, next.requester,
                                next.requester, next.requestId, {}});
            actions_.back().permanentReject = true;
            finish(work, address);
        } else if (directory_.contains(next.address)) start(work, next);
        else {
            Transaction txn;
            txn.request = next;
            txn.phase = Phase::Reconstruct;
            txn.overflow = true;
            txn.probePending = directory_.config().nodeCount == 64
                ? ~std::uint64_t{0} : (std::uint64_t{1} << directory_.config().nodeCount) - 1;
            work.active = txn;
            for (std::uint32_t node = 0; node < directory_.config().nodeCount; ++node)
                emit(ActionKind::Probe, *work.active, node, node);
        }
    } else work_.erase(address);
}

void HAController::accept(const Event &event)
{
    validateAddressNode(event.address, event.node);
    if (event.kind == EventKind::PeerExit) {
        const std::uint64_t departed = std::uint64_t{1} << event.node;
        for (std::uint64_t i = 0; i < directory_.lineCount(); ++i) {
            const std::uint64_t pa = directory_.addressOf(i);
            const std::uint64_t sharers = directory_.sharers(pa);
            if (sharers == departed)
                unavailable_[static_cast<std::size_t>(i)] = 1; // latest copy was lost
            else if (sharers & departed)
                directory_.setSharers(pa, sharers & ~departed); // Home memory is latest
        }
        std::vector<std::uint64_t> reconstructed;
        for (auto &item : work_) {
            if (!item.second.active) continue;
            Transaction &active = *item.second.active;
            active.pendingInvalidates &= ~departed;
            active.probePending &= ~departed;
            if (active.phase == Phase::Reconstruct && !active.probePending)
                reconstructed.push_back(item.first);
            if (active.dataPending && active.dataSource == event.node) {
                active.dataPending = true;
                active.dataSource = event.node; // poison; reject below after iteration
            }
        }
        std::vector<std::uint64_t> reject;
        for (const auto &item : work_)
            if (item.second.active && item.second.active->dataPending &&
                item.second.active->dataSource == event.node &&
                item.second.active->phase != Phase::Reconstruct) reject.push_back(item.first);
        for (std::uint64_t pa : reject) {
            auto found = work_.find(pa);
            if (found != work_.end() && found->second.active)
                rejectUnavailable(found->second, true);
        }
        for (std::uint64_t pa : reconstructed) {
            auto found = work_.find(pa);
            if (found == work_.end() || !found->second.active ||
                found->second.active->phase != Phase::Reconstruct) continue;
            Transaction &txn = *found->second.active;
            txn.oldSharers = txn.reconstructed;
            if (txn.reconstructOnly) {
                directory_.setSharers(pa, txn.reconstructed);
                emit(ActionKind::Commit, txn, event.node, event.node);
                emit(ActionKind::Release, txn, event.node, event.node);
                finish(found->second, pa);
            } else startKnown(found->second, txn);
        }
        return;
    }

    if (event.kind == EventKind::Writeback) {
        if (!directory_.contains(event.address) || !event.data.valid) return;
        if (writebacks_.find(event.address) != writebacks_.end()) return;
        Transaction wire;
        wire.request = {event.address, event.node, RequestKind::Write,
                        event.requestId, event.data};
        emit(ActionKind::PersistMemory, wire, event.node, event.node, event.data);
        writebacks_[event.address] = {event.node, event.requestId, event.data, event.present};
        return;
    }
    if (event.kind == EventKind::WritebackFailed) {
        auto wb = writebacks_.find(event.address);
        if (wb != writebacks_.end() && wb->second.node == event.node &&
            wb->second.requestId == event.requestId)
            writebacks_.erase(wb);
        return;
    }
    if (event.kind == EventKind::PersistenceComplete) {
        auto wb = writebacks_.find(event.address);
        if (wb != writebacks_.end() && wb->second.node == event.node &&
            wb->second.requestId == event.requestId) {
            directory_.setSharers(event.address,
                wb->second.retain ? (std::uint64_t{1} << event.node) : 0);
            unavailable_[static_cast<std::size_t>(directory_.lineIndex(event.address))] = 0;
            writebacks_.erase(wb);
            return;
        }
    }
    if (event.kind == EventKind::Evict) {
        auto wb = writebacks_.find(event.address);
        if (wb != writebacks_.end() && wb->second.node == event.node)
            wb->second.retain = false;
        else if (directory_.contains(event.address)) directory_.set(event.address, event.node, false);
        return;
    }

    auto found = work_.find(event.address);
    if (found == work_.end() || !found->second.active) return;
    LineWork &work = found->second;
    Transaction &txn = *work.active;
    if (event.requestId != txn.request.requestId) return;
    if (event.kind == EventKind::Unavailable) {
        if (directory_.contains(event.address))
            unavailable_[static_cast<std::size_t>(directory_.lineIndex(event.address))] = 1;
        rejectUnavailable(work, true);
        return;
    }
    if (event.kind == EventKind::RetryableBusy) {
        // Admission can reject only before coherence state has been disturbed.
        // Once owner data or an invalidation ack has been accepted, keep the
        // transaction alive; the adapter retries the same DSM operation.
        if (!txn.destructiveAccepted &&
            txn.phase == Phase::NeedDataAndInvalidates) {
            rejectUnavailable(work, false);
        }
        return;
    }
    const std::uint64_t nodeBit = std::uint64_t{1} << event.node;

    if (txn.phase == Phase::Reconstruct) {
        if (event.kind != EventKind::ProbeResponse || !(txn.probePending & nodeBit)) return;
        txn.probePending &= ~nodeBit;
        if (event.present) txn.reconstructed |= nodeBit;
        if (!txn.probePending) {
            txn.oldSharers = txn.reconstructed;
            if (txn.reconstructOnly) {
                directory_.setSharers(event.address, txn.reconstructed);
                emit(ActionKind::Commit, txn, event.node, event.node);
                emit(ActionKind::Release, txn, event.node, event.node);
                finish(work, event.address);
            } else startKnown(work, txn);
        }
        return;
    }
    if (event.kind == EventKind::OwnerData && txn.dataPending &&
        event.node == txn.dataSource && event.data.valid) {
        if (txn.oldSharers & nodeBit) txn.destructiveAccepted = true;
        txn.dataPending = false;
        if (txn.partialWrite) {
            txn.data = event.data;
            for (unsigned i = 0; i < 64; ++i)
                if (txn.request.byteMask & (std::uint64_t{1} << i))
                    txn.data.bytes[i] = txn.request.data.bytes[i];
            txn.data.valid = true;
        } else {
            txn.data = event.data;
        }
        maybeGrant(work);
    } else if (event.kind == EventKind::OwnerNoData && txn.dataPending &&
               event.node == txn.dataSource && !txn.fetchingMemory) {
        // The recall completed and established that this was a clean
        // presence-only participant.  Home memory is therefore authoritative.
        // Do not issue the recall again if the asynchronous memory access is
        // subsequently busy.
        txn.destructiveAccepted = true;
        txn.persistBeforeGrant = false;
        txn.dataSource = txn.request.requester;
        txn.fetchingMemory = true;
        emit(ActionKind::FetchMemory, txn, txn.request.requester,
             txn.request.requester);
    } else if (event.kind == EventKind::InvalidateAck && (txn.pendingInvalidates & nodeBit)) {
        txn.destructiveAccepted = true;
        txn.pendingInvalidates &= ~nodeBit;
        maybeGrant(work);
    } else if (event.kind == EventKind::PersistenceComplete &&
               txn.phase == Phase::NeedPersistence) {
        if (!txn.overflow)
            unavailable_[static_cast<std::size_t>(directory_.lineIndex(event.address))] = 0;
        txn.persistBeforeGrant = false;
        txn.phase = Phase::NeedDataAndInvalidates;
        maybeGrant(work);
    } else if (event.kind == EventKind::InstallAck && txn.phase == Phase::NeedInstall &&
               event.node == txn.request.requester) {
        const std::uint64_t requesterBit = std::uint64_t{1} << txn.request.requester;
        const std::uint64_t committed = txn.request.kind == RequestKind::Write
            ? requesterBit : txn.oldSharers | requesterBit;
        if (!txn.overflow) directory_.setSharers(event.address, committed);
        emit(ActionKind::Commit, txn, event.node, event.node);
        emit(ActionKind::Release, txn, event.node, event.node);
        finish(work, event.address);
    }
}

HAController::Action HAController::popAction()
{
    if (actions_.empty()) throw std::underflow_error("HAController action queue is empty");
    Action result = actions_.front(); actions_.pop_front(); return result;
}

std::size_t HAController::queued(std::uint64_t address) const
{
    auto found = work_.find(address);
    return found == work_.end() ? 0 : found->second.waiting.size();
}

bool HAController::busy(std::uint64_t address) const
{
    auto found = work_.find(address);
    return found != work_.end() && found->second.active.has_value();
}

bool HAController::retryTransient(std::uint64_t address, std::uint64_t requestId)
{
    auto found = work_.find(address);
    if (found == work_.end() || !found->second.active ||
        found->second.active->request.requestId != requestId)
        return false;
    Transaction &txn = *found->second.active;
    if (txn.phase == Phase::NeedPersistence) {
        emit(ActionKind::PersistMemory, txn, txn.dataSource,
             txn.request.requester, txn.data);
        return true;
    }
    if (txn.dataPending && txn.fetchingMemory) {
        emit(ActionKind::FetchMemory, txn, txn.request.requester,
             txn.request.requester);
        return true;
    }
    return false;
}

bool HAController::beginBroadcastReconstruction(std::uint64_t address, std::uint64_t requestId)
{
    (void)directory_.lineIndex(address);
    if (!requestId) throw std::invalid_argument("HAController requestId zero is reserved");
    LineWork &work = work_[address];
    if (work.active) return false;
    Transaction txn;
    txn.request = {address, 0, RequestKind::Read, requestId};
    txn.phase = Phase::Reconstruct;
    txn.reconstructOnly = true;
    txn.probePending = directory_.config().nodeCount == 64
        ? ~std::uint64_t{0} : (std::uint64_t{1} << directory_.config().nodeCount) - 1;
    work.active = txn;
    for (std::uint32_t node = 0; node < directory_.config().nodeCount; ++node)
        emit(ActionKind::Probe, *work.active, node, node);
    return true;
}

} // namespace cc::ha
