import re
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
FORMAL_DOCS = (
    ROOT / "docs/design/cc_ep_protocol_overview.md",
    ROOT / "docs/design/cc_ep_deliverable2_verification_reliability_ha.md",
    ROOT / "docs/design/cc_ep_deliverable3_performance_api.md",
)
FORBIDDEN_WORDING = (
    "客户确认",
    "甲方确认",
    "确认并冻结",
    "客户物理",
    "客户硬件",
    "客户 HA",
    "甲方单位",
    "项目名称：",
    "集成方确认",
    "待确认",
)
FORBIDDEN_L3_SENSITIVITY = (
    "p150",
    "150% L3",
    "两压力点",
    "两个 L3 压力点",
    "压力稳定性",
    "L3 sensitivity",
    "L3 敏感性",
)
OVER_PRECISE_DECIMAL = re.compile(r"(?<![\d.])[-+]?\d[\d,]*\.\d{4,}(?!\d)")


class DeliveryContentPolicyTest(unittest.TestCase):
    def test_formal_documents_do_not_claim_external_confirmation(self):
        for path in FORMAL_DOCS:
            text = path.read_text(encoding="utf-8")
            for wording in FORBIDDEN_WORDING:
                self.assertNotIn(wording, text, f"{path.name}: {wording}")

    def test_formal_documents_display_at_most_three_decimal_places(self):
        for path in FORMAL_DOCS:
            text = path.read_text(encoding="utf-8")
            matches = OVER_PRECISE_DECIMAL.findall(text)
            self.assertEqual(matches, [], f"{path.name}: {matches}")

    def test_formal_documents_use_one_fixed_l3_configuration(self):
        for path in FORMAL_DOCS:
            text = path.read_text(encoding="utf-8")
            for wording in FORBIDDEN_L3_SENSITIVITY:
                self.assertNotIn(wording, text, f"{path.name}: {wording}")
        performance = FORMAL_DOCS[2].read_text(encoding="utf-8")
        self.assertIn("256 KiB", performance)
        self.assertIn("100% L3 压力", performance)

    def test_formal_documents_use_release_ready_wording(self):
        for path in FORMAL_DOCS:
            text = path.read_text(encoding="utf-8")
            for wording in ("冻结", "契约"):
                self.assertNotIn(wording, text, path.name)

    def test_redundant_defensive_wording_is_removed(self):
        removed = (
            ("不能只按目录条目数衡量整个控制器", "而不是只提高事务槽",
             "不能由局部状态简单推导", "避免以 Home traffic 下降",
             "不等同于状态数量的排序", "这些缓存行数据的另一层数据缓存",
             "该路径是组织方式比较", "而非将节点内能力等同于跨节点实现",
             "协议名称本身不保证", "不构成已交付功能声明"),
            ("而不依赖源码行号", "每个模型只承担", "动作覆盖数不等于"),
            ("替代解释", "而不是单一容量计数", "而不局限于协议微场景",
             "NetworkSim", "不代表物理芯片", "不替代 TC131",
             "不进入指标 2 合同聚合", "该结论不包含端口级",
             "聚合优势不表示", "未建模硬件或禁止外推项", "不构成功能承诺"),
        )
        for path, phrases in zip(FORMAL_DOCS, removed):
            text = re.sub(r"\s+", "", path.read_text(encoding="utf-8"))
            for phrase in phrases:
                self.assertNotIn(re.sub(r"\s+", "", phrase), text, path.name)

    def test_cleanup_preserves_protocol_and_evidence_boundaries(self):
        required = (
            ("Home 持续存活", "Switch 微体系结构未建模", "58 KiB", "硬件布局信息",
             "TOMBSTONE 不终止搜索", "EP 不拥有全局目录或提交权",
             "F 语义和任意干净副本转发属于候选能力，尚未实现",
             "验证状态：未验证", "T_{\\mathrm{visible}} = \\max(T_{\\mathrm{data}}, T_{\\mathrm{authority}})"),
            ("至多存在一个有效写 owner", "committed epoch 不回退", "FairSpec/forward-progress",
             "故障最终解除", "Home 持续存活", "同一测试可计入多个类别",
             "仿真测试总数为 52", "256 KiB", "单向完成语义"),
            ("协议端点层级验证", "目标硬件和端口级 Switch 微体系结构未建模",
             "主要证据类型为参考模型仿真", "仿真值而非物理测量", "100% L3 压力",
             "未完成事件", "低于 500 ns 适用门槛", "2/3 × hot-key read + 1/3 × hot-key write",
             "保持一次权重", "-13.333%", "-0.269", "-0.488", "-9.2",
             "N/A", "验证状态：未验证", "根操作不以同步 ClearResp 作为完成条件"),
        )
        for path, phrases in zip(FORMAL_DOCS, required):
            text = re.sub(r"\s+", "", path.read_text(encoding="utf-8"))
            for phrase in phrases:
                self.assertIn(re.sub(r"\s+", "", phrase), text, path.name)
        architecture = FORMAL_DOCS[0].read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"^#### 7\.", architecture, re.M)), 19)

    def test_deliverable2_has_no_negative_boundary_section_or_q_chart(self):
        text = FORMAL_DOCS[1].read_text(encoding="utf-8")
        self.assertNotIn("### 8.2", text)
        self.assertNotIn("本次交付范围外", text)
        self.assertNotIn("ubcc-q1-q5-qualification", text)
        self.assertNotIn("图 5-1", text)


if __name__ == "__main__":
    unittest.main()
