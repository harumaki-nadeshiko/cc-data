#ifndef MODULES_NETWORKSIM_FORWARDTABLE_HH
#define MODULES_NETWORKSIM_FORWARDTABLE_HH

#include <cstdint>
#include <map>
#include <string>
#include <vector>

namespace pseudo
{

/**
 * ForwardTable: minimal forwarding engine based on precomputed
 * shortest-path routes from a topology config.
 *
 * Phase 1: static table, all-to-all connectivity assumed.
 * Phase 2: support custom topologies with path computation.
 */
class ForwardTable
{
  public:
    ForwardTable() = default;

    /**
     * Add a direct link between two port IDs. Bidirectional.
     */
    void addLink(int port_a, int port_b, int latency_ticks = 1);

    /**
     * Find the next-hop port for a destination port.
     * Returns -1 if no route exists.
     */
    int nextHop(int dst_port) const;

    /**
     * Get the latency (in ticks) for a link between two ports.
     */
    int linkLatency(int src_port, int dst_port) const;

    /**
     * Build a full all-to-all forwarding table from a list of port IDs.
     * All ports are assumed directly connected (full mesh).
     */
    void buildFullMesh(const std::vector<int>& port_ids, int latency = 1);

    /**
     * Load from a JSON config string.
     * Format: {"ports": [100,200,300], "links": [[100,200,5],[200,300,3]]}
     */
    bool loadJson(const std::string& json_path);

    const std::map<int, std::vector<std::pair<int,int>>>& links() const { return _links; }

  private:
    // adjacency: port_id → [(neighbor_port, latency)]
    std::map<int, std::vector<std::pair<int,int>>> _links;
};

} // namespace pseudo

#endif // MODULES_NETWORKSIM_FORWARDTABLE_HH
