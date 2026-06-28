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
#include <sstream>
#include <zmq.hpp>

using namespace framework;

struct Link {
    int src_mod, src_port, dst_mod, dst_port;
    uint64_t latency;
};

struct PendingFwd {
    uint64_t readyTick;
    MemMessage msg;
    int dst_mod;
    int dst_port;
};

class NetworkSim {
    zmq::context_t& _ctx;
    std::vector<Link> _links;
    std::map<int, std::unique_ptr<Port>> _ports;
    std::map<std::pair<int,int>, std::vector<Link>> _routes;
    std::deque<PendingFwd> _fifo;
    uint64_t _tick = 0;
    bool _done = false;

public:
    NetworkSim(zmq::context_t& ctx, const std::string& topoPath)
        : _ctx(ctx) { loadTopology(topoPath); buildPorts(); buildRoutes(); }

    void loadTopology(const std::string& path);
    void buildPorts();
    void buildRoutes();
    int findPortByModule(int modId, int portId) const;
    void step();
    void run(int maxSteps = -1);
};

void NetworkSim::buildPorts() {
    std::set<int> portKeys;
    for (auto& l : _links) {
        portKeys.insert(l.src_mod * 1000 + l.src_port);
        portKeys.insert(l.dst_mod * 1000 + l.dst_port);
    }
    for (int key : portKeys) {
        int mod = key / 1000;
        int portId = key % 1000;
        std::string base = "/workspace/gem5/shared_ipc/ipc";
        std::string rx = base + "_ubio_" + std::to_string(mod) + "_to_networksim_m" + std::to_string(mod);
        std::string tx = base + "_networksim_m" + std::to_string(mod) + "_to_ubio_" + std::to_string(mod);
        _ports[key] = std::make_unique<Port>(
            "nsim_p" + std::to_string(key), mod, portId,
            "ipc://" + rx, "ipc://" + tx, _ctx);
    }
}

void NetworkSim::loadTopology(const std::string& path) {
    std::ifstream f(path);
    if (!f.is_open()) { std::fprintf(stderr,"[NetworkSim] bad topo %s\n",path.c_str()); return; }
    std::string json((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    size_t pos = json.find("\"links\"");
    if (pos == std::string::npos) return;
    pos = json.find('[', pos);
    size_t end = json.find(']', pos);
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
    for (auto& l : _links) {
        _routes[{l.src_mod, l.src_port}].push_back(Link{0, 0, l.dst_mod, l.dst_port, l.latency});
        _routes[{l.dst_mod, l.dst_port}].push_back(Link{0, 0, l.src_mod, l.src_port, l.latency});
    }
}

int NetworkSim::findPortByModule(int modId, int portId) const {
    return modId * 1000 + portId;
}

void NetworkSim::step() {
    _tick++;

    int totalRecv = 0, totalFwd = 0;
    for (auto& kv : _ports) {
        Port* p = kv.second.get();
        p->emitSync(_tick);
        while (MemMessage* m = p->recv(_tick)) {
            if (m->hdr.type == (uint32_t)MemMessageType::TERMINATE) { _done = true; return; }
            if (m->hdr.type == (uint32_t)MemMessageType::CONTROL_SYNC) continue;
            totalRecv++;

            int targetKey = findPortByModule(m->hdr.dst_module, m->hdr.dst_port);
            auto rit = _routes.find({m->hdr.src_module, m->hdr.src_port});
            uint64_t lat = 1;
            if (rit != _routes.end() && !rit->second.empty()) lat = rit->second[0].latency;

            uint64_t readyTick = _tick + lat;
            PendingFwd pf{readyTick, *m, m->hdr.dst_module, m->hdr.dst_port};
            auto ins = _fifo.begin();
            while (ins != _fifo.end() && ins->readyTick <= readyTick) ++ins;
            _fifo.insert(ins, pf);
        }
    }

    while (!_fifo.empty() && _fifo.front().readyTick <= _tick) {
        auto pf = _fifo.front(); _fifo.pop_front();
        totalFwd++;
        int targetKey = findPortByModule(pf.dst_mod, pf.dst_port);
        auto it = _ports.find(targetKey);
        if (it != _ports.end()) {
            MemMessage* buf = it->second->sendAllocateBuffer(_tick);
            if (buf) {
                uint64_t ts = buf->hdr.timestamp;
                *buf = pf.msg;
                buf->hdr.timestamp = ts;
                it->second->send(buf);
            } else {
                static int no_ct = 0;
                if (++no_ct <= 3)
                    std::fprintf(stderr, "[NSIM-NOBUF] tick=%lu dst=%u:%u\n",
                                 _tick, pf.dst_mod, pf.dst_port);
            }
        } else {
            static int miss_ct = 0;
            if (++miss_ct <= 3)
                std::fprintf(stderr, "[NSIM-MISS] tick=%lu dst=%u:%u (no port)\n",
                             _tick, pf.dst_mod, pf.dst_port);
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
        }
    }
    std::printf("[NetworkSim] done after %d steps\n", s);
}

int main(int argc, char** argv) {
    if (argc < 2) { std::fprintf(stderr,"usage: networksim <topology.json>\n"); return 1; }
    zmq::context_t ctx(1);
    NetworkSim nsim(ctx, argv[1]);
    nsim.run();
    return 0;
}
