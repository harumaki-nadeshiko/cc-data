#ifndef MODULES_UBIOMODULE_STUB_UBIOMODULE_HH
#define MODULES_UBIOMODULE_STUB_UBIOMODULE_HH

#include "modules/ubiomodule/gem5_shim.hh"
#include "mem/ruby/protocol/chi/ep/CoherenceMessage.hh"

namespace gem5
{
namespace ruby
{

class UBIOModule
{
  public:
    void sendMessage(const CoherenceMessage&, Tick = 0) {}
};

} // namespace ruby
} // namespace gem5

#endif
