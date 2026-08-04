#ifndef FRAMEWORK_IFACE_LOG_HH
#define FRAMEWORK_IFACE_LOG_HH

#include <array>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <string>
#include <string_view>
#include <type_traits>
#include <utility>

namespace framework {

// Public only to permit a small, ABI-stable non-template formatting backend.
// Applications should normally use the Log* template functions below.
struct FormatArg {
    enum class Kind : std::uint8_t { Signed, Unsigned, String, Pointer };

    Kind kind = Kind::Unsigned;
    std::int64_t signedValue = 0;
    std::uint64_t unsignedValue = 0;
    std::string_view stringValue;
    const void* pointerValue = nullptr;

    FormatArg(const std::string& value) : kind(Kind::String), stringValue(value) {}
    FormatArg(std::string_view value) : kind(Kind::String), stringValue(value) {}
    FormatArg(const char* value)
        : kind(Kind::String), stringValue(value ? value : "(null)") {}
    FormatArg(char* value) : FormatArg(static_cast<const char*>(value)) {}
    FormatArg(std::nullptr_t) : kind(Kind::Pointer), pointerValue(nullptr) {}

    template <typename T,
              typename std::enable_if_t<
                  std::is_integral_v<std::remove_reference_t<T>> &&
                  std::is_signed_v<std::remove_reference_t<T>>, int> = 0>
    FormatArg(T value)
        : kind(Kind::Signed), signedValue(static_cast<std::int64_t>(value)) {}

    template <typename T,
              typename std::enable_if_t<
                  std::is_integral_v<std::remove_reference_t<T>> &&
                  std::is_unsigned_v<std::remove_reference_t<T>>, int> = 0>
    FormatArg(T value)
        : kind(Kind::Unsigned), unsignedValue(static_cast<std::uint64_t>(value)) {}

    template <typename T,
              typename std::enable_if_t<
                  std::is_pointer_v<std::remove_reference_t<T>> &&
                  !std::is_same_v<std::remove_cv_t<std::remove_pointer_t<
                                      std::remove_reference_t<T>>>, char>, int> = 0>
    FormatArg(T value)
        : kind(Kind::Pointer), pointerValue(static_cast<const void*>(value)) {}
};

namespace detail {
enum class LogLevel : std::uint8_t { Debug, Info, Warn, Error, Assert };
bool LogDebugEnabled();
void LogFormatted(LogLevel level, const char* module, std::string_view format,
                  const FormatArg* args, std::size_t argCount);
} // namespace detail

template <typename... Args>
inline void LogDebug(const char* module, std::string_view format,
                     Args&&... args)
{
    if (!detail::LogDebugEnabled())
        return;
    const std::array<FormatArg, sizeof...(Args)> values = {
        FormatArg(std::forward<Args>(args))...};
    detail::LogFormatted(detail::LogLevel::Debug, module, format, values.data(),
                         sizeof...(Args));
}

template <typename... Args>
inline void LogInfo(const char* module, std::string_view format, Args&&... args)
{
    const std::array<FormatArg, sizeof...(Args)> values = {
        FormatArg(std::forward<Args>(args))...};
    detail::LogFormatted(detail::LogLevel::Info, module, format, values.data(),
                         sizeof...(Args));
}

template <typename... Args>
inline void LogWarn(const char* module, std::string_view format, Args&&... args)
{
    const std::array<FormatArg, sizeof...(Args)> values = {
        FormatArg(std::forward<Args>(args))...};
    detail::LogFormatted(detail::LogLevel::Warn, module, format, values.data(),
                         sizeof...(Args));
}

template <typename... Args>
inline void LogError(const char* module, std::string_view format, Args&&... args)
{
    const std::array<FormatArg, sizeof...(Args)> values = {
        FormatArg(std::forward<Args>(args))...};
    detail::LogFormatted(detail::LogLevel::Error, module, format, values.data(),
                         sizeof...(Args));
}

template <typename Predicate, typename... Args>
inline void LogAssertIf(Predicate&& predicate, const char* module,
                        std::string_view format, Args&&... args)
{
    if (static_cast<bool>(predicate))
        return;
    const std::array<FormatArg, sizeof...(Args)> values = {
        FormatArg(std::forward<Args>(args))...};
    detail::LogFormatted(detail::LogLevel::Assert, module, format, values.data(),
                         sizeof...(Args));
    assert(false);
    std::abort();
}

} // namespace framework

#endif // FRAMEWORK_IFACE_LOG_HH
