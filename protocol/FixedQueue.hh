#ifndef CC_PROTOCOL_FIXED_QUEUE_HH
#define CC_PROTOCOL_FIXED_QUEUE_HH

#include <cassert>
#include <cstddef>
#include <memory>
#include <utility>

namespace cc { namespace glob {

// Runtime topology determines storage ONCE. No resize, growth or fallback.
template<class T>
class FixedQueue {
    const std::size_t limit;
    std::unique_ptr<T[]> entries;
    std::size_t count = 0;
  public:
    explicit FixedQueue(std::size_t capacity)
        : limit(capacity), entries(new T[capacity]{}) { assert(capacity); }
    using iterator = T *;
    using const_iterator = const T *;
    iterator begin() { return entries.get(); }
    iterator end() { return begin() + count; }
    const_iterator begin() const { return entries.get(); }
    const_iterator end() const { return begin() + count; }
    bool empty() const { return !count; }
    std::size_t size() const { return count; }
    std::size_t capacity() const { return limit; }
    bool push_back(const T &value) {
        if (count == limit) return false;
        entries[count++] = value;
        return true;
    }
    iterator erase(iterator position) {
        assert(position >= begin() && position < end());
        for (auto next = position + 1; next != end(); ++next)
            *(next - 1) = std::move(*next);
        entries[--count] = T{};
        return position;
    }
};
} }
#endif
