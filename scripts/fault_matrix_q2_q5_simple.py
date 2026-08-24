#!/usr/bin/env python3
"""Small, portable Q2-Q5 fault matrix."""

import json


def build_matrix():
    # Suggested per-test upper bounds, not measured runtimes.
    timeout_3n1s = 600

    q2 = []
    for label, tc, message, src, dst, address in (
        ("clear", 148, "ClearReq", 0, 1, "0x10018014800"),
        ("upgrade", 149, "UpgradeReq", 0, 1, "0x10018014900"),
        ("invack", 149, "InvalidateAck", 1, 1, "0x10018014900"),
        ("recallresp", 153, "RecallResp", 0, 1, "0x10018015300"),
    ):
        for count in (2, 3):
            q2.append({
                "numnodes": 3,
                "numsockets": 1,
                "label": f"q2-{label}-drop{count}",
                "timeout_sec": timeout_3n1s,
                "fault_rule": (
                    f"tc{tc}_q2_{label}_drop_first_{count}:"
                    f"{message}:{src}:{dst}:{address}:drop::{count}"
                ),
            })

    q3 = [
        {
            "numnodes": 3,
            "numsockets": 1,
            "label": "q3-upgrade-response-ack",
            "timeout_sec": timeout_3n1s,
            "fault_rule": (
                "tc149_q3_upgrade_resp:UpgradeResp:1:0:0x10018014900:drop::1;"
                "tc149_q3_upgrade_ack:UpgradeAckNotify:1:0:0x10018014900:drop::1"
            ),
        },
        {
            "numnodes": 3,
            "numsockets": 1,
            "label": "q3-invalidate-request-ack",
            "timeout_sec": timeout_3n1s,
            "fault_rule": (
                "tc149_q3_inv_req:InvalidateReq:1:2:0x10018014900:drop::1;"
                "tc149_q3_inv_ack:InvalidateAck:2:1:0x10018014900:drop::1"
            ),
        },
        {
            "numnodes": 3,
            "numsockets": 1,
            "label": "q3-recall-request-response",
            "timeout_sec": timeout_3n1s,
            "fault_rule": (
                "tc153_q3_recall_req:RecallReq:1:0:0x10018015300:drop::1;"
                "tc153_q3_recall_resp:RecallResp:0:1:0x10018015300:drop::1"
            ),
        },
        {
            "numnodes": 3,
            "numsockets": 1,
            "label": "q3-clear-request-response",
            "timeout_sec": timeout_3n1s,
            "fault_rule": (
                "tc148_q3_clear_req:ClearReq:0:1:0x10018014800:drop::1;"
                "tc148_q3_clear_resp:ClearResp:1:0:0x10018014800:delay:20000:1"
            ),
        },
    ]

    tc148_rules = []
    index = 0
    for action, delay in (("drop", ""), ("dup", ""),
                          ("delay", "20000"), ("reorder", "100000")):
        for item in range(8):
            address = hex(0x10018014800 + index * 64)
            tc148_rules.append(
                f"tc148_{action}_{item}:ClearReq:0:1:{address}:"
                f"{action}:{delay}:1"
            )
            index += 1

    q4 = [
        {"numnodes": 3, "numsockets": 1, "label": "q4-tc148-32pa",
         "timeout_sec": timeout_3n1s,
         "fault_rule": ";".join(tc148_rules)},
        {"numnodes": 3, "numsockets": 1, "label": "q4-clear-burst-2",
         "timeout_sec": timeout_3n1s,
         "fault_rule": "tc148_q4_clear_burst2:ClearReq:0:1:0:drop::2"},
        {"numnodes": 3, "numsockets": 1, "label": "q4-clear-burst-3",
         "timeout_sec": timeout_3n1s,
         "fault_rule": "tc148_q4_clear_burst3:ClearReq:0:1:0:drop::3"},
        {"numnodes": 3, "numsockets": 1, "label": "q4-partial-ack-n1",
         "timeout_sec": timeout_3n1s,
         "fault_rule": "tc149_q4_partial_ack_n1:InvalidateAck:1:1:0:drop::4"},
        {"numnodes": 3, "numsockets": 1, "label": "q4-partial-ack-n2",
         "timeout_sec": timeout_3n1s,
         "fault_rule": "tc149_q4_partial_ack_n2:InvalidateAck:2:1:0:delay:20000:4"},
        {
            "numnodes": 3,
            "numsockets": 1,
            "label": "q4-multi-source-acks",
            "timeout_sec": timeout_3n1s,
            "fault_rule": (
                "tc149_q4_ack_n1:InvalidateAck:1:1:0:drop::2;"
                "tc149_q4_ack_n2:InvalidateAck:2:1:0:drop::2"
            ),
        },
        {"numnodes": 3, "numsockets": 1,
         "label": "q4-near-outstanding-upgrade",
         "timeout_sec": timeout_3n1s,
         "fault_rule": "tc149_q4_upgrade_near:UpgradeReq:0:1:0:drop::3"},
        {"numnodes": 3, "numsockets": 1,
         "label": "q4-near-outstanding-recall",
         "timeout_sec": timeout_3n1s,
         "fault_rule": "tc153_q4_recall_near:RecallResp:0:1:0:delay:20000:8"},
    ]

    q5 = []
    for numnodes, numsockets in ((3, 1), (3, 2), (8, 2), (16, 1)):
        topology = f"{numnodes}n{numsockets}s"
        timeout_sec = {
            (3, 1): 600,
            (3, 2): 900,
            (8, 2): 1500,
            (16, 1): 1800,
        }[numnodes, numsockets]
        writer_plane = numnodes * numsockets - 1
        writer_node = writer_plane // numsockets
        address = hex(0x10070000000 + writer_plane * 0x10000)

        for action in ("drop_req", "drop_ack", "delay_ack"):
            rules = []
            for node in range(numnodes):
                count = numsockets - (1 if node == writer_node else 0)
                if count == 0:
                    continue
                if action == "drop_req":
                    message, src, dst, fault, delay = (
                        "InvalidateReq", 0, node, "drop", "")
                elif action == "drop_ack":
                    message, src, dst, fault, delay = (
                        "InvalidateAck", node, 0, "drop", "")
                else:
                    message, src, dst, fault, delay = (
                        "InvalidateAck", node, 0, "delay", "20000")
                rules.append(
                    f"tc230_q5_{topology}_{action}_n{node}:"
                    f"{message}:{src}:{dst}:{address}:{fault}:{delay}:{count}"
                )
            q5.append({
                "numnodes": numnodes,
                "numsockets": numsockets,
                "label": f"q5-{topology}-{action}",
                "timeout_sec": timeout_sec,
                "fault_rule": ";".join(rules),
            })

    return {"q2": q2, "q3": q3, "q4": q4, "q5": q5}


if __name__ == "__main__":
    print(json.dumps(build_matrix(), indent=2))
