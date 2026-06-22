#ifndef FRAMEWORK_PSEUDOMANAGER_HH
#define FRAMEWORK_PSEUDOMANAGER_HH

#include <cstdint>
#include <map>
#include <memory>
#include <set>
#include <string>
#include <vector>

#include "framework/PseudoMemPort.hh"

namespace pseudo
{

/**
 * PseudoManager: owns all PseudoMemPort objects and manages
 * the duplex connection topology between them.
 *
 * When a PseudoMemPort::send() is called, PseudoManager::deliver()
 * routes the packet to the destination port's receive queue.
 *
 * For Phase 1, this is a single-process switchboard. For Phase 5,
 * it will be replaced by ZeroMQ-based inter-process communication.
 */
class PseudoManager
{
  public:
    PseudoManager() = default;

    /**
     * Create a new PseudoMemPort with the given ID.
     */
    PseudoMemPort* createPort(int port_id);

    /**
     * Get an existing PseudoMemPort by ID. Returns nullptr if not found.
     */
    PseudoMemPort* getPort(int port_id);

    /**
     * Create a duplex connection between two ports.
     * Packets sent from port_a with dst=port_b_id will reach port_b,
     * and vice versa.
     */
    void connect(int port_a, int port_b);

    /**
     * Deliver a packet to its destination port.
     * Called by PseudoMemPort::send().
     */
    void deliver(const PseudoMemPacket& pkt);

    /**
     * Shutdown all ports.
     */
    void shutdown();

    /**
     * Load topology from JSON config.
     */
    bool loadTopology(const std::string& json_path);

  private:
    std::map<int, std::unique_ptr<PseudoMemPort>> _ports;
    std::map<int, std::vector<int>> _connections;
};

} // namespace pseudo

#endif // FRAMEWORK_PSEUDOMANAGER_HH
