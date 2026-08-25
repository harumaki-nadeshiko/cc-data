#!/usr/bin/env python3
"""Create a compact metric 1/2/3 report from structured parser outputs."""

import argparse
import csv
import hashlib
import json
import pathlib
import statistics
import sys


class InputError(Exception):
    pass


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path):
    with path.open() as stream:
        return json.load(stream)


def stat_mean(value, label):
    if not isinstance(value, dict) or value.get("mean") is None:
        raise InputError(f"missing statistic {label}.mean")
    return float(value["mean"])


def stat_cv_pct(value):
    if not isinstance(value, dict):
        return 0.0
    if value.get("cv_pct") is not None:
        return float(value["cv_pct"])
    if value.get("cv") is not None:
        return float(value["cv"]) * 100.0
    return 0.0


def normalize_case(value):
    text = str(value)
    return text if text.upper().startswith("TC") else f"TC{text}"


def profile_means_from_rounds(target2, case):
    values = {profile: [] for profile in ("naive", "spill-noopt", "optimized")}
    for round_report in target2.get("rounds", {}).values():
        cases = round_report.get("cases", {})
        raw_case = case[2:] if case.upper().startswith("TC") else case
        case_report = cases.get(case) or cases.get(raw_case)
        if not case_report:
            continue
        for profile in values:
            latency = case_report.get(profile, {}).get("latency")
            if latency and latency.get("ns", {}).get("mean") is not None:
                values[profile].append(float(latency["ns"]["mean"]))
    return {
        profile: statistics.mean(samples) if samples else None
        for profile, samples in values.items()
    }


def parse_corrected_metric1(data, source_path):
    """Read the corrected spill-512K versus spill-IdealDir Outer delta."""
    if isinstance(data.get("metric1"), dict):
        metric1 = data["metric1"]
        aggregate = metric1.get("aggregate", {})
        delta_ns_stats = aggregate.get("outer_delta_ns")
        delta_cycles_stats = aggregate.get("outer_delta_cycles")
        if not isinstance(delta_ns_stats, dict) or not isinstance(delta_cycles_stats, dict):
            raise InputError("Metric 1 raw report lacks Outer delta aggregates")
        definition = metric1.get("definition", {})
        expected = "mean(spill completed Outer) - mean(ideal completed Outer)"
        if definition.get("outer_delta_ns") != expected:
            raise InputError("Metric 1 raw report uses an unsupported latency definition")
        comparisons = metric1.get("comparisons", [])
        if not comparisons:
            raise InputError("Metric 1 raw report has no complete spill/IdealDir repetitions")
        return {
            "status": metric1.get("status", "INCOMPLETE"),
            "outer_delta_ns": stat_mean(delta_ns_stats, "outer_delta_ns"),
            "outer_delta_cycles": stat_mean(delta_cycles_stats, "outer_delta_cycles"),
            "latency_delta_cv_pct": stat_cv_pct(delta_ns_stats),
            "definition": definition,
            "comparisons": comparisons,
            "source": str(source_path),
            "source_sha256": sha256(source_path),
        }

    if (data.get("definition") ==
            "mean(all completed spill Outer) - mean(all completed ideal Outer)"):
        repeats = data.get("repeats", {})
        complete = [row for row in repeats.values()
                    if row.get("delta_outer_mean_ns") is not None and
                    row.get("delta_outer_mean_cycles_2ghz") is not None]
        if int(data.get("complete_repeats", len(complete))) != len(complete) or not complete:
            raise InputError("Metric 1 Outer/IdealDir summary has incomplete repetitions")
        return {
            "status": data.get("status", "INCOMPLETE"),
            "outer_delta_ns": float(data["delta_mean_ns"]),
            "outer_delta_cycles": float(data["delta_mean_cycles_2ghz"]),
            "latency_delta_cv_pct": (
                float(data.get("delta_stdev_ns", 0.0)) /
                abs(float(data["delta_mean_ns"])) * 100.0
                if float(data["delta_mean_ns"]) else 0.0),
            "definition": {
                "outer_delta_ns": "mean(spill completed Outer) - mean(ideal completed Outer)",
                "cycles_per_ns": 2.0,
                "outer_delta_cycles_strict_max": 50.0,
            },
            "comparisons": complete,
            "source": str(source_path),
            "source_sha256": sha256(source_path),
        }
    raise InputError("Metric 1 JSON is not a corrected completed-Outer result")


def parse_target12(data, source_path, metric1_result=None):
    try:
        target1 = data["target1"]
        target2 = data["target2"]
        t1_stats = target1["statistics"]
        t2_stats = target2["statistics"]
    except (KeyError, TypeError) as error:
        raise InputError("target12 JSON does not contain target1/target2 statistics") from error

    capacity_ratio = stat_mean(t1_stats.get("capacity_ratio"), "capacity_ratio")
    metric1 = {
        "status": "INCOMPLETE",
        "capacity_ratio": capacity_ratio,
        "capacity_increase_pct": (capacity_ratio - 1.0) * 100.0,
        "outer_delta_ns": None,
        "outer_delta_cycles": None,
        "capacity_cv_pct": float(t1_stats.get("capacity_ratio", {}).get("cv_pct", 0.0)),
        "latency_delta_cv_pct": None,
        "definition": {
            "capacity_ratio": "spill effective_unique / naive effective_unique",
            "outer_delta_ns": "mean(spill completed Outer) - mean(ideal completed Outer)",
            "cycles_per_ns": 2.0,
            "guest_timer": "deprecated descriptive only",
        },
        "latency_source": None,
        "latency_source_sha256": None,
    }
    if metric1_result is not None:
        definition = dict(metric1["definition"])
        definition.update(metric1_result["definition"])
        metric1.update({
            "status": ("PASS" if capacity_ratio >= 1.5 and
                       metric1_result["status"] == "PASS" else
                       "INCOMPLETE" if metric1_result["status"] in
                       ("INCOMPLETE", "NOT_REQUESTED") else "FAIL"),
            "outer_delta_ns": metric1_result["outer_delta_ns"],
            "outer_delta_cycles": metric1_result["outer_delta_cycles"],
            "latency_delta_cv_pct": metric1_result["latency_delta_cv_pct"],
            "definition": definition,
            "latency_source": metric1_result["source"],
            "latency_source_sha256": metric1_result["source_sha256"],
            "comparisons": metric1_result["comparisons"],
        })

    applicable = [normalize_case(value)
                  for value in t2_stats.get("applicable_cases", [])]
    case_rows = []
    for case, case_stats in sorted(
            target2.get("case_statistics", {}).items(), key=lambda item: str(item[0])):
        case_label = normalize_case(case)
        profile_stats = case_stats.get("profile_mean_ns", {})
        profile_means = {
            profile: (float(profile_stats[profile]["mean"])
                      if profile_stats.get(profile, {}).get("mean") is not None else None)
            for profile in ("naive", "spill-noopt", "optimized")
        }
        if all(value is None for value in profile_means.values()):
            profile_means = profile_means_from_rounds(target2, case_label)
        reduction = case_stats.get("optimized_reduction_pct")
        case_rows.append({
            "case": case_label,
            "applicable": case_label in applicable,
            "naive_mean_ns": profile_means["naive"],
            "spill_noopt_mean_ns": profile_means["spill-noopt"],
            "optimized_mean_ns": profile_means["optimized"],
            "optimized_reduction_pct": stat_mean(
                reduction, f"case {case_label} optimized_reduction_pct"),
            "reduction_cv_pct": float((reduction or {}).get("cv_pct", 0.0)),
        })
    metric2 = {
        "status": "PASS" if t2_stats.get("pass") else "FAIL",
        "applicable_cases": applicable,
        "equal_weight_mean_reduction_pct": stat_mean(
            t2_stats.get("equal_weight_mean_reduction_pct"),
            "equal_weight_mean_reduction_pct"),
        "cross_round_cv_pct": float(
            t2_stats.get("equal_weight_mean_reduction_pct", {}).get("cv_pct", 0.0)),
        "applicable_set_stable": bool(t2_stats.get("applicable_set_stable", False)),
        "cases": case_rows,
    }
    if isinstance(data.get("all_cases_pass"), bool):
        correctness_gate_status = "PASS" if data["all_cases_pass"] else "FAIL"
    elif isinstance(data.get("status_counts"), dict):
        correctness_gate_status = (
            "PASS" if data["status_counts"].get("FAIL", 0) == 0 else "FAIL")
    else:
        correctness_gate_status = "NOT_EMBEDDED_CHECK_VERIFIER_AND_CHILD_EXITS"
    return {
        "source": str(source_path),
        "source_sha256": sha256(source_path),
        "parser_schema_version": data.get("schema_version"),
        "matrix_status_counts": data.get("status_counts"),
        "correctness_gate_status": correctness_gate_status,
        "metric1": metric1,
        "metric2": metric2,
    }


def parse_metric3(path):
    if path is None:
        return {
            "status": "UNPROVEN",
            "reference_result_supplied": False,
            "contract_authoritative": False,
            "reason": "No executable-reference-model result was supplied.",
            "levels": [],
        }
    data = load_json(path)
    expected = "PASS (EXECUTABLE-REFERENCE-MODEL SCOPE)"
    if data.get("experiment") != "metric3_l3_pressure":
        raise InputError("Metric 3 JSON is not a combined L3-pressure result")
    levels = []
    for pressure in (100, 150):
        item = data.get("levels", {}).get(str(pressure))
        if not item:
            raise InputError(f"Metric 3 result is missing pressure level {pressure}")
        aggregates = item.get("aggregates", [])
        by_name = {row.get("name"): row for row in aggregates}
        if set(by_name) != {"core_equal_weight", "representative_equal_weight"}:
            raise InputError(f"Metric 3 level {pressure} lacks the two frozen tiers")
        levels.append({
            "pressure_level": pressure,
            "status": item.get("status"),
            "core_equal_weight": by_name["core_equal_weight"],
            "representative_equal_weight": by_name["representative_equal_weight"],
            "report": item.get("report"),
        })
    authoritative = data.get("contract_authoritative") is True
    passed = authoritative and data.get("overall_status") == expected and all(
        row["status"] == expected for row in levels)
    return {
        "status": expected if passed else data.get("overall_status", "UNPROVEN"),
        "reference_result_supplied": True,
        "contract_authoritative": authoritative,
        "reference_model_scope": data.get("reference_model_scope"),
        "source": str(path),
        "source_sha256": sha256(path),
        "reason": (
            "Authoritative within the frozen HA-VI executable-reference-model scope; "
            "not a claim of physical customer-silicon measurement."),
        "levels": levels,
    }


def build_report(target12_path, metric1_path, metric3_path, label):
    metric1_result = (parse_corrected_metric1(load_json(metric1_path), metric1_path)
                      if metric1_path else None)
    parsed = parse_target12(load_json(target12_path), target12_path, metric1_result)
    metric3 = parse_metric3(metric3_path)
    metric12_pass = (parsed["metric1"]["status"] == "PASS" and
                     parsed["metric2"]["status"] == "PASS")
    metric3_pass = (metric3["status"] ==
                    "PASS (EXECUTABLE-REFERENCE-MODEL SCOPE)" and
                    metric3["contract_authoritative"])
    return {
        "schema_version": 2,
        "label": label,
        "metric12_source": parsed["source"],
        "metric12_source_sha256": parsed["source_sha256"],
        "metric1_latency_source": parsed["metric1"].get("latency_source"),
        "metric1_latency_source_sha256": parsed["metric1"].get("latency_source_sha256"),
        "parser_schema_version": parsed["parser_schema_version"],
        "matrix_status_counts": parsed["matrix_status_counts"],
        "correctness_gate_status": parsed["correctness_gate_status"],
        "metric1": parsed["metric1"],
        "metric2": parsed["metric2"],
        "metric3": metric3,
        "metric12_overall_pass": metric12_pass,
        "metric123_contract_pass": metric12_pass and metric3_pass,
        "metric123_contract_status": (
            "PASS (EXECUTABLE-REFERENCE-MODEL SCOPE)"
            if metric12_pass and metric3_pass else "NOT_FULLY_SUPPORTED"),
    }


def format_optional(value):
    return "N/A" if value is None else f"{value:.6f}"


def format_optional_unit(value, unit):
    return "N/A" if value is None else f"{value:.6f} {unit}"


def render_markdown(report):
    m1 = report["metric1"]
    m2 = report["metric2"]
    m3 = report["metric3"]
    lines = [
        "# 性能指标 1-3 提取报告", "",
        f"- 数据标签：`{report['label']}`",
        f"- 指标 1：**{m1['status']}**",
        f"- 指标 2：**{m2['status']}**",
        f"- 指标 3：**{m3['status']}**",
        f"- Correctness 门禁：**{report['correctness_gate_status']}**",
        f"- 三指标合同总状态：**{report['metric123_contract_status']}**", "",
        "> 指标 3 的 PASS 绑定冻结的 HA-VI 可执行理论参考模型、2N1S/O3、",
        "> one-way completion 和当前 L3 压力合同，不外推为甲方物理芯片实测。", "",
        "> 若 Correctness 门禁显示 `NOT_EMBEDDED...`，还必须人工核对 verifier、数据 oracle、",
        "> phase 和全部受管 child exit，不能只依据性能解析结果签收。", "",
        "## 指标 1", "",
        "| 项目 | 结果 |", "|---|---:|",
        f"| spill / naive 等效容量比 | {m1['capacity_ratio']:.6f} |",
        f"| 等效容量提升 | {m1['capacity_increase_pct']:.6f}% |",
        f"| spill-512K - spill-IdealDir completed Outer 均值 | {format_optional_unit(m1['outer_delta_ns'], 'ns')} |",
        f"| 按 2 GHz 换算 | {format_optional_unit(m1['outer_delta_cycles'], 'cycles')} |",
        f"| 容量跨轮 CV | {m1['capacity_cv_pct']:.6f}% |",
        f"| 时延差跨轮 CV | {format_optional_unit(m1['latency_delta_cv_pct'], '%')} |", "",
        "## 指标 2", "",
        f"- 适用集合：{', '.join(m2['applicable_cases'])}",
        f"- case-level 等权平均降幅：**{m2['equal_weight_mean_reduction_pct']:.6f}%**",
        f"- 跨轮 CV：{m2['cross_round_cv_pct']:.6f}%", "",
        "| Case | 适用 | naive ns | spill-noopt ns | optimized ns | optimized 降幅 | CV |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for row in m2["cases"]:
        lines.append(
            f"| {row['case']} | {'是' if row['applicable'] else '否'} | "
            f"{format_optional(row['naive_mean_ns'])} | "
            f"{format_optional(row['spill_noopt_mean_ns'])} | "
            f"{format_optional(row['optimized_mean_ns'])} | "
            f"{row['optimized_reduction_pct']:.6f}% | "
            f"{row['reduction_cv_pct']:.6f}% |")
    lines.extend(["", "## 指标 3", "",
                  f"- 状态：**{m3['status']}**",
                  f"- Contract authoritative：`{m3['contract_authoritative']}`",
                  f"- 范围：{m3.get('reference_model_scope') or 'N/A'}",
                  f"- 说明：{m3['reason']}", "",
                  "| Pressure | Tier | OurCC ticks/op | HA-VI ticks/op | Delta | Reduction |",
                  "|---:|---|---:|---:|---:|---:|"])
    for level in m3.get("levels", []):
        for name in ("core_equal_weight", "representative_equal_weight"):
            row = level[name]
            lines.append(
                f"| {level['pressure_level']}% | {name} | "
                f"{row['ourcc_ticks_per_operation']:.6f} | "
                f"{row['ha_vi_ticks_per_operation']:.6f} | "
                f"{row['delta_ticks']:.6f} | {row['ourcc_reduction_pct']:.6f}% |")
    lines.extend(["", "## 输入", "",
                  f"- 指标 1/2 JSON：`{report['metric12_source']}`",
                  f"- SHA-256：`{report['metric12_source_sha256']}`"])
    if m3.get("source"):
        lines.extend([f"- 指标 3 模型 JSON：`{m3['source']}`",
                      f"- SHA-256：`{m3['source_sha256']}`"])
    if m1.get("latency_source"):
        lines.extend([f"- 指标 1 Outer/IdealDir JSON：`{m1['latency_source']}`",
                      f"- SHA-256：`{m1['latency_source_sha256']}`"])
    return "\n".join(lines) + "\n"


def compact_text(report):
    m1, m2, m3 = report["metric1"], report["metric2"], report["metric3"]
    return "\n".join([
        f"metric1={m1['status']} capacity_ratio={m1['capacity_ratio']:.6f} "
        f"increase={m1['capacity_increase_pct']:.3f}% "
        f"outer_delta={format_optional_unit(m1['outer_delta_ns'], 'ns')} "
        f"delta_cycles={format_optional(m1['outer_delta_cycles'])}",
        f"metric2={m2['status']} applicable={','.join(m2['applicable_cases'])} "
        f"equal_weight_reduction={m2['equal_weight_mean_reduction_pct']:.3f}% "
        f"cv={m2['cross_round_cv_pct']:.3f}%",
        f"metric3={m3['status']} authoritative={str(m3['contract_authoritative']).lower()}",
        f"correctness_gate={report['correctness_gate_status']}",
        f"metric123_contract={report['metric123_contract_status']}",
    ]) + "\n"


def tsv_rows(report):
    m1, m2, m3 = report["metric1"], report["metric2"], report["metric3"]
    rows = [
        ["metric1", "capacity_ratio", m1["capacity_ratio"], "ratio", m1["status"]],
        ["metric1", "capacity_increase", m1["capacity_increase_pct"], "pct", m1["status"]],
        ["metric1", "outer_delta", m1["outer_delta_ns"], "ns", m1["status"]],
        ["metric1", "outer_delta", m1["outer_delta_cycles"], "cycles", m1["status"]],
        ["metric2", "equal_weight_reduction", m2["equal_weight_mean_reduction_pct"],
         "pct", m2["status"]],
        ["metric3", "contract_status", m3["status"], "status", m3["status"]],
    ]
    for row in m2["cases"]:
        rows.append(["metric2", f"{row['case']}_optimized_reduction",
                     row["optimized_reduction_pct"], "pct",
                      "APPLICABLE" if row["applicable"] else "NOT_APPLICABLE"])
    for level in m3.get("levels", []):
        for name in ("core_equal_weight", "representative_equal_weight"):
            row = level[name]
            rows.append(["metric3", f"p{level['pressure_level']}_{name}_reduction",
                         row["ourcc_reduction_pct"], "pct", row["verdict"]])
    return rows


def write_outputs(report, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    with (out_dir / "metric123_report.json").open("w") as stream:
        json.dump(report, stream, indent=2, sort_keys=True)
        stream.write("\n")
    (out_dir / "metric123_report.md").write_text(render_markdown(report))
    (out_dir / "metric123_compact.txt").write_text(compact_text(report))
    with (out_dir / "metric123_key_values.tsv").open("w", newline="") as stream:
        writer = csv.writer(stream, delimiter="\t")
        writer.writerow(["metric", "item", "value", "unit", "status"])
        writer.writerows(tsv_rows(report))


def main():
    parser = argparse.ArgumentParser(
        description="Generate compact metric 1/2/3 reports from structured JSON")
    parser.add_argument("--target12-json", required=True,
                        help="performance_comparison.json or final summary.json")
    parser.add_argument("--metric1-json",
                        help="corrected raw report.json or spill-vs-Ideal summary.json")
    parser.add_argument("--metric3-v4-json",
                        help="combined Metric 3 v4 L3-pressure report")
    parser.add_argument("--label", default="manual-log-extraction")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    target12_path = pathlib.Path(args.target12_json).expanduser().resolve()
    metric1_path = (pathlib.Path(args.metric1_json).expanduser().resolve()
                    if args.metric1_json else None)
    metric3_path = (pathlib.Path(args.metric3_v4_json).expanduser().resolve()
                    if args.metric3_v4_json else None)
    try:
        report = build_report(target12_path, metric1_path, metric3_path, args.label)
        out_dir = pathlib.Path(args.out_dir).expanduser().resolve()
        write_outputs(report, out_dir)
    except (InputError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(compact_text(report), end="")
    print(f"out_dir={out_dir}")
    return 0 if report["metric123_contract_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
