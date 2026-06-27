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

struct ForwardEntry {
    int next_hop_mod;
    int next_hop_port;
    uint64_t latency;
};

class NetworkSim {
public:
    NetworkSim(zmq::context_t& ctx, const std::string& topoPath);
    void step();
    void run(int maxSteps = -1);
    bool done() const { return _done; }

private:
    zmq::context_t& _ctx;
    std::map<int, std::unique_ptr<Port>> _ports;  // port_id → Port
    std::vector<Link> _links;
    bool _done = false;
    uint64_t _tick = 0;

    // Forwarding: (src_mod, src_port) → list of possible next hops
    std::map<std::pair<int,int>, std::vector<ForwardEntry>> _routes;

    struct PendingFwd {
        uint64_t readyTick;
        MemMessage msg;
        int dst_mod, dst_port;
    };
    std::deque<PendingFwd> _fifo;

    void loadTopology(const std::string& path);
    void buildRoutes();
    int findPortByModule(int modId, int portId) const;
};

NetworkSim::NetworkSim(zmq::context_t& ctx, const std::string& topoPath)
    : _ctx(ctx)
{
    loadTopology(topoPath);
    buildRoutes();

    // Create ports for all unique module/port endpoints appearing in links
    std::set<int> portKeys;
    for (auto& l : _links) {
        portKeys.insert(l.src_mod * 1000 + l.src_port);
        portKeys.insert(l.dst_mod * 1000 + l.dst_port);
    }
    for (int key : portKeys) {
        int mod = key / 1000;
        int portId = key % 1000;
        std::string ep = "ipc:///tmp/networksim_m" + std::to_string(mod)
                       + "_p" + std::to_string(portId);
        _ports[key] = std::make_unique<Port>(
            "nsim_p" + std::to_string(key), mod, portId, ep, true, _ctx, 1000);
    }
}

void NetworkSim::loadTopology(const std::string& path)
{
    std::ifstream f(path);
    if (!f.is_open()) { std::fprintf(stderr,"[NetworkSim] bad topo %s\n",path.c_str()); return; }
    std::string json((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    // Parse: "links": [[src_mod,src_port,dst_mod,dst_port,lat], ...]
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

void NetworkSim::buildRoutes()
{
    for (auto& l : _links) {
        _routes[{l.src_mod, l.src_port}].push_back({l.dst_mod, l.dst_port, l.latency});
        _routes[{l.dst_mod, l.dst_port}].push_back({l.src_mod, l.src_port, l.latency});
    }
}

int NetworkSim::findPortByModule(int modId, int portId) const
{
    return modId * 1000 + portId;
}

void NetworkSim::step()
{
    _tick++;

    // 1. Receive from all ports, enqueue into FIFO with latency
    for (auto& kv : _ports) {
        Port* p = kv.second.get();
        p->emitSync(_tick);
        static int rcv_ct = 0;
        while (MemMessage* m = p->recv(_tick)) {
            if (m->hdr.type == (uint32_t)MemMessageType::TERMINATE) { _done = true; return; }
            if (m->hdr.type == (uint32_t)MemMessageType::CONTROL_SYNC) continue;

            if (++rcv_ct <= 5)
                std::fprintf(stderr, "[NSIM-RECV] tick=%lu src=%u:%u dst=%u:%u type=%u sz=%u\n",
                             _tick, m->hdr.src_module, m->hdr.src_port,
                             m->hdr.dst_module, m->hdr.dst_port,
                             m->hdr.type, m->hdr.size);

            int targetKey = findPortByModule(m->hdr.dst_module, m->hdr.dst_port);
            auto rit = _routes.find({m->hdr.src_module, m->hdr.src_port});
            uint64_t lat = 1;
            if (rit != _routes.end() && !rit->second.empty()) lat = rit->second[0].latency;

            uint64_t readyTick = _tick + lat;
            PendingFwd pf{readyTick, *m, m->hdr.dst_module, m->hdr.dst_port};
            // Insert in FIFO order by readyTick
            auto ins = _fifo.begin();
            while (ins != _fifo.end() && ins->readyTick <= readyTick) ++ins;
            _fifo.insert(ins, pf);
        }
    }

    // 2. Forward ready packets from FIFO
    while (!_fifo.empty() && _fifo.front().readyTick <= _tick) {
        auto pf = _fifo.front(); _fifo.pop_front();
        int targetKey = findPortByModule(pf.dst_mod, pf.dst_port);
        auto it = _ports.find(targetKey);
        if (it != _ports.end()) {
            MemMessage* buf = it->second->sendAllocateBuffer(pf.msg.hdr.timestamp);
            if (buf) {
                *buf = pf.msg;
                static int fwd_ct = 0;
                if (++fwd_ct <= 5)
                    std::fprintf(stderr, "[NSIM-FWD] tick=%lu dst=%u:%u type=%u\n",
                                 _tick, pf.dst_mod, pf.dst_port, pf.msg.hdr.type);
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
}

void NetworkSim::run(int maxSteps)
{
    int s = 0;
    while (!_done && (maxSteps < 0 || s < maxSteps)) { step(); s++; }
    std::printf("[NetworkSim] done after %d steps\n", s);
}

int main(int argc, char** argv)
{
    if (argc < 2) { std::fprintf(stderr,"usage: networksim <topology.json>\n"); return 1; }
    zmq::context_t ctx(1);
    NetworkSim nsim(ctx, argv[1]);
    nsim.run();
    return 0;
}
