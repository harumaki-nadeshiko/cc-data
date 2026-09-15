#include "../gem5/src/mem/ruby/protocol/chi/ep/BoundaryResponses.hh"
#include <cassert>
#include <cstdint>
#include <iostream>

struct Key {
    unsigned respType;
    uint64_t reqId;
    bool operator==(const Key &o) const {
        return respType == o.respType && reqId == o.reqId;
    }
};
struct Message {
    struct Header {
        uint64_t homeLinePa = 0, epoch = 0;
        unsigned srcNode = 0, srcSocket = 0, dstNode = 1, dstSocket = 0;
    } h;
    uint64_t data = 0;
};
int main() {
    gem5::ruby::BoundaryResponses<Key, Message, 128> bank;
    for (unsigned i = 0; i < 64; ++i) {
        Message m; m.h.homeLinePa = i * 64;
        assert(bank.reserve({0, i + 1}, m, 64));
    }
    Message request;
    assert(!bank.reserve({0, 65}, request, 64));
    assert(bank.reserve({1, 65}, request, 64));
    Message response;
    response.h.srcNode = 1; response.h.dstNode = 0;
    response.data = 0x123456789abcdef0ULL;
    assert(bank.deliver({0, 1}, response));
    assert(bank.size() == 65);
    assert(bank.find({0, 1})->second.data == response.data);
    response.data = 0;
    assert(bank.deliver({0, 1}, response));
    assert(bank.find({0, 1})->second.data == 0x123456789abcdef0ULL);
    response.h.srcNode = 2;
    assert(!bank.deliver({0, 1}, response));
    bank.erase(bank.find({0, 1}));
    assert(!bank.deliver({0, 1}, response));
    assert(bank.reserve({0, 65}, request, 64));
    std::cout << "PASS reserved response at full ordinary admission; duplicate and route isolation\n";
}
