#!/usr/bin/env python3
"""Hard gate for Metric3 L3-pressure stage 1."""

import argparse
import hashlib
import json
import pathlib
import re
import sys

EXPECTED_EXITS = {"gem5_node0.exit", "gem5_node1.exit", "networksim.exit",
                  "ubio_n0_s0.exit", "ubio_n1_s0.exit"}
OCC_SAMPLE = re.compile(
    r"\[L3-OCC-SAMPLE\].*node=(\d+).*capacity=(\d+).*current=(\d+).*"
    r"peak=(\d+).*dsm=(\d+).*metadata=(\d+).*other=(\d+).*"
    r"peak_dsm=(\d+).*peak_metadata=(\d+).*peak_other=(\d+).*"
    r"alloc=(\d+).*dealloc=(\d+).*replacements=(\d+).*"
    r"dsm_to_metadata=(\d+).*metadata_to_dsm=(\d+)"
    r"(?:.*other_to_metadata=(\d+).*metadata_to_other=(\d+))?")
PRESSURE = re.compile(
    r"\[L3-PRESSURE\]\s+node=(\d+)\s+level_pct=(\d+)\s+"
    r"target_lines_per_hnf=(\d+)\s+generated_lines=(\d+)\s+"
    r"private_cache_lines=(\d+)\s+source=(\S+)\s+"
    r"cache_lines_per_hnf=(\d+)\s+sets=(\d+)\s+seed=(\d+)\s+"
    r"phase=(\S+)\s+progress=(\d+)")


def atomic(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=pathlib.Path, required=True)
    parser.add_argument("--output-dir", type=pathlib.Path, required=True)
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    base = args.manifest.parent
    errors, checks, paired, evidence_hashes = [], [], [], {}
    if {sample.get("pair") for sample in manifest.get("samples", [])} != {1}:
        errors.append("stage1 manifest must contain pair 1 only")
    for sample in manifest.get("samples", []):
        arm_metrics = {}
        for arm in ("ourcc", "ha-vi"):
            item = sample["arms"][arm]
            result_path = base / item["result"]
            log_dir = base / item["log_dir"]
            try: result = json.loads(result_path.read_text())
            except Exception as exc:
                errors.append(f"{sample['pair_id']} {arm}: unreadable result: {exc}")
                continue
            label = f"{sample['pair_id']} {arm}"
            evidence_hashes[str(result_path.resolve())] = sha256(result_path)
            expected_identity = {
                "pair_id": sample["pair_id"], "pair": sample["pair"],
                "tc": sample["tc"], "pressure_level": sample["pressure_level"],
                "order": sample["order"], "arm": arm,
                "l3_size": manifest.get("l3", {}).get("size"),
                "l3_assoc": manifest.get("l3", {}).get("assoc"),
                "seed": sample.get("seed"),
                "experiment_mode": manifest.get("experiment_mode"),
                "directory_pressure_lines": manifest.get(
                    "directory_pressure_lines"),
            }
            for key, value in expected_identity.items():
                if result.get(key) != value:
                    errors.append(label + f": result identity mismatch {key}")
            if result.get("status") != "PASS" or result.get("return_code") != 0:
                errors.append(label + ": correctness/result failure")
            verifier = log_dir / f"verify_tc{sample['tc']}.log"
            if not verifier.is_file() or not verifier.read_text(errors="replace").rstrip().endswith(
                    f">>> TC{sample['tc']} PASSED <<<"):
                errors.append(label + ": verifier failure")
            else:
                evidence_hashes[str(verifier.resolve())] = sha256(verifier)
            child = log_dir / f"child_status_tc{sample['tc']}"
            found = {p.name for p in child.glob("*.exit")} if child.is_dir() else set()
            if found != EXPECTED_EXITS or any((child / name).read_text().strip() != "0"
                                              for name in found):
                errors.append(label + ": child exit failure/identity mismatch")
            else:
                for name in sorted(found):
                    path = child / name
                    evidence_hashes[str(path.resolve())] = sha256(path)
            expected_profile = ("ha_endpoint_profile=ubcc clear_profile=lossless-oneway"
                                if arm == "ourcc" else
                                "ha_endpoint_profile=ha-vi clear_profile=ack")
            profile_lines = []
            occ_samples = []
            pressure_rows = []
            for gem5_log in sorted(log_dir.glob(f"gem5_tc{sample['tc']}_node*/*.log")):
                text = gem5_log.read_text(errors="replace")
                profile_lines += [line for line in text.splitlines() if "EPBACKEND-PROFILE" in line]
                occ_samples += [OCC_SAMPLE.search(line) for line in text.splitlines()
                                if "[L3-OCC-SAMPLE]" in line]
            for simout in sorted(log_dir.glob(f"simout_tc{sample['tc']}_node*.log")):
                pressure_rows += [PRESSURE.search(line)
                                  for line in simout.read_text(errors="replace").splitlines()
                                  if "[L3-PRESSURE]" in line]
            if len(profile_lines) != 2 or any(expected_profile not in line for line in profile_lines):
                errors.append(label + ": profile identity mismatch")
            occ_samples = [row for row in occ_samples if row]
            pressure_rows = [row for row in pressure_rows if row]
            capacities = {int(row.group(2)) for row in occ_samples}
            expected_lines = next(iter(capacities)) if len(capacities) == 1 else None
            expected_sets = (expected_lines // result["l3_assoc"]
                             if expected_lines is not None else None)
            if expected_lines is None:
                errors.append(label + ": inconsistent/missing occupancy capacity")
            by_plane = {}
            for row in pressure_rows:
                by_plane.setdefault(int(row.group(1)), []).append(row)
            if set(by_plane) != {0, 1}:
                errors.append(label + ": missing pressure markers for both planes")
            for plane, rows in by_plane.items():
                if expected_lines is None or expected_sets is None:
                    continue
                identities = {(int(row.group(2)), int(row.group(3)),
                               int(row.group(4)), int(row.group(5)),
                               row.group(6), int(row.group(7)),
                               int(row.group(8)), int(row.group(9)))
                              for row in rows}
                target_lines = expected_lines * sample["pressure_level"] // 100
                private_lines = 4096
                working_lines = private_lines + target_lines
                expected_identity = {(sample["pressure_level"], target_lines,
                                      working_lines, private_lines,
                                      "local_private_writeback",
                                      expected_lines, expected_sets,
                                      sample["seed"])}
                if identities != expected_identity:
                    errors.append(label + f": plane {plane} pressure identity mismatch")
                if not any(row.group(10) == "fill_begin" and int(row.group(11)) == 0
                           for row in rows) or not any(
                        row.group(10) == "fill_done" and
                        int(row.group(11)) == working_lines for row in rows):
                    errors.append(label + f": plane {plane} pressure fill incomplete")
                if manifest.get("experiment_mode") == "l3-offload":
                    expected_directory = manifest.get("directory_pressure_lines")
                    if not any(row.group(10) == "directory_begin" and
                               int(row.group(11)) == 0 for row in rows) or not any(
                            row.group(10) == "directory_done" and
                            int(row.group(11)) == expected_directory for row in rows):
                        errors.append(label + f": plane {plane} directory pressure incomplete")
            if not occ_samples:
                errors.append(label + ": missing [L3-OCC-SAMPLE] time series")
            else:
                threshold = 0.95 if sample["pressure_level"] == 100 else 0.99
                by_node = {}
                for row in occ_samples:
                    by_node.setdefault(int(row.group(1)), []).append(row)
                if set(by_node) != {0, 1}:
                    errors.append(label + ": occupancy samples missing a node")
                for node, rows in by_node.items():
                    capacity = int(rows[0].group(2))
                    peak = max(int(row.group(4)) for row in rows)
                    metadata_peak = max(int(row.group(9)) for row in rows)
                    dealloc = max(int(row.group(12)) for row in rows)
                    if peak / capacity < threshold:
                        errors.append(label + f": node {node} peak {peak}/{capacity} below {threshold:.0%}")
                    if sample["pressure_level"] == 150 and dealloc <= 0:
                        errors.append(label + f": node {node} 150% pressure has no deallocations")
                    if arm == "ourcc" and metadata_peak <= 0:
                        errors.append(label + f": node {node} has no metadata L3 occupancy")
            if arm == "ourcc":
                ubio_text = "".join(
                    path.read_text(errors="replace")
                    for path in log_dir.glob(
                        f"ubio_tc{sample['tc']}_n*_s*/stdout.log"))
                if "resident_capacity=57344 entries" not in ubio_text:
                    errors.append(label + ": ResidentDir capacity is not 57344 entries")
                if manifest.get("experiment_mode") == "l3-offload" and \
                        "RESIDENT-SPILL-DONE" not in ubio_text:
                    errors.append(label + ": no completed spill/offload evidence")
                marker_count = 0
                for debug_log in log_dir.glob(
                        f"gem5_tc{sample['tc']}_node*/gem5_debug.log"):
                    marker_count += debug_log.read_text(errors="replace").count(
                        "[HNF-EP-UNIQUE-MISS-FALLBACK]")
                if sample["tc"] == 230 and marker_count == 0:
                    errors.append(label + ": unique refill fallback marker missing")
            arm_metrics[arm] = result.get("metrics")
            checks.append({"pair_id": sample["pair_id"], "arm": arm,
                            "pressure_level": sample["pressure_level"],
                            "metrics_present": isinstance(result.get("metrics"), dict),
                            "occupancy_samples": len(occ_samples),
                            "pressure_markers": len(pressure_rows),
                            "metadata_peak_sampled": max(
                                (int(row.group(9)) for row in occ_samples), default=0),
                            "dsm_to_metadata": max(
                                (int(row.group(14)) for row in occ_samples), default=0),
                            "metadata_to_dsm": max(
                                (int(row.group(15)) for row in occ_samples), default=0),
                            "other_to_metadata": max(
                                (int(row.group(16) or 0) for row in occ_samples), default=0),
                            "metadata_to_other": max(
                                (int(row.group(17) or 0) for row in occ_samples), default=0)})
        if set(arm_metrics) == {"ourcc", "ha-vi"} and all(isinstance(v, dict) for v in arm_metrics.values()):
            if set(arm_metrics["ourcc"]) != set(arm_metrics["ha-vi"]):
                errors.append(sample["pair_id"] + ": paired metric identity mismatch")
            else:
                paired.append({"pair_id": sample["pair_id"], "tc": sample["tc"],
                               "pressure_level": sample["pressure_level"],
                               "metrics": sorted(arm_metrics["ourcc"])})
        else:
            errors.append(sample["pair_id"] + ": missing paired metrics")
    decision = "CONTINUE" if not errors else "STOP"
    payload = {"schema_version": 1, "decision": decision,
                "manifest": str(args.manifest.resolve()), "errors": errors,
                "manifest_sha256": sha256(args.manifest),
                "evidence_sha256": evidence_hashes,
                "experiment_identity": {
                    "experiment_root": str(base.resolve()),
                    "l3_size": manifest.get("l3", {}).get("size"),
                    "l3_assoc": manifest.get("l3", {}).get("assoc"),
                    "pressure_levels": manifest.get("pressure_levels"),
                    "base_seed": manifest.get("seed"),
                    "experiment_mode": manifest.get("experiment_mode"),
                    "directory_pressure_lines": manifest.get(
                        "directory_pressure_lines"),
                    "testcases": manifest.get("testcases"),
                },
                "checks": checks, "paired_metrics": paired}
    out = args.output_dir
    atomic(out / "stage1_gate.json", json.dumps(payload, indent=2, sort_keys=True) + "\n")
    md = ["# Metric3 L3 pressure stage-1 gate", "", f"**Decision: {decision}**", "",
          f"- Arms checked: {len(checks)}", f"- Paired samples: {len(paired)}",
          f"- Errors: {len(errors)}", ""]
    if errors: md += ["## Errors", ""] + [f"- {error}" for error in errors]
    atomic(out / "stage1_gate.md", "\n".join(md) + "\n")
    atomic(out / "stage1_gate.decision", decision + "\n")
    print(decision)
    return 0 if decision == "CONTINUE" else 1


if __name__ == "__main__":
    sys.exit(main())
