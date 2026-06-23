#ifndef FRAMEWORK_PORT_HH
#define FRAMEWORK_PORT_HH

#include <deque>
#include <memory>
#include <string>
#include <zmq.hpp>

#include "framework/MemMessage.hh"

namespace framework {

class Port
{
  public:
    /**
     * @param name       Human-readable name
     * @param module_id  Launcher-assigned module ID
     * @param port_id    Port ID within this module
     * @param endpoint   IPC endpoint. bind=true → bind; bind=false → connect
     * @param bind       True if this side binds, false if connects
     * @param ctx        ZMQ context
     * @param syncWindow Global fixed sync window L
     */
    Port(const std::string& name,
         uint32_t module_id, uint32_t port_id,
         const std::string& endpoint,
         bool bind,
         zmq::context_t& ctx,
         uint64_t syncWindow);

    ~Port();

    MemMessage* sendAllocateBuffer(uint64_t timestamp);
    bool send(MemMessage* msg);
    MemMessage* recv(uint64_t visibleTick);
    bool emitSync(uint64_t curTick);
    uint64_t nextVisibleTick() const { return _nextVisibleTick; }
    void advanceVisibleTick(uint64_t t) {
        if (t > _nextVisibleTick) _nextVisibleTick = t;
    }
    uint32_t moduleId() const { return _moduleId; }
    uint32_t portId() const { return _portId; }

  private:
    std::string _name;
    uint32_t _moduleId, _portId;
    zmq::context_t& _ctx;
    std::unique_ptr<zmq::socket_t> _socket;
    uint64_t _syncWindow, _lastSyncTick, _nextVisibleTick;
    MemMessage _sendBuf;
    bool _sendBufInUse;
    struct Deferred { uint64_t ts; MemMessage msg; };
    std::deque<Deferred> _deferred;
};

uint64_t synced_receive_lower_bound(Port** ports, int n, uint64_t tick);

} // namespace framework
#endif
