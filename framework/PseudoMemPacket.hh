#ifndef FRAMEWORK_PSEUDOMEMPACKET_HH
#define FRAMEWORK_PSEUDOMEMPACKET_HH

#include <cstdint>
#include <cstring>

namespace pseudo
{

static constexpr uint32_t kMaxPayloadSize = 512;

struct PseudoMemPacket
{
    uint32_t type;
    uint32_t src_id;
    uint32_t dst_id;
    uint32_t payload_len;
    uint8_t  payload[kMaxPayloadSize];

    PseudoMemPacket()
        : type(0), src_id(0), dst_id(0), payload_len(0)
    {
        std::memset(payload, 0, sizeof(payload));
    }

    /**
     * Set payload from a byte buffer. Returns false if exceeds max.
     */
    bool setPayload(const uint8_t* data, uint32_t len)
    {
        if (len > kMaxPayloadSize) return false;
        payload_len = len;
        std::memcpy(payload, data, len);
        return true;
    }

    /**
     * Set payload from a POD struct (must fit within kMaxPayloadSize).
     */
    template <typename T>
    bool setPayload(const T& obj)
    {
        static_assert(sizeof(T) <= kMaxPayloadSize, "Payload too large");
        payload_len = sizeof(T);
        std::memcpy(payload, &obj, sizeof(T));
        return true;
    }

    /**
     * Get payload as a POD struct.
     */
    template <typename T>
    const T* getPayload() const
    {
        if (payload_len < sizeof(T)) return nullptr;
        return reinterpret_cast<const T*>(payload);
    }

    template <typename T>
    T* getPayload()
    {
        if (payload_len < sizeof(T)) return nullptr;
        return reinterpret_cast<T*>(payload);
    }
};

static_assert(sizeof(PseudoMemPacket) <= 1024,
              "PseudoMemPacket should be ~1KB max");

enum class PacketType : uint32_t {
    CoherenceMessage = 1,
    ControlMessage   = 2,
    Shutdown         = 3,
};

} // namespace pseudo

#endif // FRAMEWORK_PSEUDOMEMPACKET_HH
