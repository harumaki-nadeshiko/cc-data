#ifndef __MEM_RUBY_PROTOCOL_CHI_EP_EPCONTROLLER_STUB__
#define __MEM_RUBY_PROTOCOL_CHI_EP_EPCONTROLLER_STUB__
#include "modules/ubiomodule/gem5_shim.hh"
namespace gem5 { namespace ruby {
class EPController : public gem5::SimObject {
public:
    void init() override {}
    void wakeup() override {}
    void print(std::ostream& out) const override {}
};
} }
#endif
