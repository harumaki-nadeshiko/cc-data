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


def parse_target12(data, source_path):
    try:
        target1 = data["target1"]
        target2 = data["target2"]
        t1_stats = target1["statistics"]
        t2_stats = target2["statistics"]
    except (KeyError, TypeError) as error:
        raise InputError("target12 JSON does not contain target1/target2 statistics") from error

    delta_ns_stats = (t1_stats.get("guest_delta_ns_per_operation") or
                      t1_stats.get("guest_reuse_delta_ns_per_operation"))
    delta_cycles_stats = (t1_stats.get("guest_delta_cycles") or
                          t1_stats.get("guest_reuse_delta_cycles_at_2ghz"))
    metric1 = {
        "status": "PASS" if t1_stats.get("pass") else "FAIL",
        "capacity_ratio": stat_mean(t1_stats.get("capacity_ratio"), "capacity_ratio"),
        "capacity_increase_pct": (
            stat_mean(t1_stats.get("capacity_ratio"), "capacity_ratio") - 1.0) * 100.0,
        "guest_delta_ns_per_operation": stat_mean(delta_ns_stats, "guest_delta_ns"),
        "guest_delta_cycles": stat_mean(delta_cycles_stats, "guest_delta_cycles"),
        "capacity_cv_pct": float(t1_stats.get("capacity_ratio", {}).get("cv_pct", 0.0)),
        "latency_delta_cv_pct": float((delta_ns_stats or {}).get("cv_pct", 0.0)),
    }

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
            "model_supplied": False,
            "reason": "No executable theoretical-model JSON was supplied.",
            "sensitivity_rows": [],
        }
    data = load_json(path)
    model = data.get("model", {})
    weighted = data.get("caller_weighted_results", [])
    grouped = {}
    for row in weighted:
        key = (int(row["nodes"]), row["scale_scheme"])
        grouped.setdefault(key, {})[row["protocol"]] = row
    comparisons = []
    for (nodes, scheme), protocols in sorted(grouped.items()):
        ha = protocols.get("ha")
        if not ha:
            continue
        for protocol, candidate in sorted(protocols.items()):
            if not protocol.startswith("ourcc"):
                continue
            comparisons.append({
                "nodes": nodes,
                "scale_scheme": scheme,
                "ourcc_protocol": protocol,
                "ha_mean_t_resp_ns": float(ha["mean_t_resp_ns"]),
                "ourcc_mean_t_resp_ns": float(candidate["mean_t_resp_ns"]),
                "ha_minus_ourcc_resp_ns": (
                    float(ha["mean_t_resp_ns"]) - float(candidate["mean_t_resp_ns"])),
                "ha_mean_t_release_ns": float(ha["mean_t_release_ns"]),
                "ourcc_mean_t_release_ns": float(candidate["mean_t_release_ns"]),
                "ha_minus_ourcc_release_ns": (
                    float(ha["mean_t_release_ns"]) - float(candidate["mean_t_release_ns"])),
                "caller_supplied_operations": int(ha["caller_supplied_operations"]),
            })
    return {
        "status": "UNPROVEN",
        "model_supplied": True,
        "source": str(path),
        "source_sha256": sha256(path),
        "model_final_weights_frozen": bool(model.get("final_weights_frozen", False)),
        "tau_ns": model.get("tau_ns"),
        "reason": (
            "The model is sensitivity analysis only. Contract HA profile, completion "
            "boundary, placement, local P terms, and final weights are not all frozen."),
        "sensitivity_rows": comparisons,
    }


def build_report(target12_path, metric3_path, label):
    parsed = parse_target12(load_json(target12_path), target12_path)
    return {
        "schema_version": 1,
        "label": label,
        "metric12_source": parsed["source"],
        "metric12_source_sha256": parsed["source_sha256"],
        "parser_schema_version": parsed["parser_schema_version"],
        "matrix_status_counts": parsed["matrix_status_counts"],
        "correctness_gate_status": parsed["correctness_gate_status"],
        "metric1": parsed["metric1"],
        "metric2": parsed["metric2"],
        "metric3": parse_metric3(metric3_path),
        "metric12_overall_pass": (
            parsed["metric1"]["status"] == "PASS" and
            parsed["metric2"]["status"] == "PASS"),
        "metric123_contract_pass": False,
        "metric123_contract_status": "UNPROVEN_DUE_TO_METRIC3",
    }


def format_optional(value):
    return "N/A" if value is None else f"{value:.6f}"


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
        "> 指标 1/2 的 PASS 不包含指标 3。指标 3 的模型输出仅为参数敏感性分析，",
        "> 不能把正的 `HA - OurCC` 示例差值直接写成合同 PASS。", "",
        "> 若 Correctness 门禁显示 `NOT_EMBEDDED...`，还必须人工核对 verifier、数据 oracle、",
        "> phase 和全部受管 child exit，不能只依据性能解析结果签收。", "",
        "## 指标 1", "",
        "| 项目 | 结果 |", "|---|---:|",
        f"| spill / naive 等效容量比 | {m1['capacity_ratio']:.6f} |",
        f"| 等效容量提升 | {m1['capacity_increase_pct']:.6f}% |",
        f"| spill-noopt - naive guest 时延 | {m1['guest_delta_ns_per_operation']:.6f} ns/op |",
        f"| 按合同频率换算 | {m1['guest_delta_cycles']:.6f} cycles |",
        f"| 容量跨轮 CV | {m1['capacity_cv_pct']:.6f}% |",
        f"| 时延差跨轮 CV | {m1['latency_delta_cv_pct']:.6f}% |", "",
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
                  f"- 原因：{m3['reason']}"])
    if m3["model_supplied"]:
        lines.extend([
            f"- 模型最终权重已冻结：`{m3['model_final_weights_frozen']}`",
            f"- 模型 `tau`：{m3['tau_ns']} ns", "",
        ])
        if m3["sensitivity_rows"]:
            lines.extend([
                "| Nodes | Scheme | OurCC profile | HA resp ns | OurCC resp ns | HA-OurCC ns |",
                "|---:|---|---|---:|---:|---:|",
            ])
            for row in m3["sensitivity_rows"]:
                lines.append(
                    f"| {row['nodes']} | {row['scale_scheme']} | "
                    f"{row['ourcc_protocol']} | {row['ha_mean_t_resp_ns']:.3f} | "
                    f"{row['ourcc_mean_t_resp_ns']:.3f} | "
                    f"{row['ha_minus_ourcc_resp_ns']:.3f} |")
    lines.extend(["", "## 输入", "",
                  f"- 指标 1/2 JSON：`{report['metric12_source']}`",
                  f"- SHA-256：`{report['metric12_source_sha256']}`"])
    if m3.get("source"):
        lines.extend([f"- 指标 3 模型 JSON：`{m3['source']}`",
                      f"- SHA-256：`{m3['source_sha256']}`"])
    return "\n".join(lines) + "\n"


def compact_text(report):
    m1, m2, m3 = report["metric1"], report["metric2"], report["metric3"]
    return "\n".join([
        f"metric1={m1['status']} capacity_ratio={m1['capacity_ratio']:.6f} "
        f"increase={m1['capacity_increase_pct']:.3f}% "
        f"guest_delta={m1['guest_delta_ns_per_operation']:.3f}ns/op "
        f"delta_cycles={m1['guest_delta_cycles']:.3f}",
        f"metric2={m2['status']} applicable={','.join(m2['applicable_cases'])} "
        f"equal_weight_reduction={m2['equal_weight_mean_reduction_pct']:.3f}% "
        f"cv={m2['cross_round_cv_pct']:.3f}%",
        f"metric3={m3['status']} model_supplied={str(m3['model_supplied']).lower()}",
        f"correctness_gate={report['correctness_gate_status']}",
        f"metric123_contract={report['metric123_contract_status']}",
    ]) + "\n"


def tsv_rows(report):
    m1, m2, m3 = report["metric1"], report["metric2"], report["metric3"]
    rows = [
        ["metric1", "capacity_ratio", m1["capacity_ratio"], "ratio", m1["status"]],
        ["metric1", "capacity_increase", m1["capacity_increase_pct"], "pct", m1["status"]],
        ["metric1", "guest_delta", m1["guest_delta_ns_per_operation"], "ns/op", m1["status"]],
        ["metric1", "guest_delta", m1["guest_delta_cycles"], "cycles", m1["status"]],
        ["metric2", "equal_weight_reduction", m2["equal_weight_mean_reduction_pct"],
         "pct", m2["status"]],
        ["metric3", "contract_status", m3["status"], "status", m3["status"]],
    ]
    for row in m2["cases"]:
        rows.append(["metric2", f"{row['case']}_optimized_reduction",
                     row["optimized_reduction_pct"], "pct",
                     "APPLICABLE" if row["applicable"] else "NOT_APPLICABLE"])
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
    parser.add_argument("--metric3-model-json",
                        help="optional output from ha_vi_bitmap_baseline.py")
    parser.add_argument("--label", default="manual-log-extraction")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    target12_path = pathlib.Path(args.target12_json).expanduser().resolve()
    metric3_path = (pathlib.Path(args.metric3_model_json).expanduser().resolve()
                    if args.metric3_model_json else None)
    try:
        report = build_report(target12_path, metric3_path, args.label)
        out_dir = pathlib.Path(args.out_dir).expanduser().resolve()
        write_outputs(report, out_dir)
    except (InputError, OSError, ValueError, KeyError, json.JSONDecodeError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    print(compact_text(report), end="")
    print(f"out_dir={out_dir}")
    return 0 if report["metric12_overall_pass"] else 1


if __name__ == "__main__":
    sys.exit(main())
