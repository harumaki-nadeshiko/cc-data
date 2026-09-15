#!/usr/bin/env python3
"""Independent OFFLINE abstractions; imports no production header or runtime.

Finite exploration excludes infinite scheduler stuttering, network loss and
unbounded retries. Model success is NEVER evidence that production Park passed.
"""
import collections
import json
import pathlib


def explore(initial, successors, terminal, invariant):
    pending = collections.deque([initial])
    paths = {initial: []}
    dead = []
    edges = 0
    terminals = 0
    while pending:
        state = pending.popleft()
        invariant(state)
        nxt = list(successors(state))
        edges += len(nxt)
        if terminal(state):
            terminals += 1
        elif not nxt:
            dead.append(paths[state])
        for label, target in nxt:
            if target not in paths:
                paths[target] = paths[state] + [label]
                pending.append(target)
    return dict(states=len(paths), edges=edges, terminals=terminals,
                deadlocks=len(dead), counterexample=dead[:1])


def native_callbacks():
    # Two native HN sockets, same K. Native admission exists BEFORE EP descriptor.
    # Each socket: admitted, EP attached, beat0, beat1, nativeDone,
    # Park, localComplete, ResumeAck, retired. All legal interleavings explored.
    # Done may precede Park; retained completion identity handles late callback.
    names = ('nativeAdmissionBeforeEP', 'EPAttach', 'oldBeat0', 'oldBeat1',
             'nativeDone', 'Park', 'localComplete', 'ResumeAck', 'retire')
    full = (1 << len(names)) - 1
    def successors(state):
        for socket, bits in enumerate(state):
            for event in range(len(names)):
                bit = 1 << event
                if bits & bit:
                    continue
                required = {0: 0, 1: 1, 2: 3, 3: 7, 4: 15,
                            5: 3, 6: (1 << 5) | 15,
                            7: (1 << 6), 8: (1 << 4) | (1 << 7)}[event]
                if bits & required != required:
                    continue
                other = list(state)
                other[socket] |= bit
                yield 'S%d:%s' % (socket, names[event]), tuple(other)
    def invariant(state):
        for bits in state:
            if bits & (1 << 8):
                assert bits & (1 << 4) and bits & (1 << 7)
            if bits & (1 << 6):
                assert bits & 15 == 15  # no partial-old-data publication
            # Native admission identity retained even with no EP slot attached.
            if bits & 1 and not bits & 2:
                assert not bits & ~1
    result = explore((0, 0), successors, lambda s: s == (full, full), invariant)
    assert result['deadlocks'] == 0 and result['terminals'] == 1
    return result


def resources(local_release):
    # 2 lines/parents x 2 nodes x 2 sockets. One contended native admission
    # resource/node. 31 occupied fixed entries + this slot = physical 32.
    # Background 31 entries cannot drain until this cohort makes progress;
    # this is an explicit worst-case resource abstraction, not runtime layout.
    # Four children: pre-EP native admission, EP attachment, socket callbacks
    # in either order, local release, global barrier, final retire.
    # State: stages, socket masks, node holders, published-parent mask.
    initial = ((0,) * 4, (0,) * 4, (-1, -1), 0)
    def successors(state):
        stages, masks, held, published = state
        for child in range(4):
            parent, node = divmod(child, 2)
            stage = stages[child]
            choices = []
            if stage == 0 and held[node] == -1:
                choices.append(('nativeAdmissionBeforeEP', 1, 0, child))
            if stage == 1:
                choices.append(('EPAttach', 2, 0, child))
            if stage == 2:
                for sock in range(2):
                    if not masks[child] & (1 << sock):
                        choices.append(('localCallbackS%d' % sock, 2,
                                        masks[child] | (1 << sock), child))
                if masks[child] == 3:
                    choices.append(('localComplete', 3, 3, child))
            if stage == 3:
                all_local = all(stages[parent * 2 + n] >= 3 for n in range(2))
                if local_release or all_local:
                    choices.append(('localRelease', 4, 3, -1))
            if stage == 4 and published & (1 << parent):
                choices.append(('ResumeAckRetire', 5, 3, held[node]))
            for name, nextstage, mask, holder in choices:
                a, b, c = list(stages), list(masks), list(held)
                a[child], b[child], c[node] = nextstage, mask, holder
                yield 'K%dN%d:%s' % (parent, node, name), (tuple(a), tuple(b), tuple(c), published)
        for parent in range(2):
            if not published & (1 << parent) and all(stages[parent * 2 + n] >= 3 for n in range(2)):
                yield 'K%d:globalPublicationBarrier' % parent, (stages, masks, held, published | (1 << parent))
    def invariant(state):
        stages, masks, held, published = state
        for node in range(2):
            owners = [i for i in range(4) if i % 2 == node and 1 <= stages[i] <= 3]
            assert len(owners) <= 1
            assert 31 + len(owners) <= 32
            assert held[node] == (owners[0] if owners else -1)
        for parent in range(2):
            if published & (1 << parent):
                assert all(stages[parent * 2 + n] >= 3 and masks[parent * 2 + n] == 3 for n in range(2))
    result = explore(initial, successors, lambda s: s[0] == (5,) * 4, invariant)
    assert (result['deadlocks'] == 0) if local_release else (result['deadlocks'] > 0)
    return result


if __name__ == '__main__':
    assert pathlib.Path('/.dockerenv').exists(), 'Docker only'
    print(json.dumps({'scope': 'OFFLINE_MODEL_ONLY_NOT_RUNTIME',
                      'native_callbacks': native_callbacks(),
                      'resource32_old_global_release': resources(False),
                      'resource32_new_local_release': resources(True)}, indent=2))
