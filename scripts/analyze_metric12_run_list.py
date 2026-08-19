#!/usr/bin/env python3
"""Analyze performance metrics 1 and 2 from a list of run dictionaries.

Each input dictionary has exactly three fields:

    simulator_log_dir: directory containing UBIO/verifier/child logs
    workload_output_dir: directory containing nodeN/simout_nN files
    feature: caller-defined configuration label

Subclass Metric12RunListAnalyzer and override parse_feature() when the remote
feature string uses a site-specific format.
"""

import argparse
import csv
import gzip
import hashlib
import importlib
import importlib.util
import json
import pathlib
import re
import statistics
import sys
from dataclasses import asdict, dataclass, field


if __name__ == "__main__":
    # Let custom parsers import the public classes without loading a second
    # module instance whose class identity would differ from __main__.
    sys.modules.setdefault("analyze_metric12_run_list", sys.modules[__name__])


PROFILES = ("naive", "spill-noopt", "optimized")
PROFILE_ALIASES = {
    "naive": "naive",
    "spill": "spill-noopt",
    "spill-noopt": "spill-noopt",
    "spill_noopt": "spill-noopt",
    "optimized": "optimized",
    "spill-opt": "optimized",
    "spill_opt": "optimized",
}
TARGET_ALIASES = {
    "1": "target1",
    "metric1": "target1",
    "target1": "target1",
    "2": "target2",
    "metric2": "target2",
    "target2": "target2",
}
TARGET2_PHASES = {
    "TC135": "preserved_sharer_first_load",
    "TC136": "preserved_owner_store_complete",
    "TC137": "new_requester_first_load",
    "TC138": "dirty_owner_handoff_store",
    "TC139": "mixed_batch_16ops",
    "TC140": "cross_l2_owner_store",
    "TC217": "ha10_catalog_batch_16ops",
}
TARGET2_TOPOLOGIES = {
    "TC135": "3n1s",
    "TC136": "3n1s",
    "TC137": "3n1s",
    "TC138": "3n1s",
    "TC139": "3n1s",
    "TC140": "3n1s",
    "TC217": "2n1s",
}
TARGET2_MARKER_CONTRACT = {
    "TC135": {"node": 1, "samples": 24},
    "TC136": {"node": 1, "samples": 24},
    "TC137": {"node": 2, "samples": 24},
    "TC138": {"node": 2, "samples": 24},
    "TC139": {"node": 1, "samples": 16},
    "TC140": {"node": 0, "samples": 24},
    "TC217": {"node": 1, "samples": 8},
}
DEFAULT_THRESHOLDS = {
    "target1_capacity_ratio_min": 1.5,
    "target1_max_extra_cycles": 50.0,
    "target1_contract_clock_hz": 2.0e9,
    "target2_applicable_naive_mean_ns": 500.0,
    "target2_equal_weight_reduction_min_pct": 10.0,
}

STATE_RE = re.compile(r"\[UBCC-STATE\].*capacity=(\d+).*policy=([A-Za-z0-9_-]+)")
TIMER_RE = re.compile(
    r"\[GUEST-TIMER\] node=(\d+) phase=(\S+) operations=(\d+) "
    r"counter_ticks=(\d+) counter_frequency_hz=(\d+) "
    r"source=(\S+) unit=(\S+)")
LATENCY_RE = re.compile(
    r"\[PERF-LATENCY\] node=(\d+) phase=(\S+) samples=(\d+) "
    r"min=(\d+) p50=(\d+) p95=(\d+) p99=(\d+) max=(\d+) mean=(\d+) "
    r"counter_frequency_hz=(\d+) source=(\S+) unit=(\S+)")
NODE_DIR_RE = re.compile(r"node(\d+)$")
SIMOUT_RE = re.compile(r"simout_n(\d+)(?:\.gz)?$")
UBIO_DIR_RE = re.compile(r"ubio(?:_tc(\d+))?_n(\d+)_s(\d+)$")
TOPOLOGY_RE = re.compile(r"^(\d+)n(\d+)s$")


class Metric12Error(Exception):
    """Base error for invalid input or evidence."""


class InputSchemaError(Metric12Error):
    pass


class FeatureParseError(Metric12Error):
    pass


class EvidenceError(Metric12Error):
    pass


class MatrixError(Metric12Error):
    pass


@dataclass(frozen=True)
class RunFeature:
    target: str
    round_id: int
    case: str
    topology: str
    profile: str
    phase: str = ""
    home_node: int = 0
    home_socket: int = 0
    timer_nodes: tuple = (1, 2)
    extras: dict = field(default_factory=dict)


@dataclass(frozen=True)
class RunRecord:
    index: int
    simulator_log_dir: pathlib.Path
    workload_output_dir: pathlib.Path
    feature_text: str
    feature: RunFeature


@dataclass(frozen=True)
class CorrectnessEvidence:
    verifier_log: pathlib.Path
    verifier_pass: bool
    child_exit_files: tuple
    child_exit_values: tuple
    expected_child_count: int


@dataclass(frozen=True)
class ResolvedRun:
    record: RunRecord
    simout_by_node: dict
    ubio_logs: tuple
    correctness: CorrectnessEvidence


def open_text(path):
    if path.suffix == ".gz":
        return gzip.open(path, "rt", errors="replace")
    return path.open(errors="replace")


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def describe(values):
    mean = statistics.mean(values)
    stdev = statistics.stdev(values) if len(values) > 1 else 0.0
    return {
        "count": len(values),
        "min": min(values),
        "median": statistics.median(values),
        "max": max(values),
        "mean": mean,
        "stdev": stdev,
        "cv_pct": stdev / abs(mean) * 100.0 if mean else 0.0,
    }


class Metric12RunListAnalyzer:
    """Directory-based metric 1/2 analyzer with an overridable feature parser."""

    INPUT_KEYS = {"simulator_log_dir", "workload_output_dir", "feature"}

    def __init__(self, runs, *, min_rounds=3, thresholds=None,
                 hash_inputs=False, base_dir=None):
        self.runs = runs
        self.min_rounds = int(min_rounds)
        self.thresholds = dict(DEFAULT_THRESHOLDS)
        if thresholds:
            self.thresholds.update(thresholds)
        self.hash_inputs = bool(hash_inputs)
        self.base_dir = pathlib.Path(base_dir or pathlib.Path.cwd()).expanduser().resolve()
        if self.min_rounds < 1:
            raise InputSchemaError("min_rounds must be at least 1")

    def parse_feature(self, feature):
        """Parse one feature string.

        Override this method for remote labels such as:

            PERF-B-TC132-3n1s-spill

        The override must return RunFeature. The default prototype accepts a
        semicolon-separated key=value string, for example:

            target=target1;round=1;case=TC131;topology=8n1s;profile=naive
        """
        if not isinstance(feature, str) or not feature.strip():
            raise FeatureParseError("feature must be a non-empty string")
        values = {}
        for atom in feature.split(";"):
            atom = atom.strip()
            if not atom:
                continue
            if "=" not in atom:
                raise FeatureParseError(
                    "default feature parser expects semicolon-separated key=value; "
                    "override parse_feature() for site-specific labels")
            key, value = (part.strip() for part in atom.split("=", 1))
            if not key or not value or key in values:
                raise FeatureParseError(f"invalid or duplicate feature field: {atom!r}")
            values[key] = value
        required = {"target", "round", "case", "topology", "profile"}
        missing = required - set(values)
        if missing:
            raise FeatureParseError(f"feature is missing fields: {sorted(missing)}")
        timer_nodes = tuple(int(value) for value in
                            values.get("timer_nodes", "1,2").split(",") if value)
        known = required | {"phase", "home_node", "home_socket", "timer_nodes"}
        return RunFeature(
            target=values["target"],
            round_id=int(values["round"]),
            case=values["case"],
            topology=values["topology"],
            profile=values["profile"],
            phase=values.get("phase", ""),
            home_node=int(values.get("home_node", "0")),
            home_socket=int(values.get("home_socket", "0")),
            timer_nodes=timer_nodes,
            extras={key: value for key, value in values.items() if key not in known},
        )

    def _normalize_feature(self, feature, index, raw_text):
        if not isinstance(feature, RunFeature):
            raise FeatureParseError(
                f"run[{index}] parse_feature() must return RunFeature, got "
                f"{type(feature).__name__}")
        target_key = str(feature.target).strip().lower()
        try:
            target = TARGET_ALIASES[target_key]
        except KeyError as error:
            raise FeatureParseError(
                f"run[{index}] unknown target {feature.target!r}") from error
        profile_key = str(feature.profile).strip().lower()
        try:
            profile = PROFILE_ALIASES[profile_key]
        except KeyError as error:
            raise FeatureParseError(
                f"run[{index}] unknown profile {feature.profile!r}") from error
        try:
            round_id = int(feature.round_id)
        except (TypeError, ValueError) as error:
            raise FeatureParseError(f"run[{index}] round must be an integer") from error
        if round_id < 1:
            raise FeatureParseError(f"run[{index}] round must be positive")
        match = re.fullmatch(r"(?:TC)?(\d+)", str(feature.case).strip(), re.I)
        if not match:
            raise FeatureParseError(f"run[{index}] invalid case {feature.case!r}")
        case = f"TC{int(match.group(1))}"
        topology = str(feature.topology).strip().lower()
        if not TOPOLOGY_RE.fullmatch(topology):
            raise FeatureParseError(
                f"run[{index}] topology must look like 8n1s, got {topology!r}")
        phase = str(feature.phase).strip()
        if target == "target1":
            if case != "TC131":
                raise FeatureParseError(
                    f"run[{index}] official target1 requires TC131, got {case}")
            if topology != "8n1s":
                raise FeatureParseError(
                    f"run[{index}] official target1 requires topology 8n1s, "
                    f"got {topology}")
            phase = phase or "post_pressure_catalog_reuse"
            if not feature.timer_nodes:
                raise FeatureParseError(f"run[{index}] target1 requires timer_nodes")
        else:
            expected = TARGET2_PHASES.get(case)
            if expected is None:
                raise FeatureParseError(
                    f"run[{index}] official target2 case must be one of "
                    f"{sorted(TARGET2_PHASES)}, got {case}")
            expected_topology = TARGET2_TOPOLOGIES[case]
            if topology != expected_topology:
                raise FeatureParseError(
                    f"run[{index}] {case} requires topology {expected_topology}, "
                    f"got {topology}")
            phase = phase or expected
            if phase != expected:
                raise FeatureParseError(
                    f"run[{index}] {case} phase must be {expected!r}, got {phase!r}")
        return RunFeature(
            target=target,
            round_id=round_id,
            case=case,
            topology=topology,
            profile=profile,
            phase=phase,
            home_node=int(feature.home_node),
            home_socket=int(feature.home_socket),
            timer_nodes=tuple(int(node) for node in feature.timer_nodes),
            extras=dict(feature.extras),
        )

    def normalize_inputs(self):
        if not isinstance(self.runs, list):
            raise InputSchemaError("top-level input must be a list")
        records = []
        for index, raw in enumerate(self.runs):
            if not isinstance(raw, dict):
                raise InputSchemaError(f"run[{index}] must be a dict")
            if set(raw) != self.INPUT_KEYS:
                raise InputSchemaError(
                    f"run[{index}] keys must be exactly {sorted(self.INPUT_KEYS)}, "
                    f"got {sorted(raw)}")
            feature_text = raw["feature"]
            parsed = self.parse_feature(feature_text)
            feature = self._normalize_feature(parsed, index, feature_text)
            simulator_dir = pathlib.Path(raw["simulator_log_dir"]).expanduser()
            workload_dir = pathlib.Path(raw["workload_output_dir"]).expanduser()
            if not simulator_dir.is_absolute():
                simulator_dir = self.base_dir / simulator_dir
            if not workload_dir.is_absolute():
                workload_dir = self.base_dir / workload_dir
            simulator_dir = simulator_dir.resolve()
            workload_dir = workload_dir.resolve()
            if not simulator_dir.is_dir():
                raise InputSchemaError(
                    f"run[{index}] simulator_log_dir is not a directory: {simulator_dir}")
            if not workload_dir.is_dir():
                raise InputSchemaError(
                    f"run[{index}] workload_output_dir is not a directory: {workload_dir}")
            records.append(RunRecord(index, simulator_dir, workload_dir,
                                     feature_text, feature))
        if not records:
            raise InputSchemaError("input run list is empty")
        return tuple(records)

    def discover_workload_logs(self, record):
        by_node = {}
        for path in record.workload_output_dir.rglob("simout_n*"):
            if not path.is_file():
                continue
            file_match = SIMOUT_RE.fullmatch(path.name)
            dir_match = NODE_DIR_RE.fullmatch(path.parent.name)
            if not file_match or not dir_match:
                continue
            file_node = int(file_match.group(1))
            dir_node = int(dir_match.group(1))
            if file_node != dir_node:
                raise EvidenceError(
                    f"run[{record.index}] node mismatch: {path.parent.name}/{path.name}")
            if file_node in by_node:
                raise EvidenceError(
                    f"run[{record.index}] duplicate simout for node {file_node}: "
                    f"{by_node[file_node]} and {path}")
            by_node[file_node] = path.resolve()
        if not by_node:
            raise EvidenceError(
                f"run[{record.index}] no node*/simout_n* files below "
                f"{record.workload_output_dir}")
        return dict(sorted(by_node.items()))

    def discover_ubio_logs(self, record):
        feature = record.feature
        case_number = int(feature.case[2:])
        current_dirs = set()
        legacy_dirs = set()
        for path in record.simulator_log_dir.rglob("ubio*_n*_s*"):
            if not path.is_dir():
                continue
            match = UBIO_DIR_RE.fullmatch(path.name)
            if not match:
                continue
            tc_text, node_text, socket_text = match.groups()
            if (int(node_text), int(socket_text)) != (
                    feature.home_node, feature.home_socket):
                continue
            if tc_text is None:
                legacy_dirs.add(path.resolve())
            elif int(tc_text) == case_number:
                current_dirs.add(path.resolve())
        directories = current_dirs or legacy_dirs
        if len(directories) != 1:
            raise EvidenceError(
                f"run[{record.index}] expected one home UBIO directory for "
                f"TC{case_number} n{feature.home_node}s{feature.home_socket}, "
                f"found {sorted(map(str, directories))}")
        directory = next(iter(directories))
        logs = tuple(path.resolve() for path in
                     (directory / "stdout.log", directory / "stderr.log")
                     if path.is_file())
        if not logs:
            raise EvidenceError(
                f"run[{record.index}] home UBIO directory has no stdout/stderr: "
                f"{directory}")
        return logs

    def discover_correctness(self, record):
        case_number = record.feature.case[2:]
        verifier_logs = sorted(
            record.simulator_log_dir.rglob(f"verify_tc{case_number}.log"))
        if len(verifier_logs) != 1:
            raise EvidenceError(
                f"run[{record.index}] expected one verify_tc{case_number}.log, "
                f"found {verifier_logs}")
        verifier = verifier_logs[0].resolve()
        verifier_lines = [line.strip() for line in
                          verifier.read_text(errors="replace").splitlines()
                          if line.strip()]
        passed = bool(verifier_lines) and verifier_lines[-1] == (
            f">>> TC{case_number} PASSED <<<")
        child_dirs = sorted(
            path.resolve() for path in
            record.simulator_log_dir.rglob(f"child_status_tc{case_number}")
            if path.is_dir())
        if len(child_dirs) != 1:
            raise EvidenceError(
                f"run[{record.index}] expected one child_status_tc{case_number} "
                f"directory, found {child_dirs}")
        child_dir = child_dirs[0]
        match = TOPOLOGY_RE.fullmatch(record.feature.topology)
        nodes, sockets = map(int, match.groups())
        expected_names = {f"gem5_node{node}.exit" for node in range(nodes)}
        expected_names.update(
            f"ubio_n{node}_s{socket}.exit"
            for node in range(nodes) for socket in range(sockets))
        expected_names.add("networksim.exit")
        actual = {path.name: path.resolve() for path in child_dir.glob("*.exit")
                  if path.is_file()}
        if set(actual) != expected_names:
            raise EvidenceError(
                f"run[{record.index}] child status files differ: "
                f"missing={sorted(expected_names - set(actual))} "
                f"extra={sorted(set(actual) - expected_names)}")
        exits = tuple(actual[name] for name in sorted(actual))
        expected = len(expected_names)
        values = tuple(path.read_text(errors="replace").strip() for path in exits)
        evidence = CorrectnessEvidence(
            verifier_log=verifier,
            verifier_pass=passed,
            child_exit_files=exits,
            child_exit_values=values,
            expected_child_count=expected,
        )
        if not passed:
            raise EvidenceError(f"run[{record.index}] verifier did not PASS")
        if len(exits) != expected:
            raise EvidenceError(
                f"run[{record.index}] expected {expected} child exits, got {len(exits)}")
        if any(value != "0" for value in values):
            raise EvidenceError(
                f"run[{record.index}] nonzero child exits: "
                f"{dict(zip(map(str, exits), values))}")
        return evidence

    def resolve_runs(self):
        resolved = []
        seen = set()
        for record in self.normalize_inputs():
            feature = record.feature
            key = ((feature.target, feature.round_id, feature.profile)
                   if feature.target == "target1" else
                   (feature.target, feature.round_id, feature.case, feature.profile))
            if key in seen:
                raise MatrixError(f"duplicate normalized run key: {key}")
            seen.add(key)
            simouts = self.discover_workload_logs(record)
            ubio = self.discover_ubio_logs(record) if feature.target == "target1" else ()
            correctness = self.discover_correctness(record)
            resolved.append(ResolvedRun(record, simouts, ubio, correctness))
        return tuple(resolved)

    @staticmethod
    def _parse_capacity(paths):
        capacity = 0
        policies = set()
        exact_live = None
        for path in paths:
            with open_text(path) as stream:
                for line in stream:
                    state = STATE_RE.search(line)
                    if state:
                        capacity = max(capacity, int(state.group(1)))
                        policies.add(state.group(2))
                    if "[UBCC-STATS]" not in line or "{" not in line:
                        continue
                    try:
                        payload = json.loads(line[line.index("{"):])
                    except json.JSONDecodeError:
                        continue
                    if payload.get("residentCapacity") is not None:
                        capacity = max(capacity, int(payload["residentCapacity"]))
                    if int(payload.get("h64ExactLiveKnown", 0)) == 1:
                        exact_live = max(exact_live or 0,
                                         int(payload.get("h64ExactLiveCount", 0)))
        if not capacity:
            raise EvidenceError("no UBCC resident capacity marker in home UBIO log")
        if len(policies) != 1:
            raise EvidenceError(f"expected one UBCC policy, found {sorted(policies)}")
        policy = policies.pop()
        if policy == "naive":
            effective = capacity
        else:
            if exact_live is None:
                raise EvidenceError("spill UBIO log has no validated H64 exact-live")
            effective = max(capacity, exact_live)
        return {
            "policy": policy,
            "resident_capacity": capacity,
            "h64_exact_live": exact_live,
            "effective_unique": effective,
        }

    @staticmethod
    def _timer_records(paths, phase):
        records = []
        for path in paths:
            with open_text(path) as stream:
                for line in stream:
                    match = TIMER_RE.search(line)
                    if not match or match.group(2) != phase:
                        continue
                    operations = int(match.group(3))
                    ticks = int(match.group(4))
                    frequency = int(match.group(5))
                    if (operations <= 0 or ticks <= 0 or frequency <= 0 or
                            match.group(6) != "arm_cntvct_el0" or
                            match.group(7) != "counter_ticks"):
                        raise EvidenceError(f"invalid GUEST-TIMER in {path}")
                    records.append({
                        "file": str(path),
                        "node": int(match.group(1)),
                        "phase": phase,
                        "operations": operations,
                        "ticks": ticks,
                        "frequency_hz": frequency,
                        "source": match.group(6),
                        "mean_ns_per_operation": ticks * 1.0e9 / frequency / operations,
                    })
        return records

    @staticmethod
    def _latency_record(paths, phase):
        records = []
        for path in paths:
            with open_text(path) as stream:
                for line in stream:
                    match = LATENCY_RE.search(line)
                    if not match or match.group(2) != phase:
                        continue
                    samples = int(match.group(3))
                    ticks = {
                        "min": int(match.group(4)),
                        "p50": int(match.group(5)),
                        "p95": int(match.group(6)),
                        "p99": int(match.group(7)),
                        "max": int(match.group(8)),
                        "mean": int(match.group(9)),
                    }
                    frequency = int(match.group(10))
                    ordered = [ticks[key] for key in ("min", "p50", "p95", "p99", "max")]
                    if (samples <= 0 or ticks["min"] <= 0 or frequency <= 0 or
                            match.group(11) != "arm_cntvct_el0" or
                            match.group(12) != "counter_ticks" or
                            ordered != sorted(ordered) or
                            not ticks["min"] <= ticks["mean"] <= ticks["max"]):
                        raise EvidenceError(f"invalid PERF-LATENCY in {path}")
                    records.append({
                        "file": str(path),
                        "node": int(match.group(1)),
                        "phase": phase,
                        "samples": samples,
                        "frequency_hz": frequency,
                        "source": match.group(11),
                        "ticks": ticks,
                        "ns": {key: value * 1.0e9 / frequency
                               for key, value in ticks.items()},
                    })
        if len(records) != 1:
            raise EvidenceError(
                f"expected exactly one PERF-LATENCY phase={phase!r}, got {len(records)}")
        return records[0]

    def _validate_matrix(self, resolved):
        target1 = {(
            run.record.feature.round_id,
            run.record.feature.profile): run for run in resolved
            if run.record.feature.target == "target1"}
        target2 = {(
            run.record.feature.round_id,
            run.record.feature.case,
            run.record.feature.profile): run for run in resolved
            if run.record.feature.target == "target2"}
        if not target1 or not target2:
            raise MatrixError("run list must contain target1 and target2 runs")
        t1_rounds = sorted({key[0] for key in target1})
        t2_rounds = sorted({key[0] for key in target2})
        if len(t1_rounds) < self.min_rounds or len(t2_rounds) < self.min_rounds:
            raise MatrixError(
                f"requires at least {self.min_rounds} rounds; "
                f"target1={t1_rounds}, target2={t2_rounds}")
        if t1_rounds != list(range(t1_rounds[0], t1_rounds[-1] + 1)):
            raise MatrixError(f"target1 rounds are not contiguous: {t1_rounds}")
        if t2_rounds != list(range(t2_rounds[0], t2_rounds[-1] + 1)):
            raise MatrixError(f"target2 rounds are not contiguous: {t2_rounds}")
        if t1_rounds != t2_rounds:
            raise MatrixError(f"target1 rounds {t1_rounds} differ from target2 {t2_rounds}")
        for round_id in t1_rounds:
            for profile in PROFILES:
                if (round_id, profile) not in target1:
                    raise MatrixError(f"missing target1 run {(round_id, profile)}")
            for case in TARGET2_PHASES:
                for profile in PROFILES:
                    if (round_id, case, profile) not in target2:
                        raise MatrixError(
                            f"missing target2 run {(round_id, case, profile)}")
        extra_cases = sorted({key[1] for key in target2} - set(TARGET2_PHASES))
        if extra_cases:
            raise MatrixError(f"unexpected target2 cases: {extra_cases}")
        return target1, target2, t1_rounds

    def _input_metadata(self, paths):
        output = []
        for path in sorted(set(paths), key=str):
            item = {"path": str(path), "size_bytes": path.stat().st_size}
            if self.hash_inputs:
                item["sha256"] = sha256(path)
            output.append(item)
        return output

    def analyze(self):
        resolved = self.resolve_runs()
        target1, target2, rounds = self._validate_matrix(resolved)
        thresholds = self.thresholds
        report = {
            "schema_version": 1,
            "input_schema": "list-of-three-field-run-dicts",
            "rounds": rounds,
            "thresholds": thresholds,
            "target1": {"rounds": {}, "statistics": {}},
            "target2": {"rounds": {}, "case_statistics": {}, "statistics": {}},
            "correctness_gate": {"status": "PASS"},
            "resolved_runs": [],
        }
        all_paths = []
        for run in resolved:
            all_paths.extend(run.simout_by_node.values())
            all_paths.extend(run.ubio_logs)
            all_paths.append(run.correctness.verifier_log)
            all_paths.extend(run.correctness.child_exit_files)
            report["resolved_runs"].append({
                "input_index": run.record.index,
                "feature_text": run.record.feature_text,
                "feature": asdict(run.record.feature),
                "simulator_log_dir": str(run.record.simulator_log_dir),
                "workload_output_dir": str(run.record.workload_output_dir),
                "simout_logs": {str(node): str(path)
                                for node, path in run.simout_by_node.items()},
                "ubio_logs": [str(path) for path in run.ubio_logs],
                "verifier_log": str(run.correctness.verifier_log),
                "child_exit_count": len(run.correctness.child_exit_files),
            })
        report["inputs"] = self._input_metadata(all_paths)

        ratios, deltas_ns, deltas_cycles = [], [], []
        for round_id in rounds:
            round_report = {}
            for profile in PROFILES:
                run = target1[(round_id, profile)]
                feature = run.record.feature
                timer_paths = []
                for node in feature.timer_nodes:
                    if node not in run.simout_by_node:
                        raise EvidenceError(
                            f"target1 round {round_id} {profile} lacks simout node {node}")
                    timer_paths.append(run.simout_by_node[node])
                timers = self._timer_records(timer_paths, feature.phase)
                counts = {node: 0 for node in feature.timer_nodes}
                for timer in timers:
                    if timer["node"] in counts:
                        counts[timer["node"]] += 1
                if any(count != 1 for count in counts.values()) or len(timers) != len(counts):
                    raise EvidenceError(
                        f"target1 round {round_id} {profile} requires exactly one "
                        f"timer for nodes {feature.timer_nodes}, got {counts}")
                frequencies = {timer["frequency_hz"] for timer in timers}
                if len(frequencies) != 1:
                    raise EvidenceError("target1 timer frequencies differ")
                round_report[profile] = {
                    "coverage": self._parse_capacity(run.ubio_logs),
                    "guest_timers": timers,
                    "guest_mean_ns_per_operation": statistics.mean(
                        timer["mean_ns_per_operation"] for timer in timers),
                }
            if round_report["naive"]["coverage"]["policy"] != "naive":
                raise EvidenceError(f"target1 round {round_id} naive policy mismatch")
            for profile in ("spill-noopt", "optimized"):
                if round_report[profile]["coverage"]["policy"] != "spill":
                    raise EvidenceError(
                        f"target1 round {round_id} {profile} policy mismatch")
            ratio = (round_report["spill-noopt"]["coverage"]["effective_unique"] /
                     round_report["naive"]["coverage"]["effective_unique"])
            delta_ns = (round_report["spill-noopt"]["guest_mean_ns_per_operation"] -
                        round_report["naive"]["guest_mean_ns_per_operation"])
            delta_cycles = (delta_ns * thresholds["target1_contract_clock_hz"] / 1.0e9)
            round_report["comparison"] = {
                "capacity_ratio": ratio,
                "capacity_increase_pct": (ratio - 1.0) * 100.0,
                "guest_delta_ns_per_operation": delta_ns,
                "guest_delta_cycles": delta_cycles,
                "capacity_pass": ratio >= thresholds["target1_capacity_ratio_min"],
                "latency_pass": delta_cycles < thresholds["target1_max_extra_cycles"],
            }
            report["target1"]["rounds"][str(round_id)] = round_report
            ratios.append(ratio)
            deltas_ns.append(delta_ns)
            deltas_cycles.append(delta_cycles)
        report["target1"]["statistics"] = {
            "capacity_ratio": describe(ratios),
            "guest_delta_ns_per_operation": describe(deltas_ns),
            "guest_delta_cycles": describe(deltas_cycles),
            "pass": (all(value >= thresholds["target1_capacity_ratio_min"]
                         for value in ratios) and
                     all(value < thresholds["target1_max_extra_cycles"]
                         for value in deltas_cycles)),
        }

        per_case = {case: {profile: [] for profile in PROFILES}
                    for case in TARGET2_PHASES}
        per_case_reductions = {case: [] for case in TARGET2_PHASES}
        applicable_sets, equal_weight, csv_rows = [], [], []
        for round_id in rounds:
            round_report = {"cases": {}}
            applicable, reductions = [], []
            for case in TARGET2_PHASES:
                case_report = {}
                for profile in PROFILES:
                    run = target2[(round_id, case, profile)]
                    latency = self._latency_record(
                        tuple(run.simout_by_node.values()), run.record.feature.phase)
                    contract = TARGET2_MARKER_CONTRACT[case]
                    if (latency["node"] != contract["node"] or
                            latency["samples"] != contract["samples"]):
                        raise EvidenceError(
                            f"target2 round {round_id} {case} {profile} marker "
                            f"requires node={contract['node']} samples={contract['samples']}, "
                            f"got node={latency['node']} samples={latency['samples']}")
                    case_report[profile] = {"latency": latency}
                    per_case[case][profile].append(latency["ns"]["mean"])
                    csv_rows.append({
                        "round": round_id,
                        "case": case,
                        "profile": profile,
                        "phase": latency["phase"],
                        "samples": latency["samples"],
                        "mean_ns": latency["ns"]["mean"],
                        "p50_ns": latency["ns"]["p50"],
                        "p95_ns": latency["ns"]["p95"],
                        "p99_ns": latency["ns"]["p99"],
                        "max_ns": latency["ns"]["max"],
                    })
                naive = case_report["naive"]["latency"]["ns"]["mean"]
                spill = case_report["spill-noopt"]["latency"]["ns"]["mean"]
                optimized = case_report["optimized"]["latency"]["ns"]["mean"]
                if naive <= 0:
                    raise EvidenceError(
                        f"target2 round {round_id} {case} naive mean must be positive")
                reduction = (naive - optimized) / naive * 100.0
                is_applicable = naive >= thresholds["target2_applicable_naive_mean_ns"]
                case_report["comparison"] = {
                    "naive_mean_ns": naive,
                    "spill_noopt_mean_ns": spill,
                    "optimized_mean_ns": optimized,
                    "applicable": is_applicable,
                    "spill_noopt_reduction_pct": (naive - spill) / naive * 100.0,
                    "optimized_reduction_pct": reduction,
                }
                round_report["cases"][case] = case_report
                per_case_reductions[case].append(reduction)
                if is_applicable:
                    applicable.append(case)
                    reductions.append(reduction)
            if not applicable:
                raise EvidenceError(f"target2 round {round_id} has no applicable cases")
            round_report["applicable_cases"] = applicable
            round_report["equal_weight_mean_reduction_pct"] = statistics.mean(reductions)
            round_report["pass"] = (
                round_report["equal_weight_mean_reduction_pct"] >=
                thresholds["target2_equal_weight_reduction_min_pct"])
            report["target2"]["rounds"][str(round_id)] = round_report
            applicable_sets.append(tuple(applicable))
            equal_weight.append(round_report["equal_weight_mean_reduction_pct"])
        for case in TARGET2_PHASES:
            report["target2"]["case_statistics"][case] = {
                "profile_mean_ns": {
                    profile: describe(per_case[case][profile]) for profile in PROFILES},
                "optimized_reduction_pct": describe(per_case_reductions[case]),
            }
        report["target2"]["statistics"] = {
            "applicable_set_stable": len(set(applicable_sets)) == 1,
            "applicable_cases": list(applicable_sets[0]),
            "equal_weight_mean_reduction_pct": describe(equal_weight),
            "pass": (len(set(applicable_sets)) == 1 and
                     all(value >= thresholds["target2_equal_weight_reduction_min_pct"]
                         for value in equal_weight)),
        }
        report["overall_pass"] = (
            report["target1"]["statistics"]["pass"] and
            report["target2"]["statistics"]["pass"])
        report["csv_rows"] = csv_rows
        return report

    @staticmethod
    def render_markdown(report):
        target1 = report["target1"]["statistics"]
        target2 = report["target2"]["statistics"]
        lines = [
            "# Metric 1/2 Run-List Analysis", "",
            f"- Overall: {'PASS' if report['overall_pass'] else 'FAIL'}",
            f"- Metric 1: {'PASS' if target1['pass'] else 'FAIL'}",
            f"- Metric 2: {'PASS' if target2['pass'] else 'FAIL'}",
            f"- Correctness gate: {report['correctness_gate']['status']}", "",
            "## Metric 1", "",
            f"- Capacity ratio: {target1['capacity_ratio']['mean']:.6f}",
            f"- Guest delta: {target1['guest_delta_ns_per_operation']['mean']:.6f} ns/op",
            f"- Delta cycles: {target1['guest_delta_cycles']['mean']:.6f}", "",
            "## Metric 2", "",
            f"- Applicable cases: {', '.join(target2['applicable_cases'])}",
            f"- Equal-weight reduction: "
            f"{target2['equal_weight_mean_reduction_pct']['mean']:.6f}%", "",
            "| Case | Naive ns | Spill-noopt ns | Optimized ns | Reduction |",
            "|---|---:|---:|---:|---:|",
        ]
        for case, values in report["target2"]["case_statistics"].items():
            profiles = values["profile_mean_ns"]
            reduction = values["optimized_reduction_pct"]
            lines.append(
                f"| {case} | {profiles['naive']['mean']:.6f} | "
                f"{profiles['spill-noopt']['mean']:.6f} | "
                f"{profiles['optimized']['mean']:.6f} | "
                f"{reduction['mean']:.6f}% |")
        return "\n".join(lines) + "\n"

    def write_outputs(self, report, out_dir):
        out_dir = pathlib.Path(out_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        serializable = dict(report)
        rows = serializable.pop("csv_rows")
        with (out_dir / "performance_comparison.json").open("w") as stream:
            json.dump(serializable, stream, indent=2, sort_keys=True)
            stream.write("\n")
        (out_dir / "performance_comparison.md").write_text(
            self.render_markdown(report))
        with (out_dir / "resolved_runs.json").open("w") as stream:
            json.dump(report["resolved_runs"], stream, indent=2, sort_keys=True)
            stream.write("\n")
        fields = ["round", "case", "profile", "phase", "samples",
                  "mean_ns", "p50_ns", "p95_ns", "p99_ns", "max_ns"]
        with (out_dir / "target2_samples.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)
        return out_dir


def load_analyzer_class(spec):
    if not spec:
        return Metric12RunListAnalyzer
    if ":" not in spec:
        raise InputSchemaError("--analyzer must be MODULE_OR_FILE:ClassName")
    module_value, class_name = spec.rsplit(":", 1)
    if module_value.endswith(".py") or "/" in module_value:
        path = pathlib.Path(module_value).expanduser().resolve()
        module_spec = importlib.util.spec_from_file_location("metric12_custom_parser", path)
        if module_spec is None or module_spec.loader is None:
            raise InputSchemaError(f"cannot load analyzer module from {path}")
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
    else:
        module = importlib.import_module(module_value)
    analyzer_class = getattr(module, class_name, None)
    if not isinstance(analyzer_class, type) or not issubclass(
            analyzer_class, Metric12RunListAnalyzer):
        raise InputSchemaError(
            f"{spec} must name a Metric12RunListAnalyzer subclass")
    return analyzer_class


def main():
    parser = argparse.ArgumentParser(
        description="Analyze metric 1/2 logs from a list of three-field run dicts")
    parser.add_argument("--input", required=True,
                        help="JSON file containing the top-level run list")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--analyzer",
                        help="optional custom analyzer as MODULE_OR_FILE:ClassName")
    parser.add_argument("--min-rounds", type=int, default=3)
    parser.add_argument("--hash-inputs", action="store_true")
    args = parser.parse_args()
    try:
        input_path = pathlib.Path(args.input).expanduser().resolve()
        with input_path.open() as stream:
            runs = json.load(stream)
        analyzer_class = load_analyzer_class(args.analyzer)
        analyzer = analyzer_class(
            runs,
            min_rounds=args.min_rounds,
            hash_inputs=args.hash_inputs,
            base_dir=input_path.parent,
        )
        report = analyzer.analyze()
        out_dir = analyzer.write_outputs(report, args.out_dir)
    except (Metric12Error, OSError, ValueError, KeyError,
            TypeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps({
        "overall_pass": report["overall_pass"],
        "target1_pass": report["target1"]["statistics"]["pass"],
        "target2_pass": report["target2"]["statistics"]["pass"],
        "correctness_gate": report["correctness_gate"]["status"],
        "out_dir": str(out_dir),
    }, indent=2))
    return 0 if report["overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
