#!/usr/bin/env python3
"""Canonical Q1-Q5 physical fault-qualification matrix.

The matrix intentionally contains representative physical runs rather than a
Cartesian product.  The orchestration runner imports this module and writes a
resolved per-run verifier manifest, so rule generation and verification cannot
drift apart.
"""
from __future__ import annotations

import argparse
import json
from typing import Dict, Iterable, List


Q1_TCS = (47, 48, 49, 110, 111, 117, 118, 119, *range(148, 160))
ACTION_NAME = {"drop": "Drop", "dup": "Duplicate", "delay": "Delay",
               "reorder": "Reorder"}


def rule(name, message, src, dst, pa, action, count=1, delay=None):
    delay_field = "" if delay is None else str(delay)
    text = f"{name}:{message}:{src}:{dst}:{pa}:{action}:{delay_field}:{count}"
    return {"name": name, "message": message, "action": ACTION_NAME[action],
            "trigger_count": count, "delivery_count": count if action in
            ("delay", "reorder") else 0, "text": text}


def case(case_id, qualification, tc, topology="1s", rules=(), cpus=4,
         exclusive=False, env=None, tags=(), supported=True, reason=""):
    return {"id": case_id, "qualification": qualification, "tc": tc,
            "topology": topology, "rules": list(rules), "cpus": cpus,
            "exclusive": exclusive, "env": dict(env or {}), "tags": list(tags),
            "supported": supported, "reason": reason}


def q1_rules(tc):
    if tc == 47: return [rule("tc47_drop_clear", "ClearReq", 1, 0, "0", "drop")]
    if tc == 48: return [rule("tc48_dup_inv_ack", "InvalidateAck", 2, 0, "0", "dup")]
    if tc == 49: return [rule("tc49_reorder_inv_ack", "InvalidateAck", 1, 0, "0", "reorder", delay=100000)]
    if tc == 110: return [rule("tc110_drop_clear", "ClearReq", 1, 1, "0", "drop")]
    if tc == 111: return [rule("tc111_silent_upgrade_drop", "UpgradeReq", 1, 1, "0", "drop")]
    if tc == 117: return [rule("tc117_reorder_clear", "ClearReq", 0, 1, "0", "reorder", delay=100000)]
    if tc == 118:
        return [rule("tc118_drop", "ClearReq", 0, 1, "0x10018011800", "drop"),
                rule("tc118_delay", "ClearReq", 0, 1, "0x10018011900", "delay", delay=100000)]
    if tc == 119:
        return [rule("tc119_drop", "ClearReq", 0, 1, "0x10018011900", "drop"),
                rule("tc119_dup", "ClearReq", 0, 1, "0x10018011940", "dup"),
                rule("tc119_delay", "ClearReq", 0, 1, "0x10018011980", "delay", delay=100000)]
    if tc == 148:
        out = []
        index = 0
        for action in ("drop", "dup", "delay", "reorder"):
            for i in range(8):
                pa = hex(0x10018014800 + index * 64)
                out.append(rule(f"tc148_{action}_{i}", "ClearReq", 0, 1, pa,
                                action, delay=20000 if action == "delay" else
                                100000 if action == "reorder" else None))
                index += 1
        return out
    if tc == 149:
        return [rule(f"tc149_upgrade_drop_{i}", "UpgradeReq", 0, 1,
                     hex(0x10018014900 + i * 64), "drop") for i in range(8)]
    if tc in (150, 151, 152, 157):
        action = {150: "dup", 151: "delay", 152: "reorder", 157: "drop"}[tc]
        prefix = {150: "invack_dup", 151: "invack_delay", 152: "invack_reorder",
                  157: "invack_drop"}[tc]
        return [rule(f"tc{tc}_{prefix}_n{node}_{i}", "InvalidateAck", node, 1,
                     hex(0x10018014900 + i * 64), action,
                     delay=20000 if action == "delay" else
                     100000 if action == "reorder" else None)
                for node in (1, 2) for i in range(8)]
    if tc in (153, 154, 155, 156):
        action = {153: "dup", 154: "delay", 155: "reorder", 156: "drop"}[tc]
        prefix = {153: "recall_dup", 154: "recall_delay", 155: "recall_reorder",
                  156: "recall_drop"}[tc]
        return [rule(f"tc{tc}_{prefix}_{i}", "RecallResp", 0, 1,
                     hex(0x10018015300 + i * 64), action,
                     delay=20000 if action == "delay" else
                     100000 if action == "reorder" else None) for i in range(16)]
    if tc in (158, 159):
        message, prefix = (("UpgradeResp", "upgraderesp_drop") if tc == 158
                           else ("UpgradeAckNotify", "upgradeack_drop"))
        return [rule(f"tc{tc}_{prefix}_{i}", message, 1, 0,
                     hex(0x10018014900 + i * 64), "drop") for i in range(8)]
    raise KeyError(tc)


def topology_rules(topology, action):
    nodes, sockets = {"3n1s": (3, 1), "3n2s": (3, 2),
                      "8n2s": (8, 2), "16n1s": (16, 1)}[topology]
    writer_plane = nodes * sockets - 1
    writer_node = writer_plane // sockets
    message = "InvalidateReq" if action == "drop_req" else "InvalidateAck"
    direction = "dst" if action == "drop_req" else "src"
    fault_action = "delay" if action == "delay_ack" else "drop"
    out = []
    per_node_pa = {0: hex(0x10070000000 + writer_plane * 0x10000)}
    for node in range(nodes):
        count = sockets - (1 if node == writer_node else 0)
        if count == 0:
            continue
        src = 0 if direction == "dst" else node
        dst = node if direction == "dst" else 0
        pa = per_node_pa.setdefault(node, hex(0x10070000000 + writer_plane * 0x10000))
        out.append(rule(f"tc230_q5_{topology}_{action}_n{node}", message,
                        src, dst, pa, fault_action, count=count,
                        delay=20000 if fault_action == "delay" else None))
    return out


def build_matrix():
    cases = [case(f"q1-tc{tc}", "Q1", tc, rules=q1_rules(tc),
                  tags=("existing",)) for tc in Q1_TCS]
    repeated = (("clear", 148, "ClearReq", 0, 1, "0x10018014800"),
                ("upgrade", 149, "UpgradeReq", 0, 1, "0x10018014900"),
                ("invack", 149, "InvalidateAck", 1, 1, "0x10018014900"),
                ("recallresp", 153, "RecallResp", 0, 1, "0x10018015300"))
    for label, tc, msg, src, dst, pa in repeated:
        for count in (2, 3):
            name = f"tc{tc}_q2_{label}_drop_first_{count}"
            cases.append(case(f"q2-{label}-drop{count}", "Q2", tc,
                              rules=[rule(name, msg, src, dst, pa, "drop", count)],
                              tags=("repeated-loss", "stable-reqid")))
    combos = [
        ("upgrade-response-ack", 149,
         [rule("tc149_q3_upgrade_resp", "UpgradeResp", 1, 0, "0x10018014900", "drop"),
          rule("tc149_q3_upgrade_ack", "UpgradeAckNotify", 1, 0, "0x10018014900", "drop")]),
        ("invalidate-request-ack", 149,
         [rule("tc149_q3_inv_req", "InvalidateReq", 1, 2, "0x10018014900", "drop"),
          rule("tc149_q3_inv_ack", "InvalidateAck", 2, 1, "0x10018014900", "drop")]),
        ("recall-request-response", 153,
         [rule("tc153_q3_recall_req", "RecallReq", 1, 0, "0x10018015300", "drop"),
          rule("tc153_q3_recall_resp", "RecallResp", 0, 1, "0x10018015300", "drop")]),
        ("clear-request-response", 148,
         [rule("tc148_q3_clear_req", "ClearReq", 0, 1, "0x10018014800", "drop"),
          rule("tc148_q3_clear_resp", "ClearResp", 1, 0, "0x10018014800", "delay", delay=20000)]),
    ]
    cases += [case(f"q3-{name}", "Q3", tc, rules=rules,
                   tags=("combination",)) for name, tc, rules in combos]
    q4 = [
        ("tc148-32pa", 148, q1_rules(148), {}),
        ("clear-burst-2", 148, [rule("tc148_q4_clear_burst2", "ClearReq", 0, 1, "0", "drop", 2)], {}),
        ("clear-burst-3", 148, [rule("tc148_q4_clear_burst3", "ClearReq", 0, 1, "0", "drop", 3)], {}),
        ("partial-ack-n1", 149, [rule("tc149_q4_partial_ack_n1", "InvalidateAck", 1, 1, "0", "drop", 4)], {}),
        ("partial-ack-n2", 149, [rule("tc149_q4_partial_ack_n2", "InvalidateAck", 2, 1, "0", "delay", 4, 20000)], {}),
        ("multi-source-acks", 149, [rule("tc149_q4_ack_n1", "InvalidateAck", 1, 1, "0", "drop", 2), rule("tc149_q4_ack_n2", "InvalidateAck", 2, 1, "0", "drop", 2)], {}),
        ("near-outstanding-upgrade", 149, [rule("tc149_q4_upgrade_near", "UpgradeReq", 0, 1, "0", "drop", 3)], {"EP_SEQUENCER_MAX_OUTSTANDING": "16"}),
        ("near-outstanding-recall", 153, [rule("tc153_q4_recall_near", "RecallResp", 0, 1, "0", "delay", 8, 20000)], {"EP_SEQUENCER_MAX_OUTSTANDING": "16"}),
    ]
    cases += [case(f"q4-{name}", "Q4", tc, rules=rules, env=env,
                   tags=("burst", "queue-drain", "no-duplicate-commit"))
              for name, tc, rules, env in q4]
    for topology in ("3n1s", "3n2s", "8n2s", "16n1s"):
        for action in ("drop_req", "drop_ack", "delay_ack"):
            large = topology in ("8n2s", "16n1s")
            cases.append(case(f"q5-{topology}-{action}", "Q5", 230,
                              topology=topology, rules=topology_rules(topology, action),
                              cpus=32 if large else 8, exclusive=large,
                              tags=("topology", "node15" if topology == "16n1s" else
                                    "multi-socket" if topology.endswith("2s") else "node-aware")))
    assert len(cases) == 52
    return cases


MATRIX = build_matrix()


def resolved_manifest(entry):
    return {"schema": 1, "case_id": entry["id"], "qualification": entry["qualification"],
            "tc": entry["tc"], "topology": entry["topology"],
            "rules": [{k: r[k] for k in ("name", "message", "action",
                                          "trigger_count", "delivery_count")}
                      for r in entry["rules"]],
            "checks": {"no_unexpected_fault_hits": True,
                       "stable_reqid_per_rule": entry["qualification"] == "Q2",
                       "no_extra_retries_after_recovery": entry["qualification"] == "Q2",
                       "queue_drained": entry["qualification"] == "Q4",
                       "no_duplicate_commit": entry["qualification"] == "Q4"}}


def select(ids: Iterable[str] = (), qualifications: Iterable[str] = ()):
    ids, qualifications = set(ids), set(qualifications)
    return [c for c in MATRIX if (not ids or c["id"] in ids) and
            (not qualifications or c["qualification"] in qualifications)]


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--qualification", action="append", default=[])
    args = parser.parse_args()
    chosen = select(args.case, args.qualification)
    print(json.dumps(chosen, indent=2, sort_keys=True) if args.json else
          "\n".join(f"{c['id']}\t{c['qualification']}\tTC{c['tc']}\t{c['topology']}"
                    for c in chosen))
