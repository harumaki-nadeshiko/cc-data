#include "framework/PseudoMemPort.hh"
#include "framework/PseudoManager.hh"

namespace pseudo
{

PseudoMemPort::PseudoMemPort(int port_id, PseudoManager* mgr)
    : _port_id(port_id), _manager(mgr), _shutdown(false)
{
}

void
PseudoMemPort::send(const PseudoMemPacket& pkt)
{
    if (_manager)
        _manager->deliver(pkt);
}

bool
PseudoMemPort::recv(PseudoMemPacket& pkt)
{
    std::unique_lock<std::mutex> lock(_mutex);
    _cv.wait(lock, [this] { return !_rx_queue.empty() || _shutdown; });

    if (_shutdown && _rx_queue.empty())
        return false;

    pkt = _rx_queue.front();
    _rx_queue.pop_front();
    return true;
}

bool
PseudoMemPort::recv(PseudoMemPacket& pkt, int timeout_ms)
{
    std::unique_lock<std::mutex> lock(_mutex);
    bool ok = _cv.wait_for(lock, std::chrono::milliseconds(timeout_ms),
                           [this] { return !_rx_queue.empty() || _shutdown; });

    if (!ok || (_shutdown && _rx_queue.empty()))
        return false;

    pkt = _rx_queue.front();
    _rx_queue.pop_front();
    return true;
}

bool
PseudoMemPort::poll() const
{
    std::lock_guard<std::mutex> lock(_mutex);
    return !_rx_queue.empty();
}

void
PseudoMemPort::enqueue(const PseudoMemPacket& pkt)
{
    {
        std::lock_guard<std::mutex> lock(_mutex);
        _rx_queue.push_back(pkt);
    }
    _cv.notify_one();
}

void
PseudoMemPort::shutdown()
{
    {
        std::lock_guard<std::mutex> lock(_mutex);
        _shutdown = true;
    }
    _cv.notify_all();
}

} // namespace pseudo
