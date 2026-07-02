#include "framework/Port.hh"
#include "framework/MemMessage.hh"

#include <cstdio>
#include <cstring>
#include <deque>
#include <map>
#include <set>
#include <vector>
#include <string>
#include <fstream>
#include <thread>
#include <sstream>

using namespace framework;

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
    // Per-source-module link latency (module -> outgoing link latency).
    std::map<int, uint64_t> _linkLatency;
    std::deque<PendingFwd> _fifo;
    uint64_t _tick = 0;
    bool _done = false;

public:
    NetworkSim(const std::string& topoPath)
    { loadTopology(topoPath); buildPorts(); buildRoutes(); }

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
    // Links are bidirectional; record the minimum link latency per source
    // module so a forwarded message gets a representative delay.
    for (auto& l : _links) {
        auto rec = [&](int mod, uint64_t lat) {
            auto it = _linkLatency.find(mod);
            if (it == _linkLatency.end() || lat < it->second) _linkLatency[mod] = lat;
        };
        rec(l.src_mod, l.latency);
        rec(l.dst_mod, l.latency);
    }
}

void NetworkSim::step() {
    // NOTE: no unconditional _tick++ here. Advancing the clock is decided in
    // run() strictly from safeTs, so nsim never drifts ahead of its peers.
    int totalRecv = 0, totalFwd = 0;
    for (auto& kv : _ports) {
        Port* p = kv.second.get();
        p->emitSync(_tick);
        while (MemMessage* m = p->recv(_tick)) {
            if (m->hdr.type == (uint32_t)MemMessageType::TERMINATE) { _done = true; return; }
            if (m->hdr.type == (uint32_t)MemMessageType::CONTROL_SYNC) continue;
            totalRecv++;

            uint64_t lat = 1;
            auto lit = _linkLatency.find((int)m->hdr.sourceId);
            if (lit != _linkLatency.end()) lat = lit->second;

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
            framework::TxHandle* fh = it->second->allocateSendBuffer(_tick);
            if (fh) {
                MemMessage* buf = fh->buffer();
                uint64_t ts = buf->hdr.timestamp;
                *buf = pf.msg;
                buf->hdr.timestamp = ts;
                fh->send();
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
        if (++stat_ct <= 30 || _fifo.size() > 10000)
            std::fprintf(stderr, "[NSIM-STAT] tick=%lu recv=%d fwd=%d fifo=%zu\n",
                         _tick, totalRecv, totalFwd, _fifo.size());
    }
}

void NetworkSim::run(int maxSteps) {
    int s = 0;
    while (!_done && (maxSteps < 0 || s < maxSteps)) {
        step();
        s++;

        uint64_t minTs = UINT64_MAX;
        for (auto& kv : _ports) {
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
    NetworkSim nsim(argv[1]);
    nsim.run();
    return 0;
}
