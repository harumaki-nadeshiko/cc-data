#!/usr/bin/env python3
"""Audit metric 1/2 manifests before delegating complete evidence to the analyzer.

Schema v2 separates physical ``runs`` (directories) from logical ``uses``
(metric/repetition/case/profile views).  The historical top-level run list is
accepted as schema v1 and converted in memory.
"""

import argparse
import csv
import importlib.util
import json
import pathlib
import statistics
import sys
from collections import defaultdict


HERE = pathlib.Path(__file__).resolve().parent
RUN_LIST_SCRIPT = HERE / "analyze_metric12_run_list.py"
SPEC = importlib.util.spec_from_file_location("analyze_metric12_run_list", RUN_LIST_SCRIPT)
RUN_LIST = importlib.util.module_from_spec(SPEC)
sys.modules.setdefault("analyze_metric12_run_list", RUN_LIST)
SPEC.loader.exec_module(RUN_LIST)

PROFILES = RUN_LIST.PROFILES
TARGET2_CASES = tuple(RUN_LIST.TARGET2_PHASES)
EXIT_CODES = {"PASS": 0, "FAIL": 1, "INVALID": 2, "INCOMPLETE": 3}


def _issue(code, message, **context):
    value = {"code": code, "message": message}
    value.update(context)
    return value


def _target(value):
    key = str(value).strip().lower()
    if key not in RUN_LIST.TARGET_ALIASES:
        raise ValueError(f"unknown metric/target {value!r}")
    return RUN_LIST.TARGET_ALIASES[key]


def _repetitions(value, name):
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 1:
            raise ValueError(f"{name}.repetitions must be positive")
        return list(range(1, value + 1))
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name}.repetitions must be a positive count or non-empty list")
    output = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (str, int, float)):
            raise ValueError(f"{name}.repetitions contains invalid value {item!r}")
        identity = str(item)
        if identity in output:
            raise ValueError(f"{name}.repetitions contains duplicate {identity!r}")
        output.append(identity)
    return output


def _slot_key(target, repetition, case, profile):
    return (target, str(repetition), case if target == "target2" else "TC131", profile)


def _slot_dict(key):
    target, repetition, case, profile = key
    return {
        "metric": "metric1" if target == "target1" else "metric2",
        "target": target,
        "repetition": repetition,
        "case": case,
        "profile": profile,
    }


class Metric12ManifestAuditor:
    """Collecting manifest auditor; malformed evidence does not stop other slots."""

    def __init__(self, manifest, *, base_dir=None, thresholds=None,
                 hash_inputs=False, analyzer_class=None,
                 legacy_min_repetitions=3):
        self.raw = manifest
        self.base_dir = pathlib.Path(base_dir or pathlib.Path.cwd()).expanduser().resolve()
        self.thresholds = dict(RUN_LIST.DEFAULT_THRESHOLDS)
        if thresholds:
            self.thresholds.update(thresholds)
        self.hash_inputs = bool(hash_inputs)
        self.analyzer_class = analyzer_class or RUN_LIST.Metric12RunListAnalyzer
        self.legacy_min_repetitions = int(legacy_min_repetitions)
        if self.legacy_min_repetitions < 1:
            raise ValueError("legacy_min_repetitions must be positive")
        self.issues = []

    def _parse_feature(self, text, index=0):
        analyzer = self.analyzer_class([], min_rounds=1, base_dir=self.base_dir)
        return analyzer._normalize_feature(analyzer.parse_feature(text), index, text)

    def _legacy(self):
        runs, uses = [], []
        repetitions = {"target1": [], "target2": []}
        for index, raw in enumerate(self.raw):
            run_id = f"run-{index + 1}"
            if not isinstance(raw, dict):
                self.issues.append(_issue("RUN_SCHEMA", f"run[{index}] must be an object"))
                continue
            runs.append(dict(raw, id=run_id))
            try:
                feature = self._parse_feature(raw.get("feature"), index)
            except Exception as error:  # collect parser/schema failures
                self.issues.append(_issue("FEATURE_INVALID", str(error), physical_run_id=run_id))
                continue
            repetition = str(feature.round_id)
            if repetition not in repetitions[feature.target]:
                repetitions[feature.target].append(repetition)
            uses.append({
                "id": f"use-{index + 1}", "physical_run_id": run_id,
                "metric": feature.target, "repetition": repetition,
                "case": feature.case, "profile": feature.profile,
                "feature": feature,
            })
        requirements = {
            target: {"repetitions": values}
            for target, values in repetitions.items()
        }
        for target, spec in requirements.items():
            deficit = self.legacy_min_repetitions - len(spec["repetitions"])
            for index in range(max(0, deficit)):
                spec["repetitions"].append(
                    f"__missing_repetition_{len(spec['repetitions']) + 1}")
        return {"source_schema_version": 1, "runs": runs, "uses": uses,
                "requirements": requirements, "policy": {}}

    def _v2(self):
        if not isinstance(self.raw, dict):
            raise ValueError("manifest must be a schema-v2 object or legacy top-level list")
        version = self.raw.get("schema_version", 2)
        if version not in (2, "2"):
            raise ValueError(f"unsupported schema_version {version!r}")
        runs = self.raw.get("runs", self.raw.get("physical_runs"))
        uses = self.raw.get("uses", self.raw.get("logical_uses"))
        requirements = self.raw.get("requirements")
        if not isinstance(runs, list):
            raise ValueError("schema v2 runs must be a list")
        if not isinstance(uses, list):
            raise ValueError("schema v2 uses must be a list")
        if not isinstance(requirements, dict):
            raise ValueError("schema v2 requirements must be an object")
        return {"source_schema_version": 2, "runs": runs, "uses": uses,
                "requirements": requirements, "policy": self.raw.get("policy", {})}

    def _requirements(self, raw):
        output = {}
        for target, aliases in (("target1", ("metric1", "target1")),
                                ("target2", ("metric2", "target2"))):
            spec = next((raw[name] for name in aliases if name in raw), None)
            if not isinstance(spec, dict):
                raise ValueError(f"requirements must define {aliases[0]}")
            repetitions = _repetitions(spec.get("repetitions"), aliases[0])
            profiles = spec.get("profiles", list(PROFILES))
            if not isinstance(profiles, list) or not profiles:
                raise ValueError(f"{aliases[0]}.profiles must be a non-empty list")
            normalized_profiles = []
            for profile in profiles:
                key = str(profile).strip().lower()
                if key not in RUN_LIST.PROFILE_ALIASES:
                    raise ValueError(f"unknown profile {profile!r}")
                normalized = RUN_LIST.PROFILE_ALIASES[key]
                if normalized in normalized_profiles:
                    raise ValueError(f"duplicate profile {normalized!r}")
                normalized_profiles.append(normalized)
            cases = ["TC131"]
            if target == "target2":
                cases = spec.get("cases", list(TARGET2_CASES))
                if not isinstance(cases, list) or not cases:
                    raise ValueError("metric2.cases must be a non-empty list")
                cases = [f"TC{int(str(case).upper()[2:] if str(case).upper().startswith('TC') else str(case))}"
                         for case in cases]
                unknown = sorted(set(cases) - set(TARGET2_CASES))
                if unknown or len(set(cases)) != len(cases):
                    raise ValueError(f"metric2 cases are unknown or duplicate: {cases}")
            output[target] = {"repetitions": repetitions,
                              "profiles": normalized_profiles, "cases": cases}
        return output

    def _physical_runs(self, raws):
        output = {}
        for index, raw in enumerate(raws):
            if not isinstance(raw, dict):
                self.issues.append(_issue("RUN_SCHEMA", f"runs[{index}] must be an object"))
                continue
            run_id = raw.get("id", raw.get("run_id"))
            if run_id is None:
                self.issues.append(_issue("RUN_SCHEMA", f"runs[{index}] lacks id"))
                continue
            run_id = str(run_id)
            if run_id in output:
                self.issues.append(_issue("RUN_DUPLICATE_ID", f"duplicate physical run id {run_id!r}"))
                continue
            missing = [key for key in ("simulator_log_dir", "workload_output_dir")
                       if key not in raw]
            if missing:
                self.issues.append(_issue("RUN_SCHEMA", f"physical run {run_id!r} lacks {missing}",
                                          physical_run_id=run_id))
            output[run_id] = raw
        return output

    def _use_feature(self, raw, physical, index):
        supplied = raw.get("feature")
        if isinstance(supplied, RUN_LIST.RunFeature):
            return supplied
        if isinstance(supplied, str):
            return self._parse_feature(supplied, index)
        view = raw.get("view", {})
        if not isinstance(view, dict):
            raise ValueError("view must be an object")
        merged = dict(view)
        merged.update({key: value for key, value in raw.items() if key in {
            "metric", "target", "repetition", "round", "round_id", "case",
            "topology", "profile", "phase", "home_node", "home_socket", "timer_nodes"}})
        target = _target(merged.get("metric", merged.get("target")))
        repetition = merged.get("repetition", merged.get("round", merged.get("round_id")))
        if repetition is None:
            raise ValueError("logical use lacks repetition")
        case = merged.get("case", "TC131" if target == "target1" else None)
        if case is None:
            raise ValueError("metric2 logical use lacks case")
        case_text = str(case).upper()
        case = f"TC{int(case_text[2:] if case_text.startswith('TC') else case_text)}"
        topology = merged.get("topology")
        if topology is None:
            topology = "8n1s" if target == "target1" else RUN_LIST.TARGET2_TOPOLOGIES.get(case)
        profile = merged.get("profile")
        if profile is None:
            raise ValueError("logical use lacks profile")
        return RUN_LIST.RunFeature(
            target=target, round_id=1, case=case, topology=topology, profile=profile,
            phase=merged.get("phase", ""), home_node=int(merged.get("home_node", 0)),
            home_socket=int(merged.get("home_socket", 0)),
            timer_nodes=tuple(merged.get("timer_nodes", (1, 2))),
            extras={"manifest_repetition": str(repetition)})

    @staticmethod
    def _feature_text(feature, canonical_round):
        values = [
            f"target={feature.target}", f"round={canonical_round}",
            f"case={feature.case}", f"topology={feature.topology}",
            f"profile={feature.profile}", f"phase={feature.phase}",
            f"home_node={feature.home_node}", f"home_socket={feature.home_socket}",
            "timer_nodes=" + ",".join(str(node) for node in feature.timer_nodes),
        ]
        return ";".join(values)

    def _logical_uses(self, raws, physical_runs, requirements):
        output = []
        rep_maps = {target: {str(value): index + 1 for index, value in enumerate(spec["repetitions"])}
                    for target, spec in requirements.items()}
        for index, raw in enumerate(raws):
            if not isinstance(raw, dict):
                self.issues.append(_issue("USE_SCHEMA", f"uses[{index}] must be an object"))
                continue
            use_id = str(raw.get("id", f"use-{index + 1}"))
            run_id = raw.get("physical_run_id", raw.get("run_id", raw.get("run")))
            run_id = None if run_id is None else str(run_id)
            physical = physical_runs.get(run_id)
            item = {"id": use_id, "physical_run_id": run_id, "raw": raw,
                    "issues": [], "allow_reuse": bool(raw.get("allow_reuse", False)),
                    "reuse_group": raw.get("reuse_group")}
            if physical is None:
                item["issues"].append(_issue("UNKNOWN_RUN", f"logical use references unknown run {run_id!r}"))
                output.append(item)
                continue
            try:
                feature = raw.get("feature") if isinstance(raw.get("feature"), RUN_LIST.RunFeature) else None
                feature = feature or self._use_feature(raw, physical, index)
                # v1 feature carries its real round in extras only through this branch.
                repetition = (raw.get("repetition", raw.get("round", raw.get("round_id")))
                              if not isinstance(raw.get("feature"), RUN_LIST.RunFeature)
                              else raw["feature"].round_id)
                if repetition is None:
                    repetition = feature.extras.get("manifest_repetition", feature.round_id)
                normalized = RUN_LIST.Metric12RunListAnalyzer([], min_rounds=1)._normalize_feature(
                    feature, index, str(raw.get("feature", "manifest view")))
                target = normalized.target
                key = _slot_key(target, repetition, normalized.case, normalized.profile)
                canonical = rep_maps.get(target, {}).get(str(repetition), 1)
                item.update({"feature": normalized, "slot_key": key,
                             "canonical_round": canonical,
                             "run_dict": {
                                 "simulator_log_dir": physical.get("simulator_log_dir"),
                                 "workload_output_dir": physical.get("workload_output_dir"),
                                 "feature": self._feature_text(normalized, canonical),
                             }})
            except Exception as error:
                item["issues"].append(_issue("USE_FEATURE_INVALID", str(error)))
            output.append(item)
        return output

    @staticmethod
    def _expected(requirements):
        keys = []
        for target, spec in requirements.items():
            for repetition in spec["repetitions"]:
                for case in spec["cases"]:
                    for profile in spec["profiles"]:
                        keys.append(_slot_key(target, repetition, case, profile))
        return keys

    def _validate_evidence(self, use):
        if use["issues"] or "run_dict" not in use:
            return
        try:
            analyzer = RUN_LIST.Metric12RunListAnalyzer(
                [use["run_dict"]], min_rounds=1, thresholds=self.thresholds,
                hash_inputs=self.hash_inputs, base_dir=self.base_dir)
            use["resolved"] = analyzer.resolve_runs()[0]
            run = use["resolved"]
            feature = run.record.feature
            if feature.target == "target1":
                timer_paths = []
                for node in feature.timer_nodes:
                    if node not in run.simout_by_node:
                        raise RUN_LIST.EvidenceError(f"target1 lacks simout node {node}")
                    timer_paths.append(run.simout_by_node[node])
                timers = RUN_LIST.Metric12RunListAnalyzer._timer_records(
                    timer_paths, feature.phase)
                counts = {node: 0 for node in feature.timer_nodes}
                for timer in timers:
                    if timer["node"] in counts:
                        counts[timer["node"]] += 1
                if len(timers) != len(counts) or any(value != 1 for value in counts.values()):
                    raise RUN_LIST.EvidenceError(f"target1 timer contract mismatch: {counts}")
                if len({timer["frequency_hz"] for timer in timers}) != 1:
                    raise RUN_LIST.EvidenceError("target1 timer frequencies differ")
                capacity = RUN_LIST.Metric12RunListAnalyzer._parse_capacity(run.ubio_logs)
                expected_policy = "naive" if feature.profile == "naive" else "spill"
                if capacity["policy"] != expected_policy:
                    raise RUN_LIST.EvidenceError(
                        f"target1 profile {feature.profile} requires policy {expected_policy}")
            else:
                latency = RUN_LIST.Metric12RunListAnalyzer._latency_record(
                    tuple(run.simout_by_node.values()), feature.phase)
                contract = RUN_LIST.TARGET2_MARKER_CONTRACT[feature.case]
                if latency["node"] != contract["node"] or latency["samples"] != contract["samples"]:
                    raise RUN_LIST.EvidenceError(
                        f"target2 marker requires node={contract['node']} "
                        f"samples={contract['samples']}")
        except Exception as error:
            use["issues"].append(_issue("EVIDENCE_INVALID", str(error)))

    @staticmethod
    def _aggregate_key(slot_key):
        target, repetition, case, _profile = slot_key
        return (target, repetition, case)

    def _reuse_audit(self, uses, policy):
        by_run = defaultdict(list)
        for use in uses:
            if not use.get("slot_key") or use["issues"]:
                continue
            by_run[use["physical_run_id"]].append(use)
        globally_allowed = bool(policy.get("allow_reuse", False)) if isinstance(policy, dict) else False
        for run_id, group in by_run.items():
            slots = {use["slot_key"] for use in group}
            if len(slots) < 2:
                continue
            aggregates = [self._aggregate_key(use["slot_key"]) for use in group]
            if len(aggregates) != len(set(aggregates)):
                issue = _issue("REUSE_WITHIN_AGGREGATE",
                               "one physical run cannot be weighted more than once in an aggregate",
                               physical_run_id=run_id)
                for use in group:
                    use["issues"].append(issue)
                continue
            explicit = all(use["allow_reuse"] for use in group)
            reuse_groups = {use["reuse_group"] for use in group}
            grouped = len(reuse_groups) == 1 and None not in reuse_groups and "" not in reuse_groups
            if not ((globally_allowed or explicit) and grouped):
                issue = _issue("REUSE_FORBIDDEN",
                               "physical run reused across required slots without an allowed reuse_group",
                               physical_run_id=run_id)
                for use in group:
                    use["issues"].append(issue)
            else:
                for use in group:
                    use["allowed_reuse"] = True

    def _provisional(self, slot_map, requirements):
        result = {"metric1": {"comparisons": []}, "metric2": {"comparisons": []}}
        thresholds = self.thresholds
        # A comparison is computable only when all three official profiles are valid.
        for repetition in requirements["target1"]["repetitions"]:
            selected = {}
            for profile in PROFILES:
                slot = slot_map.get(_slot_key("target1", repetition, "TC131", profile))
                if slot and slot["status"] in ("VALID", "REUSE"):
                    selected[profile] = slot["selected_use"]["resolved"]
            if len(selected) != len(PROFILES):
                continue
            try:
                values = {}
                for profile, run in selected.items():
                    feature = run.record.feature
                    timers = RUN_LIST.Metric12RunListAnalyzer._timer_records(
                        [run.simout_by_node[node] for node in feature.timer_nodes], feature.phase)
                    if len(timers) != len(feature.timer_nodes):
                        raise RUN_LIST.EvidenceError("target1 timer count mismatch")
                    values[profile] = {
                        "capacity": RUN_LIST.Metric12RunListAnalyzer._parse_capacity(run.ubio_logs),
                        "mean_ns": statistics.mean(item["mean_ns_per_operation"] for item in timers),
                    }
                ratio = values["spill-noopt"]["capacity"]["effective_unique"] / values["naive"]["capacity"]["effective_unique"]
                delta_ns = values["spill-noopt"]["mean_ns"] - values["naive"]["mean_ns"]
                delta_cycles = delta_ns * thresholds["target1_contract_clock_hz"] / 1.0e9
                result["metric1"]["comparisons"].append({
                    "repetition": str(repetition), "capacity_ratio": ratio,
                    "guest_delta_ns_per_operation": delta_ns, "guest_delta_cycles": delta_cycles,
                    "would_pass": ratio >= thresholds["target1_capacity_ratio_min"] and
                                  delta_cycles < thresholds["target1_max_extra_cycles"],
                })
            except Exception as error:
                result["metric1"].setdefault("issues", []).append(str(error))
        by_repetition = defaultdict(list)
        for repetition in requirements["target2"]["repetitions"]:
            for case in requirements["target2"]["cases"]:
                selected = {}
                for profile in PROFILES:
                    slot = slot_map.get(_slot_key("target2", repetition, case, profile))
                    if slot and slot["status"] in ("VALID", "REUSE"):
                        selected[profile] = slot["selected_use"]["resolved"]
                if len(selected) != len(PROFILES):
                    continue
                try:
                    means = {}
                    for profile, run in selected.items():
                        latency = RUN_LIST.Metric12RunListAnalyzer._latency_record(
                            tuple(run.simout_by_node.values()), run.record.feature.phase)
                        contract = RUN_LIST.TARGET2_MARKER_CONTRACT[case]
                        if latency["node"] != contract["node"] or latency["samples"] != contract["samples"]:
                            raise RUN_LIST.EvidenceError("target2 marker contract mismatch")
                        means[profile] = latency["ns"]["mean"]
                    reduction = (means["naive"] - means["optimized"]) / means["naive"] * 100.0
                    comparison = {"repetition": str(repetition), "case": case,
                                  "means_ns": means, "optimized_reduction_pct": reduction,
                                  "applicable": means["naive"] >= thresholds["target2_applicable_naive_mean_ns"]}
                    result["metric2"]["comparisons"].append(comparison)
                    if comparison["applicable"]:
                        by_repetition[str(repetition)].append(reduction)
                except Exception as error:
                    result["metric2"].setdefault("issues", []).append(str(error))
        result["metric2"]["repetition_equal_weight"] = [
            {"repetition": repetition, "available_applicable_cases": len(values),
             "mean_reduction_pct": statistics.mean(values)}
            for repetition, values in by_repetition.items() if values]
        return result

    def audit(self):
        try:
            normalized = self._legacy() if isinstance(self.raw, list) else self._v2()
            requirements = self._requirements(normalized["requirements"])
        except Exception as error:
            return self._fatal_report(str(error))
        physical = self._physical_runs(normalized["runs"])
        uses = self._logical_uses(normalized["uses"], physical, requirements)
        expected = self._expected(requirements)
        expected_set = set(expected)
        by_slot = defaultdict(list)
        unexpected = []
        for use in uses:
            if use.get("slot_key") not in expected_set:
                if use.get("slot_key"):
                    use["issues"].append(_issue("UNEXPECTED_SLOT", "logical use does not match requirements"))
                unexpected.append(use)
            else:
                by_slot[use["slot_key"]].append(use)
        self._reuse_audit(uses, normalized["policy"])
        for use in uses:
            self._validate_evidence(use)

        slots, slot_map = [], {}
        for key in expected:
            candidates = by_slot.get(key, [])
            item = _slot_dict(key)
            item["uses"] = [use["id"] for use in candidates]
            item["physical_run_ids"] = [use["physical_run_id"] for use in candidates]
            item["issues"] = [issue for use in candidates for issue in use["issues"]]
            if not candidates:
                item["status"] = "MISSING"
            elif len(candidates) > 1:
                item["status"] = "DUPLICATE"
                item["issues"].append(_issue("DUPLICATE_SLOT", "multiple logical uses claim one slot"))
            elif candidates[0]["issues"]:
                item["status"] = "INVALID"
            elif candidates[0].get("allowed_reuse"):
                item["status"] = "REUSE"
                item["selected_use"] = candidates[0]
            else:
                item["status"] = "VALID"
                item["selected_use"] = candidates[0]
            slots.append(item)
            slot_map[key] = item
        unexpected_rows = []
        for use in unexpected:
            row = _slot_dict(use["slot_key"]) if use.get("slot_key") else {
                "metric": None, "target": None, "repetition": None, "case": None, "profile": None}
            row.update({"status": "UNEXPECTED", "uses": [use["id"]],
                        "physical_run_ids": [use["physical_run_id"]], "issues": use["issues"]})
            unexpected_rows.append(row)

        status_counts = defaultdict(int)
        for row in slots + unexpected_rows:
            status_counts[row["status"]] += 1
        invalid = bool(self.issues or status_counts["INVALID"] or status_counts["DUPLICATE"] or
                       status_counts["UNEXPECTED"])
        incomplete = bool(status_counts["MISSING"])
        provisional = self._provisional(slot_map, requirements)
        formal = None
        if not invalid and not incomplete:
            selected = [row["selected_use"]["run_dict"] for row in slots]
            t1_count = len(requirements["target1"]["repetitions"])
            t2_count = len(requirements["target2"]["repetitions"])
            if t1_count == t2_count and set(requirements["target1"]["profiles"]) == set(PROFILES) and \
                    set(requirements["target2"]["profiles"]) == set(PROFILES) and \
                    set(requirements["target2"]["cases"]) == set(TARGET2_CASES):
                try:
                    analyzer = RUN_LIST.Metric12RunListAnalyzer(
                        selected, min_rounds=min(t1_count, t2_count), thresholds=self.thresholds,
                        hash_inputs=self.hash_inputs, base_dir=self.base_dir)
                    formal = analyzer.analyze()
                except Exception as error:
                    invalid = True
                    self.issues.append(_issue("FORMAL_ANALYSIS_INVALID", str(error)))
            else:
                # Independent repetition counts cannot be represented by the legacy
                # matrix validator.  Comparisons still use its resolvers/parsers and
                # are formally reduced here without inventing or reweighting runs.
                m1 = provisional["metric1"]["comparisons"]
                m2_rows = provisional["metric2"]["repetition_equal_weight"]
                complete_m2_rounds = len(m2_rows) == t2_count
                formal = {
                    "adapter": "independent-repetition-reduction",
                    "target1_pass": len(m1) == t1_count and all(row["would_pass"] for row in m1),
                    "target2_pass": complete_m2_rounds and all(
                        row["mean_reduction_pct"] >= self.thresholds["target2_equal_weight_reduction_min_pct"]
                        for row in m2_rows),
                }
                formal["overall_pass"] = formal["target1_pass"] and formal["target2_pass"]
        if invalid:
            status = "INVALID"
        elif incomplete:
            status = "INCOMPLETE"
        else:
            overall_pass = formal["overall_pass"]
            status = "PASS" if overall_pass else "FAIL"

        contributing = [row["selected_use"] for row in slots if row["status"] in ("VALID", "REUSE")]
        evidence = defaultdict(list)
        for use in contributing:
            evidence[use["physical_run_id"]].append(use["slot_key"])
        ledger = {
            "weighted_contribution_count": len(contributing),
            "independent_evidence_count": len(evidence),
            "physical_runs": [
                {"physical_run_id": run_id, "contribution_count": len(keys),
                 "slots": [_slot_dict(key) for key in keys], "independent_evidence_count": 1}
                for run_id, keys in sorted(evidence.items())
            ],
        }
        # Internal resolved objects are deliberately excluded from report files.
        for row in slots:
            row.pop("selected_use", None)
        return {
            "schema_version": 2, "source_schema_version": normalized["source_schema_version"],
            "status": status, "exit_code": EXIT_CODES[status], "requirements": requirements,
            "coverage": {"expected_slot_count": len(expected), "counts": dict(status_counts),
                         "slots": slots, "unexpected": unexpected_rows},
            "evidence_ledger": ledger, "issues": self.issues,
            "provisional": provisional, "formal_analysis": formal,
        }

    def _fatal_report(self, message):
        return {"schema_version": 2, "status": "INVALID", "exit_code": 2,
                "requirements": {}, "coverage": {"expected_slot_count": 0, "counts": {},
                                                   "slots": [], "unexpected": []},
                "evidence_ledger": {"weighted_contribution_count": 0,
                                    "independent_evidence_count": 0, "physical_runs": []},
                "issues": [_issue("MANIFEST_SCHEMA", message)], "provisional": None,
                "formal_analysis": None}

    @staticmethod
    def render_markdown(report):
        coverage = report["coverage"]
        lines = ["# Metric 1/2 Manifest Audit", "", f"- Status: **{report['status']}**",
                 f"- Expected slots: {coverage['expected_slot_count']}",
                 f"- Independent evidence: {report['evidence_ledger']['independent_evidence_count']}",
                 f"- Weighted contributions: {report['evidence_ledger']['weighted_contribution_count']}",
                 "", "## Coverage", "",
                 "| Metric | Repetition | Case | Profile | Status | Physical run |",
                 "|---|---|---|---|---|---|"]
        for row in coverage["slots"] + coverage["unexpected"]:
            lines.append("| {metric} | {repetition} | {case} | {profile} | {status} | {runs} |".format(
                metric=row.get("metric") or "-", repetition=row.get("repetition") or "-",
                case=row.get("case") or "-", profile=row.get("profile") or "-",
                status=row["status"], runs=", ".join(str(value) for value in row["physical_run_ids"])))
        if report["issues"]:
            lines.extend(["", "## Issues", ""] +
                         [f"- `{issue['code']}`: {issue['message']}" for issue in report["issues"]])
        return "\n".join(lines) + "\n"

    def write_outputs(self, report, out_dir):
        out_dir = pathlib.Path(out_dir).expanduser().resolve()
        out_dir.mkdir(parents=True, exist_ok=True)
        with (out_dir / "metric12_manifest_audit.json").open("w") as stream:
            json.dump(report, stream, indent=2, sort_keys=True, default=str)
            stream.write("\n")
        (out_dir / "metric12_manifest_audit.md").write_text(self.render_markdown(report))
        fields = ["metric", "repetition", "case", "profile", "status",
                  "physical_run_ids", "use_ids", "issue_codes"]
        with (out_dir / "metric12_manifest_coverage.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            for row in report["coverage"]["slots"] + report["coverage"]["unexpected"]:
                writer.writerow({
                    "metric": row.get("metric"), "repetition": row.get("repetition"),
                    "case": row.get("case"), "profile": row.get("profile"),
                    "status": row["status"],
                    "physical_run_ids": ";".join(str(value) for value in row["physical_run_ids"]),
                    "use_ids": ";".join(row["uses"]),
                    "issue_codes": ";".join(issue["code"] for issue in row["issues"]),
                })
        return out_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description="Audit a metric 1/2 physical-run/logical-use manifest")
    parser.add_argument("--input", required=True)
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--analyzer", help="custom feature parser MODULE_OR_FILE:ClassName (legacy input)")
    parser.add_argument("--hash-inputs", action="store_true")
    parser.add_argument("--legacy-min-repetitions", type=int, default=3)
    args = parser.parse_args(argv)
    input_path = pathlib.Path(args.input).expanduser().resolve()
    try:
        manifest = json.loads(input_path.read_text())
        analyzer_class = RUN_LIST.load_analyzer_class(args.analyzer)
        auditor = Metric12ManifestAuditor(manifest, base_dir=input_path.parent,
                                           hash_inputs=args.hash_inputs,
                                           analyzer_class=analyzer_class,
                                           legacy_min_repetitions=
                                           args.legacy_min_repetitions)
        report = auditor.audit()
        out_dir = auditor.write_outputs(report, args.out_dir)
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps({"status": report["status"], "exit_code": report["exit_code"],
                      "out_dir": str(out_dir)}, indent=2))
    return report["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
