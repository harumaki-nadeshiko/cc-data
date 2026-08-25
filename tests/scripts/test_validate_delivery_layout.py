#!/usr/bin/env python3

import importlib.util
import pathlib
import tempfile
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/validate_delivery_layout.py"
SPEC = importlib.util.spec_from_file_location("validate_delivery_layout", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class ValidateDeliveryLayoutTest(unittest.TestCase):
    def page(self, number=2, ratio=0.08, text=None, images=None, width=600, height=800):
        text = text or [MOD.TextBox(50, 100, 400, 12, 10, "正文内容" * 30)]
        return MOD.PageInput(number, width, height, text, images or [], ratio)

    def codes(self, page):
        return {issue["code"] for issue in MOD.analyze_page(page)["issues"]}

    def test_sparse_body_page_fails_both_density_thresholds(self):
        codes = self.codes(self.page(ratio=0.005,
                                     text=[MOD.TextBox(50, 100, 50, 12, 10, "短页")]))
        self.assertIn("LOW_NON_WHITE_RATIO", codes)
        self.assertIn("VERY_FEW_TEXT_CHARACTERS", codes)

    def test_cover_and_toc_are_exempt_from_density_only(self):
        cover = self.page(number=1, ratio=0.0,
                          text=[MOD.TextBox(50, 100, 50, 12, 10, "封面")])
        toc = self.page(number=2, ratio=0.0,
                        text=[MOD.TextBox(50, 100, 50, 12, 10, "目录")])
        for page in (cover, toc):
            codes = self.codes(page)
            self.assertNotIn("LOW_NON_WHITE_RATIO", codes)
            self.assertNotIn("VERY_FEW_TEXT_CHARACTERS", codes)

    def test_orphan_heading_large_figure_and_clipping(self):
        text = [MOD.TextBox(50, 200, 400, 12, 10, "正文" * 50),
                MOD.TextBox(50, 680, 200, 20, 16, "新章节", True),
                MOD.TextBox(-5, 300, 20, 10, 10, "越界文本")]
        image = MOD.ImageBox(10, 10, 590, 700)
        codes = self.codes(self.page(text=text, images=[image]))
        self.assertIn("ORPHAN_HEADING_NEAR_PAGE_BOTTOM", codes)
        self.assertIn("FIGURE_EXCESSIVE_PAGE_SHARE", codes)
        self.assertIn("FIGURES_EXCESSIVE_COMBINED_PAGE_SHARE", codes)
        self.assertIn("CONTENT_CLIPPED_OUTSIDE_PAGE", codes)

    def test_stretched_cjk_and_tiny_text_are_detected(self):
        text = [MOD.TextBox(50, 100, 400, 12, 10, "正文" * 50),
                MOD.TextBox(50, 300, 100, 8, 6, "很小的注释文字"),
                MOD.TextBox(50, 400, 140, 12, 10, "拉伸文字")]
        codes = self.codes(self.page(text=text))
        self.assertIn("UNEXPECTEDLY_TINY_TEXT", codes)
        self.assertIn("LIKELY_STRETCHED_OR_JUSTIFIED_TABLE_TEXT", codes)

    def test_pgm_density_reader(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "page.pgm"
            path.write_bytes(b"P5\n# fixture\n4 1\n255\n" + bytes([255, 255, 0, 100]))
            self.assertEqual(MOD.read_pgm_non_white_ratio(path, 250), 0.5)

    def test_clean_page_passes(self):
        report = MOD.analyze_page(self.page())
        self.assertEqual(report["status"], "PASS")
        self.assertEqual(report["issues"], [])

    def test_inconsistent_page_size_is_attached_to_changed_page(self):
        pages = [self.page(number=1), self.page(number=2, width=620)]
        reports = [MOD.analyze_page(page) for page in pages]
        issues = MOD.apply_page_size_checks(pages, reports)
        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0]["code"], "INCONSISTENT_PAGE_SIZE")
        self.assertEqual(reports[1]["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
