#include "NodeAddressMap.hh"

#include <stdexcept>

namespace cc
{

namespace glob
{

NodeAddressMap::NodeAddressMap(int num_nodes, int num_sockets, uint64_t seg_size)
  : _numNodes(num_nodes),
    _numSockets(num_sockets),
    _segSize(seg_size)
{
    if (_numNodes < 1 || _numNodes > MAX_NODES)
        throw std::invalid_argument("num_nodes must be in [1,16]");
    if (_numSockets < 1 ||
        static_cast<uint64_t>(_numNodes) * _numSockets > 32)
        throw std::invalid_argument("topology must contain 1..32 planes");
    if (_segSize == 0 || (_segSize & (_segSize - 1)) != 0)
        throw std::invalid_argument("segment size must be a power of two");
    const uint64_t planes = static_cast<uint64_t>(_numNodes) * _numSockets;
    const uint64_t segmentsPerNode = 2 + planes;
    const uint64_t nodeWindowBytes = 1ULL << NODE_ADDR_SHIFT;
    if (_segSize > nodeWindowBytes / segmentsPerNode)
        throw std::invalid_argument("DSM layout exceeds the per-node PA window");
}

} // namespace glob
} // namespace cc
