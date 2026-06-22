#include "modules/networksim/NetworkSim.hh"
#include "framework/PseudoManager.hh"

#include <cstdio>
#include <chrono>
#include <thread>

namespace pseudo
{

NetworkSim::NetworkSim(PseudoManager* mgr)
    : _manager(mgr), _should_stop(false), _step_count(0)
{
}

PseudoMemPort*
NetworkSim::addPort(int port_id)
{
    if (!_manager) return nullptr;
    _port_ids.push_back(port_id);
    return _manager->createPort(port_id);
}

bool
NetworkSim::configure(const std::string& topology_path)
{
    return _forward.loadJson(topology_path);
}

void
NetworkSim::configureFullMesh(const std::vector<int>& port_ids, int latency)
{
    _forward.buildFullMesh(port_ids, latency);
}

void
NetworkSim::step()
{
    _step_count++;

    // Collect all pending packets from all ports
    for (int pid : _port_ids) {
        auto* port = _manager->getPort(pid);
        if (!port) continue;

        while (port->poll()) {
            PseudoMemPacket pkt;
            if (!port->recv(pkt, 0))
                break;

            // Forward to destination
            // Phase 1: direct delivery (full mesh)
            pkt.src_id = pid;  // update source to reflect this hop
            // In a real multi-hop network, we'd compute next-hop here
            // For full mesh, just deliver directly to dst
            PseudoMemPort* dst_port = _manager->getPort(pkt.dst_id);
            if (dst_port) {
                dst_port->enqueue(pkt);
            } else {
                std::fprintf(stderr, "[NetworkSim] step %d: no route to dst=%d\n",
                             _step_count, pkt.dst_id);
            }
        }
    }
}

void
NetworkSim::run(int max_steps)
{
    int steps = 0;
    while (!_should_stop && (max_steps < 0 || steps < max_steps)) {
        step();
        steps++;

        // Simulate fixed latency tick
        std::this_thread::sleep_for(std::chrono::microseconds(1));
    }
}

} // namespace pseudo
