#include "modules/hamodule/HAController.hh"

#include <cassert>
#include <cstdint>
#include <iostream>
#include <stdexcept>
#include <vector>

using cc::ha::FlatBitmapDirectory;
using cc::ha::HAController;

namespace {

using AK = HAController::ActionKind;
using EK = HAController::EventKind;
using RK = HAController::RequestKind;

HAController::Payload payload(std::uint64_t value)
{
    return HAController::Payload::fromU64(value);
}

HAController::Payload patternedPayload()
{
    HAController::Payload result;
    result.valid = true;
    for (std::size_t i = 0; i < result.bytes.size(); ++i)
        result.bytes[i] = static_cast<std::uint8_t>((i * 37) ^ 0xa5);
    return result;
}

HAController::Event event(EK kind, std::uint64_t pa, std::uint32_t node,
                          std::uint64_t id = 0,
                          HAController::Payload data = {}, bool present = false,
                          bool dirty = false)
{
    return {kind, pa, node, id, data, present, dirty};
}

HAController makeController(std::uint32_t nodes = 4, std::size_t depth = 4)
{
    return HAController({{0x100000, 64 * 32, 64, nodes}, depth});
}

std::vector<HAController::Action> drain(HAController &ha)
{
    std::vector<HAController::Action> result;
    while (ha.hasAction()) result.push_back(ha.popAction());
    return result;
}

bool has(const std::vector<HAController::Action> &actions, AK kind, std::uint64_t id)
{
    for (const auto &action : actions)
        if (action.kind == kind && action.requestId == id) return true;
    return false;
}

void testBitmapBudgets()
{
    constexpr std::uint64_t budgetBits = FlatBitmapDirectory::MaxPayloadBytes * 8ULL;
    for (std::uint32_t nodes : {2u, 3u, 4u, 6u, 8u, 16u}) {
        const std::uint64_t lines = budgetBits / nodes;
        FlatBitmapDirectory dir({0, lines * 64, 64, nodes});
        assert(dir.payloadBits() == lines * nodes);
        assert(dir.payloadBytes() <= FlatBitmapDirectory::MaxPayloadBytes);
        const std::uint64_t last = (lines - 1) * 64;
        dir.setSharers(last, (std::uint64_t{1} << nodes) - 1);
        assert(dir.sharers(last) == (std::uint64_t{1} << nodes) - 1);
    }
    bool rejected = false;
    try { FlatBitmapDirectory tooLarge({0, (budgetBits / 2 + 1) * 64, 64, 2}); }
    catch (const std::invalid_argument &) { rejected = true; }
    assert(rejected);
    rejected = false;
    try { FlatBitmapDirectory misaligned({1, 64, 64, 2}); }
    catch (const std::invalid_argument &) { rejected = true; }
    assert(rejected);
}

void testSingletonAndMultiRead()
{
    auto ha = makeController();
    const std::uint64_t pa = 0x100000;

    ha.directoryForTest().setSharers(pa, 1u << 2);
    assert(ha.submit({pa, 2, RK::Read, 9}));
    auto actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::FetchOwner &&
           actions[0].source == 2 && actions[0].target == 2);
    const auto selfLine = payload(0x900d);
    ha.accept(event(EK::OwnerData, pa, 2, 9, selfLine));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::PersistMemory &&
           actions[0].data == selfLine);
    ha.accept(event(EK::PersistenceComplete, pa, 2, 9));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::GrantRead &&
           actions[0].data == selfLine);
    ha.accept({EK::InstallAck, pa, 2, 9});
    drain(ha);

    ha.directoryForTest().setSharers(pa, 1u << 1);
    assert(ha.submit({pa, 2, RK::Read, 10}));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::FetchOwner && actions[0].source == 1);
    const auto fullLine = patternedPayload();
    ha.accept(event(EK::OwnerData, pa, 1, 10, fullLine));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::PersistMemory &&
           actions[0].data == fullLine);
    ha.accept(event(EK::PersistenceComplete, pa, 1, 10));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::GrantRead &&
           actions[0].data == fullLine);
    assert(ha.directory().sharers(pa) == (1u << 1)); // install Ack gates commit
    ha.accept({EK::InstallAck, pa, 2, 10});
    actions = drain(ha);
    assert(has(actions, AK::Commit, 10) && has(actions, AK::Release, 10));
    assert(ha.directory().sharers(pa) == ((1u << 1) | (1u << 2)));

    const std::uint64_t cleanPa = pa + 128;
    ha.directoryForTest().setSharers(cleanPa, 1u << 1);
    assert(ha.submit({cleanPa, 2, RK::Read, 13}));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::FetchOwner);
    ha.accept(event(EK::OwnerNoData, cleanPa, 1, 13));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::FetchMemory);
    ha.accept(event(EK::OwnerData, cleanPa, 2, 13, payload(0xc1ea)));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::GrantRead);

    assert(ha.submit({pa, 3, RK::Read, 11}));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::FetchMemory);
    ha.accept(event(EK::OwnerData, pa, 3, 11, payload(0xdef)));
    assert(has(drain(ha), AK::GrantRead, 11));
    ha.accept({EK::InstallAck, pa, 3, 11});
    drain(ha);
    assert(ha.directory().sharers(pa) == 0xe);

    const std::uint64_t multiPa = pa + 64;
    ha.directoryForTest().setSharers(multiPa, (1u << 1) | (1u << 2));
    assert(ha.submit({multiPa, 2, RK::Read, 12}));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::FetchMemory);
    const auto memoryLine = payload(0x12345678);
    ha.accept(event(EK::OwnerData, multiPa, 2, 12, memoryLine));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::GrantRead &&
           actions[0].data == memoryLine);
}

void testWriterBarrierAndAckQueueGating()
{
    auto ha = makeController(4, 2);
    const std::uint64_t pa = 0x100040;
    ha.directoryForTest().setSharers(pa, (1u << 0) | (1u << 1) | (1u << 2));
    const auto writeLine = payload(0x20);
    assert(ha.submit({pa, 3, RK::Write, 20, writeLine}));
    assert(ha.submit({pa, 0, RK::Read, 21}));
    assert(ha.queued(pa) == 1);
    auto actions = drain(ha);
    assert(actions.size() == 3); // invalidates gate the write-back grant
    ha.accept({EK::InvalidateAck, pa, 2, 20});
    ha.accept({EK::InvalidateAck, pa, 0, 999}); // stale ID ignored
    ha.accept({EK::InvalidateAck, pa, 0, 20});
    assert(drain(ha).empty());
    ha.accept({EK::InvalidateAck, pa, 1, 20});
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::GrantWrite &&
           actions[0].data == writeLine);
    assert(ha.directory().sharers(pa) == 0x7);
    ha.accept({EK::InstallAck, pa, 3, 999});
    assert(drain(ha).empty() && ha.queued(pa) == 1);
    ha.accept({EK::InstallAck, pa, 3, 20});
    actions = drain(ha);
    assert(has(actions, AK::Commit, 20) && has(actions, AK::Release, 20));
    assert(has(actions, AK::FetchOwner, 21)); // queued request starts only now
    assert(ha.directory().sharers(pa) == 0x8);
    ha.accept({EK::InstallAck, pa, 3, 20}); // duplicate cannot finish request 21
    assert(drain(ha).empty() && ha.busy(pa));

    const std::uint64_t handoffPa = pa + 64;
    ha.directoryForTest().setSharers(handoffPa, 1u << 1);
    const auto handoffLine = payload(0x22);
    assert(ha.submit({handoffPa, 2, RK::Write, 22, handoffLine}));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::Invalidate &&
           actions[0].target == 1);
    ha.accept(event(EK::InvalidateAck, handoffPa, 1, 22));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::GrantWrite &&
           actions[0].data == handoffLine);
}

void testDifferentAddressConcurrencyAndBroadcast()
{
    auto ha = makeController();
    const std::uint64_t a = 0x100080, b = 0x1000c0;
    assert(ha.submit({a, 0, RK::Read, 30}));
    assert(ha.submit({b, 1, RK::Read, 31}));
    auto actions = drain(ha);
    assert(has(actions, AK::FetchMemory, 30) && has(actions, AK::FetchMemory, 31));

    ha.accept(event(EK::OwnerData, b, 1, 31, payload(31)));
    assert(has(drain(ha), AK::GrantRead, 31));
    ha.accept({EK::InstallAck, b, 1, 31});
    drain(ha);
    assert(!ha.busy(b) && ha.busy(a));

    const std::uint64_t c = 0x100100;
    assert(ha.beginBroadcastReconstruction(c, 40));
    actions = drain(ha);
    assert(actions.size() == 4);
    ha.accept(event(EK::ProbeResponse, c, 2, 40, {}, true));
    ha.accept(event(EK::ProbeResponse, c, 2, 40, {}, false)); // duplicate ignored
    ha.accept(event(EK::ProbeResponse, c, 0, 39, {}, true));  // stale ignored
    ha.accept(event(EK::ProbeResponse, c, 3, 40, {}, true));
    ha.accept(event(EK::ProbeResponse, c, 0, 40, {}, false));
    ha.accept(event(EK::ProbeResponse, c, 1, 40, {}, false));
    actions = drain(ha);
    assert(has(actions, AK::Commit, 40) && has(actions, AK::Release, 40));
    assert(ha.directory().sharers(c) == ((1u << 2) | (1u << 3)));
}

void testWritebackEvictAndPeerExit()
{
    auto ha = makeController();
    const std::uint64_t pa = 0x100140;
    const auto fullLine = patternedPayload();
    ha.accept(event(EK::Writeback, pa, 2, 50, fullLine, true, true));
    auto actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::PersistMemory &&
           actions[0].data == fullLine);
    assert(ha.directory().sharers(pa) == 0); // persistence gates stable state
    ha.accept(event(EK::PersistenceComplete, pa, 2, 50));
    assert(ha.directory().sharers(pa) == (1u << 2));
    ha.accept({EK::Evict, pa, 2});
    assert(ha.directory().sharers(pa) == 0);
    ha.directoryForTest().setSharers(pa, (1u << 1) | (1u << 3));
    ha.accept({EK::PeerExit, pa, 1});
    assert(ha.directory().sharers(pa) == (1u << 3));

    ha.directoryForTest().setSharers(pa, 1u << 3);
    ha.accept({EK::PeerExit, pa, 3});
    assert(ha.directory().sharers(pa) == (1u << 3)); // never claim stale memory
    assert(!ha.submit({pa, 0, RK::Read, 51}));
    assert(has(drain(ha), AK::Reject, 51));

    const std::uint64_t wbPa = 0x100180;
    ha.directoryForTest().setSharers(wbPa, 1u << 1);
    ha.accept(event(EK::Writeback, wbPa, 1, 52, fullLine, true, true));
    drain(ha);
    ha.accept({EK::Evict, wbPa, 1});
    assert(ha.directory().sharers(wbPa) == (1u << 1));
    ha.accept(event(EK::PersistenceComplete, wbPa, 1, 52));
    assert(ha.directory().sharers(wbPa) == 0); // clear only after persistence

    HAController::Payload invalid;
    ha.accept(event(EK::Writeback, wbPa, 1, 53, invalid, true, true));
    assert(drain(ha).empty());

    const std::uint64_t failedPa = 0x1001c0;
    ha.directoryForTest().setSharers(failedPa, 1u << 2);
    ha.accept(event(EK::Writeback, failedPa, 2, 54, fullLine, false, true));
    assert(has(drain(ha), AK::PersistMemory, 54));
    ha.accept(event(EK::WritebackFailed, failedPa, 2, 54));
    assert(ha.directory().sharers(failedPa) == (1u << 2));
    ha.accept(event(EK::Writeback, failedPa, 2, 55, fullLine, false, true));
    assert(has(drain(ha), AK::PersistMemory, 55));
}

void testOverflowTransactionsAreTransient()
{
    auto ha = makeController();
    const std::uint64_t pa = 0x200000; // exact-directory miss
    assert(ha.submit({pa, 2, RK::Read, 60}));
    auto actions = drain(ha);
    assert(actions.size() == 4);
    for (std::uint32_t node = 0; node < 4; ++node)
        ha.accept(event(EK::ProbeResponse, pa, node, 60, {}, node == 1));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::FetchOwner);
    ha.accept(event(EK::OwnerData, pa, 1, 60, payload(0x1234)));
    assert(has(drain(ha), AK::PersistMemory, 60));
    ha.accept(event(EK::PersistenceComplete, pa, 1, 60));
    assert(has(drain(ha), AK::GrantRead, 60));
    ha.accept(event(EK::InstallAck, pa, 2, 60));
    actions = drain(ha);
    assert(has(actions, AK::Commit, 60) && has(actions, AK::Release, 60));
    assert(!ha.busy(pa));

    assert(ha.submitOverflow({pa + 64, 3, RK::Write, 61,
                              payload(0x5678)}));
    drain(ha);
    for (std::uint32_t node = 0; node < 4; ++node)
        ha.accept(event(EK::ProbeResponse, pa + 64, node, 61, {}, node < 2));
    actions = drain(ha);
    assert(has(actions, AK::Invalidate, 61));
    ha.accept(event(EK::InvalidateAck, pa + 64, 0, 61));
    ha.accept(event(EK::InvalidateAck, pa + 64, 1, 61));
    actions = drain(ha);
    assert(has(actions, AK::GrantWrite, 61));
    ha.accept(event(EK::InstallAck, pa + 64, 3, 61));
    assert(!ha.busy(pa + 64));
}

void testUnavailableRejectKeepsControllerLive()
{
    auto ha = makeController();
    const std::uint64_t pa = 0x1001c0;
    assert(ha.submit({pa, 1, RK::Read, 70}));
    assert(has(drain(ha), AK::FetchMemory, 70));
    ha.accept(event(EK::Unavailable, pa, 1, 70));
    auto actions = drain(ha);
    assert(has(actions, AK::Reject, 70));
    assert(!ha.busy(pa));
    assert(!ha.submit({pa, 2, RK::Read, 71}));
    assert(has(drain(ha), AK::Reject, 71));
}

void testMaskedWrites()
{
    auto ha = makeController();
    const std::uint64_t pa = 0x100200;
    HAController::Payload patch;
    patch.valid = true;
    patch.bytes[1] = 0xaa;
    patch.bytes[63] = 0xbb;
    const std::uint64_t mask = (std::uint64_t{1} << 1) |
                               (std::uint64_t{1} << 63);

    ha.directoryForTest().setSharers(pa, 1u << 2);
    assert(ha.submit({pa, 2, RK::Write, 80, patch, mask}));
    auto actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::FetchOwner &&
           actions[0].source == 2); // singleton participant is probed first
    auto base = patternedPayload();
    ha.accept(event(EK::OwnerData, pa, 2, 80, base));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::GrantWrite);
    auto finalLine = actions[0].data;
    assert(finalLine.bytes[1] == 0xaa && finalLine.bytes[63] == 0xbb);
    assert(finalLine.bytes[0] == base.bytes[0] && finalLine.bytes[62] == base.bytes[62]);
    assert(ha.directory().sharers(pa) == (1u << 2));
    ha.accept({EK::InstallAck, pa, 2, 80});
    drain(ha);
    assert(ha.directory().sharers(pa) == (1u << 2));

    const std::uint64_t memoryPa = pa + 64;
    ha.directoryForTest().setSharers(memoryPa, (1u << 0) | (1u << 1));
    assert(ha.submit({memoryPa, 3, RK::Write, 81, patch, mask}));
    actions = drain(ha);
    assert(has(actions, AK::FetchMemory, 81));
    assert(has(actions, AK::Invalidate, 81));

    const std::uint64_t remotePa = pa + 192;
    ha.directoryForTest().setSharers(remotePa, 1u << 1);
    assert(ha.submit({remotePa, 3, RK::Write, 84, patch, mask}));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::FetchOwner &&
           actions[0].source == 1);
    ha.accept(event(EK::OwnerData, remotePa, 1, 84, base));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::GrantWrite);

    const std::uint64_t cleanPa = pa + 256;
    ha.directoryForTest().setSharers(cleanPa, 1u << 1);
    assert(ha.submit({cleanPa, 3, RK::Write, 85, patch, mask}));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::FetchOwner);
    ha.accept(event(EK::OwnerNoData, cleanPa, 1, 85));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::FetchMemory);
    ha.accept(event(EK::RetryableBusy, cleanPa, 3, 85));
    assert(ha.busy(cleanPa) && drain(ha).empty());
    assert(ha.retryTransient(cleanPa, 85));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::FetchMemory);
    ha.accept(event(EK::OwnerData, cleanPa, 3, 85, base));
    actions = drain(ha);
    assert(actions.size() == 1 && actions[0].kind == AK::GrantWrite);

    const std::uint64_t busyPa = pa + 128;
    assert(ha.submit({busyPa, 0, RK::Read, 82}));
    drain(ha);
    ha.accept(event(EK::RetryableBusy, busyPa, 0, 82));
    assert(has(drain(ha), AK::Reject, 82));
    assert(!ha.busy(busyPa));
    assert(ha.submit({busyPa, 0, RK::Read, 83})); // line was not poisoned
    assert(has(drain(ha), AK::FetchMemory, 83));
}

} // namespace

int main()
{
    testBitmapBudgets();
    testSingletonAndMultiRead();
    testWriterBarrierAndAckQueueGating();
    testDifferentAddressConcurrencyAndBroadcast();
    testWritebackEvictAndPeerExit();
    testOverflowTransactionsAreTransient();
    testUnavailableRejectKeepsControllerLive();
    testMaskedWrites();
    std::cout << "ha_controller_reference_test: PASS\n";
    return 0;
}
