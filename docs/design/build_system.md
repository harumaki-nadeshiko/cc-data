# Build System Refactoring Design

## Architecture

`framework/` → `libccframework.a` (static lib). Four module scripts consume it. Gem5 links via SConscript.

## Version A (Recommended)

### Directory Structure
```
build/framework/{include,lib,obj}/
build/bin/{ubio,networksim,barrier_manager}
```

### Scripts
- `build_framework.sh` — compiles Port.o+ZMQChannel.o → libframework.a, exports Port.hh+MemMessage.hh
- `build_ubio.sh` — links framework + ubio sources → build/bin/ubio
- `build_networksim.sh` — links framework + nsim sources → build/bin/networksim
- `build_barrier.sh` — links framework + barrier sources → build/bin/barrier_manager
- `build_all.sh` — calls all three module scripts

### run_multi.sh
Fixed paths: `build/bin/ubio`, `build/bin/networksim`, `build/bin/barrier_manager`. Missing → error exit.

### gem5 SConscript
Local change: `CPPPATH += framework/include`, `LIBPATH += framework/lib`, `LIBS += framework`. No more `Source(framework/Port.cc)`.

## Version B (Full)
- A + `common.sh` shared helpers
- env.sh/pkgconfig for framework
- AUTO_BUILD=1 option in run_multi.sh
- Environment variable overrides for bin paths
