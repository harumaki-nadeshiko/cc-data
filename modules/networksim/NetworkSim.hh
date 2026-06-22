#ifndef MODULES_NETWORKSIM_NETWORKSIM_HH
#define MODULES_NETWORKSIM_NETWORKSIM_HH

#include <cstdint>
#include <deque>
#include <vector>

#include "framework/PseudoMemPort.hh"
#include "modules/networksim/ForwardTable.hh"

namespace pseudo
{

/**
 * NetworkSim: minimal network simulator that forwards PseudoMemPackets
 * between ports according to a configurable topology.
 *
 * Phase 1: fixed per-hop latency, FIFO ordering.
 * Phase 2: configurable reorder / fault injection hooks.
 *
 * Owns its PseudoMemPorts. Polls all ports in its main loop,
 * forwards packets to the next-hop port based on ForwardTable.
 */
class NetworkSim
{
  public:
    NetworkSim(PseudoManager* mgr = nullptr);

    /**
     * Add a port with the given ID. The port is created via PseudoManager.
     */
    PseudoMemPort* addPort(int port_id);

    /**
     * Configure the forwarding table from a topology JSON file.
     */
    bool configure(const std::string& topology_path);

    /**
     * Set all-to-all full mesh connectivity for the given ports.
     */
    void configureFullMesh(const std::vector<int>& port_ids, int latency = 1);

    /**
     * Main processing step: poll all ports, forward any received packets
     * to their destination. Call in a loop.
     */
    void step();

    /**
     * Run the forwarding loop for a given number of steps, or until
     * all ports are idle (no packets queued).
     */
    void run(int max_steps = -1);

    bool shouldStop() const { return _should_stop; }
    void requestStop() { _should_stop = true; }

    PseudoManager* manager() { return _manager; }

  private:
    PseudoManager* _manager;
    ForwardTable _forward;
    std::vector<int> _port_ids;
    bool _should_stop;
    int _step_count;
};

} // namespace pseudo

#endif // MODULES_NETWORKSIM_NETWORKSIM_HH
