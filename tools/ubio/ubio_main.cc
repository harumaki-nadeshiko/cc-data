#include "framework/Port.hh"
#include "framework/MemMessage.hh"
#include "modules/ubiomodule/CoherenceMessage.hh"
#include "modules/ubiomodule/gem5_shim.hh"

#include <cstdio>
#include <cstring>
#include <fstream>
#include <sstream>
#include <memory>
#include <vector>
#include <thread>
#include <chrono>
#include <zmq.hpp>

using namespace framework;
using namespace gem5::ruby;

static bool _done = false;

int main(int argc, char** argv)
{
    if (argc < 2) { std::fprintf(stderr,"usage: ubio_main <config.json>\n"); return 1; }

    // Load config
    std::ifstream f(argv[1]);
    if (!f.is_open()) { std::fprintf(stderr,"bad config\n"); return 1; }
    std::string json((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    std::fprintf(stderr,"[ubio] config loaded, %zu bytes\n", json.size());

    // Parse port
    char endpoint[256] = {};
    int bind_val = 0;
    size_t ep_pos = json.find("\"endpoint\"");
    if (ep_pos != std::string::npos) {
        size_t q1 = json.find('"', ep_pos + 12);
        size_t q2 = json.find('"', q1 + 1);
        if (q2 != std::string::npos)
            std::strncpy(endpoint, json.substr(q1+1, q2-q1-1).c_str(), 255);
    }
    size_t bpos = json.find("\"bind\"");
    if (bpos != std::string::npos) {
        // find bool value
        if (json.find("true", bpos) < json.find("}", bpos)) bind_val = 1;
    }
    std::fprintf(stderr,"[ubio] endpoint=%s bind=%d\n", endpoint, bind_val);

    zmq::context_t ctx(1);
    Port port("ubio", 20, 0, endpoint, bind_val != 0, ctx, 100000);
    std::fprintf(stderr,"[ubio] port created, entering loop\n");

    uint64_t tick = 0;
    while (!_done) {
        port.emitSync(tick);
        MemMessage* msg = port.recv(tick);
        if (msg) {
            std::fprintf(stderr,"[ubio] tick=%lu recv type=%d req_id=%lu\n",
                         tick, msg->hdr.type, msg->hdr.req_id);
            if (msg->hdr.type == (uint32_t)MemMessageType::TERMINATE) {
                _done = true; break;
            }
            if (msg->hdr.type == (uint32_t)MemMessageType::COH_MSG) {
                const CoherenceMessage* coh = msg->getPayload<CoherenceMessage>();
                if (coh) {
                    MemMessage* buf = port.sendAllocateBuffer(tick);
                    if (buf) {
                        buf->hdr.type = (uint32_t)MemMessageType::COH_MSG;
                        buf->hdr.dst_module = msg->hdr.src_module;
                        buf->hdr.dst_port = msg->hdr.src_port;
                        buf->hdr.req_id = coh->h.reqId;
                        CoherenceMessage resp;
                        resp.h.type = CoherenceMessageType::ReadResp;
                        resp.h.epoch = coh->h.epoch;
                        resp.h.reqId = coh->h.reqId;
                        resp.b.readResp.grantType = 2;
                        buf->setPayload(resp);
                        port.send(buf);
                        std::fprintf(stderr,"[ubio] sent ReadResp\n");
                    }
                }
            }
        }
        tick++;
        if (tick % 1000 == 0) std::this_thread::sleep_for(std::chrono::milliseconds(1));
    }
    std::fprintf(stderr,"[ubio] done\n");
    return 0;
}
