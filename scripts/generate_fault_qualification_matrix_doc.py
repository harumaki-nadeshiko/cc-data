#!/usr/bin/env python3
"""Generate portable Q1-Q5 fault qualification matrix artifacts."""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tests" / "e2e"))
from fault_qualification_matrix import RUNTIME_DEFAULTS, resolved_matrix  # noqa: E402

JSON_PATH = ROOT / "scripts" / "fault_qualification_matrix.json"
TSV_PATH = ROOT / "docs" / "tool" / "fault_qualification_matrix.tsv"
MD_PATH = ROOT / "docs" / "tool" / "fault_qualification_matrix_zh.md"


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def render_json(rows):
    return json.dumps({"schema": 1, "case_count": len(rows),
                       "common_defaults": RUNTIME_DEFAULTS, "cases": rows},
                      ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def render_tsv(rows):
    columns = ["case_id", "qualification", "tc", "tcid", "topology_label",
               "runner_flag", "cpu_allocation", "gem5_params", "gem5_args",
               "ubio_directory_args", "ubio_args", "networksim_params",
               "compile_args", "fault_rules",
               "verifier_expectations"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns, delimiter="\t",
                            lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({
            "case_id": row["case_id"], "qualification": row["qualification"],
            "tc": row["tc"], "tcid": row["tcid"],
            "topology_label": row["topology"]["label"],
            "runner_flag": row["topology"]["runner_flag"],
            "cpu_allocation": compact(row["cpu_allocation"]),
            "gem5_params": compact(row["gem5"]["params"]),
            "gem5_args": compact(row["gem5"]["args_per_node"]),
            "ubio_directory_args": compact(row["ubio"]["directory_args_per_tc"]),
            "ubio_args": compact({"params": row["ubio"]["params"],
                                    "argv": row["ubio"]["args_per_node_socket"]}),
            "networksim_params": compact(row["networksim"]["params"]),
            "compile_args": compact(row["compile"]),
            "fault_rules": row["fault_rules"],
            "verifier_expectations": compact(row["verifier"]),
        })
    return output.getvalue()


def render_markdown(rows):
    lines = [
        "# 故障资格认证矩阵（Q1–Q5）", "",
        "> 本文档及同目录 TSV、`scripts/fault_qualification_matrix.json` 均由 "
        "`tests/e2e/fault_qualification_matrix.py` 生成；请勿手工修改生成物。", "",
        "## 公共默认值（仅在此集中说明）", "",
        "- CPU：timing；Ruby Sequencer 使用模型默认上限（值以 `0` 表示不覆盖），仅 Q4 near-outstanding 两例显式为 16。",
        "- gem5 协议优化有效默认：`silent-upgrade=0`、`direct-fwd=0`、`ubcc-batch-rs=1`。这些 fault TC 不进入 `run_multi.sh` 的性能 profile 分支。",
        "- HA/清除：`ha-profile=ubcc`、`clear-profile=ack`；metadata 128 MiB（134217728 bytes）。",
        "- L3：256kB、16-way；sync/link 均 2500 ps；Port HWM 8192；networksim pending 65536。",
        "- 无 L3 压力：level=0、target 为空、directory pressure=0、occupancy tracking=0。",
        "- UBIO：这些 TC 的 per-TC directory 参数分支均为空；仅追加完整 fault rules 与 `--metadata-dram-bytes=134217728`。HA-VI 专用的 exact/active/queue 参数在 UBCC profile 下不传递。",
        "- 编译：`aarch64-linux-gnu-gcc -static -O0 -g`，动态加入 NUM_NODES/NUM_SOCKETS；TC230 再加入 `HA_TOPOLOGY_SCENARIO=3`。",
        "- 普通 case 使用四个 8-CPU lane 之一；8N2S/16N1S case 独占 `0-31` 并串行。", "",
        "## 逐 case 自包含参数", "",
    ]
    for row in rows:
        lines += [
            f"### {row['case_id']} — {row['qualification']} / {row['tcid']} / `{row['workload_id']}`",
            "",
            f"- 拓扑：{row['topology']['label']}；runner flag `{row['topology']['runner_flag']}`。",
            f"- CPU：`{compact(row['cpu_allocation'])}`。",
            f"- gem5 effective params：`{compact(row['gem5']['params'])}`。",
            f"- gem5 每节点 args：`{compact(row['gem5']['args_per_node'])}`。",
            f"- UBIO per-TC directory args：`{compact(row['ubio']['directory_args_per_tc'])}`。",
            f"- UBIO effective params：`{compact(row['ubio']['params'])}`。",
            f"- UBIO 每 node/socket args：`{compact(row['ubio']['args_per_node_socket'])}`。",
            f"- networksim params：`{compact(row['networksim']['params'])}`。",
            f"- compile：`{compact(row['compile'])}`。",
            f"- 完整 fault rules：`{row['fault_rules']}`。",
            f"- verifier：`{compact(row['verifier'])}`。", "",
        ]
    return "\n".join(lines).rstrip() + "\n"


def generated_contents():
    rows = resolved_matrix()
    return {JSON_PATH: render_json(rows), TSV_PATH: render_tsv(rows),
            MD_PATH: render_markdown(rows)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="fail if checked-in artifacts differ")
    args = parser.parse_args()
    stale = []
    for path, content in generated_contents().items():
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                stale.append(str(path.relative_to(ROOT)))
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    if stale:
        print("stale generated files: " + ", ".join(stale), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
