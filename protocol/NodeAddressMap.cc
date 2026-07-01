#include "NodeAddressMap.hh"

namespace cc
{

namespace glob
{

NodeAddressMap::NodeAddressMap(int num_nodes, int num_sockets, uint64_t seg_size)
  : _numNodes(num_nodes),
    _numSockets(num_sockets),
    _segSize(seg_size)
{
}

} // namespace glob
} // namespace cc
