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
from typing import Iterable


Q1_TCS = (47, 48, 49, 110, 111, 117, 118, 119, *range(148, 160))
ACTION_NAME = {"drop": "Drop", "dup": "Duplicate", "delay": "Delay",
               "reorder": "Reorder"}

# Portable descriptions of the runtime selected by run_fault_qualification.py
# and tests/e2e/run_multi.sh.  Documentation generators consume these helpers;
# the executable matrix above remains the single source of case selection and
# fault rules.
TOPOLOGIES = {
    "1s": {"label": "3N1S（3 节点 × 每节点 1 socket）", "runner_flag": "--1s",
           "num_nodes": 3, "num_sockets": 1},
    "3n1s": {"label": "3N1S（3 节点 × 每节点 1 socket）", "runner_flag": "--3n1s",
             "num_nodes": 3, "num_sockets": 1},
    "3n2s": {"label": "3N2S（3 节点 × 每节点 2 sockets）", "runner_flag": "--3n2s",
             "num_nodes": 3, "num_sockets": 2},
    "8n2s": {"label": "8N2S（8 节点 × 每节点 2 sockets）", "runner_flag": "--8n2s",
             "num_nodes": 8, "num_sockets": 2},
    "16n1s": {"label": "16N1S（16 节点 × 每节点 1 socket）", "runner_flag": "--16n1s",
              "num_nodes": 16, "num_sockets": 1},
}

TCIDS = {
    47: "e2e_tc47_drop_clear", 48: "e2e_tc48_dup_inv_ack",
    49: "e2e_tc49_reorder_acks", 110: "e2e_tc110_drop_clear",
    111: "e2e_tc111_silent_upgrade_drop", 117: "e2e_tc117_clear_reorder",
    118: "e2e_tc118_mixed_fault", 119: "e2e_tc119_triple_fault",
    148: "e2e_tc148_fault_qualification",
    149: "e2e_tc149_upgrade_invalidate_fault_qualification",
    150: "e2e_tc149_upgrade_invalidate_fault_qualification",
    151: "e2e_tc149_upgrade_invalidate_fault_qualification",
    152: "e2e_tc149_upgrade_invalidate_fault_qualification",
    153: "e2e_tc153_recallresp_fault_qualification",
    154: "e2e_tc153_recallresp_fault_qualification",
    155: "e2e_tc153_recallresp_fault_qualification",
    156: "e2e_tc153_recallresp_fault_qualification",
    157: "e2e_tc149_upgrade_invalidate_fault_qualification",
    158: "e2e_tc149_upgrade_invalidate_fault_qualification",
    159: "e2e_tc149_upgrade_invalidate_fault_qualification",
    230: "e2e_ha_topology",
}

RUNTIME_DEFAULTS = {
    "cpu_model": "timing", "sequencer_max_outstanding": 0,
    "sequencer_source": "model-default", "ha_profile": "ubcc",
    "clear_profile": "ack", "metadata_bytes": 134217728,
    "l3_size": "256kB", "l3_assoc": 16,
    "sync_interval_ps": 2500, "link_latency_ps": 2500,
    "port_hwm": 8192,
    "l3_pressure_level": 0, "l3_pressure_target_lines": "",
    "l3_directory_pressure_lines": 0, "track_l3_occupancy": 0,
    # test_e2e.py effective defaults when run_multi does not add a TC profile.
    "silent_upgrade": 0, "direct_fwd": 0, "ubcc_batch_rs": 1,
}


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


def _workload_expectation(tc):
    if tc == 47:
        return "Node1/Node2 均读到 0x47AA0011"
    if tc == 48:
        return "Node0/1/2 最终值均为 0x48BB0022"
    if tc == 49:
        return "Node0/1/2 最终值均为 0x49CC0033"
    if tc == 110:
        return "至少 3 个 READ_VAL；三节点最终一致且属于 0x11000001/2/3"
    if tc == 111:
        return "所有读取各自 MATCH，且每个参与节点均以 0x1110BBB2 完成升级后收敛"
    if tc == 117:
        return "至少 2 个 READ_VAL 且全部 MATCH"
    if tc == 118:
        return "至少 2 个 READ_VAL 且全部 MATCH"
    if tc == 119:
        return "至少 3 个 READ_VAL 且全部 MATCH"
    if tc == 148:
        return "至少 32 个 READ_VAL 且全部 MATCH"
    if tc in (149, 150, 151, 152, 157, 158, 159):
        return "至少 32 个 READ_VAL 且全部 MATCH"
    if tc in (153, 154, 155, 156):
        return "至少 16 个 READ_VAL 且全部 MATCH"
    if tc == 230:
        return ("HAT03：拓扑参与者、无 L3 压力、逐 plane validation、精确 READ_VAL "
                "数量与 16-op guest timer 均通过")
    raise KeyError(tc)


def resolved_runtime(entry):
    """Return one self-contained, portable row matching the real runners."""
    topo = dict(TOPOLOGIES[entry["topology"]])
    nodes, sockets = topo["num_nodes"], topo["num_sockets"]
    sequencer = int(entry["env"].get("EP_SEQUENCER_MAX_OUTSTANDING", "0"))
    gem5_params = dict(RUNTIME_DEFAULTS)
    gem5_params.update({
        "num_nodes": nodes, "num_sockets": sockets,
        "sequencer_max_outstanding": sequencer,
        "sequencer_source": "runner-override" if sequencer else "model-default",
    })
    gem5_args = [
        "--node-id=<node>", f"--num-nodes={nodes}", f"--num-sockets={sockets}",
        "--workload=<run-dir>/workload.elf", "--l3-size=256kB", "--l3-assoc=16",
        "--ubcc_metadata_size=134217728", "--ha-profile=ubcc",
        "--clear-profile=ack", "--cpu-model=timing",
    ]
    if sequencer:
        gem5_args.append(f"--sequencer-max-outstanding={sequencer}")
    # Fault qualification TCs do not enter any run_multi protocol-profile arm.
    # These values are therefore test_e2e.py's actual effective defaults.
    gem5_args += ["effective:silent-upgrade=0", "effective:direct-fwd=0",
                  "effective:ubcc-batch-rs=1"]

    rules_text = ";".join(item["text"] for item in entry["rules"])
    ubio_directory_args = []  # All matrix TCs take run_multi's empty TC branch.
    ubio_common_args = [
        "--node=<node>", "--socket=<socket>", f"--num-sockets={sockets}",
        f"--num-nodes={nodes}", f"--fault-rules={rules_text}",
        "--metadata-dram-bytes=134217728",
    ]
    compile_args = ["-static", "-O0", "-g", f"-DNUM_NODES={nodes}",
                    f"-DNUM_SOCKETS={sockets}"]
    if entry["tc"] == 230:
        compile_args.append("-DHA_TOPOLOGY_SCENARIO=3")
    compile_args.append("-I<workload-dir>")
    compile_command = ["aarch64-linux-gnu-gcc", *compile_args,
                       "-o", "<run-dir>/workload.elf",
                       f"tests/e2e/workloads/{TCIDS[entry['tc']]}.c"]

    manifest = resolved_manifest(entry)
    effective_checks = {
        "workload": _workload_expectation(entry["tc"]),
        "strict_fault_trigger_action_counts": entry["tc"] != 230,
        "strict_buffered_delivery_counts": entry["tc"] != 230,
        "no_unexpected_fault_hits": entry["tc"] != 230,
        "stable_reqid_per_rule": entry["qualification"] == "Q2",
        "no_duplicate_commit": entry["qualification"] == "Q4",
        "no_extra_retries_after_recovery": False,
        "queue_drained": False,
        "peer_exit_contract": True,
        "note": ("TC230 的现有 verifier 验证 HAT03 工作负载，但不消费 fault manifest；"
                 "Q2 的 no_extra_retries_after_recovery 与 Q4 的 queue_drained "
                 "当前仅为 manifest 声明，未在 verifier 中实施。"),
    }
    return {
        "case_id": entry["id"], "qualification": entry["qualification"],
        "tc": entry["tc"], "tcid": f"TC{entry['tc']}",
        "workload_id": TCIDS[entry["tc"]], "topology": topo,
        "cpu_allocation": {
            "cpus": 32 if entry["exclusive"] else 8,
            "matrix_cpu_hint": entry["cpus"], "exclusive": entry["exclusive"],
            "runner_cpuset": "0-31" if entry["exclusive"] else "one of 0-7,8-15,16-23,24-31",
        },
        "runtime_env": {
            "EP_CPU_MODEL": "timing", "EP_SEQUENCER_MAX_OUTSTANDING": str(sequencer),
            "EP_HA_PROFILE": "ubcc", "OURCC_CLEAR_PROFILE": "ack",
            "UBCC_METADATA_SIZE": "134217728", "EP_L3_SIZE": "256kB",
            "EP_L3_ASSOC": "16", "EP_SYNC_INTERVAL_PS": "2500",
            "EP_LINK_LATENCY_PS": "2500", "EP_PORT_HWM": "8192",
            "EP_NSIM_MAX_PENDING": "65536", "L3_PRESSURE_LEVEL": "0",
            "L3_PRESSURE_TARGET_LINES": "", "L3_DIRECTORY_PRESSURE_LINES": "0",
            "EP_TRACK_L3_OCCUPANCY": "0",
        },
        "gem5": {"params": gem5_params, "args_per_node": gem5_args},
        "ubio": {"params": {
                     "num_nodes": nodes, "num_sockets": sockets,
                     "metadata_dram_bytes": 134217728,
                     "fault_rules": rules_text, "directory_args_per_tc": [],
                     "ha_profile": "ubcc", "clear_profile": "ack",
                     "sync_interval_ps": 2500, "link_latency_ps": 2500,
                     "port_hwm": 8192, "peer_exit_retry_ms": 100,
                     "peer_exit_quiesce_ms": 2000,
                     "peer_exit_delivery_budget_ms": 1000,
                     "trace_perf": "sample", "trace_perf_first_n": 500,
                     "trace_perf_max": 2000, "trace_perf_every": 0,
                     "ha_vi_only_args_passed": False,
                 },
                 "directory_args_per_tc": ubio_directory_args,
                 "args_per_node_socket": ubio_common_args},
        "networksim": {"params": {"max_pending": 65536}},
        "compile": {"compiler": "aarch64-linux-gnu-gcc", "args": compile_args,
                    "command_argv": compile_command,
                    "source": f"tests/e2e/workloads/{TCIDS[entry['tc']]}.c"},
        "fault_rules": rules_text, "fault_rule_records": entry["rules"],
        "verifier": {"manifest_checks": manifest["checks"],
                     "effective_checks": effective_checks},
        "tags": entry["tags"], "supported": entry["supported"],
        "reason": entry["reason"],
    }


def resolved_matrix():
    return [resolved_runtime(entry) for entry in MATRIX]


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
