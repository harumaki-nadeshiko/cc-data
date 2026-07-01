#include "framework/Log.hh"

#include <cstdio>

namespace framework {

void LogInfo(const char *module_name, const char *format_str, ...)
{
    std::fprintf(stderr, "[%s] ", module_name);
    va_list args;
    va_start(args, format_str);
    std::vfprintf(stderr, format_str, args);
    va_end(args);
    std::fprintf(stderr, "\n");
}

void LogError(const char *module_name, const char *format_str, ...)
{
    std::fprintf(stderr, "[%s:ERROR] ", module_name);
    va_list args;
    va_start(args, format_str);
    std::vfprintf(stderr, format_str, args);
    va_end(args);
    std::fprintf(stderr, "\n");
}

} // namespace framework
