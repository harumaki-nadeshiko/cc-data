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


if __name__ == "__main__":
    unittest.main()
