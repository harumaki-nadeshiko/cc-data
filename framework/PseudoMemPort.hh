#ifndef FRAMEWORK_PSEUDOMEMPORT_HH
#define FRAMEWORK_PSEUDOMEMPORT_HH

#include <condition_variable>
#include <cstdint>
#include <deque>
#include <functional>
#include <mutex>

#include "framework/PseudoMemPacket.hh"

namespace pseudo
{

class PseudoManager;

class PseudoMemPort
{
  public:
    explicit PseudoMemPort(int port_id, PseudoManager* mgr = nullptr);

    int id() const { return _port_id; }

    /**
     * send: non-blocking. Packet is copied into the target port's
     * receive queue via PseudoManager. Never returns failure under
     * normal conditions; backpressure is hidden internally.
     */
    void send(const PseudoMemPacket& pkt);

    /**
     * recv: blocking. Waits until a packet is available.
     * Returns true on success, false on shutdown.
     */
    bool recv(PseudoMemPacket& pkt);

    /**
     * recv with timeout (milliseconds). Returns false if timed out
     * or no data available within timeout.
     */
    bool recv(PseudoMemPacket& pkt, int timeout_ms);

    /**
     * poll: non-blocking. Returns true if at least one packet is
     * available in the receive queue.
     */
    bool poll() const;

    /**
     * Called by PseudoManager to enqueue an incoming packet.
     */
    void enqueue(const PseudoMemPacket& pkt);

    /**
     * Signal shutdown to wake up any blocking recv().
     */
    void shutdown();

  private:
    int _port_id;
    PseudoManager* _manager;
    std::deque<PseudoMemPacket> _rx_queue;
    mutable std::mutex _mutex;
    std::condition_variable _cv;
    bool _shutdown;
};

} // namespace pseudo

#endif // FRAMEWORK_PSEUDOMEMPORT_HH
