#!/usr/bin/env python3
"""Compare remote TC98/TC134 process logs with the local parameter contract."""

import argparse
import json
import pathlib
import re
import sys

MARKER = "[PROCESS-MANIFEST]"
DEFAULT_CONTRACT = (pathlib.Path(__file__).resolve().parents[1] /
                    "configs/tc98_tc134_process_contracts.json")


def load_jsonl(path):
    records = []
    if not path:
        return records
    with pathlib.Path(path).open(encoding="utf-8", errors="replace") as source:
        for number, line in enumerate(source, 1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{number}: invalid JSONL: {exc}") from exc
    return records


def discover_launch(root, tc, explicit):
    if explicit:
        return load_jsonl(explicit)
    candidates = list(root.rglob(f"launch_commands_tc{tc}.jsonl"))
    # Remote collections are commonly flattened into a single directory.
    candidates += list(root.glob(f"*launch_commands_tc{tc}*.jsonl"))
    unique = sorted(set(candidates))
    if not unique:
        return []
    if len(unique) > 1:
        raise ValueError("multiple launch JSONL files found; pass --launch-jsonl")
    return load_jsonl(unique[0])


def load_process_manifests(root, tc):
    records = []
    for path in root.rglob("*"):
        if (not path.is_file() or
                path.suffix.lower() not in {"", ".log", ".txt", ".out", ".err"}):
            continue
        try:
            with path.open(encoding="utf-8", errors="replace") as source:
                for number, line in enumerate(source, 1):
                    pos = line.find(MARKER)
                    if pos < 0:
                        continue
                    payload = line[pos + len(MARKER):].strip()
                    try:
                        record = json.loads(payload)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"{path}:{number}: malformed process manifest: {exc}") from exc
                    record["_source"] = str(path)
                    if record.get("tc", tc) in (0, tc):
                        records.append(record)
        except OSError as exc:
            raise ValueError(f"cannot read {path}: {exc}") from exc
    return records


def option_values(argv, name, alias=()):
    names = (name,) + tuple(alias)
    values = []
    i = 0
    while i < len(argv):
        token = argv[i]
        matched = False
        for option in names:
            if token == option:
                values.append(argv[i + 1] if i + 1 < len(argv) else None)
                i += 1
                matched = True
                break
            if token.startswith(option + "="):
                values.append(token.split("=", 1)[1])
                matched = True
                break
        i += 1
        if matched:
            continue
    return values


class Audit:
    def __init__(self):
        self.errors = []

    def expect(self, condition, message):
        if not condition:
            self.errors.append(message)

    def equal(self, actual, expected, label):
        self.expect(actual == expected, f"{label}: expected={expected!r} actual={actual!r}")


def audit(args):
    root = pathlib.Path(args.log_root)
    if not root.is_dir():
        raise ValueError(f"log root is not a directory: {root}")
    launches = load_jsonl(args.launch_jsonl) if args.launch_jsonl else []
    manifests = load_process_manifests(root, args.tc)
    try:
        contract = json.loads(pathlib.Path(args.contract).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read process contract {args.contract}: {exc}") from exc
    if contract.get("schema_version") != 1:
        raise ValueError("process contract schema_version must be 1")
    topology = contract["topology"]
    common = contract["common"]
    check = Audit()

    check.expect(bool(manifests), "missing [PROCESS-MANIFEST] records")
    by_launch = {kind: [r for r in launches if r.get("component") == kind]
                 for kind in ("ubio", "gem5", "networksim")}
    by_process = {kind: [r for r in manifests if r.get("component") == kind]
                  for kind in ("ubio", "gem5-config", "networksim")}
    if launches:
        for kind, expected in (("ubio", topology["ubio_processes"]),
                               ("gem5", topology["gem5_processes"]),
                               ("networksim", topology["networksim_processes"])):
            check.equal(len(by_launch[kind]), expected, f"optional launch count {kind}")
    for kind, expected in (("ubio", topology["ubio_processes"]),
                           ("gem5-config", topology["gem5_processes"]),
                           ("networksim", topology["networksim_processes"])):
        check.equal(len(by_process[kind]), expected, f"process count {kind}")

    expected_planes = {(node, socket) for node in range(topology["num_nodes"])
                       for socket in range(topology["num_sockets"])}
    if launches:
        check.equal({(r.get("node"), r.get("socket")) for r in by_launch["ubio"]},
                    expected_planes, "optional UBIO launch identities")
    check.equal({(r.get("node"), r.get("socket")) for r in by_process["ubio"]},
                expected_planes, "UBIO effective identities")
    if launches:
        check.equal({r.get("node") for r in by_launch["gem5"]},
                    set(range(topology["num_nodes"])), "optional gem5 launch nodes")
    check.equal({r.get("node") for r in by_process["gem5-config"]},
                set(range(topology["num_nodes"])),
                "gem5 effective nodes")

    ubio_launch_by_id = {(r.get("node"), r.get("socket")): r
                         for r in by_launch["ubio"]}
    for record in by_process["ubio"]:
        launched = ubio_launch_by_id.get((record.get("node"), record.get("socket")))
        if launched:
            check.equal(record.get("argv"), launched.get("argv"),
                        f"UBIO actual argv {record.get('node')}:{record.get('socket')}")
    if by_launch["networksim"] and by_process["networksim"]:
        check.equal(by_process["networksim"][0].get("argv"),
                    by_launch["networksim"][0].get("argv"),
                    "networksim actual argv")
    gem5_launch_by_node = {r.get("node"): r for r in by_launch["gem5"]}
    for record in by_process["gem5-config"]:
        launched = gem5_launch_by_node.get(record.get("node"))
        if not launched:
            continue
        argv = launched.get("argv", [])
        config_index = next((i for i, token in enumerate(argv)
                             if token.endswith("test_e2e.py")), None)
        check.expect(config_index is not None,
                     f"gem5 node {record.get('node')} launch lacks test_e2e.py")
        if config_index is not None:
            check.equal(record.get("config_argv", record.get("argv")), argv[config_index:],
                        f"gem5 config actual argv node {record.get('node')}")

    for record in launches:
        check.equal(record.get("tc"), args.tc, "launch tc")
        check.equal(record.get("topology"), "8n2s", "launch topology")
        argv = record.get("argv", [])
        check.expect(not option_values(argv, "--fault-rules"),
                     f"fault override present in {record.get('component')} argv")
    for record in by_process["ubio"]:
        check.equal(record.get("num_nodes"), topology["num_nodes"], "UBIO num_nodes")
        check.equal(record.get("num_sockets"), topology["num_sockets"], "UBIO num_sockets")
        argv = record.get("argv", [])
        check.expect(not option_values(argv, "--fault-rules"),
                     f"fault rules present in UBIO {record.get('node')}:{record.get('socket')} argv")
        check.equal(record.get("home_controller"), common["ubio_home_controller"],
                    "UBIO home controller")
        check.equal(record.get("dram_delay_ps"), common["ubio_dram_delay_ps"],
                    "UBIO DRAM delay")
        check.equal(record.get("fault_rule_args"), common["ubio_fault_rule_args"],
                    "UBIO fault rule count")
        check.equal(record.get("blc_bytes"), common["ubio_blc_bytes"],
                    "UBIO BLC bytes")
        check.equal(record.get("desc_scratch_bytes"), common["ubio_desc_scratch_bytes"],
                    "UBIO descriptor scratch bytes")
        rd = record.get("resident_dir", {})
        check.equal(rd.get("pa_bits"), common["resident_pa_bits"], "UBIO PA bits")
        check.equal(rd.get("sharers_bits"), common["resident_sharers_bits"],
                    "UBIO sharers bits")
        check.equal(rd.get("epoch_bits"), common["resident_epoch_bits"],
                    "UBIO epoch bits")
    for record in by_process["gem5-config"]:
        check.expect(bool(record.get("process_argv")), "gem5 full process argv missing")
        check.expect(bool(record.get("config_argv")), "gem5 config argv missing")
        check.equal(record.get("num_nodes"), topology["num_nodes"], "gem5 num_nodes")
        check.equal(record.get("num_sockets"), topology["num_sockets"], "gem5 num_sockets")
        check.equal(record.get("build_nodes"), [record.get("node")],
                    "gem5 split build_nodes")
        check.equal(record.get("cpus_per_node"), topology["cpus_per_gem5_process"],
                    "gem5 cores per node")
        check.equal(record.get("process_cpu_count"), topology["cpus_per_gem5_process"],
                    "gem5 process CPU count")
        check.equal(record.get("unknown_args"), [], "gem5 unknown args")
        check.equal(record.get("ha_profile"), common["ha_profile"], "gem5 HA profile")
        check.equal(record.get("clear_profile"), common["clear_profile"], "gem5 Clear profile")
    for record in by_process["networksim"]:
        check.equal(record.get("num_nodes"), topology["num_nodes"], "networksim num_nodes")
        check.equal(record.get("num_sockets"), topology["num_sockets"], "networksim num_sockets")
        check.equal(record.get("max_pending"), common["networksim_max_pending"],
                    "networksim max_pending")
        check.equal(record.get("trace_all_forwarded"),
                    common["networksim_trace_all_forwarded"],
                    "networksim trace_all_forwarded")

    metadata = args.metadata_bytes if args.metadata_bytes is not None else common["metadata_bytes"]
    for record in by_process["ubio"]:
        check.equal(record.get("metadata_dram_bytes"), metadata, "UBIO metadata bytes")
    for record in by_process["gem5-config"]:
        check.equal(record.get("metadata_bytes"), metadata, "gem5 metadata bytes")

    for record in launches:
        env = record.get("env", {})
        check.expect(not env.get("UBCC_OPTS"), "UBCC_OPTS must be empty for audited runs")
        check.expect(not env.get("EP_GEM5_OPTS"),
                     "EP_GEM5_OPTS must be empty for audited runs")

    if args.formal:
        formal = common["formal"]
        for record in by_process["gem5-config"]:
            check.equal(record.get("cpu_model"), formal["cpu_model"], "formal CPU")
            check.equal(record.get("sequencer_max_outstanding"),
                        formal["sequencer_max_outstanding"],
                        "formal sequencer")
        for record in by_process["ubio"]:
            env = record.get("env", {})
            check.equal(env.get("EP_LINK_LATENCY_PS"), formal["link_latency_ps"],
                        "formal link latency")
            check.equal(env.get("EP_SYNC_INTERVAL_PS"), formal["sync_interval_ps"],
                        "formal sync interval")
            check.equal(env.get("EP_PORT_HWM"), formal["port_hwm"],
                        "formal port HWM")

    if args.tc == 98:
        expected_tc = contract["tc98"]
        for record in by_process["ubio"]:
            rd = record.get("resident_dir", {})
            for field in ("ways", "bloom_bytes", "sram_bytes", "set_bits"):
                check.equal(rd.get(field), expected_tc["ubio"][field], f"TC98 UBIO {field}")
            for field in ("overflow_policy", "batch_rs"):
                check.equal(record.get(field), expected_tc["ubio"][field], f"TC98 UBIO {field}")
            check.equal(str(record.get("schema", "")).lower(),
                        expected_tc["ubio"]["schema"], "TC98 schema")
        for record in by_process["gem5-config"]:
            check.equal(record.get("silent_upgrade", {}).get("effective"),
                        expected_tc["gem5"]["silent_upgrade"],
                        "TC98 gem5 silent_upgrade")
            check.equal(record.get("direct_fwd", {}).get("effective"),
                        expected_tc["gem5"]["direct_fwd"],
                        "TC98 gem5 direct_fwd")
            check.equal(record.get("batch_rs", {}).get("effective"),
                        expected_tc["gem5"]["batch_rs"],
                        "TC98 gem5 batch_rs")
    else:
        profile = args.profile
        expected_tc = contract["tc134"][profile]
        for record in by_process["ubio"]:
            rd = record.get("resident_dir", {})
            for field in ("bloom_bytes", "sram_bytes", "ways", "set_bits"):
                check.equal(rd.get(field), expected_tc["ubio"][field],
                            f"TC134 UBIO {field}")
            check.equal(record.get("overflow_policy"),
                        expected_tc["ubio"]["overflow_policy"], "TC134 UBIO policy")
            check.equal(record.get("batch_rs"), expected_tc["ubio"]["batch_rs"],
                        "TC134 UBIO batch_rs")
            check.equal(str(record.get("schema", "")).lower(),
                        expected_tc["ubio"]["schema"],
                        "TC134 UBIO schema")
        for record in by_process["gem5-config"]:
            check.equal(record.get("silent_upgrade", {}).get("effective"),
                        expected_tc["gem5"]["silent_upgrade"],
                        "TC134 gem5 silent_upgrade")
            check.equal(record.get("direct_fwd", {}).get("effective"),
                        expected_tc["gem5"]["direct_fwd"],
                        "TC134 gem5 direct_fwd")
            check.equal(record.get("batch_rs", {}).get("effective"),
                        expected_tc["gem5"]["batch_rs"],
                        "TC134 gem5 batch_rs")

        for record in by_process["ubio"]:
            argv = record.get("argv", [])
            for option in ("--bloom-bytes", "--sram-bytes", "--ways", "--set-bits",
                           "--dir-overflow-policy", "--batch-rs",
                           "--metadata-dram-bytes"):
                check.equal(len(option_values(argv, option)), 1,
                            f"TC134 unique UBIO option {option}")
        for record in by_process["gem5-config"]:
            argv = record.get("argv", [])
            for option in ("--silent-upgrade", "--direct-fwd", "--ubcc-batch-rs"):
                check.equal(len(option_values(argv, option)), 1,
                            f"TC134 unique gem5 option {option}")

    return check.errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("log_root", help="protocol LOG_BASE (nested or flat collection)")
    parser.add_argument("--launch-jsonl", help="optional explicit launch command JSONL")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT),
                        help="local process-parameter contract JSON")
    parser.add_argument("--tc", type=int, choices=(98, 134), required=True)
    parser.add_argument("--formal", action="store_true",
                        help="enforce TC98 formal O3/PDES/HWM contract")
    parser.add_argument("--profile", choices=("naive", "spill-noopt", "optimized"),
                        help="required for TC134")
    parser.add_argument("--metadata-bytes", type=int,
                        help="override metadata bytes from the local contract")
    args = parser.parse_args(argv)
    if args.tc == 134 and not args.profile:
        parser.error("TC134 requires --profile")
    try:
        errors = audit(args)
    except ValueError as exc:
        print(f"FAIL TC{args.tc}: {exc}")
        return 1
    if errors:
        print(f"FAIL TC{args.tc}: {len(errors)} mismatch(es)")
        for error in errors:
            print(f"- {error}")
        return 1
    suffix = f" profile={args.profile}" if args.profile else (" formal" if args.formal else "")
    evidence = ("remote process logs + explicit launcher evidence"
                if args.launch_jsonl else "remote process logs")
    print(f"PASS TC{args.tc}{suffix}: {evidence} match local 8n2s contract")
    return 0


if __name__ == "__main__":
    sys.exit(main())
