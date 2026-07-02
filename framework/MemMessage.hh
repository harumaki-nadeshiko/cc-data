#ifndef FRAMEWORK_MEMMESSAGE_HH
#define FRAMEWORK_MEMMESSAGE_HH

#include <cstdint>
#include <cstring>

namespace framework {

static constexpr uint32_t kMaxPayloadSize = 1024;
static constexpr uint32_t kMemMessageHeaderSize = 40;

enum class MemMessageType : uint32_t {
    CONTROL_SYNC    = 0,   // clock heartbeat (no payload)
    TERMINATE       = 1,   // shutdown notice
    PAYLOAD         = 2,   // carries a CoherenceMessage (incl. barrier control)
};

struct MemMessageHeader {
    uint64_t timestamp;
    uint32_t size;          // total size including header + payload
    uint32_t type;          // MemMessageType
    uint32_t sourceId;      // source endpoint (node*numSockets + socket)
    uint32_t _reserved0;    // previously src_port (retain offset for compat)
    uint32_t targetId;      // target endpoint (node*numSockets + socket)
    uint32_t _reserved1;    // previously dst_port (retain offset for compat)
    uint64_t req_id;        // txn matching ID
};

// Payload for MemMessageType::TERMINATE (best-effort shutdown notice).
struct TerminatePayload {
    uint32_t reason;    // 0=normal exit, 1=error, 2=peer_lost
    uint32_t exit_code;
    uint32_t sender;    // module id of the sender
};

static_assert(sizeof(MemMessageHeader) == kMemMessageHeaderSize,
              "MemMessageHeader size mismatch");

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
