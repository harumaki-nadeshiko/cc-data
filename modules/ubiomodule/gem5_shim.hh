#ifndef MODULES_UBIOMODULE_GEM5_SHIM_HH
#define MODULES_UBIOMODULE_GEM5_SHIM_HH

#ifndef TRACING_ON  // Only for standalone ubio, skip in gem5 context

#include <cstdint>
#include <cstdio>
#include <string>

// === Type shims for gem5 types used by UBIOModule ===

namespace gem5
{

using Tick = uint64_t;
using Addr = uint64_t;

inline Tick curTick() { return 0; }  // standalone uses local tick via PseudoManager

} // namespace gem5

// === Logging shims ===

#define DPRINTF(flag, fmt, ...) \
    std::fprintf(stderr, "[%s] " fmt "\n", #flag, ##__VA_ARGS__)

#define panic(fmt, ...) do { \
    std::fprintf(stderr, "PANIC: " fmt "\n", ##__VA_ARGS__); \
    std::abort(); \
} while(0)

#define panic_if(cond, fmt, ...) do { \
    if (cond) { std::fprintf(stderr, "PANIC: " fmt "\n", ##__VA_ARGS__); std::abort(); } \
} while(0)

#define fatal(fmt, ...) panic(fmt, ##__VA_ARGS__)
#define fatal_if(cond, fmt, ...) panic_if(cond, fmt, ##__VA_ARGS__)
#define warn(fmt, ...) std::fprintf(stderr, "WARN: " fmt "\n", ##__VA_ARGS__)

// === Forward decls for gem5 types not used in standalone ===

namespace gem5 { namespace ruby {
class RubySystem;
class EPBackend;
class UBRouter;  // compat (now UBIOModule)
class MetaRNFController;
} }

// === Missing DataBlock stub ===
namespace gem5 { namespace ruby {
struct DataBlock {
    uint8_t data[64];
    DataBlock(int sz = 64) {}
    uint8_t getByte(int i) const { return (i >= 0 && i < 64) ? data[i] : 0; }
    const uint8_t* getData(int off, int len) const {
        (void)len;
        return (off >= 0 && off < 64) ? (data + off) : data;
    }
    void setData(const uint8_t* src, int off, int len) {
        for (int i = 0; i < len && off+i < 64; i++) data[off+i] = src[i];
    }
};
} }

// === Missing SimObject stub ===
namespace gem5 {
class SimObject {
public:
    virtual ~SimObject() = default;
    const std::string& name() const { static std::string s = "UBIOModule"; return s; }
};
}

// === Missing Params stub ===
#define PARAMS(name) /* placeholder for SimObject-derived params */
#define PARAMS_VECTOR(name) PARAMS(name)

// === CurTick shim for queue ===
namespace gem5 { namespace ruby {
    // Tick forwarding from PseudoManager time
    extern Tick g_current_tick;
} }

#endif // TRACING_ON
#endif // MODULES_UBIOMODULE_GEM5_SHIM_HH
