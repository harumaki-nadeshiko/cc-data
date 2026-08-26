#!/usr/bin/env python3

import importlib.util
import pathlib
import struct
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zipfile


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts/sync_delivery_documents.py"
SPEC = importlib.util.spec_from_file_location("sync_delivery_documents", SCRIPT)
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
WP = "{http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing}"


def fake_png(width, height):
    # The generator needs only the PNG signature and IHDR dimensions, and stores
    # the source bytes without decoding them.
    return (b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\x0dIHDR" +
            struct.pack(">II", width, height))


class SyncDeliveryDocumentsLayoutTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.temp.name)
        self.image = self.root / "large.png"
        self.image.write_bytes(fake_png(2400, 3200))
        self.markdown = self.root / "sample.md"
        self.markdown.write_text(
            "# 布局测试\n\n"
            "正文第一段。\n\n---\n\n"
            "## 紧凑标题\n\n"
            "| 中文列 | 数值 |\n|---|---:|\n| InvalidateReq frontier evict | 1 |\n\n"
            "```text\nfirst line\nsecond line\n```\n\n"
            "![图 1 测试图](large.png)\n\n"
            "![图 2 第二张图](large.png){width=20cm height=20cm}\n",
            encoding="utf-8")
        self.docx = self.root / "sample.docx"
        MOD.build_docx(self.markdown, self.docx)
        with zipfile.ZipFile(self.docx) as archive:
            self.document = ET.fromstring(archive.read("word/document.xml"))
            self.styles = ET.fromstring(archive.read("word/styles.xml"))

    def tearDown(self):
        self.temp.cleanup()

    def style(self, style_id):
        for style in self.styles.findall(f"{W}style"):
            if style.get(f"{W}styleId") == style_id:
                return style
        self.fail(f"missing style {style_id}")

    def test_body_uses_fixed_twenty_point_spacing_without_paragraph_gap(self):
        normal = self.style("Normal")
        spacing = normal.find(f"{W}pPr/{W}spacing")
        self.assertEqual(spacing.get(f"{W}line"), "400")
        self.assertEqual(spacing.get(f"{W}lineRule"), "exact")
        self.assertEqual(spacing.get(f"{W}after"), "0")
        size = normal.find(f"{W}rPr/{W}sz")
        self.assertEqual(size.get(f"{W}val"), "21")
        indents = self.document.findall(f".//{W}pPr/{W}ind")
        self.assertIn("420", [item.get(f"{W}firstLine") for item in indents])
        empty_paragraphs = [
            para for para in self.document.findall(f".//{W}body/{W}p")
            if not para.findall(f".//{W}t") and not para.findall(f".//{W}drawing")
            and not para.findall(f".//{W}br")
        ]
        self.assertEqual(empty_paragraphs, [])

    def test_table_cells_are_left_aligned_nine_point_and_repeat_header(self):
        table = self.document.find(f".//{W}tbl")
        self.assertIsNotNone(table)
        first_row = table.find(f"{W}tr")
        self.assertIsNotNone(first_row.find(f"{W}trPr/{W}tblHeader"))
        self.assertEqual(
            first_row.find(f"{W}tc/{W}tcPr/{W}shd").get(f"{W}fill"),
            "D9E2F3")
        for cell in table.findall(f".//{W}tc"):
            paragraph = cell.find(f"{W}p")
            self.assertEqual(paragraph.find(f"{W}pPr/{W}jc").get(f"{W}val"),
                             "left")
            self.assertEqual(paragraph.find(f"{W}pPr/{W}wordWrap").get(f"{W}val"),
                             "0")
            text = "".join(cell.itertext()).replace(MOD.WORD_JOINER, "")
            expected_size = "16" if any(
                identifier in text for identifier in MOD.NARROW_TABLE_IDENTIFIERS) else "18"
            self.assertEqual(paragraph.find(f"{W}r/{W}rPr/{W}sz").get(f"{W}val"),
                             expected_size)
        margins = table.find(f"{W}tblPr/{W}tblCellMar")
        self.assertIsNotNone(margins)

    def test_ascii_identifiers_in_table_cells_use_word_joiners(self):
        text = "".join(self.document.find(f".//{W}tbl").itertext())
        self.assertEqual(MOD.WORD_JOINER, "\u2060")
        self.assertNotIn("\ufeff", text)
        for identifier in ("InvalidateReq", "frontier", "evict"):
            protected = MOD.WORD_JOINER.join(identifier)
            self.assertIn(protected, text)
            self.assertNotIn(identifier, text)

    def test_break_sensitive_table_identifiers_use_targeted_minimum_font(self):
        table = self.document.find(f".//{W}tbl")
        identifier_cell = next(
            cell for cell in table.findall(f".//{W}tc")
            if "InvalidateReq" in "".join(cell.itertext()).replace(MOD.WORD_JOINER, ""))
        self.assertEqual(
            identifier_cell.find(f"{W}p/{W}r/{W}rPr/{W}sz").get(f"{W}val"), "16")

    def test_glossary_table_uses_compact_four_column_eight_point_style(self):
        body, _ = MOD.convert_markdown(
            "## 附录 E 术语表\n\n| 术语 | 说明 |\n|---|---|\n"
            "| UBCC | 方案 |\n| InvalidateAck | 确认 |\n| ubsim |  |\n| ub |  |\n",
            self.markdown)
        document = ET.fromstring(
            '<w:body xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
            body + '</w:body>')
        table = document.find(f".//{W}tbl")
        self.assertEqual(table.find(f"{W}tblPr/{W}tblCellMar/{W}top").get(f"{W}w"), "0")
        for paragraph in table.findall(f".//{W}p"):
            self.assertEqual(paragraph.find(f"{W}pPr/{W}pStyle").get(f"{W}val"),
                             "CompactGlossaryText")
            self.assertEqual(paragraph.find(f"{W}pPr/{W}spacing").get(f"{W}line"),
                             "160")
            self.assertEqual(paragraph.find(f"{W}r/{W}rPr/{W}sz").get(f"{W}val"), "16")
        rows = table.findall(f"{W}tr")
        self.assertEqual(len(rows), 3)
        self.assertEqual(
            [column.get(f"{W}w") for column in table.findall(f"{W}tblGrid/{W}gridCol")],
            ["1500", "3300", "1500", "3300"])
        self.assertEqual(
            ["".join(cell.itertext()).replace(MOD.WORD_JOINER, "")
             for cell in rows[0].findall(f"{W}tc")],
            ["术语", "说明", "术语", "说明"])
        self.assertEqual(
            ["".join(cell.itertext()).replace(MOD.WORD_JOINER, "")
             for cell in rows[1].findall(f"{W}tc")],
            ["UBCC", "方案", "ubsim", ""])
        self.assertEqual(
            ["".join(cell.itertext()).replace(MOD.WORD_JOINER, "")
             for cell in rows[2].findall(f"{W}tc")],
            ["InvalidateAck", "确认", "ub", ""])

    def test_non_glossary_tables_remain_two_columns(self):
        body, _ = MOD.convert_markdown(
            "## 普通表格\n\n| 术语 | 说明 |\n|---|---|\n| a | b |\n",
            self.markdown)
        document = ET.fromstring(
            '<w:body xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' +
            body + '</w:body>')
        self.assertEqual(len(document.find(f".//{W}tr").findall(f"{W}tc")), 2)

    def test_images_do_not_exceed_configured_max_height_or_width(self):
        extents = self.document.findall(f".//{WP}extent")
        self.assertEqual(len(extents), 2)
        for extent in extents:
            self.assertLessEqual(int(extent.get("cx")), MOD.DEFAULT_IMAGE_MAX_WIDTH)
            self.assertLessEqual(int(extent.get("cy")), MOD.DEFAULT_IMAGE_MAX_HEIGHT)
        captions = [
            para for para in self.document.findall(f".//{W}p")
            if (para.find(f"{W}pPr/{W}pStyle") is not None and
                para.find(f"{W}pPr/{W}pStyle").get(f"{W}val") == "FigureCaption")
        ]
        self.assertEqual(len(captions), 2)
        drawing_paragraphs = [
            paragraph for paragraph in self.document.findall(f".//{W}p")
            if paragraph.find(f"{W}r/{W}drawing") is not None
        ]
        for drawing in drawing_paragraphs:
            spacing = drawing.find(f"{W}pPr/{W}spacing")
            self.assertEqual(spacing.get(f"{W}lineRule"), "auto")

    def test_drawing_ids_are_unique(self):
        ids = [item.get("id") for item in self.document.findall(f".//{WP}docPr")]
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(ids), len(set(ids)))

    def test_explicit_figure_caption_is_not_duplicated(self):
        body, _ = MOD.convert_markdown(
            "![图 2-1 示例](large.png)\n\n图 2-1　示例说明。\n",
            self.markdown)
        document = ET.fromstring(
            '<w:body xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">' +
            body + '</w:body>')
        captions = [
            para for para in document.findall(f".//{W}p")
            if (para.find(f"{W}pPr/{W}pStyle") is not None and
                para.find(f"{W}pPr/{W}pStyle").get(f"{W}val") == "FigureCaption")
        ]
        self.assertEqual(len(captions), 1)
        self.assertEqual("".join(captions[0].itertext()), "图 2-1　示例说明。")

    def test_explanation_precedes_image_and_short_caption_follows_it(self):
        body, _ = MOD.convert_markdown(
            "图示解释放在正文。\n\n![较长的替代文本](large.png)\n\n图 2-1　短标题\n",
            self.markdown)
        document = ET.fromstring(
            '<w:body xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
            'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
            'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture">' +
            body + '</w:body>')
        paragraphs = document.findall(f"{W}p")
        self.assertEqual("".join(paragraphs[0].itertext()).replace(MOD.WORD_JOINER, ""),
                         "图示解释放在正文。")
        self.assertIsNotNone(paragraphs[1].find(f".//{W}drawing"))
        self.assertEqual(paragraphs[2].find(f"{W}pPr/{W}pStyle").get(f"{W}val"),
                         "FigureCaption")
        self.assertEqual("".join(paragraphs[2].itertext()), "图 2-1　短标题")

    def test_normal_style_enables_widow_and_cjk_line_controls(self):
        normal = self.style("Normal")
        self.assertIsNotNone(normal.find(f"{W}pPr/{W}widowControl"))
        self.assertIsNotNone(normal.find(f"{W}pPr/{W}kinsoku"))

    def test_page_geometry_and_code_lines(self):
        section = self.document.find(f".//{W}sectPr")
        size = section.find(f"{W}pgSz")
        margins = section.find(f"{W}pgMar")
        self.assertEqual((size.get(f"{W}w"), size.get(f"{W}h")),
                         ("11906", "16838"))
        self.assertEqual((margins.get(f"{W}left"), margins.get(f"{W}right")),
                         ("1134", "1134"))
        self.assertEqual((margins.get(f"{W}top"), margins.get(f"{W}bottom")),
                         ("1417", "1417"))
        code_paragraphs = [
            para for para in self.document.findall(f".//{W}p")
            if (para.find(f"{W}pPr/{W}pStyle") is not None and
                para.find(f"{W}pPr/{W}pStyle").get(f"{W}val") == "Code")
        ]
        self.assertEqual(len(code_paragraphs), 2)


if __name__ == "__main__":
    unittest.main()
