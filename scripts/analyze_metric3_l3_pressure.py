#!/usr/bin/env python3
"""Analyze the combined L3-pressure manifest per pressure level.

This focused extension reuses analyze_metric3_paired.py without mixing the 100%
and 150% populations into one repeat count.
"""

import argparse
import json
import pathlib
import sys

import analyze_metric3_paired


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    parser.add_argument("--weights", type=pathlib.Path)
    args = parser.parse_args()
    source = json.loads(args.manifest.read_text())
    identity_errors = []
    for sample in source.get("samples", []):
        for arm, item in sample.get("arms", {}).items():
            result_path = (args.manifest.parent / item["result"]).resolve()
            try:
                result = json.loads(result_path.read_text())
            except Exception as error:
                identity_errors.append(f"{sample.get('pair_id')} {arm}: {error}")
                continue
            expected = {
                "pair_id": sample.get("pair_id"), "pair": sample.get("pair"),
                "tc": sample.get("tc"), "order": sample.get("order"),
                "arm": arm, "pressure_level": sample.get("pressure_level"),
                "seed": sample.get("seed"),
                "l3_size": source.get("l3", {}).get("size"),
                "l3_assoc": source.get("l3", {}).get("assoc"),
                "experiment_mode": source.get("experiment_mode"),
                "directory_pressure_lines": source.get(
                    "directory_pressure_lines"),
            }
            for key, value in expected.items():
                if result.get(key) != value:
                    identity_errors.append(
                        f"{sample.get('pair_id')} {arm}: result {key} mismatch")
    if identity_errors:
        atomic_json(args.output_dir / "metric3_l3_pressure_report.json", {
            "schema_version": 1, "experiment": "metric3_l3_pressure",
            "overall_status": "INVALID", "exit_code": 2,
            "errors": identity_errors,
        })
        print("INVALID")
        return 2
    reports = {}
    worst = 0
    for level in source.get("pressure_levels", (100, 150)):
        subset = dict(source)
        subset["pressure_levels"] = [level]
        level_samples = []
        for original in source["samples"]:
            if original.get("pressure_level") != level:
                continue
            sample = dict(original)
            sample["arms"] = {arm: dict(item)
                              for arm, item in original["arms"].items()}
            for item in sample["arms"].values():
                item["result"] = str((args.manifest.parent / item["result"]).resolve())
                item["log_dir"] = str((args.manifest.parent / item["log_dir"]).resolve())
            level_samples.append(sample)
        subset["samples"] = level_samples
        subset["expected_repeats"] = len({sample["pair"] for sample in subset["samples"]})
        path = args.output_dir / f"manifest_p{level}.json"
        atomic_json(path, subset)
        report, samples, gates, contributions, code = analyze_metric3_paired.analyze(
            path.resolve(), None, args.weights.resolve() if args.weights else None)
        level_dir = args.output_dir / f"p{level}"
        analyze_metric3_paired.write_outputs(level_dir, report, samples, gates, contributions)
        reports[str(level)] = {"status": report["overall_status"], "exit_code": code,
                               "contract_authoritative": report.get("contract_authoritative", False),
                               "reference_model_scope": report.get("reference_model_scope"),
                               "aggregates": report.get("aggregates", []),
                               "primary_values": report.get("primary_values", {}),
                               "report": str(level_dir / "metric3_paired_report.json")}
        worst = max(worst, code)
    statuses = [item["status"] for item in reports.values()]
    authoritative = bool(reports) and all(
        item.get("contract_authoritative") for item in reports.values())
    scopes = {item.get("reference_model_scope") for item in reports.values()}
    reference_scope = scopes.pop() if len(scopes) == 1 else None
    expected_pass = analyze_metric3_paired.REFERENCE_MODEL_PASS
    if worst == 0 and authoritative and all(status == expected_pass for status in statuses):
        overall = expected_pass
    elif worst == 0:
        overall = "DESCRIPTIVE/NON-AUTHORITATIVE"
    elif any(status == "INVALID" for status in statuses):
        overall = "INVALID"
    elif any(status == "INCOMPLETE" for status in statuses):
        overall = "INCOMPLETE"
    else:
        overall = "FAIL (EXECUTABLE-REFERENCE-MODEL SCOPE)"
    combined = {"schema_version": 2, "experiment": "metric3_l3_pressure",
                 "source_manifest": str(args.manifest.resolve()), "levels": reports,
                 "contract_authoritative": authoritative,
                 "reference_model_scope": reference_scope,
                 "weights_source": str(args.weights.resolve()) if args.weights else None,
                 "overall_status": overall,
                 "exit_code": worst}
    atomic_json(args.output_dir / "metric3_l3_pressure_report.json", combined)
    print(combined["overall_status"])
    return worst


if __name__ == "__main__":
    sys.exit(main())
