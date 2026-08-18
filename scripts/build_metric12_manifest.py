#!/usr/bin/env python3
"""Build a target 1/2 log manifest from an easy-to-edit TSV inventory."""

import argparse
import csv
import json
import pathlib
import sys


PROFILES = {"naive", "spill-noopt", "optimized", "spill-opt", "spill_opt",
            "spill_noopt"}
TARGET_ALIASES = {
    "1": "target1", "metric1": "target1", "target1": "target1",
    "2": "target2", "metric2": "target2", "target2": "target2",
}
DEFAULT_THRESHOLDS = {
    "target1_capacity_ratio_min": 1.5,
    "target1_max_extra_cycles": 50.0,
    "target1_contract_clock_hz": 2000000000,
    "target2_applicable_naive_mean_ns": 500.0,
    "target2_equal_weight_reduction_min_pct": 10.0,
}


class InputError(Exception):
    pass


def split_list(value):
    return [item.strip() for item in (value or "").split(";") if item.strip()]


def load_json_object(path):
    if not path:
        return {}
    with pathlib.Path(path).open() as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise InputError(f"{path} must contain a JSON object")
    return value


def build_manifest(args):
    inventory = pathlib.Path(args.inventory)
    with inventory.open(newline="") as stream:
        reader = csv.DictReader(
            (line for line in stream if line.strip() and not line.lstrip().startswith("#")),
            delimiter="\t")
        required_columns = {"target", "round", "case", "profile", "phase",
                            "simout_logs", "ubio_logs", "required_markers"}
        missing_columns = required_columns - set(reader.fieldnames or [])
        if missing_columns:
            raise InputError(f"inventory is missing columns: {sorted(missing_columns)}")

        target1_runs = []
        target2_runs = []
        seen = set()
        for line_number, row in enumerate(reader, 2):
            target_value = (row["target"] or "").strip().lower()
            try:
                target = TARGET_ALIASES[target_value]
            except KeyError as error:
                raise InputError(
                    f"line {line_number}: unknown target {row['target']!r}") from error
            try:
                round_id = int(row["round"])
            except ValueError as error:
                raise InputError(
                    f"line {line_number}: round must be an integer") from error
            if round_id < 1:
                raise InputError(f"line {line_number}: round must be positive")
            profile = (row["profile"] or "").strip()
            if profile not in PROFILES:
                raise InputError(
                    f"line {line_number}: unsupported profile {profile!r}")
            simout_logs = split_list(row["simout_logs"])
            ubio_logs = split_list(row["ubio_logs"])
            markers = split_list(row["required_markers"])
            phase = (row["phase"] or "").strip()
            case = (row["case"] or "").strip()

            if target == "target1":
                key = (target, round_id, profile)
                if not simout_logs or not ubio_logs:
                    raise InputError(
                        f"line {line_number}: target1 requires simout_logs and ubio_logs")
                record = {
                    "round": round_id,
                    "profile": profile,
                    "timer_phase": phase or "post_pressure_catalog_reuse",
                    "simout_logs": simout_logs,
                    "ubio_logs": ubio_logs,
                }
                if markers:
                    record["required_markers"] = markers
                target1_runs.append(record)
            else:
                if not case or not phase or not simout_logs:
                    raise InputError(
                        f"line {line_number}: target2 requires case, phase, and simout_logs")
                key = (target, round_id, case, profile)
                record = {
                    "round": round_id,
                    "case": case,
                    "profile": profile,
                    "phase": phase,
                    "simout_logs": simout_logs,
                }
                if markers:
                    record["required_markers"] = markers
                target2_runs.append(record)
            if key in seen:
                raise InputError(f"line {line_number}: duplicate record {key}")
            seen.add(key)

    if not target1_runs or not target2_runs:
        raise InputError("inventory must contain both target1 and target2 records")
    return {
        "schema_version": 1,
        "base_dir": args.base_dir,
        "metadata": load_json_object(args.metadata_json),
        "thresholds": DEFAULT_THRESHOLDS,
        "target1_runs": target1_runs,
        "target2_runs": target2_runs,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Convert a TSV log inventory to a target 1/2 JSON manifest")
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--base-dir", default=".")
    parser.add_argument("--metadata-json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        manifest = build_manifest(args)
        output = pathlib.Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w") as stream:
            json.dump(manifest, stream, indent=2, sort_keys=True)
            stream.write("\n")
    except (InputError, OSError, ValueError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(json.dumps({
        "output": str(output.resolve()),
        "target1_records": len(manifest["target1_runs"]),
        "target2_records": len(manifest["target2_runs"]),
    }, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
