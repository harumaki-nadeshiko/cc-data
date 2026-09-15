// Exercise the actual production MetaRNFClient producer without a fake ACK.
#define main ubio_program_main
#include "../modules/ubiomodule/ubio_main.cc"
#undef main
#include <cassert>
int main() {
    uint64_t tick = 0;
    MetaRNFClient client(tick);
    client.init(nullptr, 0, 0);
    unsigned completed = 0;
    for (unsigned i = 0; i < 64; ++i)
        assert(client.readPage(i, [&](const uint8_t *) { ++completed; }));
    assert(client.pageFree() == 0);
    assert(!client.readPage(64, [&](const uint8_t *) { ++completed; }));
    assert(completed == 0);
    // Empty transport cannot consume the retained descriptor.
    // Its sent bit stays false; no callback is fabricated on admission Busy.
    for (const auto &slot : client.pageFlights) assert(slot.live && !slot.sent);
    CoherenceMessage response;
    response.h.type = CoherenceMessageType::MetaRNFReadResp;
    response.h.reqId = client.pageFlights[0].message.h.reqId;
    response.b.metaRNF = UBMetaRNFBody{};
    client.handleResp(response);
    assert(completed == 1 && client.pageFree() == 1);
    assert(client.readPage(64, [&](const uint8_t *) { ++completed; }));
    assert(client.pageFree() == 0);
    client.handleResp(response);
    assert(completed == 1 && client.pageFree() == 0);
    std::cout << "PASS production legacy page64 reservation, Busy retention, duplicate response\n";
}
