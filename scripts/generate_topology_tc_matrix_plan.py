#!/usr/bin/env python3
"""Generate a deterministic topology/TC execution plan without executing it.

This module only writes JSON/JSONL.  In particular, it does not import or call
subprocess and never starts Docker, a build, a workload, or a simulation.
"""

import argparse
import hashlib
import json
import pathlib


SCHEMA_VERSION = 1
IMAGE = "ubcc-dev:ubuntu20.04"
TOPOLOGIES = (
    ("2n1s", "--2n1s", 2, 1, "small", 7200, 4, "8g"),
    ("3n1s", "--3n1s", 3, 1, "small", 7200, 6, "12g"),
    ("3n2s", "--3n2s", 3, 2, "medium", 10800, 12, "24g"),
    ("8n1s", "--8n1s", 8, 1, "medium", 10800, 16, "32g"),
    ("8n2s", "--8n2s", 8, 2, "large", 21600, 32, "64g"),
    ("16n1s", "--16n1s", 16, 1, "large", 21600, 32, "64g"),
)
PORTABLE_TCS = tuple(range(142, 148))
METRIC3_TCS = tuple(range(228, 236))
PROFILES = ("naive", "spill-noopt", "optimized")
METRIC1_ROLES = ("naive", "spill", "ideal")
ARMS = ("ourcc", "ha-vi")
HOT_PER_PLANE = {142: 32, 143: 137, 144: 192, 145: 136, 146: 192, 147: 136}
PORTABLE_OPERATIONS_PER_PLANE = {142: 1024, 143: 2048, 144: 1024,
                                 145: 2048, 146: 2048, 147: 2048}
PORTABLE_END_TO_END_PHASE = {142: "db_oltp_end_to_end", 143: "db_btree_end_to_end",
                             144: "db_wal_end_to_end", 145: "faas_end_to_end",
                             146: "graph_end_to_end", 147: "feature_end_to_end"}
TARGET_LINES = 98304
NAIVE_CAPACITY = 65536


def json_bytes(value):
    return (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode()


def stable_token(value, length=12):
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:length]


def profile_environment(profile):
    if profile == "naive":
        return {
            "EP_PERF_PROFILE": "naive",
            "UBCC_POLICY": "naive",
            "UBCC_OPTS": "--dir-overflow-policy=naive",
            "EP_GEM5_OPTS": "--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0",
        }
    if profile == "spill-noopt":
        return {
            "EP_PERF_PROFILE": "spill-noopt",
            "UBCC_POLICY": "spill",
            "UBCC_OPTS": "--dir-overflow-policy=spill",
            "EP_GEM5_OPTS": "--silent-upgrade=0 --direct-fwd=0 --ubcc-batch-rs=0",
        }
    if profile == "optimized":
        return {
            "EP_PERF_PROFILE": "optimized",
            "UBCC_POLICY": "spill",
            "UBCC_OPTS": "--dir-overflow-policy=spill",
            "EP_GEM5_OPTS": "--silent-upgrade=1 --direct-fwd=0 --ubcc-batch-rs=1",
        }
    raise ValueError(profile)


def topology_records():
    records = []
    for name, flag, nodes, sockets, resource, timeout, cpus, memory in TOPOLOGIES:
        planes = nodes * sockets
        records.append({
            "id": name,
            "runner_flag": flag,
            "nodes": nodes,
            "sockets_per_node": sockets,
            "active_planes": planes,
            "expected_child_exit_count": nodes + planes + 1,
            "expected_children": {
                "gem5": nodes,
                "ubio": planes,
                "networksim": 1,
            },
            "resource_class": resource,
            "timeout_sec": timeout,
            "docker_resources": {"cpus": cpus, "memory": memory},
        })
    return records


def shell_atom(value):
    """Quote a command atom while retaining the two documented shell mounts."""
    if value in ('${WORKSPACE:?set WORKSPACE}', '${RESULT_ROOT:?set RESULT_ROOT}'):
        return '"' + value + '"'
    if value.startswith('${WORKSPACE:?set WORKSPACE}:'):
        return '"' + value + '"'
    if value.startswith('${RESULT_ROOT:?set RESULT_ROOT}:'):
        return '"' + value + '"'
    safe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_@%+=:,./-"
    if value and all(char in safe for char in value):
        return value
    return "'" + value.replace("'", "'\"'\"'") + "'"


def docker_command(job, topology, environment):
    env = {
        "E2E_RUN_ID": job["run_id"],
        "LOG_BASE": job["result_path"],
        "TIMEOUT_SEC": str(job["timeout_sec"]),
        "EP_SUPERVISOR_PROGRESS_STALL_SEC": str(
            min(1800, job["timeout_sec"] // 3)),
        "EP_CPU_MODEL": "o3",
        "EP_SEQUENCER_MAX_OUTSTANDING": "16",
        "EP_TRACE_PERF": "off",
        "EP_SUPERVISOR": "1",
        "RESULT_TIER": job["tier"],
        "QUALIFICATION_ID": job.get("qualification_id") or "none",
        "SOURCE_RESULT_TIER": job["source"]["result_tier"],
        "SOURCE_QUALIFICATION_ID": job["source"]["qualification_id"] or "none",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    env.update(environment)
    argv = [
        "docker", "run", "--rm", "--name", job["container_name"],
        "--network", "none",
        "--cpus", str(topology["docker_resources"]["cpus"]),
        "--memory", topology["docker_resources"]["memory"],
        "-v", "${WORKSPACE:?set WORKSPACE}:/workspace",
        "-v", "${RESULT_ROOT:?set RESULT_ROOT}:/results",
        "-w", "/workspace", IMAGE, "env",
    ]
    for key in sorted(env):
        argv.append(f"{key}={env[key]}")
    argv.extend(["bash", "tests/e2e/run_multi.sh", topology["runner_flag"], str(job["tc"])])
    return argv, " ".join(shell_atom(atom) for atom in argv)


def portable_environment(tc, topology, profile):
    pressure = TARGET_LINES - HOT_PER_PLANE[tc] * topology["active_planes"]
    flags = (
        f"-DPORTABLE_PRESSURE_LINES={pressure} "
        f"-DPORTABLE_TARGET_FOOTPRINT_LINES={TARGET_LINES} "
        f"-DPORTABLE_NAIVE_CAPACITY_LINES={NAIVE_CAPACITY} "
        "-DPORTABLE_PRESSURE_LEVEL_PCT=150 -DPORTABLE_BATCHES=32"
    )
    return {
        **profile_environment(profile),
        "PORTABLE_512K_DIR": "1",
        "EP_HA_PROFILE": "ubcc",
        "OURCC_CLEAR_PROFILE": "ack",
        "WORKLOAD_CFLAGS": flags,
    }


def portable_ideal_environment(tc, topology):
    environment = portable_environment(tc, topology, "spill-noopt")
    environment.update({
        "EP_PERF_PROFILE": "spill-noopt",
        "UBCC_POLICY": "spill",
        "UBCC_OPTS": ("--dir-overflow-policy=spill --bloom-bytes=61440 "
                      "--sram-bytes=2097152 --ways=32 --set-bits=0 "
                      "--allow-oversized-resident-dir-for-test --batch-rs=0"),
        "METRIC1_ROLE": "ideal",
    })
    return environment


def metric3_environment(arm, pair_seed):
    return {
        **profile_environment("spill-noopt"),
        "EP_HA_PROFILE": "ubcc" if arm == "ourcc" else "ha-vi",
        "OURCC_CLEAR_PROFILE": "lossless-oneway" if arm == "ourcc" else "ack",
        "METRIC3_L3_EXPERIMENT_MODE": "l3-only",
        "L3_DIRECTORY_PRESSURE_LINES": "0",
        "EP_L3_SIZE": "256kB",
        "EP_L3_ASSOC": "16",
        "METRIC3_L3_SEED": str(pair_seed),
        "EP_TRACK_L3_OCCUPANCY": "1",
        "HA_MAX_ACTIVE": "256",
        "HA_MAX_QUEUE": "8",
    }


def make_job(tier, topology, tc, identity, qualification_id, source, environment,
             extra=None):
    token = stable_token(identity)
    job_id = f"{tier}-{topology['id']}-tc{tc}-{token}"
    run_id = f"tp_{tier[0]}_{token}_tc{tc}"
    job = {
        "job_id": job_id,
        "tier": tier,
        "topology": topology["id"],
        "topology_flag": topology["runner_flag"],
        "tc": tc,
        "qualification_id": qualification_id,
        "source": source,
        "depends_on": ([qualification_id] if tier == "formal" else []),
        "run_id": run_id,
        "container_name": f"topoplan-{token}",
        "result_path": f"/results/{tier}/cases/{job_id}",
        "timeout_sec": topology["timeout_sec"],
        "resource_class": topology["resource_class"],
        "expected_child_exit_count": topology["expected_child_exit_count"],
        "support": "supported",
    }
    if extra:
        job.update(extra)
    argv, command = docker_command(job, topology, environment)
    job["command_argv"] = argv
    job["command"] = command
    return job


def pair_order(topology_index, tc_index, pair):
    return "AB" if (topology_index + tc_index + pair - 1) % 2 == 0 else "BA"


def metric_annotations(topology, tc):
    annotations = {
        "experiment_mode": "l3-only",
        "dual_socket_ring_semantics": (
            "current workload keeps socket fixed while the node index advances; "
            "dual-socket runs therefore form one node ring per socket, not one all-plane ring"
            if topology["sockets_per_node"] == 2 else "not dual-socket"
        ),
    }
    if tc == 232:
        planes = topology["active_planes"]
        annotations["primary_value_formula"] = {
            "formula": f"({planes}/{planes + 1})*hot_key_read + "
                       f"(1/{planes + 1})*hot_key_write",
            "read_operations": 16 * planes,
            "write_operations": 16,
            "read_weight": planes / (planes + 1),
            "write_weight": 1 / (planes + 1),
            "note": ("the frozen 2/3 read + 1/3 write formula applies only to 2n1s; "
                     "extension topologies use their actual global operation mix"),
        }
    if tc == 234:
        annotations["limitation"] = (
            "serialized-token workload exposes a serial dependency chain; it is useful "
            "for end-to-end latency but weak evidence for parallel throughput scaling"
        )
    return annotations


def build_plan(formal_repetitions, metric3_pairs):
    topologies = topology_records()
    smoke_jobs = []
    qualification_jobs = []
    formal_jobs = []
    qualification_sets = []

    # One representative portable case and one representative paired Metric3 case
    # per topology.  This is intentionally a subset, not a hidden qualification run.
    smoke_metric_tcs = (228, 232, 234, 235, 230, 233)
    for topo_index, topology in enumerate(topologies):
        tc = PORTABLE_TCS[topo_index]
        identity = ["smoke", topology["id"], tc, "spill-noopt", 1]
        smoke_jobs.append(make_job(
            "smoke", topology, tc, identity, None,
            {"result_tier": "none", "qualification_id": None},
            portable_environment(tc, topology, "spill-noopt"),
            {"family": "portable", "profile": "spill-noopt", "repetition": 1,
             "ha_vi_support": "not planned; central-home portable workload defaults to UBCC"},
        ))
        tc = smoke_metric_tcs[topo_index]
        tc_index = METRIC3_TCS.index(tc)
        order = pair_order(topo_index, tc_index, 1)
        sequence = ARMS if order == "AB" else tuple(reversed(ARMS))
        pair_id = f"smoke-{topology['id']}-tc{tc}-pair01"
        for sequence_index, arm in enumerate(sequence, 1):
            identity = ["smoke", topology["id"], tc, 1, arm]
            smoke_jobs.append(make_job(
                "smoke", topology, tc, identity, None,
                {"result_tier": "none", "qualification_id": None},
                metric3_environment(arm, 228235 + topo_index * 1000 + tc),
                {"family": "metric3", "arm": arm, "pair": 1,
                 "pair_id": pair_id, "pair_order": order,
                 "sequence_index": sequence_index, **metric_annotations(topology, tc)},
            ))

    # Qualification is the complete topology x TC cross product.  Metric3 slots
    # are paired and therefore contain exactly two executable arm jobs.
    for topo_index, topology in enumerate(topologies):
        for tc in PORTABLE_TCS:
            qid = f"qualification-{topology['id']}-tc{tc}-ubcc"
            identity = ["qualification", topology["id"], tc, "spill-noopt", 1]
            job = make_job(
                "qualification", topology, tc, identity, qid,
                {"result_tier": "qualification", "qualification_id": qid},
                portable_environment(tc, topology, "spill-noopt"),
                {"family": "portable", "profile": "spill-noopt", "repetition": 1,
                 "ha_vi_support": "HA-VI unsupported beyond 3n1s without striped-home changes; UBCC-only by default"},
            )
            qualification_jobs.append(job)
            qualification_sets.append({
                "qualification_id": qid,
                "gate_type": "all_members_pass",
                "topology": topology["id"], "tc": tc,
                "member_job_ids": [job["job_id"]],
                "expected_member_count": 1,
            })
        for tc_index, tc in enumerate(METRIC3_TCS):
            qid = f"qualification-{topology['id']}-tc{tc}-paired"
            pair_id = f"qualification-{topology['id']}-tc{tc}-pair01"
            order = pair_order(topo_index, tc_index, 1)
            sequence = ARMS if order == "AB" else tuple(reversed(ARMS))
            members = []
            for sequence_index, arm in enumerate(sequence, 1):
                identity = ["qualification", topology["id"], tc, 1, arm]
                job = make_job(
                    "qualification", topology, tc, identity, qid,
                    {"result_tier": "qualification", "qualification_id": qid},
                    metric3_environment(arm, 228235 + topo_index * 1000 + tc),
                    {"family": "metric3", "arm": arm, "pair": 1,
                     "pair_id": pair_id, "pair_order": order,
                     "sequence_index": sequence_index, **metric_annotations(topology, tc)},
                )
                qualification_jobs.append(job)
                members.append(job["job_id"])
            qualification_sets.append({
                "qualification_id": qid,
                "gate_type": "paired_all_arms_pass",
                "topology": topology["id"], "tc": tc,
                "pair_id": pair_id, "pair_order": order,
                "member_job_ids": members,
                "expected_member_count": 2,
                "required_arms": list(ARMS),
            })

    # Formal portable jobs are UBCC-only and cover all three profiles.
    for topology in topologies:
        for tc in PORTABLE_TCS:
            qid = f"qualification-{topology['id']}-tc{tc}-ubcc"
            for repetition in range(1, formal_repetitions + 1):
                for profile in PROFILES:
                    identity = ["formal", topology["id"], tc, profile, repetition]
                    formal_jobs.append(make_job(
                        "formal", topology, tc, identity, qid,
                        {"result_tier": "qualification", "qualification_id": qid},
                        portable_environment(tc, topology, profile),
                        {"family": "portable", "profile": profile,
                         "repetition": f"r{repetition}",
                         "ha_vi_support": "not planned: central-home by default; HA-VI beyond 3n1s requires striped-home changes"},
                    ))
            # Metric1 extension qualification needs a counterfactual IdealDir
            # role in addition to the ordinary three application profiles.
            for repetition in range(1, formal_repetitions + 1):
                identity = ["formal", topology["id"], tc, "ideal", repetition]
                formal_jobs.append(make_job(
                    "formal", topology, tc, identity, qid,
                    {"result_tier": "qualification", "qualification_id": qid},
                    portable_ideal_environment(tc, topology),
                    {"family": "portable", "profile": "spill-noopt",
                     "metric1_role": "ideal", "repetition": f"r{repetition}",
                     "ha_vi_support": ("HA-VI not planned: Metric1 counterfactual IdealDir "
                                       "is UBCC-only and retains the central-home workload")},
                ))

    # Formal Metric3 jobs use deterministic AB/BA pairs in L3-only mode.
    for topo_index, topology in enumerate(topologies):
        for tc_index, tc in enumerate(METRIC3_TCS):
            qid = f"qualification-{topology['id']}-tc{tc}-paired"
            for pair in range(1, metric3_pairs + 1):
                order = pair_order(topo_index, tc_index, pair)
                sequence = ARMS if order == "AB" else tuple(reversed(ARMS))
                pair_id = f"formal-{topology['id']}-tc{tc}-pair{pair:02d}"
                pair_seed = 228235 + topo_index * 100000 + pair * 1000 + tc
                for sequence_index, arm in enumerate(sequence, 1):
                    identity = ["formal", topology["id"], tc, pair, arm]
                    formal_jobs.append(make_job(
                        "formal", topology, tc, identity, qid,
                        {"result_tier": "qualification", "qualification_id": qid},
                        metric3_environment(arm, pair_seed),
                         {"family": "metric3", "arm": arm, "pair": f"p{pair}",
                          "repetition": f"p{pair}",
                         "pair_id": pair_id, "pair_order": order,
                         "sequence_index": sequence_index, **metric_annotations(topology, tc)},
                    ))

    all_jobs = smoke_jobs + qualification_jobs + formal_jobs
    if len({job["job_id"] for job in all_jobs}) != len(all_jobs):
        raise AssertionError("duplicate deterministic job ID")
    limitations = {
        "TC142-TC147": (
            "central-home by default; HA-VI paired execution is unsupported beyond 3n1s "
            "unless the workload changes to striped-home, so this plan is UBCC-only"
        ),
        "TC228-TC235": (
            "ourcc/ha-vi pairs are planned on every topology in L3-only mode; current "
            "TC228/229/233 dual-socket workloads form one same-socket node ring per socket"
        ),
        "TC232": ("2n1s keeps the frozen 2/3 read plus 1/3 write value; other topologies "
                  "use read=P/(P+1), write=1/(P+1) for P active planes"),
        "TC234": "serialized token is weak evidence for parallel throughput scaling",
    }
    extractor_qualifications = []
    metric1_candidates = []
    for topology in topologies:
        planes = topology["active_planes"]
        repetitions = [f"r{value}" for value in range(1, formal_repetitions + 1)]
        extractor_qualifications.append({
            "id": f"m2-portable-{topology['id']}", "metric": 2,
            "coordinates": [{"tc": tc, "topology": topology["id"],
                             "phase": PORTABLE_END_TO_END_PHASE[tc],
                             "kind": "timer", "reduction": "aggregate",
                             "expected_nodes": list(range(planes)),
                             "expected_count": PORTABLE_OPERATIONS_PER_PLANE[tc] * planes}
                            for tc in PORTABLE_TCS],
            "repetitions": repetitions, "profiles": list(PROFILES),
            "baseline_profile": "naive", "result_profile": "optimized",
            "thresholds": {"baseline_applicable_min_ns": 500.0,
                           "reduction_pct_min": 10.0},
        })
        extractor_qualifications.append({
            "id": f"m1-portable-{topology['id']}", "metric": 1,
            "coordinates": [{"tc": tc, "topology": topology["id"],
                             "home_node": 0, "home_socket": 0}
                            for tc in PORTABLE_TCS],
            "repetitions": repetitions, "ideal_min_capacity": 102656,
        })
        extractor_qualifications.append({
            "id": f"m3-paired-{topology['id']}", "metric": 3,
            "mode": "paired", "topologies": [topology["id"]],
            "testcases": list(METRIC3_TCS), "arms": list(ARMS),
            "pairs": [f"p{pair}" for pair in range(1, metric3_pairs + 1)],
        })
        metric1_candidates.append({
            "id": f"m1-portable-{topology['id']}", "metric": 1,
            "coordinates": [{"tc": tc, "topology": topology["id"],
                             "home_node": 0, "home_socket": 0}
                            for tc in PORTABLE_TCS],
            "repetitions": repetitions,
            "required_roles": list(METRIC1_ROLES),
            "status": "registered: formal matrix includes explicit ideal role; qualification still requires completed Outer evidence",
        })

    counts = {
        "smoke_jobs": len(smoke_jobs),
        "qualification_jobs": len(qualification_jobs),
        "qualification_cross_product_slots": len(TOPOLOGIES) * (len(PORTABLE_TCS) + len(METRIC3_TCS)),
        "qualification_portable_jobs": len(TOPOLOGIES) * len(PORTABLE_TCS),
        "qualification_metric3_pairs": len(TOPOLOGIES) * len(METRIC3_TCS),
        "qualification_metric3_arm_jobs": len(TOPOLOGIES) * len(METRIC3_TCS) * 2,
        "formal_jobs": len(formal_jobs),
        "formal_portable_jobs": (len(TOPOLOGIES) * len(PORTABLE_TCS) *
                                 (len(PROFILES) + 1) * formal_repetitions),
        "formal_metric3_pairs": len(TOPOLOGIES) * len(METRIC3_TCS) * metric3_pairs,
        "formal_metric3_arm_jobs": len(TOPOLOGIES) * len(METRIC3_TCS) * metric3_pairs * 2,
        "all_jobs": len(all_jobs),
    }
    common = {
        "schema_version": SCHEMA_VERSION,
        "generator": "scripts/generate_topology_tc_matrix_plan.py",
        "non_executing": True,
        "docker_contract": {
            "image": IMAGE, "network": "none",
            "workspace_host_env": "WORKSPACE", "result_host_env": "RESULT_ROOT",
        },
        "topologies": topologies,
        "testcases": {"portable": list(PORTABLE_TCS), "metric3": list(METRIC3_TCS)},
        "limitations": limitations,
        "configuration": {"formal_repetitions": formal_repetitions,
                          "metric3_pairs": metric3_pairs},
    }
    return {
        "execution_plan.json": {**common, "counts": counts, "jobs": all_jobs},
        "smoke_manifest.json": {**common, "tier": "smoke", "job_count": len(smoke_jobs),
                                "jobs": smoke_jobs},
        "qualification_manifest.json": {
            **common, "tier": "qualification", "job_count": len(qualification_jobs),
            "cross_product_slot_count": counts["qualification_cross_product_slots"],
            "jobs": qualification_jobs,
        },
        "formal_manifest.json": {**common, "tier": "formal", "job_count": len(formal_jobs),
                                 "jobs": formal_jobs},
        "qualification_sets.json": {
            **common, "gate_count": len(qualification_sets), "sets": qualification_sets,
            "extractor_requirements": {"qualification_sets": extractor_qualifications},
            "metric1_candidate_templates": metric1_candidates,
        },
        "commands.jsonl": all_jobs,
    }


def write_plan(output_dir, formal_repetitions, metric3_pairs):
    output_dir.mkdir(parents=True, exist_ok=True)
    plan = build_plan(formal_repetitions, metric3_pairs)
    for name, value in plan.items():
        path = output_dir / name
        if name.endswith(".jsonl"):
            data = b"".join(
                (json.dumps({"job_id": job["job_id"], "tier": job["tier"],
                             "command": job["command"]}, sort_keys=True) + "\n").encode()
                for job in value
            )
        else:
            data = json_bytes(value)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_bytes(data)
        temporary.replace(path)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=pathlib.Path)
    parser.add_argument("--formal-repetitions", type=int, default=3)
    parser.add_argument("--metric3-pairs", type=int, default=5)
    args = parser.parse_args(argv)
    if args.formal_repetitions < 1:
        parser.error("--formal-repetitions must be positive")
    if args.metric3_pairs < 1:
        parser.error("--metric3-pairs must be positive")
    write_plan(args.output_dir, args.formal_repetitions, args.metric3_pairs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
