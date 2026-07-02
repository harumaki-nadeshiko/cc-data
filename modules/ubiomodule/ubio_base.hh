#ifndef UBIO_BASE_HH
#define UBIO_BASE_HH

// Base types and error macros for the standalone ubio process and its
// coherence-directory code (UBCCController / ResidentDir / ...).
//
// These are ubio's OWN foundational types — plain integer aliases, a 64-byte
// cache-line container, and printf-style error macros. They are NOT gem5
// types and carry no dependency on gem5; the ubio module is a self-contained
// OS process. (This header replaces the former "gem5_shim.hh", whose name
// falsely implied a gem5 dependency and whose TRACING_ON in-process build mode
// is no longer used.)

#include <cstdint>
#include <cstdio>
#include <cstdlib>

namespace cc {

using Tick   = uint64_t;
using Addr   = uint64_t;
using NodeID = uint16_t;
using Cycles = uint64_t;

// The standalone ubio process advances its virtual clock in ubio_main's loop
// (see Port::safeTs). Directory bookkeeping timestamps read curTick(); in the
// process model there is no global event queue, so this returns 0 and callers
// use it only for relative/debug bookkeeping.
inline Tick curTick() { return 0; }

} // namespace cc

// ── Error / diagnostic macros ───────────────────────────────────────
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

// ── 64-byte cache-line data container ───────────────────────────────
namespace cc { namespace glob {

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

#endif // UBIO_BASE_HH
