#!/usr/bin/env python3
"""Analyze Metric 3 paired OurCC versus HA-VI evidence.

The analyzer deliberately treats deterministic simulator repeats as descriptive
consistency checks.  It never emits an inferential confidence interval or
p-value.  Delta is always HA-VI minus OurCC, so a positive value favors OurCC.
"""

import argparse
import csv
import hashlib
import json
import pathlib
import re
import statistics
import sys


CORE_TCS = (228, 229, 230)
REPRESENTATIVE_TCS = (231, 232, 233, 234, 235)
ALL_TCS = CORE_TCS + REPRESENTATIVE_TCS
ARMS = ("ourcc", "ha-vi")
METRIC_REGISTRY = {
    228: {"remote_read": {"kind": "aggregate_timer", "phase": "topology_remote_read"}},
    229: {"ownership_handoff": {"kind": "aggregate_timer", "phase": "topology_ownership_handoff"}},
    230: {"shared_to_writer": {"kind": "aggregate_timer", "phase": "topology_all_sharer_to_writer"}},
    231: {"clean_shared_control": {"kind": "aggregate_timer", "phase": "clean_shared_read_service"}},
    232: {
        "hot_key_read": {"kind": "aggregate_timer", "phase": "hot_key_read_service"},
        "hot_key_write": {"kind": "aggregate_timer", "phase": "hot_key_write_service"},
    },
    233: {
        "producer_consumer_load": {"kind": "aggregate_latency", "phase": "producer_consumer_load"},
        "producer_consumer_service": {"kind": "aggregate_timer", "phase": "producer_consumer_service"},
    },
    234: {
        "queued_token_end_to_end": {"kind": "aggregate_timer", "phase": "queued_token_end_to_end"},
        "queued_token_store": {"kind": "aggregate_timer", "phase": "queued_token_store"},
    },
    235: {
        "catalog_kv_end_to_end": {"kind": "max_timer", "phase": "catalog_kv_end_to_end"},
        "catalog_kv_service": {"kind": "aggregate_timer", "phase": "catalog_kv_service"},
    },
}
REFERENCE_MODEL_PASS = "PASS (EXECUTABLE-REFERENCE-MODEL SCOPE)"
PRIMARY_VALUE_DEFINITIONS = {
    "TC228": {"formula": "remote_read", "metrics": {"TC228_remote_read": 1.0}},
    "TC229": {"formula": "ownership_handoff", "metrics": {"TC229_ownership_handoff": 1.0}},
    "TC230": {"formula": "shared_to_writer", "metrics": {"TC230_shared_to_writer": 1.0}},
    "TC231": {"formula": "clean_shared_control", "metrics": {"TC231_clean_shared_control": 1.0}},
    "TC232": {
        "formula": "2/3 * hot_key_read + 1/3 * hot_key_write",
        "metrics": {"TC232_hot_key_read": 2.0 / 3.0,
                    "TC232_hot_key_write": 1.0 / 3.0},
    },
    "TC233": {"formula": "producer_consumer_service",
              "metrics": {"TC233_producer_consumer_service": 1.0}},
    "TC234": {"formula": "queued_token_end_to_end",
              "metrics": {"TC234_queued_token_end_to_end": 1.0}},
    "TC235": {"formula": "catalog_kv_end_to_end",
              "metrics": {"TC235_catalog_kv_end_to_end": 1.0}},
}
FROZEN_AGGREGATE_WEIGHTS = {
    "core_equal_weight": {
        "TC228_remote_read": 1.0 / 3.0,
        "TC229_ownership_handoff": 1.0 / 3.0,
        "TC230_shared_to_writer": 1.0 / 3.0,
    },
    "representative_equal_weight": {
        "TC231_clean_shared_control": 1.0 / 5.0,
        "TC232_hot_key_read": 2.0 / 15.0,
        "TC232_hot_key_write": 1.0 / 15.0,
        "TC233_producer_consumer_service": 1.0 / 5.0,
        "TC234_queued_token_end_to_end": 1.0 / 5.0,
        "TC235_catalog_kv_end_to_end": 1.0 / 5.0,
    },
}


class InvalidEvidence(Exception):
    pass


def load_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        raise InvalidEvidence(f"cannot read JSON {path}: {error}") from error


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def metric_id(tc, metric):
    return f"TC{tc}_{metric}"


def tier(tc):
    return "core" if tc in CORE_TCS else "representative"


def resolve(base, value):
    path = pathlib.Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def historical_records(root, manifest):
    records = []
    cases = root / "cases"
    if cases.is_dir():
        for result_path in sorted(cases.glob("*/**/result.json")):
            if result_path.parent.name not in ARMS:
                continue
            records.append({
                "result": result_path,
                "log_dir": result_path.parent,
                "declared": {},
                "fingerprint": None,
            })
    return records


def v2_records(manifest_path, manifest):
    base = manifest_path.parent
    raw_records = manifest.get("records", manifest.get("arms"))
    records = []
    if raw_records is not None:
        if not isinstance(raw_records, list):
            raise InvalidEvidence("schema v2 records/arms must be a list")
        for index, item in enumerate(raw_records):
            if not isinstance(item, dict) or "result" not in item:
                raise InvalidEvidence(f"record[{index}] must contain result")
            result = resolve(base, item["result"])
            records.append({
                "result": result,
                "log_dir": resolve(base, item.get("log_dir", str(result.parent))),
                "declared": item,
                "fingerprint": item.get("fingerprint"),
            })
        return records
    samples = manifest.get("samples")
    if not isinstance(samples, list):
        raise InvalidEvidence("schema v2 requires records (or samples)")
    for index, sample in enumerate(samples):
        if not isinstance(sample, dict) or not isinstance(sample.get("arms"), dict):
            raise InvalidEvidence(f"sample[{index}] requires an arms object")
        declared_common = {key: sample[key] for key in ("sample_id", "pair_id", "pair", "repeat", "tc", "order") if key in sample}
        for arm, arm_item in sample["arms"].items():
            if arm_item is None:
                continue
            if not isinstance(arm_item, dict) or "result" not in arm_item:
                raise InvalidEvidence(f"sample[{index}] arm {arm} requires result")
            result = resolve(base, arm_item["result"])
            declared = dict(declared_common)
            declared.update(arm_item)
            declared["arm"] = arm
            records.append({
                "result": result,
                "log_dir": resolve(base, arm_item.get("log_dir", str(result.parent))),
                "declared": declared,
                "fingerprint": arm_item.get("fingerprint"),
            })
    return records


def expected_exit_names(tc):
    return {
        "gem5_node0.exit", "gem5_node1.exit", "networksim.exit",
        "ubio_n0_s0.exit", "ubio_n1_s0.exit",
    }


def validate_correctness(record, result, manifest_fingerprint):
    tc = int(result.get("tc", -1))
    arm = result.get("arm")
    log_dir = record["log_dir"]
    errors = []
    if result.get("status") != "PASS" or result.get("return_code") != 0:
        errors.append("result status/return_code is not PASS/0")
    verifier = log_dir / f"verify_tc{tc}.log"
    if not verifier.is_file():
        errors.append("missing verifier log")
    else:
        lines = [line.strip() for line in verifier.read_text(errors="replace").splitlines() if line.strip()]
        if not lines or lines[-1] != f">>> TC{tc} PASSED <<<":
            errors.append("verifier final non-empty line is not PASS")
    child_dir = log_dir / f"child_status_tc{tc}"
    found = {path.name for path in child_dir.glob("*.exit")} if child_dir.is_dir() else set()
    expected = expected_exit_names(tc)
    if found != expected:
        errors.append(f"child exit identity mismatch expected={sorted(expected)} found={sorted(found)}")
    else:
        for name in sorted(expected):
            if (child_dir / name).read_text(errors="replace").strip() != "0":
                errors.append(f"nonzero/invalid child exit {name}")
    marker = ("ha_endpoint_profile=ubcc clear_profile=lossless-oneway reliability=eventual-delivery"
              if arm == "ourcc" else
              "ha_endpoint_profile=ha-vi clear_profile=ack reliability=clear-ack")
    profile_lines = []
    for path in sorted(log_dir.glob(f"gem5_tc{tc}_node*/stderr.log")):
        profile_lines.extend(line for line in path.read_text(errors="replace").splitlines()
                             if "EPBACKEND-PROFILE" in line)
    if len(profile_lines) != 2 or any(marker not in line for line in profile_lines):
        errors.append("profile marker identity/count mismatch")
    if arm == "ha-vi":
        ha_manifests = []
        for path in sorted(log_dir.glob(f"ubio_tc{tc}_n*_s*/stdout.log")):
            ha_manifests.extend(line for line in path.read_text(errors="replace").splitlines()
                                if "UBIO-HA-MANIFEST" in line and
                                "controller=ha-vi" in line)
        if len(ha_manifests) != 2:
            errors.append("HA-VI controller manifest identity/count mismatch")
    arm_fp = record.get("fingerprint") or result.get("fingerprint")
    if arm_fp is None:
        fingerprint_status = "MANIFEST_ONLY" if manifest_fingerprint else "UNAVAILABLE"
    elif manifest_fingerprint and arm_fp != manifest_fingerprint:
        errors.append("arm fingerprint differs from manifest fingerprint")
        fingerprint_status = "MISMATCH"
    else:
        fingerprint_status = "VERIFIED"
    return errors, fingerprint_status


def normalize_records(records, manifest):
    normalized = []
    invalid = []
    seen = {}
    manifest_fp = manifest.get("fingerprint")
    for index, record in enumerate(records):
        if not record["result"].is_file():
            invalid.append(f"record[{index}] missing result {record['result']}")
            continue
        result = load_json(record["result"])
        declared = record["declared"]
        for field in ("tc", "arm", "pair_id", "pair", "order"):
            if field in declared and field in result and declared[field] != result[field]:
                invalid.append(f"record[{index}] declared/result {field} mismatch")
        try:
            tc = int(declared.get("tc", result["tc"]))
            arm = declared.get("arm", result["arm"])
            pair_id = str(declared.get("pair_id", result.get("pair_id", declared.get("sample_id", ""))))
            pair = int(declared.get("pair", declared.get("repeat", result.get("pair", 0))))
            order = declared.get("order", result.get("order"))
        except (KeyError, TypeError, ValueError) as error:
            invalid.append(f"record[{index}] missing/invalid identity: {error}")
            continue
        if tc not in ALL_TCS or arm not in ARMS or not pair_id or pair < 1 or order not in ("AB", "BA"):
            invalid.append(f"record[{index}] invalid tc/arm/pair/order identity")
            continue
        key = (pair_id, tc, arm)
        if key in seen:
            invalid.append(f"duplicate arm {pair_id} TC{tc} {arm}: records {seen[key]} and {index}")
            continue
        seen[key] = index
        expected_metrics = METRIC_REGISTRY[tc]
        actual = result.get("metrics")
        if not isinstance(actual, dict) or set(actual) != set(expected_metrics):
            invalid.append(f"{pair_id} {arm}: metric registry mismatch expected={sorted(expected_metrics)}")
            continue
        values = {}
        frequencies = {}
        try:
            for name in expected_metrics:
                values[name] = float(actual[name]["ticks_per_operation"])
                frequencies[name] = int(actual[name]["counter_frequency_hz"])
                if frequencies[name] <= 0:
                    raise ValueError("non-positive frequency")
        except (KeyError, TypeError, ValueError) as error:
            invalid.append(f"{pair_id} {arm}: invalid metric value: {error}")
            continue
        errors, fp_status = validate_correctness(record, result, manifest_fp)
        if errors:
            invalid.extend(f"{pair_id} {arm}: {error}" for error in errors)
            continue
        normalized.append({
            "pair_id": pair_id, "pair": pair, "tc": tc, "arm": arm,
            "order": order, "metrics": values, "frequencies": frequencies,
            "result_path": str(record["result"]), "log_dir": str(record["log_dir"]),
            "fingerprint_status": fp_status,
        })
    return normalized, invalid


def pair_records(records, manifest):
    groups = {}
    for row in records:
        groups.setdefault((row["pair_id"], row["tc"]), {})[row["arm"]] = row
    expected_pairs = int(manifest.get("pairs", manifest.get("expected_repeats", 0)) or 0)
    expected_tcs = [int(tc) for tc in manifest.get("testcases", ALL_TCS)]
    paired, unmatched, invalid = [], [], []
    for (pair_id, tc), arms in sorted(groups.items()):
        if set(arms) != set(ARMS):
            unmatched.append({"pair_id": pair_id, "tc": tc, "present_arms": sorted(arms),
                              "missing_arms": sorted(set(ARMS) - set(arms))})
            continue
        left, right = arms["ourcc"], arms["ha-vi"]
        if left["pair"] != right["pair"] or left["order"] != right["order"]:
            invalid.append(f"{pair_id} TC{tc}: paired arm identity/order mismatch")
            continue
        paired.append((left, right))
    by_tc = {}
    for left, _ in paired:
        by_tc[left["tc"]] = by_tc.get(left["tc"], 0) + 1
    expected = expected_pairs * len(expected_tcs) if expected_pairs else len(groups)
    coverage = {
        "expected_repeats": expected_pairs or None,
        "expected_testcases": expected_tcs,
        "expected_pair_slots": expected,
        "observed_unique_pair_slots": len(groups),
        "complete_pair_slots": len(paired),
        "unmatched_pair_slots": unmatched,
        "complete_pairs_by_tc": {f"TC{tc}": by_tc.get(tc, 0) for tc in expected_tcs},
        "balanced_repeat_counts": len({by_tc.get(tc, 0) for tc in expected_tcs}) <= 1,
        "complete": not unmatched and len(paired) == expected and all(by_tc.get(tc, 0) == expected_pairs for tc in expected_tcs) if expected_pairs else not unmatched,
        "pairing_policy": "identity-only; duplicate/orphan arms are never Cartesian paired",
    }
    return paired, coverage, invalid


def make_samples(pairs):
    samples, invalid = [], []
    for ourcc, havi in pairs:
        tc = ourcc["tc"]
        for metric in METRIC_REGISTRY[tc]:
            if ourcc["frequencies"][metric] != havi["frequencies"][metric]:
                invalid.append(f"{ourcc['pair_id']} TC{tc} {metric}: paired frequency mismatch")
                continue
            left = ourcc["metrics"][metric]
            right = havi["metrics"][metric]
            delta = right - left
            frequency = ourcc["frequencies"][metric]
            samples.append({
                "sample_id": f"{ourcc['pair_id']}:{metric}", "pair_id": ourcc["pair_id"],
                "pair": ourcc["pair"], "tc": tc, "tier": tier(tc), "order": ourcc["order"],
                "metric": metric, "metric_id": metric_id(tc, metric),
                "ourcc_ticks_per_operation": left, "ha_vi_ticks_per_operation": right,
                "delta_ticks": delta, "delta_ns": delta * 1e9 / frequency,
                "counter_frequency_hz": frequency,
                "positive_favors": "OurCC",
            })
    return samples, invalid


def summarize(samples):
    groups = {}
    for row in samples:
        groups.setdefault(row["metric_id"], []).append(row)
    output = []
    for key, rows in sorted(groups.items()):
        deltas = [row["delta_ticks"] for row in rows]
        mean = statistics.mean(deltas)
        output.append({
            "metric_id": key, "tc": rows[0]["tc"], "tier": rows[0]["tier"],
            "metric": rows[0]["metric"], "pairs": len(rows),
            "ourcc_mean_ticks": statistics.mean(row["ourcc_ticks_per_operation"] for row in rows),
            "ha_vi_mean_ticks": statistics.mean(row["ha_vi_ticks_per_operation"] for row in rows),
            "delta_mean_ticks": mean, "delta_min_ticks": min(deltas), "delta_max_ticks": max(deltas),
            "delta_stdev_ticks": statistics.stdev(deltas) if len(deltas) > 1 else 0.0,
            "deterministic_repeat": len(set(deltas)) == 1,
            "direction": "OURCC_FASTER" if mean > 0 else "HA_VI_FASTER" if mean < 0 else "TIE",
        })
    return output


def primary_values(summaries):
    by_metric = {row["metric_id"]: row for row in summaries}
    output = {}
    for testcase, definition in PRIMARY_VALUE_DEFINITIONS.items():
        missing = sorted(set(definition["metrics"]) - set(by_metric))
        if missing:
            output[testcase] = {
                "formula": definition["formula"],
                "component_weights": definition["metrics"],
                "components": {},
                "ourcc_mean_ticks": None,
                "ha_vi_mean_ticks": None,
                "delta_mean_ticks": None,
                "missing_metrics": missing,
            }
            continue
        ourcc = sum(weight * by_metric[key]["ourcc_mean_ticks"]
                    for key, weight in definition["metrics"].items())
        havi = sum(weight * by_metric[key]["ha_vi_mean_ticks"]
                   for key, weight in definition["metrics"].items())
        output[testcase] = {
            "formula": definition["formula"],
            "component_weights": definition["metrics"],
            "components": {
                key: {
                    "ourcc_mean_ticks": by_metric[key]["ourcc_mean_ticks"],
                    "ha_vi_mean_ticks": by_metric[key]["ha_vi_mean_ticks"],
                    "delta_mean_ticks": by_metric[key]["delta_mean_ticks"],
                }
                for key in definition["metrics"]
            },
            "ourcc_mean_ticks": ourcc,
            "ha_vi_mean_ticks": havi,
            "delta_mean_ticks": havi - ourcc,
        }
    return output


def order_diagnostics(samples):
    pair_orders = {}
    for row in samples:
        pair_orders[(row["pair_id"], row["tc"])] = row["order"]
    counts = {"AB": 0, "BA": 0}
    by_tc = {}
    for (_, tc), order in pair_orders.items():
        counts[order] += 1
        by_tc.setdefault(f"TC{tc}", {"AB": 0, "BA": 0})[order] += 1
    return {
        "counts": counts, "by_tc": by_tc,
        "balanced_overall": abs(counts["AB"] - counts["BA"]) <= 1,
        "balanced_by_tc": all(abs(value["AB"] - value["BA"]) <= 1 for value in by_tc.values()),
        "diagnostic_only": True,
    }


def parse_weights(path, manifest_path, summaries):
    by_metric = {row["metric_id"]: row for row in summaries}
    registry_tiers = {metric_id(tc, name): tier(tc)
                      for tc, metrics in METRIC_REGISTRY.items() for name in metrics}
    core_ids = [metric_id(tc, next(iter(METRIC_REGISTRY[tc]))) for tc in CORE_TCS]
    if path is None:
        return ({
            "source": "implicit_equal_core_descriptive", "status": "UNFROZEN",
            "contract_authoritative": False, "reference_model_scope": None,
            "aggregates": [{"name": "core_equal_weight_descriptive", "scope": "core", "threshold": None,
                            "comparison": "GT",
                            "weights": {key: 1.0 / len(core_ids) for key in core_ids}}],
        }, [])
    data = load_json(path)
    errors = []
    schema_version = data.get("schema_version")
    status = data.get("status")
    if status not in ("FROZEN", "UNFROZEN"):
        errors.append("weights status must be FROZEN or UNFROZEN")
    expected_sha = data.get("expected_manifest_sha256")
    if expected_sha and expected_sha != sha256(manifest_path):
        errors.append("weights expected_manifest_sha256 mismatch")
    aggregates = data.get("aggregates")
    if aggregates is None and "weights" in data:
        aggregates = [{"name": data.get("name", "metric3_contract"), "scope": data.get("scope", "core"),
                       "threshold": data.get("threshold", data.get("threshold_ticks", 0.0)), "weights": data["weights"]}]
    if not isinstance(aggregates, list) or not aggregates:
        errors.append("weights requires a non-empty aggregates list")
        aggregates = []
    names = set()
    normalized = []
    for index, aggregate in enumerate(aggregates):
        if not isinstance(aggregate, dict) or not isinstance(aggregate.get("weights"), dict):
            errors.append(f"aggregate[{index}] requires weights object")
            continue
        name = aggregate.get("name")
        if not isinstance(name, str) or not name or name in names:
            errors.append(f"aggregate[{index}] has missing/duplicate name")
            continue
        names.add(name)
        weights = {}
        try:
            for key, value in aggregate["weights"].items():
                number = float(value)
                if number < 0:
                    raise ValueError(f"negative weight for {key}")
                if key not in registry_tiers:
                    raise ValueError(f"unknown metric {key}")
                weights[key] = number
            if abs(sum(weights.values()) - 1.0) > 1e-9:
                raise ValueError(f"weights sum is {sum(weights.values())}, expected 1")
        except (TypeError, ValueError) as error:
            errors.append(f"aggregate {name}: {error}")
            continue
        scope = aggregate.get("scope", "core")
        has_representative = any(registry_tiers[key] == "representative" for key in weights)
        if has_representative and scope != "representative":
            errors.append(f"aggregate {name}: representative metrics require scope=representative")
            continue
        if scope == "core" and any(registry_tiers[key] != "core" for key in weights):
            errors.append(f"aggregate {name}: core scope may contain only core metrics")
            continue
        threshold = aggregate.get("threshold", aggregate.get("threshold_ticks"))
        if status == "FROZEN" and threshold is None:
            errors.append(f"aggregate {name}: frozen aggregate requires threshold")
            continue
        comparison = aggregate.get("comparison", "GT")
        if comparison != "GT":
            errors.append(f"aggregate {name}: comparison must be GT")
            continue
        normalized.append({"name": name, "scope": scope,
                           "threshold": None if threshold is None else float(threshold),
                           "comparison": comparison, "weights": weights})
    declared_authority = data.get("contract_authoritative") is True
    reference_scope = data.get("reference_model_scope")
    authoritative = status == "FROZEN" and declared_authority
    if authoritative:
        if schema_version != 2:
            errors.append("authoritative frozen weights require schema_version 2")
        if not isinstance(reference_scope, str) or not reference_scope.strip():
            errors.append("authoritative frozen weights require reference_model_scope")
        by_name = {item["name"]: item for item in normalized}
        if set(by_name) != set(FROZEN_AGGREGATE_WEIGHTS):
            errors.append("authoritative weights require exactly core_equal_weight and representative_equal_weight")
        for name, expected in FROZEN_AGGREGATE_WEIGHTS.items():
            item = by_name.get(name)
            if not item:
                continue
            expected_scope = "core" if name == "core_equal_weight" else "representative"
            if item["scope"] != expected_scope:
                errors.append(f"aggregate {name}: scope must be {expected_scope}")
            if item["threshold"] != 0.0 or item["comparison"] != "GT":
                errors.append(f"aggregate {name}: authoritative contract requires GT 0")
            if set(item["weights"]) != set(expected) or any(
                    abs(item["weights"].get(key, -1.0) - value) > 1e-12
                    for key, value in expected.items()):
                errors.append(f"aggregate {name}: weights differ from frozen contract")
    return ({"schema_version": schema_version, "source": str(path), "status": status,
              "contract_authoritative": authoritative,
              "reference_model_scope": reference_scope,
              "expected_manifest_sha256": expected_sha, "aggregates": normalized,
              "primary_value_definitions": PRIMARY_VALUE_DEFINITIONS}, errors)


def aggregate_results(weight_spec, summaries, samples):
    by_metric = {row["metric_id"]: row for row in summaries}
    results, gate_ledger, contribution_ledger = [], [], []
    for aggregate in weight_spec["aggregates"]:
        name = aggregate["name"]
        missing = sorted(set(aggregate["weights"]) - set(by_metric))
        if missing:
            results.append({"name": name, "scope": aggregate["scope"], "delta_ticks": None,
                            "threshold_ticks": aggregate["threshold"], "verdict": "UNAVAILABLE_INCOMPLETE",
                            "comparison": aggregate.get("comparison", "GT"),
                            "contract_authoritative": False,
                            "reference_model_scope": weight_spec.get("reference_model_scope"),
                            "missing_metrics": missing})
            gate_ledger.append({"gate": name, "scope": aggregate["scope"], "value_ticks": None,
                                "threshold_ticks": aggregate["threshold"], "status": "UNAVAILABLE_INCOMPLETE",
                                "contract_authoritative": False,
                                "reference_model_scope": weight_spec.get("reference_model_scope")})
            continue
        total = 0.0
        ourcc_total = 0.0
        havi_total = 0.0
        for key, weight in aggregate["weights"].items():
            contribution = weight * by_metric[key]["delta_mean_ticks"]
            total += contribution
            ourcc_total += weight * by_metric[key]["ourcc_mean_ticks"]
            havi_total += weight * by_metric[key]["ha_vi_mean_ticks"]
            metric_samples = [row for row in samples if row["metric_id"] == key]
            sample_weight = weight / len(metric_samples)
            for sample in metric_samples:
                contribution_ledger.append({
                    "aggregate": name, "metric_id": key,
                    "paired_sample_id": sample["sample_id"],
                    "execution_unit_id": f"{sample['pair_id']}:TC{sample['tc']}",
                    "weight": sample_weight,
                    "sample_delta_ticks": sample["delta_ticks"],
                    "weighted_contribution_ticks":
                        sample_weight * sample["delta_ticks"],
                    "independent_evidence_increment": 0,
                    "dedup_key": f"{name}:{sample['sample_id']}",
                })
        threshold = aggregate["threshold"]
        authoritative = weight_spec.get("contract_authoritative", False)
        if authoritative:
            verdict = REFERENCE_MODEL_PASS if total > threshold else "FAIL (EXECUTABLE-REFERENCE-MODEL SCOPE)"
        else:
            verdict = "DESCRIPTIVE/NON-AUTHORITATIVE"
        result = {"name": name, "scope": aggregate["scope"], "delta_ticks": total,
                  "ourcc_ticks_per_operation": ourcc_total,
                  "ha_vi_ticks_per_operation": havi_total,
                  "ourcc_reduction_pct":
                      total / havi_total * 100.0 if havi_total else None,
                  "threshold_ticks": threshold, "comparison": aggregate.get("comparison", "GT"),
                  "verdict": verdict, "contract_authoritative": authoritative,
                  "reference_model_scope": weight_spec.get("reference_model_scope")}
        results.append(result)
        gate_ledger.append({"gate": name, "scope": aggregate["scope"], "value_ticks": total,
                            "threshold_ticks": threshold, "status": verdict,
                            "contract_authoritative": authoritative,
                            "reference_model_scope": weight_spec.get("reference_model_scope")})
    return results, gate_ledger, contribution_ledger


def write_csv(path, rows, fieldnames):
    with path.open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def write_outputs(output_dir, report, samples, gates, contributions):
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "metric3_paired_report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    write_csv(output_dir / "samples.csv", samples, [
        "sample_id", "pair_id", "pair", "tc", "tier", "order", "metric", "metric_id",
        "ourcc_ticks_per_operation", "ha_vi_ticks_per_operation", "delta_ticks", "delta_ns",
        "counter_frequency_hz", "positive_favors"])
    write_csv(output_dir / "gate_ledger.csv", gates,
              ["gate", "scope", "value_ticks", "threshold_ticks", "status",
               "contract_authoritative", "reference_model_scope"])
    write_csv(output_dir / "contribution_ledger.csv", contributions,
              ["aggregate", "metric_id", "paired_sample_id", "execution_unit_id",
               "weight", "sample_delta_ticks", "weighted_contribution_ticks",
               "independent_evidence_increment", "dedup_key"])
    lines = ["# Metric 3 Paired Analysis", "", "Delta = HA-VI - OurCC; positive favors OurCC.", "",
             f"Overall: **{report['overall_status']}**", "",
             f"Contract authoritative: `{report.get('contract_authoritative', False)}`", "",
             f"Reference-model scope: {report.get('reference_model_scope') or 'N/A'}", "",
             "Deterministic repeats are descriptive only; no authoritative CI or p-value is reported.", "",
             "| Metric | Tier | Pairs | Mean delta ticks | Direction |", "|---|---|---:|---:|---|"]
    for row in report.get("metric_summaries", []):
        lines.append(f"| {row['metric_id']} | {row['tier']} | {row['pairs']} | {row['delta_mean_ticks']:.9g} | {row['direction']} |")
    lines += ["", "## Aggregates", "",
              "| Name | Scope | OurCC ticks/op | HA-VI ticks/op | Delta ticks | Reduction | Threshold | Verdict |",
              "|---|---|---:|---:|---:|---:|---:|---|"]
    for row in report.get("aggregates", []):
        value = "N/A" if row["delta_ticks"] is None else f"{row['delta_ticks']:.9g}"
        ourcc = "N/A" if row.get("ourcc_ticks_per_operation") is None else f"{row['ourcc_ticks_per_operation']:.9g}"
        havi = "N/A" if row.get("ha_vi_ticks_per_operation") is None else f"{row['ha_vi_ticks_per_operation']:.9g}"
        reduction = "N/A" if row.get("ourcc_reduction_pct") is None else f"{row['ourcc_reduction_pct']:.6f}%"
        lines.append(f"| {row['name']} | {row['scope']} | {ourcc} | {havi} | {value} | {reduction} | {row['threshold_ticks']} | {row['verdict']} |")
    if report.get("errors"):
        lines += ["", "## Errors"] + [f"- {item}" for item in report["errors"]]
    (output_dir / "metric3_paired_report.md").write_text("\n".join(lines) + "\n")
    compact = [f"METRIC3 {report['overall_status']}",
               f"coverage={report.get('coverage', {}).get('complete_pair_slots', 0)}/{report.get('coverage', {}).get('expected_pair_slots', 0)}",
               "delta=HA-VI-OurCC;positive=OurCC", "ci=NONE;pvalue=NONE;repeats=DESCRIPTIVE"]
    compact.extend(
        f"{row['name']}="
        f"delta:{'N/A' if row['delta_ticks'] is None else format(row['delta_ticks'], '.9g')},"
        f"ourcc:{'N/A' if row.get('ourcc_ticks_per_operation') is None else format(row['ourcc_ticks_per_operation'], '.9g')},"
        f"havi:{'N/A' if row.get('ha_vi_ticks_per_operation') is None else format(row['ha_vi_ticks_per_operation'], '.9g')},"
        f"reduction:{'N/A' if row.get('ourcc_reduction_pct') is None else format(row['ourcc_reduction_pct'], '.9g')}%,"
        f"verdict:{row['verdict']}"
        for row in report.get("aggregates", []))
    (output_dir / "compact.txt").write_text(" ".join(compact) + "\n")


def analyze(manifest_path, evidence_root, weights_path):
    manifest = load_json(manifest_path)
    version = manifest.get("schema_version")
    if evidence_root is not None:
        if version != 1:
            raise InvalidEvidence("--evidence-root expects historical schema_version 1")
        records = historical_records(evidence_root, manifest)
        input_mode = "historical_evidence_root"
    else:
        if version != 2:
            raise InvalidEvidence("--manifest requires explicit schema_version 2")
        records = v2_records(manifest_path, manifest)
        input_mode = "explicit_manifest_v2"
    normalized, errors = normalize_records(records, manifest)
    pairs, coverage, pairing_errors = pair_records(normalized, manifest)
    errors.extend(pairing_errors)
    samples, sample_errors = make_samples(pairs)
    errors.extend(sample_errors)
    summaries = summarize(samples)
    core_summaries = [row for row in summaries if row["tier"] == "core"]
    core_sensitivity = {
        "delta_definition": "sum(weight_i * delta_i)",
        "weights_constraint": "all weights >= 0 and sum(weights) = 1",
        "coefficients": {row["metric_id"]: row["delta_mean_ticks"]
                         for row in core_summaries},
        "strictly_faster_condition": "weighted delta_ticks > 0",
        "min_vertex_delta_ticks": min(
            (row["delta_mean_ticks"] for row in core_summaries), default=None),
        "max_vertex_delta_ticks": max(
            (row["delta_mean_ticks"] for row in core_summaries), default=None),
        "contract_verdict": None,
    }
    weight_spec, weight_errors = parse_weights(weights_path, manifest_path, summaries)
    errors.extend(weight_errors)
    core_sensitivity["contract_verdict"] = (
        REFERENCE_MODEL_PASS if weight_spec.get("contract_authoritative") else
        "DESCRIPTIVE/NON-AUTHORITATIVE")
    aggregates, gates, contributions = aggregate_results(weight_spec, summaries, samples) if not weight_errors else ([], [], [])
    incomplete = not coverage["complete"]
    authoritative = weight_spec.get("contract_authoritative", False)
    frozen_failure = authoritative and any(
        row["verdict"] != REFERENCE_MODEL_PASS for row in aggregates)
    if errors:
        status, exit_code = "INVALID", 2
    elif incomplete:
        status, exit_code = "INCOMPLETE", 3
    elif frozen_failure:
        status, exit_code = "FAIL (EXECUTABLE-REFERENCE-MODEL SCOPE)", 1
    elif authoritative:
        status, exit_code = REFERENCE_MODEL_PASS, 0
    else:
        status, exit_code = "DESCRIPTIVE/NON-AUTHORITATIVE", 0
    report = {
        "schema_version": 2, "metric": 3, "input_mode": input_mode,
        "manifest": str(manifest_path), "manifest_sha256": sha256(manifest_path),
        "delta_definition": "HA-VI - OurCC", "positive_favors": "OurCC",
        "comparison": "strict OurCC mean < HA-VI mean",
        "contract_authoritative": authoritative,
        "reference_model_scope": weight_spec.get("reference_model_scope"),
        "testcase_tiers": {"core": list(CORE_TCS),
                           "representative": list(REPRESENTATIVE_TCS)},
        "metric_registry": {f"TC{tc}": value for tc, value in METRIC_REGISTRY.items()},
        "primary_value_definitions": PRIMARY_VALUE_DEFINITIONS,
        "primary_values": primary_values(summaries),
        "coverage": coverage, "order_diagnostics": order_diagnostics(samples),
        "correctness": {"validated_arms": len(normalized),
                        "fingerprint_status_counts": {key: sum(row["fingerprint_status"] == key for row in normalized)
                                                      for key in ("VERIFIED", "MANIFEST_ONLY", "UNAVAILABLE")}},
        "inference": {"deterministic_repeats": "DESCRIPTIVE_ONLY", "authoritative_ci": None,
                      "authoritative_pvalue": None, "independent_random_samples": False},
        "metric_summaries": summaries, "weights": weight_spec, "aggregates": aggregates,
        "core_weight_sensitivity": core_sensitivity,
        "overall_status": status, "exit_code": exit_code, "errors": errors,
    }
    return report, samples, gates, contributions, exit_code


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--evidence-root", type=pathlib.Path)
    source.add_argument("--manifest", type=pathlib.Path)
    parser.add_argument("--weights", type=pathlib.Path)
    parser.add_argument("--output-dir", type=pathlib.Path, default=pathlib.Path("metric3_paired_output"))
    args = parser.parse_args(argv)
    evidence_root = args.evidence_root.expanduser().resolve() if args.evidence_root else None
    manifest_path = (evidence_root / "manifest.json") if evidence_root else args.manifest.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    try:
        report, samples, gates, contributions, code = analyze(
            manifest_path, evidence_root, args.weights.expanduser().resolve() if args.weights else None)
    except InvalidEvidence as error:
        report = {"schema_version": 1, "metric": 3, "overall_status": "INVALID", "exit_code": 2,
                  "errors": [str(error)], "coverage": {}}
        samples, gates, contributions, code = [], [], [], 2
    write_outputs(output_dir, report, samples, gates, contributions)
    print(report["overall_status"])
    return code


if __name__ == "__main__":
    sys.exit(main())
