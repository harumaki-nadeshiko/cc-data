#ifndef CC_EP_HAMODULE_FLAT_BITMAP_DIRECTORY_HH
#define CC_EP_HAMODULE_FLAT_BITMAP_DIRECTORY_HH

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

namespace cc::ha {

// An exact, flat directory: every cache line in the configured PA range owns
// exactly nodeCount participant bits. A participant may be a node or a
// node/socket plane, as selected by the adapter. There are no tags, hashes,
// allocator overheads, or hidden address shadows in the accounted payload.
class FlatBitmapDirectory {
  public:
    static constexpr std::size_t MaxPayloadBytes = 512u * 1024u;

    struct Config {
        std::uint64_t base = 0;
        std::uint64_t bytes = 0;
        std::uint32_t lineBytes = 64;
        std::uint32_t nodeCount = 2;
    };

    explicit FlatBitmapDirectory(const Config &config);

    const Config &config() const noexcept { return config_; }
    std::uint64_t lineCount() const noexcept { return lineCount_; }
    std::uint64_t payloadBits() const noexcept { return payloadBits_; }
    std::size_t payloadBytes() const noexcept { return bytes_.size(); }
    std::size_t exactPayloadBytes() const noexcept { return static_cast<std::size_t>((payloadBits_ + 7) / 8); }

    bool contains(std::uint64_t address) const noexcept;
    std::uint64_t lineIndex(std::uint64_t address) const;
    std::uint64_t addressOf(std::uint64_t lineIndex) const;

    std::uint64_t sharers(std::uint64_t address) const;
    void setSharers(std::uint64_t address, std::uint64_t mask);
    bool test(std::uint64_t address, std::uint32_t node) const;
    void set(std::uint64_t address, std::uint32_t node, bool present);
    void clear(std::uint64_t address) { setSharers(address, 0); }

    std::string startupManifestJson() const;

  private:
    std::uint64_t readPacked(std::uint64_t bitOffset) const noexcept;
    void writePacked(std::uint64_t bitOffset, std::uint64_t value) noexcept;
    std::uint64_t validNodeMask() const noexcept;

    Config config_;
    std::uint64_t lineCount_ = 0;
    std::uint64_t payloadBits_ = 0;
    std::vector<std::uint8_t> bytes_;
};

} // namespace cc::ha

#endif
