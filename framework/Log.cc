#include "framework/iface/Log.hh"

#include <cstdio>
#include <cstdlib>
#include <string>

namespace framework {
namespace detail {
namespace {

bool EnvEnabled(const char* value)
{
    if (!value || !*value)
        return false;
    const std::string_view text(value);
    return text != "0" && text != "false" && text != "FALSE" &&
           text != "off" && text != "OFF";
}

void AppendArgument(std::string& output, const FormatArg& argument, bool hex)
{
    char buffer[2 + sizeof(std::uint64_t) * 2 + 1];
    switch (argument.kind) {
      case FormatArg::Kind::Signed:
        if (hex) {
            std::snprintf(buffer, sizeof(buffer), "%llx",
                          static_cast<unsigned long long>(argument.signedValue));
            output += buffer;
        } else {
            output += std::to_string(argument.signedValue);
        }
        break;
      case FormatArg::Kind::Unsigned:
        if (hex) {
            std::snprintf(buffer, sizeof(buffer), "%llx",
                          static_cast<unsigned long long>(argument.unsignedValue));
            output += buffer;
        } else {
            output += std::to_string(argument.unsignedValue);
        }
        break;
      case FormatArg::Kind::String:
        output.append(argument.stringValue.data(), argument.stringValue.size());
        break;
      case FormatArg::Kind::Pointer:
        if (!argument.pointerValue) {
            output += "0x0";
        } else {
            std::snprintf(buffer, sizeof(buffer), "%p", argument.pointerValue);
            output += buffer;
        }
        break;
    }
}

std::string Format(std::string_view format, const FormatArg* arguments,
                   std::size_t argumentCount)
{
    std::string output;
    output.reserve(format.size() + argumentCount * 8);
    std::size_t argumentIndex = 0;
    for (std::size_t i = 0; i < format.size();) {
        if (format[i] == '{') {
            if (i + 1 < format.size() && format[i + 1] == '{') {
                output.push_back('{');
                i += 2;
                continue;
            }
            const bool plain = i + 1 < format.size() && format[i + 1] == '}';
            const bool hex = i + 3 < format.size() && format[i + 1] == ':' &&
                             format[i + 2] == 'x' && format[i + 3] == '}';
            if (plain || hex) {
                const std::size_t tokenSize = plain ? 2 : 4;
                if (argumentIndex < argumentCount)
                    AppendArgument(output, arguments[argumentIndex++], hex);
                else
                    output.append(format.substr(i, tokenSize));
                i += tokenSize;
                continue;
            }
        } else if (format[i] == '}' && i + 1 < format.size() &&
                   format[i + 1] == '}') {
            output.push_back('}');
            i += 2;
            continue;
        }
        output.push_back(format[i++]);
    }
    return output;
}

} // namespace

bool LogDebugEnabled()
{
    static const bool enabled = EnvEnabled(std::getenv("FRAMEWORK_LOG_DEBUG")) ||
                                EnvEnabled(std::getenv("EP_DEBUG_FRAMEWORK"));
    return enabled;
}

void LogFormatted(LogLevel level, const char* module, std::string_view format,
                  const FormatArg* arguments, std::size_t argumentCount)
{
    (void)module; // The frozen contract intentionally adds no automatic prefix.
    if (level == LogLevel::Debug && !LogDebugEnabled())
        return;
    std::string output = Format(format, arguments, argumentCount);
    while (!output.empty() &&
           (output.back() == '\n' || output.back() == '\r'))
        output.pop_back();
    const auto write = [&output](FILE* stream) {
        std::fwrite(output.data(), 1, output.size(), stream);
        std::fputc('\n', stream);
        std::fflush(stream);
    };
    if (level == LogLevel::Debug || level == LogLevel::Info) {
        write(stdout);
    } else {
        // Match the production backend contract: warnings, errors, and
        // assertions are visible in both the normal transcript and stderr.
        write(stdout);
        write(stderr);
    }
}

} // namespace detail
} // namespace framework
