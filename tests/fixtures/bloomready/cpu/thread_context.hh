#pragma once
namespace gem5 {
class ThreadContext {
  public:
    bool suspended = false;
    void activate() { suspended = false; }
    void suspend() { suspended = true; }
};
}
