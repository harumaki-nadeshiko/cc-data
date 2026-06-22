#include "framework/PseudoManager.hh"

#include <cstdio>
#include <fstream>
#include <sstream>

namespace pseudo
{

PseudoMemPort*
PseudoManager::createPort(int port_id)
{
    auto it = _ports.find(port_id);
    if (it != _ports.end())
        return it->second.get();

    auto port = std::make_unique<PseudoMemPort>(port_id, this);
    PseudoMemPort* ptr = port.get();
    _ports[port_id] = std::move(port);
    _connections[port_id] = {};
    return ptr;
}

PseudoMemPort*
PseudoManager::getPort(int port_id)
{
    auto it = _ports.find(port_id);
    return (it != _ports.end()) ? it->second.get() : nullptr;
}

void
PseudoManager::connect(int port_a, int port_b)
{
    _connections[port_a].push_back(port_b);
    _connections[port_b].push_back(port_a);
}

void
PseudoManager::deliver(const PseudoMemPacket& pkt)
{
    auto it = _ports.find(pkt.dst_id);
    if (it == _ports.end()) {
        std::fprintf(stderr, "[PseudoManager] deliver: dst port %d not found\n",
                     pkt.dst_id);
        return;
    }
    it->second->enqueue(pkt);
}

void
PseudoManager::shutdown()
{
    for (auto& kv : _ports)
        kv.second->shutdown();
}

bool
PseudoManager::loadTopology(const std::string& json_path)
{
    std::ifstream f(json_path);
    if (!f.is_open()) {
        std::fprintf(stderr, "[PseudoManager] loadTopology: cannot open %s\n",
                     json_path.c_str());
        return false;
    }
    // Phase 4: parse JSON topology for NetworkSim
    // For now, stub: just report success
    std::string content((std::istreambuf_iterator<char>(f)),
                         std::istreambuf_iterator<char>());
    return !content.empty();
}

} // namespace pseudo
