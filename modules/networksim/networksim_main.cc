#include "framework/iface/Message.hh"
#include "framework/iface/Port.hh"
#include "framework/iface/Log.hh"
#include "protocol/TracePerfPolicy.hh"

#include <algorithm>
#include <cstdio>
#include <signal.h>
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
#include <stdexcept>

using namespace framework;

namespace {
volatile sig_atomic_t g_shutdownRequested = 0;

void
requestShutdown(int)
{
    g_shutdownRequested = 1;
    // The runner requests this only after every gem5 and UBIO child has
    // completed successfully. networksim has no persistent state to drain,
    // and a peer-disconnect can leave a ZeroMQ send uninterruptibly blocked.
    _Exit(0);
}
} // anonymous namespace

struct Link {
    int src_mod, src_port, dst_mod, dst_port;
    uint64_t latency;
};

struct PendingFwd {
    uint64_t readyTick;
    Message *msg;
    int dst_mod;
};

class NetworkSim {
    std::vector<Link> _links;
    // Keyed by module ID only. Each module has exactly one IPC channel to nsim;
    // topology port IDs are a link-latency attribute, not a routing selector.
    std::map<int, Port *> _ports;
    // Per-(src,dst) link latency (ps). Bidirectional: both (a,b) and (b,a)
    // are stored.  TODO(2-hop): cross-node+cross-socket currently single-hop
    // heterogeneous delay. Revert to multi-hop when nsim supports it.
    std::map<std::pair<int,int>, uint64_t> _linkLatency;
    std::deque<PendingFwd> _fifo;
    uint64_t _tick = 0;
    std::set<int> _donePorts;
    size_t _maxPendingFwd = 65536;

public:
    NetworkSim(const std::string& topoPath, int numNodes, int numSockets)
        : _numNodes(numNodes), _numSockets(numSockets)
    {
        if (const char* env = std::getenv("EP_NSIM_MAX_PENDING")) {
            const long requested = std::strtol(env, nullptr, 10);
            if (requested > 0 && requested <= 1048576)
                _maxPendingFwd = static_cast<size_t>(requested);
        }
        loadTopology(topoPath);
        if (requiredModules(_links) > _numNodes * _numSockets)
            throw std::runtime_error("networksim topology exceeds configured dimensions");
        buildRoutes(); buildPorts();
        if (_ports.empty())
            throw std::runtime_error("networksim created no transport ports");
    }

    ~NetworkSim();

    void loadTopology(const std::string& path);
    void buildPorts();
    void buildRoutes();
    void step();
    void run(int maxSteps = -1);

private:
    static int requiredModules(const std::vector<Link> &links);
    int _numNodes;
    int _numSockets;
};

int NetworkSim::requiredModules(const std::vector<Link> &links)
{
    int required = 0;
    for (const Link &link : links)
        required = std::max(required, std::max(link.src_mod, link.dst_mod) + 1);
    return required;
}

NetworkSim::~NetworkSim()
{
    for (auto &pending : _fifo)
        ReleaseMessage(pending.msg);
    _fifo.clear();
    for (auto &kv : _ports) {
        TerminatePort(kv.second);
        DestroyPort(kv.second);
    }
}

void NetworkSim::buildPorts() {
    const auto moduleId = [this](int node, int socket) {
        return node * _numSockets + socket;
    };
    std::map<int, Port *> ports;
    for (int node = 0; node < _numNodes; ++node) {
        for (int socket = 0; socket < _numSockets; ++socket) {
            const int mod = moduleId(node, socket);
            PortConfig config;
            config.selfRole = "networksim";
            config.peerRole = "ubio";
            config.channelName = "network";
            config.nodeId = node;
            config.socketId = socket;
            config.numNodes = _numNodes;
            config.numSockets = _numSockets;
            Port *p = CreatePort(config);
            if (!p) {
                LogError("NetworkSim", "[NetworkSim] port init failed mod={}", mod);
                for (auto &kv : ports) {
                    TerminatePort(kv.second);
                    DestroyPort(kv.second);
                }
                throw std::runtime_error(
                    "networksim failed to create all configured transport ports");
            }
            ports[mod] = p;
        }
    }
    _ports.swap(ports);
}

void NetworkSim::loadTopology(const std::string& path) {
    std::ifstream f(path);
    if (!f.is_open())
        throw std::runtime_error("networksim cannot open topology file");
    std::string json((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    const auto topologyDimension = [&json](const char *name) {
        const std::string key = std::string("\"") + name + "\"";
        size_t keyPos = json.find(key);
        if (keyPos == std::string::npos)
            throw std::runtime_error("networksim topology is missing dimensions");
        size_t colon = json.find(':', keyPos + key.size());
        if (colon == std::string::npos)
            throw std::runtime_error("networksim topology dimension is malformed");
        char *end = nullptr;
        const long value = std::strtol(json.c_str() + colon + 1, &end, 10);
        if (end == json.c_str() + colon + 1 || value <= 0)
            throw std::runtime_error("networksim topology dimension is invalid");
        return value;
    };
    if (topologyDimension("num_nodes") != _numNodes ||
        topologyDimension("num_sockets") != _numSockets)
        throw std::runtime_error("networksim topology dimensions do not match runtime");
    size_t pos = json.find("\"links\"");
    if (pos == std::string::npos)
        throw std::runtime_error("networksim topology has no links array");
    pos = json.find('[', pos);          // opening '[' of the links array
    if (pos == std::string::npos)
        throw std::runtime_error("networksim topology links array is malformed");
    size_t end = pos;
    int depth = 0;
    for (; end < json.size(); ++end) {
        if (json[end] == '[') ++depth;
        if (json[end] == ']' && --depth == 0) break;
    }
    if (end >= json.size() || depth != 0)
        throw std::runtime_error("networksim topology links array is malformed");
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
        if (n != 5)
            throw std::runtime_error("networksim topology link is malformed");
        _links.push_back(l);
    }
    LogInfo("NetworkSim", "[NetworkSim] loaded {} links", _links.size());
}

void NetworkSim::buildRoutes() {
    _linkLatency.clear();
    const int modules = _numNodes * _numSockets;
    for (const auto& l : _links) {
        if (l.src_mod < 0 || l.src_mod >= modules ||
            l.dst_mod < 0 || l.dst_mod >= modules ||
            l.src_mod == l.dst_mod || l.latency == 0)
            throw std::runtime_error("networksim topology contains an invalid link");
        if (_linkLatency.count({l.src_mod, l.dst_mod}) ||
            _linkLatency.count({l.dst_mod, l.src_mod}))
            throw std::runtime_error("networksim topology contains a duplicate link");
        _linkLatency[{l.src_mod, l.dst_mod}] = l.latency;
        _linkLatency[{l.dst_mod, l.src_mod}] = l.latency;
    }
    const size_t expectedRoutes = static_cast<size_t>(modules) * (modules - 1);
    if (_linkLatency.size() != expectedRoutes)
        throw std::runtime_error("networksim topology is not a complete full mesh");
}

void NetworkSim::step() {
    int totalRecv = 0, totalFwdAttempted = 0, totalFwdSuccessful = 0;
    for (auto& kv : _ports) {
        int mod = kv.first;
        if (_donePorts.count(mod)) continue;
        Port *p = kv.second;
        EmitSync(p, _tick);
        // Stop receiving before the bounded FIFO is full. The bounded Port
        // HWM then backpressures the source rather than allocating forever.
        while (_fifo.size() < _maxPendingFwd) {
            ReceiveStatus status;
            const Message *m = ReceiveMessage(p, _tick, &status);
            if (status != ReceiveStatus::Message || !m)
                break;
            if (GetMessageType(m) == MessageType::Terminate) {
                _donePorts.insert(mod);
                break;
            }
            if (GetMessageType(m) == MessageType::ControlSync) continue;
            totalRecv++;
            const uint64_t timestamp = GetMessageTimestamp(m);
            const uint64_t requestId = GetMessageRequestId(m);
            const uint32_t sourceId = GetMessageSourceId(m);
            const uint32_t targetId = GetMessageTargetId(m);
            if (TracePerfPolicy::get().shouldEmit("nsim")) {
                LogInfo("NetworkSim", "[TRACE-PERF] {}|{}|nsim|{}|0x0|RECV|src={} dst={}",
                        timestamp, mod, requestId, sourceId, targetId);
            }

            auto lit = _linkLatency.find({static_cast<int>(sourceId),
                                           static_cast<int>(targetId)});
            if (lit == _linkLatency.end())
                throw std::runtime_error("networksim received a message with no route");
            const uint64_t lat = lit->second;

            uint64_t readyTick = _tick + lat;
            auto destination = _ports.find(static_cast<int>(targetId));
            if (destination == _ports.end()) {
                static int miss_ct = 0;
                if (++miss_ct <= 3)
                    LogWarn("NetworkSim", "[NSIM-MISS] tick={} dst={}:{} (no port)",
                            _tick, targetId, 0);
                continue;
            }
            Message *owned = AllocateSendMessage(destination->second, readyTick);
            if (!owned) {
                static int no_ct = 0;
                if (++no_ct <= 3)
                    LogWarn("NetworkSim", "[NSIM-NOBUF] tick={} dst={}:{}",
                            _tick, targetId, 0);
                continue;
            }
            CopyMessage(owned, m);
            PendingFwd pf{readyTick, owned, static_cast<int>(targetId)};
            auto ins = _fifo.begin();
            while (ins != _fifo.end() && ins->readyTick <= readyTick) ++ins;
            _fifo.insert(ins, pf);
        }
    }

    while (!_fifo.empty() && _fifo.front().readyTick <= _tick) {
        PendingFwd pf = _fifo.front(); _fifo.pop_front();
        totalFwdAttempted++;
        auto it = _ports.find(pf.dst_mod);
        if (it != _ports.end() && !_donePorts.count(pf.dst_mod)) {
            const uint64_t requestId = GetMessageRequestId(pf.msg);
            const bool sent = SendMessage(it->second, pf.msg);
            // SendMessage consumes pf.msg even on failure; never reuse it.
            if (sent) {
                totalFwdSuccessful++;
            } else {
                LogError("NetworkSim",
                         "[NSIM-FWD-FAIL] tick={} dst={} requestId={}",
                         _tick, pf.dst_mod, requestId);
            }
            if (sent && TracePerfPolicy::get().shouldEmit("nsim")) {
                LogInfo("NetworkSim", "[TRACE-PERF] {}|{}|nsim|{}|0x0|FWD|dst={}",
                        pf.readyTick, pf.dst_mod, requestId, pf.dst_mod);
            }
        } else {
            ReleaseMessage(pf.msg);
            static int miss_ct = 0;
            if (it == _ports.end() && ++miss_ct <= 3)
                LogWarn("NetworkSim", "[NSIM-MISS] tick={} dst={}:{} (no port)",
                        _tick, pf.dst_mod, 0);
        }
    }

    if (totalRecv > 0 || totalFwdAttempted > 0 || _fifo.size() > 500) {
        static int stat_ct = 0;
        if (++stat_ct <= 30 || _fifo.size() > (_maxPendingFwd * 3) / 4)
            LogDebug("NetworkSim",
                     "[NSIM-STAT] tick={} recv={} fwd_attempted={} fwd_successful={} fifo={}",
                     _tick, totalRecv, totalFwdAttempted, totalFwdSuccessful,
                     _fifo.size());
    }
}

void NetworkSim::run(int maxSteps) {
    int s = 0;
    while (!g_shutdownRequested &&
           (_donePorts.size() < _ports.size() || !_fifo.empty()) &&
           (maxSteps < 0 || s < maxSteps)) {
        step();
        s++;

        uint64_t minTs = UINT64_MAX;
        for (auto& kv : _ports) {
            if (_donePorts.count(kv.first)) continue;
            uint64_t b = SafeTimestamp(kv.second, _tick);
            if (b < minTs) minTs = b;
        }
        if (minTs == UINT64_MAX && !_fifo.empty())
            minTs = _fifo.front().readyTick;
        if (minTs > _tick) {
            _tick = minTs;
        } else {
            // Bounded by a peer: wait instead of drifting forward, so nsim stays
            // clock-locked to the slowest peer (no ++tick skew).
            std::this_thread::yield();
        }
    }
    LogInfo("NetworkSim", "[NetworkSim] done after {} steps", s);
}

int main(int argc, char** argv) {
    if (argc < 2) {
        LogError("NetworkSim", "usage: networksim <topology.json> [num_nodes] [num_sockets]");
        return 1;
    }
    const char *nodesEnv = std::getenv("NUM_NODES");
    const char *socketsEnv = std::getenv("NUM_SOCKETS");
    const int numNodes = argc > 2 ? std::atoi(argv[2])
        : (nodesEnv ? std::atoi(nodesEnv) : 3);
    const int numSockets = argc > 3 ? std::atoi(argv[3])
        : (socketsEnv ? std::atoi(socketsEnv) : 1);
    if (numNodes <= 0 || numSockets <= 0) {
        LogError("NetworkSim", "networksim topology dimensions must be positive");
        return 1;
    }
    struct sigaction shutdownAction {};
    shutdownAction.sa_handler = requestShutdown;
    sigemptyset(&shutdownAction.sa_mask);
    // Do not restart a blocked ZeroMQ send after shutdown is requested.
    sigaction(SIGTERM, &shutdownAction, nullptr);
    sigaction(SIGINT, &shutdownAction, nullptr);
    try {
        NetworkSim nsim(argv[1], numNodes, numSockets);
        nsim.run();
    } catch (const std::exception &error) {
        LogError("NetworkSim", "[NetworkSim] startup failed: {}", error.what());
        return 1;
    }
    return 0;
}
