#ifndef UBIO_GEM5_SHIM_HH
#define UBIO_GEM5_SHIM_HH

// Standalone types for ubio / modules independent compilation.
// When TRACING_ON (gem5 in-process), gem5 provides the real types and
// this header's body is skipped.
#ifndef TRACING_ON

#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <string>

namespace cc {

using Tick = uint64_t;
using Addr = uint64_t;
using NodeID = uint16_t;
using Cycles = uint64_t;

inline Tick curTick() { return 0; }

} // namespace cc

// Debug/logging macros
#define panic(fmt, ...) do { \
    std::fprintf(stderr, "PANIC: " fmt "\n", ##__VA_ARGS__); \
    std::abort(); \
} while(0)

#define panic_if(cond, fmt, ...) do { \
    if (cond) { panic(fmt, ##__VA_ARGS__); } \
} while(0)

#define fatal(fmt, ...) panic(fmt, ##__VA_ARGS__)
#define fatal_if(cond, fmt, ...) panic_if(cond, fmt, ##__VA_ARGS__)

#define warn(fmt, ...) \
    std::fprintf(stderr, "WARN: " fmt "\n", ##__VA_ARGS__)

namespace cc { namespace glob {

class RubySystem {};

struct DataBlock {
    uint8_t data[64];
    DataBlock(int sz = 64) { (void)sz; }
    uint8_t getByte(int i) const { return (i >= 0 && i < 64) ? data[i] : 0; }
    const uint8_t* getData(int off, int len) const {
        (void)len; return (off >= 0 && off < 64) ? (data + off) : data;
    }
    void setData(const uint8_t* src, int off, int len) {
        for (int i = 0; i < len && off+i < 64; i++) data[off+i] = src[i];
    }
};

} } // namespace cc::glob

// SimObject stub
namespace cc {
class SimObject {
public:
    virtual ~SimObject() = default;
    const std::string& name() const {
        static std::string s = "UBIOModule"; return s;
    }
};
} // namespace cc

#define PARAMS(name)
#define PARAMS_VECTOR(name) PARAMS(name)

#endif // !TRACING_ON
#endif
