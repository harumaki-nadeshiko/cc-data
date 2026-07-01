#ifndef UBIO_TYPES_HH
#define UBIO_TYPES_HH

#include <cstdint>
#include <cstdio>

namespace ubiocc {

using Tick = uint64_t;

inline Tick curTick() { return 0; }

} // namespace ubiocc

#endif
