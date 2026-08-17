#include "modules/hamodule/FlatBitmapDirectory.hh"

#include <limits>
#include <sstream>
#include <stdexcept>

namespace cc::ha {

namespace {
bool isPowerOfTwo(std::uint32_t value)
{
    return value && !(value & (value - 1));
}
}

FlatBitmapDirectory::FlatBitmapDirectory(const Config &config) : config_(config)
{
    if (config_.nodeCount < 1 || config_.nodeCount > 64)
        throw std::invalid_argument("FlatBitmapDirectory nodeCount must be in [1,64]");
    if (!isPowerOfTwo(config_.lineBytes))
        throw std::invalid_argument("FlatBitmapDirectory lineBytes must be a power of two");
    if (!config_.bytes || config_.base % config_.lineBytes || config_.bytes % config_.lineBytes)
        throw std::invalid_argument("FlatBitmapDirectory range must be non-empty and line aligned");
    if (config_.base > std::numeric_limits<std::uint64_t>::max() - config_.bytes)
        throw std::invalid_argument("FlatBitmapDirectory PA range overflows uint64_t");

    lineCount_ = config_.bytes / config_.lineBytes;
    if (lineCount_ > std::numeric_limits<std::uint64_t>::max() / config_.nodeCount)
        throw std::invalid_argument("FlatBitmapDirectory bitmap size overflows uint64_t");
    payloadBits_ = lineCount_ * config_.nodeCount;
    const std::uint64_t byteCount = (payloadBits_ + 7) / 8;
    if (byteCount > MaxPayloadBytes)
        throw std::invalid_argument("FlatBitmapDirectory bitmap payload exceeds 512 KiB");
    bytes_.assign(static_cast<std::size_t>(byteCount), 0);
}

bool FlatBitmapDirectory::contains(std::uint64_t address) const noexcept
{
    return address >= config_.base && address < config_.base + config_.bytes &&
           (address - config_.base) % config_.lineBytes == 0;
}

std::uint64_t FlatBitmapDirectory::lineIndex(std::uint64_t address) const
{
    if (!contains(address))
        throw std::out_of_range("FlatBitmapDirectory address is outside/aligned incorrectly");
    return (address - config_.base) / config_.lineBytes;
}

std::uint64_t FlatBitmapDirectory::addressOf(std::uint64_t index) const
{
    if (index >= lineCount_)
        throw std::out_of_range("FlatBitmapDirectory line index is outside range");
    return config_.base + index * config_.lineBytes;
}

std::uint64_t FlatBitmapDirectory::validNodeMask() const noexcept
{
    return config_.nodeCount == 64 ? ~std::uint64_t{0} : ((std::uint64_t{1} << config_.nodeCount) - 1);
}

std::uint64_t FlatBitmapDirectory::readPacked(std::uint64_t offset) const noexcept
{
    std::uint64_t value = 0;
    for (std::uint32_t bit = 0; bit < config_.nodeCount; ++bit) {
        const std::uint64_t absolute = offset + bit;
        if (bytes_[static_cast<std::size_t>(absolute / 8)] & (std::uint8_t{1} << (absolute % 8)))
            value |= std::uint64_t{1} << bit;
    }
    return value;
}

void FlatBitmapDirectory::writePacked(std::uint64_t offset, std::uint64_t value) noexcept
{
    value &= validNodeMask();
    for (std::uint32_t bit = 0; bit < config_.nodeCount; ++bit) {
        const std::uint64_t absolute = offset + bit;
        std::uint8_t &byte = bytes_[static_cast<std::size_t>(absolute / 8)];
        const std::uint8_t mask = std::uint8_t{1} << (absolute % 8);
        if (value & (std::uint64_t{1} << bit)) byte |= mask;
        else byte &= static_cast<std::uint8_t>(~mask);
    }
}

std::uint64_t FlatBitmapDirectory::sharers(std::uint64_t address) const
{
    return readPacked(lineIndex(address) * config_.nodeCount);
}

void FlatBitmapDirectory::setSharers(std::uint64_t address, std::uint64_t mask)
{
    if (mask & ~validNodeMask())
        throw std::invalid_argument("FlatBitmapDirectory sharer mask names an absent node");
    writePacked(lineIndex(address) * config_.nodeCount, mask);
}

bool FlatBitmapDirectory::test(std::uint64_t address, std::uint32_t node) const
{
    if (node >= config_.nodeCount)
        throw std::out_of_range("FlatBitmapDirectory node is outside range");
    return (sharers(address) & (std::uint64_t{1} << node)) != 0;
}

void FlatBitmapDirectory::set(std::uint64_t address, std::uint32_t node, bool present)
{
    if (node >= config_.nodeCount)
        throw std::out_of_range("FlatBitmapDirectory node is outside range");
    std::uint64_t mask = sharers(address);
    const std::uint64_t bit = std::uint64_t{1} << node;
    setSharers(address, present ? mask | bit : mask & ~bit);
}

std::string FlatBitmapDirectory::startupManifestJson() const
{
    std::ostringstream out;
    out << "{\"component\":\"FlatBitmapDirectory\",\"base\":" << config_.base
        << ",\"range_bytes\":" << config_.bytes << ",\"line_bytes\":" << config_.lineBytes
        << ",\"line_count\":" << lineCount_ << ",\"nodes\":" << config_.nodeCount
        << ",\"bits_per_line\":" << config_.nodeCount << ",\"payload_bits\":" << payloadBits_
        << ",\"payload_bytes_exact\":" << exactPayloadBytes()
        << ",\"payload_bytes_allocated\":" << payloadBytes()
        << ",\"budget_bytes\":" << MaxPayloadBytes << ",\"within_budget\":true} ";
    return out.str();
}

} // namespace cc::ha
