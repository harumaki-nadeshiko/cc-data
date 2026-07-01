#ifndef FRAMEWORK_LOG_HH
#define FRAMEWORK_LOG_HH

#include <cstdarg>

namespace framework {

void LogInfo(const char *module_name, const char *format_str, ...);
void LogError(const char *module_name, const char *format_str, ...);

} // namespace framework

#endif
