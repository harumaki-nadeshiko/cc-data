#!/usr/bin/env python3
"""Generate the UBCC testcase matrix workbook (.xlsx).

The testcase inventory, per-TC purpose / pass criteria and the fault
qualification cases are read from repository source documents at generation
time (no hand-copied inventory).  The centralized enumerations below are the
single source of truth for every workbook value.

Run on the host (NOT in Docker):  python3 scripts/generate_testcase_matrix_xlsx.py
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs/dev_manual/e2e_testcase_reference.md"
PERF_API = ROOT / "docs/design/cc_ep_deliverable3_performance_api.md"
REGISTRY = ROOT / "tests/e2e/test_e2e.py"
FAULT_MATRIX = ROOT / "scripts/fault_qualification_matrix.json"
OUTPUT = Path("/mnt/data2/cgc/fileserver/UBCC_testcase_matrix.xlsx")
COPY_OUTPUT = ROOT / "docs/deliverables/UBCC_testcase_matrix.xlsx"

# ─────────────────────────  centralized enumerations  ─────────────────────────
SEGMENTS = ("1-119", "120-140", "142-147", "148-160", "200-227", "228-235")
TYPES = ("Correctness", "Perf", "Correctness+Perf")
TOPOLOGIES = ("1s", "2s", "8n1s", "8n2s", "五拓扑", "多拓扑")
TEST_SETS = ("BASIC", "ADVANCED", "FAULT_INJECTION", "FAULT_Q", "PERF_A", "PERF_B",
             "PERF_G", "PERF_R", "PERF_M1", "PERF_M2", "HA_EXT")
METRICS = ("指标 1", "指标 2", "指标 3", "支撑", "—")
CATEGORIES = ("基础 DSM", "一致性状态转换", "内存序", "并发", "故障恢复", "epoch",
              "writeback/evict", "目录机制", "延迟与容量测量", "应用性能", "故障注入", "HA/CC")
FAULT_GROUPS = {
    "G1": "G1 单消息传输故障",
    "G2": "G2 连续丢失故障",
    "G3": "G3 成对依赖故障",
    "G4": "G4 并发与聚合故障",
    "G5": "G5 多拓扑故障",
}
ACTION_MAP = {"Drop": "LOSS", "Delay": "DELAY", "Duplicate": "DUP", "Reorder": "REORDER"}

# named test-set membership (D3 §7.2)
SET_RANGES = {
    "BASIC": [(1, 64)],
    "ADVANCED": [(80, 116)],
    "FAULT_INJECTION": [(47, 49), (117, 119)],
    "FAULT_Q": [(151, 159)],
    "PERF_A": [(120, 129)],
    "PERF_B": [(130, 134)],
    "PERF_G": [(135, 140)],
    "PERF_R": [(142, 147)],
    "PERF_M1": [(131, 131), (142, 147)],
    "PERF_M2": [(135, 140), (217, 217), (142, 147)],
    "HA_EXT": [(228, 235)],
}
METRIC_SETS = {"PERF_M1": "指标 1", "PERF_M2": "指标 2", "HA_EXT": "指标 3"}

HEADERS = [
    "TC ID / 编号", "Name / 名称", "Segment / 编号段", "Type / 类型", "Category / 功能类别",
    "Function / 功能描述", "Behavior / 具体行为", "Topology / 拓扑",
    "Pressure-WorkingSet / 压力与工作集", "Special Config / 特殊配置",
    "Completion Boundary / 完成边界", "Pass Criteria / 通过标准",
    "Test Set / 所属测试集合", "Metric / 对应指标", "Scored / 是否正式计分",
    "Status / 状态", "Notes / 备注",
]

DARK_BLUE = "17365D"
FILL_HEADER = PatternFill("solid", fgColor=DARK_BLUE)
FILL_METRIC1 = PatternFill("solid", fgColor="D9EAF7")   # 蓝
FILL_METRIC2 = PatternFill("solid", fgColor="CFF0EF")   # 青
FILL_METRIC3 = PatternFill("solid", fgColor="FCE4D6")   # 橙
FILL_SUPPORT = PatternFill("solid", fgColor="E7E6E6")   # 灰
FILL_FAULT = PatternFill("solid", fgColor="F8D7DA")     # 浅红
FILL_GRAY = PatternFill("solid", fgColor="D9D9D9")      # 未计分/未运行

# ─────────────────────────  authoritative scenario tables  ─────────────────────────
# TC200-TC203: Phase A3/A5/C1/D1 targeted regressions (execution catalog + workloads).
REGRESSION = {
    200: dict(function="naive 容量驱逐 dirty recall，验证脏数据经 RecallResp 保存并由 home 持久化",
              behavior="node1 写 pattern 获 G_M → node0 同 set 触发 naive eviction（RecallReq→RecallResp→persist）→ node2 读回验证",
              pressure="8-entry naive ResidentDir；target/trigger 同 set", special="A3 profile：--sram-bytes=64 --ways=1 --set-bits=0；仅 naive",
              boundary="evictDone/waiterReplay 完成，node2 READ_VAL 发布",
              passed="payload+BEEFCAFE 及 3 个 naive markers；node2 精确匹配"),
    201: dict(function="spill → H64 fill → recall，验证回收数据与重建目录",
              behavior="node1 写 pattern 获 G_M → node0 填满各 set 触发 spill（persist→ack→force-remove）→ node2 读触发 backstore fill→recall→验证",
              pressure="8-entry spill ResidentDir；填满 7 个 set", special="A5 profile：--bloom-bytes=128 --sram-bytes=4352 --ways=1 --set-bits=0；naive SKIP",
              boundary="backstore fill + Recall 完成，node2 READ_VAL 发布",
              passed="MATCH + spill/fill markers"),
    202: dict(function="spill authoritative home-data push-grant",
              behavior="node1 写 target 获 G_M → node0 填满 set 触发 spill → node2 读触发 backstore fill→G_I grant→验证",
              pressure="8-entry spill ResidentDir", special="C1/D1 profile：--bloom-bytes=128 --sram-bytes=4352；naive SKIP",
              boundary="spill completion + node2 READ_VAL 发布",
              passed="MATCH + spill completion"),
    203: dict(function="H64 metadata spill/onload（非 Schema A page-chain overflow）",
              behavior="node1 写 25 lines → node0 写 50 cold lines 触发 spill → node2 读回 25 lines",
              pressure="group-5 25 lines + 50 cold pressure lines", special="C1/D1 profile：--bloom-bytes=128 --sram-bytes=4352；naive SKIP",
              boundary="node2 25-line readback 完成",
              passed="node2 MATCH + spill/fill markers"),
}

# TC210-TC221: HA01-HA12 portable scenarios (ha_workload_delivery_guide + scenario catalog).
HA = {
    210: dict(scenario="HA01", function="Home0 line 在 node0 本地写入后可被同一 participant 正确复用，并验证最小 timer/JSONL 路径",
              behavior="node0 store 0x101 → barrier → node0 timed load 同一 line", pressure="1 op（local_reuse）；2N1S 两 participant",
              special="seed=131；barrier mask=0x3；HA_SCENARIO=1", passed="node0 读到 0x101；两 node validation=0；sample 非零且 timer frequency 有效",
              typ="Correctness+Perf"),
    211: dict(scenario="HA02", function="最小跨 node 数据可见性和 remote read latency",
              behavior="node0 store 0x202 → barrier → node1 timed load", pressure="1 op（remote_read）；2N1S 两 participant",
              special="seed=131；HA_SCENARIO=2", passed="node1 读到 0x202；无 zero-data fallback；双 validation=0",
              typ="Correctness+Perf"),
    212: dict(scenario="HA03", function="writer ownership 从 node0 转交 node1，并由 node0 读回新值",
              behavior="node0 store 0x303 → barrier → node1 timed store 0x304 → barrier → node0 timed readback",
              pressure="ownership_write / ownership_readback 各 1 op", special="seed=131；HA_SCENARIO=3",
              passed="最终值 0x304；两个 phase 完整；双 validation=0", typ="Correctness+Perf"),
    213: dict(scenario="HA04", function="line 被双方读为 shared 后 node1 转 writer 的 invalidation/upgrade 路径",
              behavior="node0 store 0x404 → 双方 load → barrier → node1 timed store 0x405 → node0 readback",
              pressure="shared_read / shared_to_writer / writer_readback", special="seed=131；HA_SCENARIO=4",
              passed="node0 最终读到 0x405；invalidate 未完成不得提前报告 store complete", typ="Correctness+Perf"),
    214: dict(scenario="HA07", function="16 条 record 的生产、跨 node 消费和 barrier generation",
              behavior="每条 record：node0 store → barrier → node1 load/verify → barrier",
              pressure="producer / consumer 各 16 ops", special="seed=131；HA_SCENARIO=7",
              passed="16 条值全部匹配；32 次配对 barrier 无丢代或死锁", typ="Correctness+Perf"),
    215: dict(scenario="HA05", function="clean shared copy 在 capacity pressure 后是否被销毁或被保留",
              behavior="node0 seed hot line 0x505 → node1 warm read → node0 写 640 pressure lines → node1 连续 load hot line 64 次",
              pressure="first_revisit=64；640 pressure lines", special="seed=131；HA_SCENARIO=5",
              passed="64 次后最终 load 仍为 0x505；不能以 local hit 缺少 Outer trace 判失败", typ="Correctness+Perf"),
    216: dict(scenario="HA06", function="remote dirty owner 在 capacity admission 后的保留、回收和再次访问",
              behavior="node1 store 0x606 → node0 timed 写 640 pressure lines → node1 timed load hot line 64 次",
              pressure="eviction_admission=640；first_revisit=64", special="seed=131；HA_SCENARIO=6",
              passed="最终值 0x606；dirty data 不能由 metadata store 或零值伪造", typ="Correctness+Perf"),
    217: dict(scenario="HA10", function="实际 read-mostly catalog workload，在分批 pressure 下测量 hot lookup/update",
              behavior="每 batch node0 写 80 pressure lines → node1 timed 14 次 skewed read + 2 次 completed update",
              pressure="16 catalog lines；640 pressure lines；8 batches",
              special="seed=131；HA_SCENARIO=10；512-entry 容量模型；perf 验收入口",
              boundary="8 条 catalog_batch（各 16 ops）+ 1 条 catalog_useful_throughput（128 ops）",
              passed="8 个 iteration 齐全；两个 update key 为最后 batch 值；双 validation=0", typ="Correctness+Perf"),
    218: dict(scenario="HA08", function="分离 barrier 成本和单 line 双向 ownership ping-pong",
              behavior="先执行 16 次 barrier；再执行 16 轮奇/偶值交替 store、load 和双 barrier",
              pressure="barrier=16；seq_lock_handoff=16 rounds", special="seed=131；HA_SCENARIO=8",
              passed="每轮奇偶值严格递增；无 barrier timeout；双 validation=0", typ="Correctness+Perf"),
    219: dict(scenario="HA09", function="node0 hot-local 更新与 node1 remote pressure 并发时的数据稳定性",
              behavior="node0 对 16 条 hot line 做 64 次 store；node1 同时写 16 条另一 offset pressure line；barrier 后 node0 验证 hot[0]",
              pressure="local_under_pressure=64；remote_pressure=16", special="seed=131；HA_SCENARIO=9",
              passed="node0 hot[0] 为 0x9000；两个 timer 和双 validation 完整", typ="Correctness+Perf"),
    220: dict(scenario="HA11", function="精确 150% footprint 下 clean/shared admission 和 first revisit",
              behavior="node0 seed 64 hot → node1 share 64 hot → node0 timed admission 704 pressure → node1 timed revisit 64 hot",
              pressure="64 hot + 704 pressure = 768 unique lines；Resident capacity=512；ratio=1.5",
              special="seed=131；HA_SCENARIO=11", passed="64 final reads 全 MATCH；capacity record 精确 ratio=1.500000",
              typ="Correctness+Perf"),
    221: dict(scenario="HA12", function="精确 150% footprint 下 dirty admission、owner revisit 和 ownership handoff",
              behavior="node1 dirty-seed 64 hot → node0 timed admission 704 → node1 revisit 前 32 → node0 completed-store 后 32 → node1 验证 64",
              pressure="dirty_capacity_admission=704；first_revisit=32；handoff=32；64 hot + 704 pressure = 768 lines",
              special="seed=131；HA_SCENARIO=12", passed="前 32 保留原值、后 32 为 node0 新值；64 reads MATCH；ratio=1.500000",
              typ="Correctness+Perf"),
    # TC222-TC227: C-group 2N1S adaptations (e2e_ha_cgroup_2n1s.c).
    222: dict(scenario="C123-HA", function="TC123 shared-to-writer batch 的 2N1S 适配",
              behavior="shared read → pressure → periodic upgrade → verify", pressure="4 reads；timer+latency；双 manifest",
              special="e2e_ha_cgroup_2n1s.c C 组 1；Home0=node0；barrier mask=0x3",
              passed="双 node validation=0；数据 MATCH；timer 完整", typ="Correctness"),
    223: dict(scenario="C130-HA", function="TC130 overflow hot reuse 的 2N1S 适配",
              behavior="24 hot → 96 pressure → 24 reuse", pressure="24 reads；96-op timer；24 samples",
              special="e2e_ha_cgroup_2n1s.c C 组 2", passed="数据 MATCH；timer/sample 完整", typ="Correctness+Perf"),
    224: dict(scenario="C132-HA", function="TC132 dirty checkpoint recovery 的 2N1S 适配",
              behavior="dirty seed → pressure → recover reads", pressure="默认 8192 active + 65536 pressure；已验证 qualification profile 512 active + 4096 pressure",
              special="e2e_ha_cgroup_2n1s.c C 组 3；默认规模触发 600s progress-stall，不得声明已验收",
              passed="两 node config 一致、抽样全 MATCH、双 timer；默认全量 profile 未通过", typ="Correctness"),
    225: dict(scenario="C135-HA", function="TC135 preserved sharer first revisit 的 2N1S 适配",
              behavior="seed/share → pressure → 24 first loads", pressure="48 reads；24 samples",
              special="e2e_ha_cgroup_2n1s.c C 组 4", passed="数据 MATCH；sample 完整", typ="Correctness+Perf"),
    226: dict(scenario="C138-HA", function="TC138 dirty owner handoff 的 2N1S 适配",
              behavior="dirty seed → pressure → 24 handoff stores → verify", pressure="24 reads；24 samples",
              special="e2e_ha_cgroup_2n1s.c C 组 5", passed="数据 MATCH；sample 完整", typ="Correctness+Perf"),
    227: dict(scenario="C139-HA", function="TC139 mixed batch throughput 的 2N1S 适配",
              behavior="seed/share/owner → pressure → mixed batch", pressure="9 reads；16 samples；256-op timer",
              special="e2e_ha_cgroup_2n1s.c C 组 6", passed="数据 MATCH；timer/sample 完整", typ="Correctness+Perf"),
}

# TC228-TC235: metric-3 HA_EXT paired scenarios (D3 §5 / appendix C).
HA_EXT = {
    228: dict(title="Remote Read", function="requester 获得数据与共享授权",
              behavior="请求 → 数据源回收 → 返回 → 授权", boundary="数据与共享授权可见",
              helper="Home UBCC 依据 owner 状态选择权威数据源并在数据返回后建立共享关系"),
    229: dict(title="Ownership Handoff", function="新 owner 获得最新数据与独占权限",
              behavior="回收 → 最新数据 → 新权限", boundary="数据与独占权限可见",
              helper="Home UBCC 定位旧 owner，组织释放、数据返回和新 owner 授权"),
    230: dict(title="Shared-to-Writer", function="Ack 收敛并建立单写者",
              behavior="失效 → Ack 收敛 → 授权", boundary="全部目标确认且建立单写者",
              helper="Home UBCC 确定精确 sharer 目标集合，并行失效，Ack 收敛后授权"),
    231: dict(title="Clean Shared Reuse", function="压力后复用干净共享行（clean shared read service）",
              behavior="建立共享 → 压力 → 重读", boundary="共享读服务",
              helper="复用已提交共享关系"),
    232: dict(title="Hot-Key Mixed", function="hot key 上的多参与者读写服务（固定 2/3 读 + 1/3 写）",
              behavior="读写分别计时，固定 2:1 合成", boundary="复合主值计一次权重",
              helper="共享读取与精确写权限迁移"),
    233: dict(title="Producer-Consumer", function="生产者发布、消费者读取（发布—消费服务）",
              behavior="发布 → 读取 → 服务完成", boundary="服务主值；consumer load 辅助",
              helper="数据发布与服务完成"),
    234: dict(title="Queued Ownership Token", function="token 在参与者间排队交接（交接端到端）",
              behavior="排队 → 写入 → 有序观察", boundary="端到端／交接次数",
              helper="写入、权限交接和有序观察"),
    235: dict(title="Catalog / KV", function="catalog 查询与稀疏更新（批处理端到端）",
              behavior="查询更新 → 批次同步 → 完成", boundary="最大归一化端到端时延",
              helper="查询、更新和批次同步"),
}

HA_SCENARIO = {n: HA[n]["scenario"] for n in HA}

# ─────────────────────────  source parsing helpers  ─────────────────────────
def registry_cases():
    text = REGISTRY.read_text(encoding="utf-8")
    block = re.search(r"TESTCASES\s*=\s*\{(.*?)\n\}", text, re.S).group(1)
    cases = {}
    for n, workload in re.findall(r"^\s*(\d+)\s*:\s*['\"]([^'\"]+)['\"]", block, re.M):
        cases.setdefault(int(n), workload)
    return cases


def md_tables(text):
    rows = []
    lines = text.splitlines()
    for i, line in enumerate(lines[:-2]):
        if not line.strip().startswith("|") or "|" not in lines[i + 1] or "---" not in lines[i + 1]:
            continue
        headers = [x.strip() for x in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        j = i + 2
        while j < len(lines) and lines[j].strip().startswith("|"):
            vals = [x.strip().replace(r"\|", "|") for x in re.split(r"(?<!\\)\|", lines[j].strip().strip("|"))]
            if len(vals) == len(headers):
                rows.append(dict(zip(headers, vals)))
            j += 1
    return rows


def reference_data():
    text = REFERENCE.read_text(encoding="utf-8")
    out = {}
    for row in md_tables(text):
        tc = row.get("TC", "")
        if "名称" not in row or not re.fullmatch(r"(?:\d+)(?:/\d+)?", tc):
            continue
        for part in tc.split("/"):
            out[int(part)] = {
                "name": row.get("名称", ""),
                "topology": row.get("拓扑", ""),
                "special": row.get("特殊配置", row.get("配置", "")),
                "function": row.get("目的", row.get("功能", "")),
                "pass": row.get("通过标准", row.get("主要通过标准", "")),
            }
    for m in re.finditer(r"### TC(\d+).*?(?=\n### TC|\Z)", text, re.S):
        n, body = int(m.group(1)), m.group(0)
        out.setdefault(n, {})
        for label, key in (("目的", "function"), ("通过标准", "pass"), ("配置", "special"),
                           ("故障规则", "special"), ("Fault 规则", "special"),
                           ("流量", "behavior"), ("目的", "function")):
            v = re.search(r"- \*\*" + label + r"\*\*:\s*([^\n]+)", body)
            if v:
                out[n].setdefault(key, v.group(1))
        flow = re.search(r"- \*\*工作流\*\*:(.*?)(?=\n- \*\*|\Z)", body, re.S)
        if flow:
            out[n]["behavior"] = flow.group(1).strip()
        sm = re.search(r"\*\*状态\*\*:\s*([^\n]+)", body)
        if sm:
            out[n]["status"] = re.sub(r"[✅❌⚠️🔶🔴⚡]", "", sm.group(1)).strip()
    return out


def perf_appendix():
    out = {}
    text = PERF_API.read_text(encoding="utf-8")
    for row in md_tables(text):
        m = re.fullmatch(r"TC(\d+)", row.get("TC", ""))
        if not m:
            continue
        n = int(m.group(1))
        out.setdefault(n, {})
        for source, target in (("操作序列", "behavior"), ("参与者与工作集", "pressure"),
                               ("完成边界", "boundary"), ("验证能力", "verify")):
            if source in row:
                out[n][target] = row[source]
    return out


def fault_cases():
    if not FAULT_MATRIX.exists():
        return []
    return json.loads(FAULT_MATRIX.read_text(encoding="utf-8")).get("cases", [])


# ─────────────────────────  classification  ─────────────────────────
def segment(n):
    if n <= 119:
        return "1-119"
    if n <= 140:
        return "120-140"
    if n <= 147:
        return "142-147"
    if n <= 160:
        return "148-160"
    if n <= 227:
        return "200-227"
    return "228-235"


def classify_type(n):
    if 228 <= n <= 235:
        return "Correctness+Perf"
    if 210 <= n <= 227:
        return HA[n]["typ"]
    if 200 <= n <= 203 or 148 <= n <= 160:
        return "Correctness"
    if 142 <= n <= 147:
        return "Correctness+Perf"
    if 130 <= n <= 140:
        return "Correctness+Perf"
    if 125 <= n <= 129:
        return "Correctness"
    if 120 <= n <= 124:
        return "Correctness+Perf"
    if 90 <= n <= 102 or n in (80, 81, 82, 84, 85):
        return "Correctness+Perf"
    return "Correctness"


def classify_category(n):
    if n >= 228 or 210 <= n <= 227:
        return "HA/CC"
    if 200 <= n <= 203 or 125 <= n <= 129 or 116 == n or n in (18, 22, 23, 45):
        return "目录机制"
    if 148 <= n <= 159 or n in (47, 48, 49, 110, 111, 117, 118, 119):
        return "故障注入"
    if n == 160 or n in (3, 4, 8, 11, 14, 25, 29, 32, 33, 34, 36, 37, 39, 41, 44, 46,
                         113, 114, 115):
        return "一致性状态转换"
    if 142 <= n <= 147:
        return "应用性能"
    if 120 <= n <= 124 or 130 <= n <= 140 or n in (80, 81, 82, 84, 85) or 90 <= n <= 102:
        return "延迟与容量测量"
    if n in (40, 63, 64):
        return "故障恢复"
    if n == 13:
        return "内存序"
    if n == 112 or n in (10, 15, 16, 24, 31, 43, 50, 51, 52, 53, 54):
        return "并发"
    if n in (7, 17, 19, 26, 28, 102):
        return "writeback/evict"
    if n in (27, 30, 38, 42):
        return "epoch"
    return "基础 DSM"


def topology(value, n):
    if 210 <= n <= 227 or n == 160:
        return "多拓扑"
    if 142 <= n <= 147 or 228 <= n <= 235:
        return "五拓扑"
    value = value.replace("**", "").strip()
    if value in ("3n1s", "3N1S", "1s"):
        return "1s"
    if value in ("3n2s", "3N2S", "2s"):
        return "2s"
    if value.lower() == "8n1s":
        return "8n1s"
    if value.lower() == "8n2s":
        return "8n2s"
    if "五拓扑" in value or "/" in value:
        return "五拓扑"
    default = {82: "8n1s", 90: "8n1s", 91: "8n1s", 92: "8n1s", 93: "8n1s", 94: "8n1s",
               131: "8n1s", 133: "8n1s",
               32: "2s", 33: "2s", 34: "2s", 35: "2s", 39: "2s", 81: "2s",
               95: "8n2s", 96: "8n2s", 97: "8n2s", 98: "8n2s", 99: "8n2s",
               100: "8n2s", 101: "8n2s", 134: "8n2s"}
    return default.get(n, "1s")


def named_sets(n):
    return [s for s, ranges in SET_RANGES.items() if any(lo <= n <= hi for lo, hi in ranges)]


def test_set_text(n):
    sets = named_sets(n)
    return ", ".join(sets) if sets else "支撑"


def metric(n):
    metrics = [METRIC_SETS[s] for s in named_sets(n) if s in METRIC_SETS]
    return " / ".join(dict.fromkeys(metrics)) if metrics else "支撑"


def scored(n):
    return "是" if named_sets(n) else "否"


def fault_instances(n, cases):
    """G-group instances that involve TC n, rendered as 组-动作-对象."""
    labels = []
    for c in cases:
        if c["tc"] != n:
            continue
        group = c["qualification"].replace("Q", "G")
        for r in c["fault_rule_records"]:
            obj = r["message"].replace("ClearReq", "Clear")
            labels.append(f"{group}-{ACTION_MAP[r['action']]}-{obj}")
    return sorted(set(labels))


# ─────────────────────────  row assembly  ─────────────────────────
def workload_comments(workload):
    src = ROOT / "tests/e2e/workloads" / (workload + ".c")
    if not src.exists():
        return "", ""
    code = src.read_text(encoding="utf-8", errors="replace")
    comments = re.findall(r"/\*(.*?)\*/|//([^\n]+)", code, re.S)
    behavior = "\n".join((x or y).strip().strip("*").strip()
                         for x, y in comments if len((x or y).strip()) > 15)[:1200]
    defines = re.findall(r"^#define\s+(\w*(?:LINES|COUNT|ITERS|ROUNDS|HOT|BATCH|CAPACITY)\w*)\s+([^\n]+)",
                         code, re.M)
    pressure = "; ".join(k + "=" + v.strip() for k, v in defines)[:400]
    return behavior, pressure


def make_rows():
    cases = registry_cases()
    ref = reference_data()
    app = perf_appendix()
    fcases = fault_cases()
    rows = []
    for n, workload in sorted(cases.items()):
        if not (n <= 160 or 200 <= n <= 235):
            continue
        r, a = ref.get(n, {}), app.get(n, {})
        function = r.get("function", "")
        behavior = r.get("behavior", "")
        pressure = a.get("pressure", "")
        special = r.get("special", "—").replace("**", "").strip()
        boundary = a.get("boundary", "")
        passed = r.get("pass", a.get("verify", ""))
        status = r.get("status", "")

        # ---- per-segment enrichment from source documents ----
        if n in REGRESSION:
            function, behavior = REGRESSION[n]["function"], REGRESSION[n]["behavior"]
            pressure, special = REGRESSION[n]["pressure"], REGRESSION[n]["special"]
            boundary, passed = REGRESSION[n]["boundary"], REGRESSION[n]["passed"]
            status = "已注册；目录/工作负载存在；本次未重跑"
        if n in HA:
            function, behavior = HA[n]["function"], HA[n]["behavior"]
            pressure, special = HA[n]["pressure"], HA[n]["special"]
            passed = HA[n]["passed"]
            if not boundary:
                boundary = HA[n].get("boundary", "guest timer / JSONL validation 完成")
            status = ("HA10 为指标 2 正式计分集；本次未重跑" if n == 217
                      else "HA 可移植场景；2N1S；本次未重跑")
        if n in HA_EXT:
            function = HA_EXT[n]["function"]
            behavior = HA_EXT[n]["behavior"]
            pressure = "五拓扑；256 KiB 固定 L3；P0/P50/P100 三压力"
            special = "O3；256 KiB L3；P0/P50/P100；单向完成语义；UBCC 与 HA-VI 配对"
            boundary = HA_EXT[n]["boundary"]
            passed = ("逐 plane 数据验证通过、计时完整；核心组 TC228-230 与代表组 TC231-235 "
                      "各自等权均值 UBCC < HA-VI（非逐 TC 全改善）")
            status = "D3：120 对/240 运行，15 坐标两场景组通过；保留 7 对逐 TC 负降幅"
        if n == 160:
            function = "16 节点共享 node15 home line，node0 写后使其他 sharer 失效"
            behavior = "node15 home line 初始化 → 16-way share → node0 写入/失效 → 各节点验证"
            pressure = "16 nodes × 1 socket；32 reads（16 节点各 2 次）"
            special = "NUM_NODES=16 NUM_SOCKETS=1；无故障注入"
            boundary = "READ_VAL 发布 / workload 完成"
            passed = "32 READ_VAL 全部 MATCH；home=15"
            status = "已注册；本次未重跑，不声明 PASS"
        if 148 <= n <= 159:
            fents = [c for c in fcases if c["tc"] == n]
            msgs = sorted({x["message"] for c in fents for x in c["fault_rule_records"]})
            function = "故障资格：" + ", ".join(msgs)
            counts = Counter()
            for c in fents:
                for x in c["fault_rule_records"]:
                    counts[(ACTION_MAP[x["action"]], x["message"])] += 1
            special = "; ".join(f"{act}/{msg} ×{v} 条规则"
                                for (act, msg), v in sorted(counts.items()))
            if n in (149, 150, 151, 152, 157, 158, 159):
                behavior = ("node0 向 home1 写 8 lines V1 → node1/node2 共享读 → node0 写 V2 "
                            "触发 upgrade/invalidate → node1/node2 读回 V2；注入动作见配置")
            elif n in (153, 154, 155, 156):
                behavior = "node0 owner line → 触发 Recall → RecallResp 注入 dup/delay/reorder/drop → 读回验证"
            else:
                behavior = ("node0 向 home1 写 32 条不同 PA → node1 读回全部 32 条；"
                            "ClearReq 注入 drop/dup/delay/reorder")
            boundary = "逐规则 trigger/action 与 buffered delivery 精确验收；READ_VAL 发布"
            if fents:
                passed = (fents[0]["verifier"]["effective_checks"]["workload"] +
                          "；逐规则 trigger/action 与 buffered delivery 精确验收")
            status = ("正式结果 logs/fault_all_20260803_strict PASS" if n == 148
                      else "资格矩阵已定义；本次未重跑")

        # ---- fallbacks for missing descriptive fields ----
        if not behavior and not function:
            c_beh, c_pres = workload_comments(workload)
            behavior, pressure = c_beh or behavior, pressure or c_pres

        # ---- notes / provenance ----
        notes = [f"来源：tests/e2e/workloads/{workload}.c；test_e2e.py TESTCASES；e2e_testcase_reference.md；D3 §7/附录B/C"]
        if 148 <= n <= 159:
            notes.append("故障实例：" + " / ".join(fault_instances(n, fcases)))
        if 210 <= n <= 227:
            notes.append(f"实际拓扑 2n1s（HA_SCENARIO {HA_SCENARIO[n]}），枚举无该值，归多拓扑并保留此限定")
        if n == 160:
            notes.append("实际拓扑 16n1s；无故障注入，非 G5 的替代品")
        if n in (47, 49):
            notes.append("来源冲突：reference 默认 DUP；资格矩阵 TC47=LOSS、TC49=REORDER，属于不同配置")
        if n == 230:
            notes.append("G5 故障变体不属于指标 3 正式配对运行，不能重复计数；现 verifier 不消费 fault manifest")
        if 142 <= n <= 147:
            notes.append("指标 1 正式五拓扑×P175/P200；指标 2 仅 service；附录 B 的 16N1S 为保留场景")
        if n == 140:
            notes.append("指标 2 中性控制：naive=119.209ns < 500ns，不进入聚合")
        if n == 224:
            notes.append("默认全量 profile 未通过，仅 compact qualification 已验证")
        if 200 <= n <= 203:
            notes.append("定向回归（Phase A3/A5/C1/D1）；不在 D3 §7.2 命名测试集合内")
        if list(cases.values()).count(workload) > 1:
            notes.append("共享 workload，TC ID 独立保留")

        rows.append({
            HEADERS[0]: f"TC{n}", HEADERS[1]: workload, HEADERS[2]: segment(n),
            HEADERS[3]: classify_type(n), HEADERS[4]: classify_category(n),
            HEADERS[5]: function or "已注册 workload；参考文档未提供独立目的描述",
            HEADERS[6]: behavior or "见 workload 源码：" + workload + ".c",
            HEADERS[7]: topology(r.get("topology", ""), n),
            HEADERS[8]: pressure or "参考文档未给出；以 workload/运行参数为准",
            HEADERS[9]: special or "—",
            HEADERS[10]: boundary or "以 verifier/READ_VAL 或 workload 完成标记为边界",
            HEADERS[11]: passed or "以对应 verifier 和文档验收条件为准",
            HEADERS[12]: test_set_text(n), HEADERS[13]: metric(n), HEADERS[14]: scored(n),
            HEADERS[15]: status or "已注册；未核实逐 TC 运行状态（不等于未运行）",
            HEADERS[16]: "；".join(notes),
        })
    return rows


# ─────────────────────────  styling  ─────────────────────────
def style_table(ws):
    last_row, last_col = ws.max_row, ws.max_column
    ws.freeze_panes = "B2"
    ws.auto_filter.ref = f"A1:{get_column_letter(last_col)}{last_row}"
    for cell in ws[1]:
        cell.fill = FILL_HEADER
        cell.font = Font(color="FFFFFF", bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
    ws.row_dimensions[1].height = 36
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    for col in range(1, last_col + 1):
        width = max((len(str(ws.cell(r, col).value or "")) for r in range(1, last_row + 1)), default=0)
        ws.column_dimensions[get_column_letter(col)].width = min(58, max(11, width * 1.05 + 2))


def add_conditional_formatting(ws):
    last = ws.max_row
    metric_range = f"N2:N{last}"
    type_range = f"D2:D{last}"
    row_range = f"A2:{get_column_letter(ws.max_column)}{last}"
    specs = (("指标 1", FILL_METRIC1), ("指标 2", FILL_METRIC2),
             ("指标 3", FILL_METRIC3), ("支撑", FILL_SUPPORT))
    for token, fill in specs:
        formula = [f'ISNUMBER(SEARCH("{token}",$N2))']
        ws.conditional_formatting.add(metric_range, FormulaRule(formula=formula, fill=fill, stopIfTrue=True))
        ws.conditional_formatting.add(type_range, FormulaRule(formula=formula, fill=fill, stopIfTrue=True))
    # gray row: 不计分 / 未运行
    ws.conditional_formatting.add(
        row_range, FormulaRule(formula=['OR($O2="否",ISNUMBER(SEARCH("未运行",$P2)))'],
                               fill=FILL_GRAY, stopIfTrue=True))
    # light-red row: fault injection
    ws.conditional_formatting.add(
        row_range, FormulaRule(formula=['$E2="故障注入"'], fill=FILL_FAULT, stopIfTrue=True))


def write_table_sheet(wb, title, rows, predicate=None):
    ws = wb.create_sheet(title)
    ws.append(HEADERS)
    data = rows if predicate is None else [r for r in rows if predicate(int(r[HEADERS[0]][2:]))]
    for r in data:
        ws.append([r[h] for h in HEADERS])
    style_table(ws)
    add_conditional_formatting(ws)
    return ws


# ─────────────────────────  workbook  ─────────────────────────
def build_legend(wb, rows):
    ws = wb.create_sheet("说明")
    ws.append(["UBCC Testcase Matrix", "生成日期", dt.date.today().isoformat()])
    ws.append(["用途", "逐条整理已注册 TC 的场景、拓扑、行为、验收与指标归属；不替代运行日志。", ""])
    ws.append([])
    ws.append(["【图例】", "颜色", "含义"])
    for label, color in (("指标 1", "深蓝底白字/浅蓝 D9EAF7"), ("指标 2", "青色 CFF0EF"),
                         ("指标 3", "橙色 FCE4D6"), ("支撑/未计分", "灰色 E7E6E6 / D9D9D9"),
                         ("故障注入行", "浅红 F8D7DA"), ("表头", "深蓝 17365D 白字")):
        ws.append([label, color, ""])
    ws.append([])
    ws.append(["【列含义】", "列", "说明"])
    for h in HEADERS:
        ws.append([h, "", ""])
    ws.append([])
    ws.append(["【枚举】", "类型", "取值"])
    for name, values in (("Segment / 编号段", SEGMENTS), ("Type / 类型", TYPES),
                         ("Topology / 拓扑", TOPOLOGIES), ("Test Set / 所属测试集合", TEST_SETS),
                         ("Metric / 对应指标", METRICS), ("Category / 功能类别", CATEGORIES)):
        ws.append([name, " / ".join(values), ""])
    ws.append(["故障组", "G1 单消息传输故障 / G2 连续丢失故障 / G3 成对依赖故障 / "
                        "G4 并发与聚合故障 / G5 多拓扑故障；用例编号 组-动作-对象"
                        "（动作 LOSS/DELAY/DUP/REORDER）", ""])
    ws.append([])
    ws.append(["【指标↔测试集合】", "测试集合", "TC 列表"])
    for m, s, tcs in (("指标 1", "PERF_M1", "TC131、TC142-147"),
                      ("指标 2", "PERF_M2", "TC135-140、TC217、TC142-147"),
                      ("指标 3", "HA_EXT", "TC228-235")):
        ws.append([m, s, tcs])
    ws.append([])
    ws.append(["【测试集合定义】", "", ""])
    for s, desc in (("BASIC", "TC1~64 基本正确性"), ("ADVANCED", "TC80~116 更多正确性"),
                    ("FAULT_INJECTION", "TC47~49、117~119"), ("FAULT_Q", "TC151~159 故障注入增强"),
                    ("PERF_A", "TC120~129"), ("PERF_B", "TC130~134"), ("PERF_G", "TC135~140"),
                    ("PERF_R", "TC142~147"), ("PERF_M1", "TC131、142~147 指标 1"),
                    ("PERF_M2", "TC135~140、217、142~147 指标 2"), ("HA_EXT", "TC228~235 指标 3")):
        ws.append([s, desc, ""])
    ws.append([])
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True,
                            capture_output=True).stdout.strip()
    ws.append(["【来源与日期】", "", ""])
    for src in (REFERENCE.relative_to(ROOT), PERF_API.relative_to(ROOT), REGISTRY.relative_to(ROOT),
                FAULT_MATRIX.relative_to(ROOT), "tests/e2e/workloads/*.c"):
        ws.append([str(src), "", ""])
    ws.append(["生成时 commit", commit, ""])
    ws.append(["总 TC 数", len(rows), ""])
    widths = {1: 34, 2: 78, 3: 40}
    for col, w in widths.items():
        ws.column_dimensions[get_column_letter(col)].width = w
    for row in ws.iter_rows():
        row[0].font = Font(bold=True)
        for c in row:
            c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"
    return ws


def build_overview(wb, rows):
    ws = wb.create_sheet("总览")
    ws.append(["指标 / Metric", "测试集合 / Test Set", "TC / Testcase", "状态 / Status"])
    by_id = {int(r[HEADERS[0]][2:]): r for r in rows}
    mapping = (("指标 1", "PERF_M1", [131, *range(142, 148)]),
               ("指标 2", "PERF_M2", [*range(135, 141), 217, *range(142, 148)]),
               ("指标 3", "HA_EXT", list(range(228, 236))))
    seen = {}
    for m, s, tcs in mapping:
        for n in tcs:
            if n in by_id:
                seen.setdefault(n, []).append(m)
                ws.append([m, s, f"TC{n}", by_id[n][HEADERS[15]]])
    for n, r in by_id.items():
        if n not in seen:
            ws.append(["支撑", r[HEADERS[12]], f"TC{n}", r[HEADERS[15]]])
    style_table(ws)
    return ws, by_id


def build_stats(ws, rows, by_id):
    ws.append([])
    ws.append(["统计维度", "取值", "数量", "说明"])
    stats = (("编号段", Counter(r[HEADERS[2]] for r in rows)),
             ("类型", Counter(r[HEADERS[3]] for r in rows)),
             ("拓扑", Counter(r[HEADERS[7]] for r in rows)),
             ("指标", Counter(r[HEADERS[13]] for r in rows)),
             ("是否计分", Counter(r[HEADERS[14]] for r in rows)))
    for label, counter in stats:
        for k, v in sorted(counter.items()):
            ws.append([label, k, v, "按纳入本矩阵范围的已注册 TC 统计"])
    set_counter = Counter()
    for r in rows:
        for s in re.split(r",\s*", r[HEADERS[12]]):
            if s:
                set_counter[s] += 1
    for k, v in sorted(set_counter.items()):
        ws.append(["测试集合", k, v, "同一 TC 可属多个集合"])
    ws.append(["合计", "总 TC 数", len(rows), "TC1~160 与 TC200~235 中的已注册编号"])
    style_table(ws)


def build():
    rows = make_rows()
    wb = Workbook()
    wb.remove(wb.active)
    build_legend(wb, rows)

    ov, by_id = build_overview(wb, rows)
    build_stats(ov, rows, by_id)

    write_table_sheet(wb, "全部Testcase", rows)
    write_table_sheet(wb, "TC1-119 基础正确性", rows, lambda n: n <= 119)
    write_table_sheet(wb, "TC120-140 性能", rows, lambda n: 120 <= n <= 140)
    write_table_sheet(wb, "TC142-147 应用性能", rows, lambda n: 142 <= n <= 147)
    write_table_sheet(wb, "TC148-160 故障资格", rows, lambda n: 148 <= n <= 160)
    write_table_sheet(wb, "TC200-227 定向回归", rows, lambda n: 200 <= n <= 227)
    write_table_sheet(wb, "TC228-235 HA_EXT（指标3）", rows, lambda n: 228 <= n <= 235)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    wb.save(OUTPUT)
    COPY_OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OUTPUT, COPY_OUTPUT)
    return rows


if __name__ == "__main__":
    result = build()
    seg = Counter(r[HEADERS[2]] for r in result)
    print(f"generated: {OUTPUT}")
    print(f"copied to: {COPY_OUTPUT}")
    print(f"total TCs: {len(result)}")
    for s in SEGMENTS:
        print(f"  {s}: {seg.get(s, 0)}")
