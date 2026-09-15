// Resource-order check for the proposed Park protocol, not a CHI simulator.
// Two independent Home transactions each invalidate both target nodes. Each
// node has one native control context, retained through AcquireResumeAck.
#include <array>
#include <cassert>
#include <iostream>
#include <queue>
#include <set>
#include <utility>

struct State {
    // Bits (parent * 2 + target) record local control completions. A completed
    // local control still holds its context until its resume obligation ends.
    unsigned done = 0;
    std::array<int, 2> held{{-1, -1}};
    bool operator<(const State &b) const {
        return std::make_pair(done, held) < std::make_pair(b.done, b.held);
    }
};

static unsigned
explore(bool resumeNeedsHomeCompletion)
{
    std::set<State> visited;
    std::queue<State> pending;
    pending.push(State{});
    unsigned deadlocks = 0;
    while (!pending.empty()) {
        const State s = pending.front();
        pending.pop();
        if (!visited.insert(s).second) continue;
        bool enabled = false;
        for (unsigned target = 0; target < 2; ++target) {
            if (s.held[target] < 0) {
                for (unsigned parent = 0; parent < 2; ++parent) {
                    const unsigned bit = 1U << (parent * 2 + target);
                    if (s.done & bit) continue;
                    State n = s;
                    n.held[target] = parent;
                    n.done |= bit; // Give local drain/snoop every advantage.
                    pending.push(n);
                    enabled = true;
                }
            } else {
                const unsigned parent = s.held[target];
                const unsigned allTargets = 3U << (parent * 2);
                if (!resumeNeedsHomeCompletion ||
                    (s.done & allTargets) == allTargets) {
                    State n = s;
                    n.held[target] = -1;
                    pending.push(n);
                    enabled = true;
                }
            }
        }
        const bool terminal = s.done == 15 &&
            s.held[0] == -1 && s.held[1] == -1;
        if (!enabled && !terminal) {
            ++deadlocks;
            std::cout << "DEADLOCK done=" << s.done
                      << " target0_parent=" << s.held[0]
                      << " target1_parent=" << s.held[1] << '\n';
        }
    }
    std::cout << "resume_requires_home=" << resumeNeedsHomeCompletion
              << " states=" << visited.size()
              << " deadlocks=" << deadlocks << '\n';
    return deadlocks;
}

int main()
{
    // This explicitly tests the lifetime decision. It makes no claim to prove
    // data, epoch, publication, native routing, or the production TC147 case.
    assert(explore(true) == 2);
    assert(explore(false) == 0);
}
