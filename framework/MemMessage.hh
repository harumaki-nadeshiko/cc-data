#ifndef FRAMEWORK_MEMMESSAGE_HH
#define FRAMEWORK_MEMMESSAGE_HH

#include <cstdint>
#include <cstring>

namespace framework {

static constexpr uint32_t kMaxPayloadSize = 1024;
static constexpr uint32_t kMemMessageHeaderSize = 40;

enum class MemMessageType : uint32_t {
    CONTROL_SYNC    = 0,
    TERMINATE       = 1,
    COH_MSG         = 2,
    BARRIER_REACHED = 3,
    BARRIER_RELEASE = 4,
    PORT_HELLO      = 5,
    PORT_HELLO_ACK  = 6,
};

struct MemMessageHeader {
    uint64_t timestamp;
    uint32_t size;          // total size including header + payload
    uint32_t type;          // MemMessageType
    uint32_t src_module;    // launcher-assigned module ID
    uint32_t src_port;      // port ID within module
    uint32_t dst_module;
    uint32_t dst_port;
    uint64_t req_id;        // txn matching ID
};

static_assert(sizeof(MemMessageHeader) == kMemMessageHeaderSize,
              "MemMessageHeader must be exactly 40 bytes");

struct MemMessage {
    MemMessageHeader hdr;
    uint8_t payload[kMaxPayloadSize];

    MemMessage() { clear(); }

    void clear() {
        std::memset(&hdr, 0, sizeof(hdr));
        std::memset(payload, 0, sizeof(payload));
    }

    bool isValid() const { return hdr.size >= kMemMessageHeaderSize; }

    template<typename T>
    bool setPayload(const T& obj) {
        if (sizeof(T) > kMaxPayloadSize) return false;
        hdr.size = kMemMessageHeaderSize + sizeof(T);
        std::memcpy(payload, &obj, sizeof(T));
        return true;
    }

    template<typename T>
    const T* getPayload() const {
        uint32_t plen = hdr.size - kMemMessageHeaderSize;
        if (plen < sizeof(T)) return nullptr;
        return reinterpret_cast<const T*>(payload);
    }

    template<typename T>
    T* getPayload() {
        uint32_t plen = hdr.size - kMemMessageHeaderSize;
        if (plen < sizeof(T)) return nullptr;
        return reinterpret_cast<T*>(payload);
    }

    void setRawPayload(const uint8_t* data, uint32_t len) {
        if (len > kMaxPayloadSize) return;
        hdr.size = kMemMessageHeaderSize + len;
        std::memcpy(payload, data, len);
    }

    const uint8_t* rawPayload() const { return payload; }
    uint32_t payloadLen() const { return hdr.size - kMemMessageHeaderSize; }
};

} // namespace framework

#endif // FRAMEWORK_MEMMESSAGE_HH
