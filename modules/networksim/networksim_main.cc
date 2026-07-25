#include "framework/Port.hh"
#include "framework/MemMessage.hh"
#include "framework/TracePerfPolicy.hh"

#include <cstdio>
#include <csignal>
#include <cstring>
#include <deque>
#include <map>
#include <set>
#include <utility>
#include <vector>
#include <string>
#include <fstream>
#include <thread>
#include <sstream>
#include <cstdlib>

using namespace framework;

namespace {
volatile std::sig_atomic_t g_shutdownRequested = 0;

void
requestShutdown(int)
{
    g_shutdownRequested = 1;
}
} // anonymous namespace

struct Link {
    int src_mod, src_port, dst_mod, dst_port;
    uint64_t latency;
};

struct PendingFwd {
    uint64_t readyTick;
    MemMessage msg;
    int dst_mod;
};

class NetworkSim {
    std::vector<Link> _links;
    // Keyed by module ID only. Each module has exactly one IPC channel to nsim;
    // topology port IDs are a link-latency attribute, not a routing selector.
    std::map<int, std::unique_ptr<Port>> _ports;
    // Per-(src,dst) link latency (ps). Bidirectional: both (a,b) and (b,a)
    // are stored.  TODO(2-hop): cross-node+cross-socket currently single-hop
    // heterogeneous delay. Revert to multi-hop when nsim supports it.
    std::map<std::pair<int,int>, uint64_t> _linkLatency;
    std::deque<PendingFwd> _fifo;
    uint64_t _tick = 0;
    std::set<int> _donePorts;
    size_t _maxPendingFwd = 65536;

public:
    NetworkSim(const std::string& topoPath)
    {
        if (const char* env = std::getenv("EP_NSIM_MAX_PENDING")) {
            const long requested = std::strtol(env, nullptr, 10);
            if (requested > 0 && requested <= 1048576)
                _maxPendingFwd = static_cast<size_t>(requested);
        }
        loadTopology(topoPath); buildPorts(); buildRoutes();
    }

    void loadTopology(const std::string& path);
    void buildPorts();
    void buildRoutes();
    void step();
    void run(int maxSteps = -1);
};

void NetworkSim::buildPorts() {
    std::set<int> mods;
    for (auto& l : _links) {
        mods.insert(l.src_mod);
        mods.insert(l.dst_mod);
    }
    for (int mod : mods) {
        framework::PortParams pp = framework::PortEnvLoader::nsimUbioPort(mod);
        auto p = std::make_unique<Port>();
        if (!p->init(pp)) {
            std::fprintf(stderr, "[NetworkSim] port init failed mod=%d\n", mod);
            return;
        }
        _ports[mod] = std::move(p);
    }
}

void NetworkSim::loadTopology(const std::string& path) {
    std::ifstream f(path);
    if (!f.is_open()) { std::fprintf(stderr,"[NetworkSim] bad topo %s\n",path.c_str()); return; }
    std::string json((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    size_t pos = json.find("\"links\"");
    if (pos == std::string::npos) return;
    pos = json.find('[', pos);          // opening '[' of the links array
    size_t end = json.rfind(']');       // closing ']' of the links array (NOT
                                        // the first inner link's ']' — that bug
                                        // dropped every link after the first,
                                        // so mod2 never got a port/route).
    std::string arr = json.substr(pos+1, end-pos-1);
    std::istringstream iss(arr);
    std::string triple;
    while (std::getline(iss, triple, ']')) {
        size_t b = triple.find('[');
        if (b == std::string::npos) continue;
        std::string nums = triple.substr(b+1);
        Link l;
        int n = std::sscanf(nums.c_str(), "%d,%d,%d,%d,%lu",
                            &l.src_mod, &l.src_port, &l.dst_mod, &l.dst_port, &l.latency);
        if (n >= 4) { if (n < 5) l.latency = 1; _links.push_back(l); }
    }
    std::printf("[NetworkSim] loaded %zu links\n", _links.size());
}

void NetworkSim::buildRoutes() {
    _linkLatency.clear();
    for (auto& l : _links) {
        _linkLatency[{l.src_mod, l.dst_mod}] = l.latency;
        _linkLatency[{l.dst_mod, l.src_mod}] = l.latency;
    }
}

void NetworkSim::step() {
    int totalRecv = 0, totalFwd = 0;
    for (auto& kv : _ports) {
        int mod = kv.first;
        if (_donePorts.count(mod)) continue;
        Port* p = kv.second.get();
        p->emitSync(_tick);
        // Stop receiving before the bounded FIFO is full. The bounded Port
        // HWM then backpressures the source rather than allocating forever.
        while (_fifo.size() < _maxPendingFwd) {
            MemMessage* m = p->recv(_tick);
            if (!m)
                break;
            if (m->hdr.type == (uint32_t)MemMessageType::TERMINATE) { _donePorts.insert(mod); break; }
            if (m->hdr.type == (uint32_t)MemMessageType::CONTROL_SYNC) continue;
            totalRecv++;
            if (TracePerfPolicy::get().shouldEmit("nsim")) {
                std::fprintf(stderr, "[TRACE-PERF] %lu|%d|nsim|%lu|0x0|RECV|src=%u dst=%u\n",
                             m->hdr.timestamp, mod, m->hdr.req_id, m->hdr.sourceId, m->hdr.targetId);
            }

            uint64_t lat = 1;
            auto lit = _linkLatency.find({(int)m->hdr.sourceId,
                                           (int)m->hdr.targetId});
            if (lit != _linkLatency.end()) lat = lit->second;
            else
                std::fprintf(stderr, "[NSIM-NOROUTE] src=%u dst=%u falling back to 1ps\n",
                             m->hdr.sourceId, m->hdr.targetId);

            uint64_t readyTick = _tick + lat;
            PendingFwd pf{readyTick, *m, m->hdr.targetId};
            auto ins = _fifo.begin();
            while (ins != _fifo.end() && ins->readyTick <= readyTick) ++ins;
            _fifo.insert(ins, pf);
        }
    }

    while (!_fifo.empty() && _fifo.front().readyTick <= _tick) {
        auto pf = _fifo.front(); _fifo.pop_front();
        totalFwd++;
        auto it = _ports.find(pf.dst_mod);
        if (it != _ports.end()) {
            framework::MemMessage* buf = it->second->allocateSendBuffer(_tick);
            if (buf) {
                uint64_t ts = buf->hdr.timestamp;
                *buf = pf.msg;
                buf->hdr.timestamp = ts;
                it->second->send(buf);
                if (TracePerfPolicy::get().shouldEmit("nsim")) {
                    std::fprintf(stderr, "[TRACE-PERF] %lu|%d|nsim|%lu|0x0|FWD|dst=%u\n",
                                 pf.readyTick, pf.dst_mod, pf.msg.hdr.req_id, pf.dst_mod);
                }
            } else {
                static int no_ct = 0;
                if (++no_ct <= 3)
                    std::fprintf(stderr, "[NSIM-NOBUF] tick=%lu dst=%u:%u\n",
                                 _tick, pf.dst_mod, 0);
            }
        } else {
            static int miss_ct = 0;
            if (++miss_ct <= 3)
                std::fprintf(stderr, "[NSIM-MISS] tick=%lu dst=%u:%u (no port)\n",
                             _tick, pf.dst_mod, 0);
        }
    }

    if (totalRecv > 0 || totalFwd > 0 || _fifo.size() > 500) {
        static int stat_ct = 0;
        if (++stat_ct <= 30 || _fifo.size() > (_maxPendingFwd * 3) / 4)
            std::fprintf(stderr, "[NSIM-STAT] tick=%lu recv=%d fwd=%d fifo=%zu\n",
                          _tick, totalRecv, totalFwd, _fifo.size());
    }
}

void NetworkSim::run(int maxSteps) {
    int s = 0;
    while (!g_shutdownRequested && _donePorts.size() < _ports.size() &&
           (maxSteps < 0 || s < maxSteps)) {
        step();
        s++;

        uint64_t minTs = UINT64_MAX;
        for (auto& kv : _ports) {
            if (_donePorts.count(kv.first)) continue;
            uint64_t b = kv.second->safeTs(_tick);
            if (b < minTs) minTs = b;
        }
        if (minTs > _tick) {
            _tick = minTs;
        } else {
            // Bounded by a peer: wait instead of drifting forward, so nsim stays
            // clock-locked to the slowest peer (no ++tick skew).
            std::this_thread::yield();
        }
    }
    std::printf("[NetworkSim] done after %d steps\n", s);
}

int main(int argc, char** argv) {
    if (argc < 2) { std::fprintf(stderr,"usage: networksim <topology.json>\n"); return 1; }
    std::signal(SIGTERM, requestShutdown);
    std::signal(SIGINT, requestShutdown);
    NetworkSim nsim(argv[1]);
    nsim.run();
    return 0;
}
