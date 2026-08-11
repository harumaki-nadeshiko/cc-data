#ifndef FRAMEWORK_IFACE_MESSAGE_HH
#define FRAMEWORK_IFACE_MESSAGE_HH

#include <cstddef>
#include <cstdint>

namespace framework {

// Message is deliberately opaque.  A Message obtained from
// AllocateSendMessage is owned by the caller until it is sent or released.  A
// Message obtained from ReceiveMessage is borrowed from its Port.
struct Message;

enum class MessageType : std::uint32_t {
    ControlSync = 0,
    Terminate = 1,
    Payload = 2,
};

std::uint64_t GetMessageTimestamp(const Message* message);

MessageType GetMessageType(const Message* message);

std::uint32_t GetMessageSourceId(const Message* message);
void SetMessageSourceId(Message* message, std::uint32_t sourceId);

std::uint32_t GetMessageTargetId(const Message* message);
void SetMessageTargetId(Message* message, std::uint32_t targetId);

std::uint64_t GetMessageRequestId(const Message* message);
void SetMessageRequestId(Message* message, std::uint64_t requestId);

void SetMessagePayload(Message* message, const void* data, std::size_t size);
const void* GetMessagePayloadData(const Message* message);
std::size_t GetMessagePayloadSize(const Message* message);
std::size_t GetMaxPayloadSize();

// Copy all application-visible fields except the destination timestamp.  The
// destination's allocation, capacity, and backend ownership are unchanged.
void CopyMessage(Message* destination, const Message* source);

// Releases an allocated message which has not been passed to SendMessage.
void ReleaseMessage(Message* message);

} // namespace framework

#endif // FRAMEWORK_IFACE_MESSAGE_HH
