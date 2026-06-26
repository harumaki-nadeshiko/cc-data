#ifndef __BASE_LOGGING_HH_STUB__
#define __BASE_LOGGING_HH_STUB__
#ifdef TRACING_ON  // gem5 context: redirect to real gem5 logging
#include "base/logging.hh"
#else
#include "modules/ubiomodule/gem5_shim.hh"
#endif
#endif
