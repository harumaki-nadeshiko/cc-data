import json
import math
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = ROOT / "docs/design/performance_preview_data.json"
FORMAL_PATH = ROOT / "docs/design/cc_ep_deliverable3_performance_api.md"

EXPECTED_TCS = {
    *(f"TC{tc}" for tc in range(120, 148)),
    "TC217",
    *(f"TC{tc}" for tc in range(228, 236)),
}


def _walk_values(value):
    if isinstance(value, dict):
        for child in value.values():
            yield from _walk_values(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_values(child)
    else:
        yield value


class PerformancePreviewDataTest(unittest.TestCase):
    def test_preview_json_and_appendix_cover_all_referenced_testcases(self):
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        formal = FORMAL_PATH.read_text(encoding="utf-8")
        appendix = formal.split("## 附录 B", 1)[1]

        self.assertEqual(set(data["required_testcases"]), EXPECTED_TCS)
        self.assertEqual(set(data["testcases"]), EXPECTED_TCS)
        for tc in EXPECTED_TCS:
            self.assertIn(tc, appendix)
            item = data["testcases"][tc]
            for field in (
                "topology_roles",
                "phases",
                "pressure_working_set",
                "completion_boundary",
                "demonstrated_capability",
            ):
                self.assertTrue(item[field].strip(), f"{tc} missing {field}")

    def test_formal_report_has_no_forbidden_term_and_metric2_is_normal_sentence(self):
        formal = FORMAL_PATH.read_text(encoding="utf-8")
        self.assertNotIn("legacy", formal.lower())
        self.assertNotIn("```text\n64.759%\n```", formal)
        self.assertIn("**64.759%**", formal)
        self.assertNotIn("p150", formal)
        self.assertNotIn("150% L3", formal)
        self.assertIn("256 KiB", formal)

    def test_preview_numeric_values_are_finite_or_null(self):
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        for value in _walk_values(data):
            if isinstance(value, bool) or value is None or isinstance(value, str):
                continue
            self.assertIsInstance(value, (int, float))
            self.assertTrue(math.isfinite(value))

    def test_required_special_coverage_is_present(self):
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        tc131 = data["testcases"]["TC131"]["measurements"]["outer_arms"]
        self.assertLessEqual({"spill_512k", "ideal_dir"}, set(tc131))
        self.assertEqual(
            set(data["portable_spill_noopt_four_topology"]),
            {*(f"TC{tc}" for tc in range(142, 148))},
        )
        for tc in ("TC228", "TC229", "TC230", "TC231", "TC232", "TC233", "TC234", "TC235"):
            self.assertEqual({"p100"}, set(data["testcases"][tc]["metric3"]))

    def test_retained_absolute_support_values_are_not_omitted(self):
        data = json.loads(DATA_PATH.read_text(encoding="utf-8"))["testcases"]
        for tc in ("TC120", "TC121", "TC122", "TC123", "TC124"):
            self.assertTrue(any(key.startswith("retained_") for key in data[tc]["measurements"]))
        for tc in ("TC125", "TC126", "TC127", "TC128", "TC129", "TC141"):
            self.assertTrue(any(key.startswith("retained_") for key in data[tc]["measurements"]))

    def test_formal_report_uses_docx_compatible_markdown_images(self):
        formal = FORMAL_PATH.read_text(encoding="utf-8")
        self.assertNotIn("<img", formal.lower())
        for stem in (
            "ubcc-metric1-capacity-latency",
            "ubcc-metric2-reductions",
            "ubcc-tc120-124-scenarios",
            "ubcc-tc130-134-pressure",
            "ubcc-tc142-147-applications",
            "ubcc-ha-vi-comparison",
            "ubcc-metric3-per-tc-reductions",
        ):
            self.assertIn(stem + ".png", formal)


if __name__ == "__main__":
    unittest.main()
