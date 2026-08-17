#include "modules/hamodule/FlatBitmapDirectory.hh"

#include <cerrno>
#include <cstdlib>
#include <exception>
#include <iostream>
#include <limits>
#include <stdexcept>

namespace {
std::uint64_t parse(const char *text, const char *name)
{
    errno = 0;
    char *end = nullptr;
    const unsigned long long value = std::strtoull(text, &end, 0);
    if (errno || !end || *end) throw std::invalid_argument(std::string("invalid ") + name);
    return static_cast<std::uint64_t>(value);
}
}

int main(int argc, char **argv)
{
    if (argc != 5) {
        std::cerr << "usage: ha_controller_manifest <base> <range-bytes> <line-bytes> <nodes>\n";
        return 2;
    }
    try {
        const auto base = parse(argv[1], "base");
        const auto bytes = parse(argv[2], "range-bytes");
        const auto line = parse(argv[3], "line-bytes");
        const auto nodes = parse(argv[4], "nodes");
        if (line > std::numeric_limits<std::uint32_t>::max() ||
            nodes > std::numeric_limits<std::uint32_t>::max())
            throw std::invalid_argument("line-bytes/nodes exceeds uint32_t");
        cc::ha::FlatBitmapDirectory directory(
            {base, bytes, static_cast<std::uint32_t>(line), static_cast<std::uint32_t>(nodes)});
        std::cout << directory.startupManifestJson() << '\n';
        return 0;
    } catch (const std::exception &error) {
        std::cerr << "ha_controller_manifest: " << error.what() << '\n';
        return 1;
    }
}
