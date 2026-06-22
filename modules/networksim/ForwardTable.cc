#include "modules/networksim/ForwardTable.hh"

#include <cstdio>
#include <fstream>
#include <sstream>

namespace pseudo
{

void
ForwardTable::addLink(int port_a, int port_b, int latency_ticks)
{
    _links[port_a].push_back({port_b, latency_ticks});
    _links[port_b].push_back({port_a, latency_ticks});
}

int
ForwardTable::nextHop(int dst_port) const
{
    for (const auto& kv : _links) {
        int neighbor = kv.first;
        for (const auto& nb : kv.second) {
            if (nb.first == dst_port)
                return neighbor;
        }
    }
    return -1;
}

int
ForwardTable::linkLatency(int src_port, int dst_port) const
{
    auto it = _links.find(src_port);
    if (it == _links.end()) return -1;
    for (const auto& [nb, lat] : it->second)
        if (nb == dst_port) return lat;
    return -1;
}

void
ForwardTable::buildFullMesh(const std::vector<int>& port_ids, int latency)
{
    for (size_t i = 0; i < port_ids.size(); ++i) {
        for (size_t j = i + 1; j < port_ids.size(); ++j) {
            addLink(port_ids[i], port_ids[j], latency);
        }
    }
}

bool
ForwardTable::loadJson(const std::string& json_path)
{
    std::ifstream f(json_path);
    if (!f.is_open()) {
        std::fprintf(stderr, "[ForwardTable] cannot open %s\n", json_path.c_str());
        return false;
    }
    std::string content((std::istreambuf_iterator<char>(f)),
                         std::istreambuf_iterator<char>());
    // Minimal JSON parser for topology:
    // {"links": [[src,dst,lat], ...]}
    size_t pos = content.find("\"links\"");
    if (pos == std::string::npos) {
        std::fprintf(stderr, "[ForwardTable] no 'links' field in %s\n", json_path.c_str());
        return false;
    }
    pos = content.find('[', pos);
    if (pos == std::string::npos) return false;

    // Parse array of [src, dst, lat] triples
    size_t end = content.find(']', pos);
    std::string arr = content.substr(pos + 1, end - pos - 1);
    std::istringstream iss(arr);
    std::string triple;
    while (std::getline(iss, triple, ']')) {
        size_t b1 = triple.find('[');
        if (b1 == std::string::npos) continue;
        std::string nums = triple.substr(b1 + 1);
        int src = -1, dst = -1, lat = 1;
        std::sscanf(nums.c_str(), "%d,%d,%d", &src, &dst, &lat);
        if (src >= 0 && dst >= 0)
            addLink(src, dst, lat);
    }
    return true;
}

} // namespace pseudo
