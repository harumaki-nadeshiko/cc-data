#ifndef __MEM_RUBY_PROTOCOL_CHI_EP_NODEADDRESSMAP_HH__
#define __MEM_RUBY_PROTOCOL_CHI_EP_NODEADDRESSMAP_HH__

#include <cstdint>
#include <stdexcept>

namespace cc
{

namespace glob
{

class NodeAddressMap
{
  public:
    static constexpr int NODE_ADDR_SHIFT = 40;
    static constexpr int MAX_NODES = 16;
    static constexpr int DEFAULT_NUM_SOCKETS = 1;

    NodeAddressMap(int num_nodes, int num_sockets = DEFAULT_NUM_SOCKETS,
                   uint64_t seg_size = 128ULL * 1024 * 1024);

    uint64_t nodeBase(int node_id) const {
        if (node_id < 0 || node_id >= _numNodes)
            throw std::out_of_range("node_id is outside configured topology");
        return static_cast<uint64_t>(node_id) << NODE_ADDR_SHIFT;
    }

    bool isDsm(int node_id, uint64_t pa) const {
        uint64_t base = nodeBase(node_id);
        uint64_t dsm_start = base + 2 * _segSize;
        uint64_t dsm_end = dsm_start + _numNodes * _numSockets * _segSize;
        return pa >= dsm_start && pa < dsm_end;
    }

    int homeNode(int node_id, uint64_t pa) const {
        if (!isDsm(node_id, pa)) return -1;
        uint64_t base = nodeBase(node_id);
        int dsm_index = static_cast<int>((pa - base - 2 * _segSize) / _segSize);
        return dsm_index / _numSockets;
    }

    // v4-dual-socket: extract homeSocket from PA encoding
    int homeSocket(int node_id, uint64_t pa) const {
        if (!isDsm(node_id, pa)) return -1;
        uint64_t base = nodeBase(node_id);
        int dsm_index = static_cast<int>((pa - base - 2 * _segSize) / _segSize);
        return dsm_index % _numSockets;
    }

    bool isDsmLocal(int node_id, uint64_t pa) const {
        return isDsm(node_id, pa) && homeNode(node_id, pa) == node_id;
    }

    bool isDsmRemote(int node_id, uint64_t pa) const {
        return isDsm(node_id, pa) && homeNode(node_id, pa) != node_id;
    }

    int srcNodeId(uint64_t pa) const {
        return static_cast<int>(pa >> NODE_ADDR_SHIFT);
    }

    uint64_t dsmOffset(uint64_t pa) const {
        return pa & (_segSize - 1);
    }

    // v4-dual-socket: buildDsmPA now takes homeSocket
    uint64_t buildDsmPA(int tgt_node, int home_node, uint64_t offset,
                        int home_socket = 0) const {
        if (home_node < 0 || home_node >= _numNodes)
            throw std::out_of_range("home_node is outside configured topology");
        if (home_socket < 0 || home_socket >= _numSockets)
            throw std::out_of_range("home_socket is outside configured topology");
        if (offset >= _segSize)
            throw std::out_of_range("DSM offset exceeds segment size");
        return nodeBase(tgt_node) + 2 * _segSize
               + (home_node * _numSockets + home_socket) * _segSize
               + offset;
    }

    uint64_t segSize() const { return _segSize; }
    int numNodes() const { return _numNodes; }
    int numSockets() const { return _numSockets; }

  private:
    int _numNodes;
    int _numSockets;
    uint64_t _segSize;
};

} // namespace glob
} // namespace cc

#endif // __MEM_RUBY_PROTOCOL_CHI_EP_NODEADDRESSMAP_HH__
