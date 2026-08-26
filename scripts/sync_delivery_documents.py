#!/usr/bin/env python3
"""Generate/check paired Markdown and DOCX delivery documents."""

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import struct
from xml.sax.saxutils import escape
import zipfile


ROOT = Path(__file__).resolve().parents[1]
EMU_PER_CM = 360_000
EMU_PER_INCH = 914_400
EMU_PER_POINT = 12_700
EMU_PER_PIXEL = 9_525
DEFAULT_IMAGE_MAX_WIDTH = int(15.5 * EMU_PER_CM)
DEFAULT_IMAGE_MAX_HEIGHT = int(11.5 * EMU_PER_CM)
# U+2060 is the Unicode word-joiner intended to prohibit a line break without
# changing the visible identifier.  LibreOffice treats U+FEFF as a removable
# byte-order mark in DOCX text, so it does not reliably protect table content.
WORD_JOINER = "\u2060"
ASCII_IDENTIFIER = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]*(?:-[A-Za-z0-9_]+)*)(?![A-Za-z0-9_])")
NARROW_TABLE_IDENTIFIERS = (
    "InvalidateReq", "UpgradeAckNotify", "InvalidateAck", "frontier", "evict",
)
PAIRS = (
    "docs/design/cc_ep_protocol_overview.md",
    "docs/design/cc_ep_deliverable2_verification_reliability_ha.md",
    "docs/design/cc_ep_deliverable3_performance_api.md",
)
MANIFEST = ROOT / "docs/design/delivery_document_pairs.json"


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def plain(text):
    text = re.sub(r"!\[([^]]*)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"\[([^]]+)\]\([^)]+\)", r"\1", text)
    return text.replace("**", "").replace("__", "").replace("`", "").strip()


def prevent_ascii_identifier_breaks(text):
    """Keep English/ASCII identifiers together without changing their appearance."""
    return ASCII_IDENTIFIER.sub(
        lambda match: WORD_JOINER.join(match.group(1)), text)


def prevent_short_line_tail(text, protected_characters=6):
    """Keep a short paragraph tail together to avoid isolated final glyphs."""
    if len(text) < protected_characters:
        return text
    start = len(text) - protected_characters
    tail = text[start:]
    if not re.search(r"[\u3400-\u9fff]", tail):
        return text
    return text[:start] + WORD_JOINER.join(tail)


def run(text, bold=False, mono=False, heading=False, size=None,
        nonbreaking_identifiers=False, protect_tail=False):
    font = "Consolas" if mono else "Calibri"
    east_asia = "Microsoft YaHei" if mono or not heading else "SimHei"
    if nonbreaking_identifiers:
        text = prevent_ascii_identifier_breaks(text)
    if protect_tail:
        text = prevent_short_line_tail(text)
    props = (f'<w:rFonts w:ascii="{font}" w:hAnsi="{font}" '
             f'w:eastAsia="{east_asia}" w:cs="{font}"/>'
             + ("<w:b/>" if bold else "")
             + (f'<w:sz w:val="{size}"/><w:szCs w:val="{size}"/>'
                if size else ""))
    return f"<w:r><w:rPr>{props}</w:rPr><w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r>"


def paragraph(text="", style=None, bold=False, mono=False, indent=0,
              first_line=0, center=False, keep_next=False,
              protect_tail=False):
    props = (f'<w:pStyle w:val="{style}"/>' if style else "")
    if indent or first_line:
        props += f'<w:ind w:left="{indent}" w:firstLine="{first_line}"/>'
    if center:
        props += '<w:jc w:val="center"/>'
    if keep_next:
        props += '<w:keepNext/>'
    return (f"<w:p><w:pPr>{props}</w:pPr>"
            f"{run(text, bold, mono, bool(style and (style == 'Title' or style.startswith('Heading'))), protect_tail=protect_tail)}"
            "</w:p>")


def table(rows, compact=False):
    cell_top_bottom = 0 if compact else 60
    cell_left_right = 45 if compact else 90
    line_height = 160 if compact else 320
    font_size = 16 if compact else 18
    paragraph_style = "CompactGlossaryText" if compact else "TableText"
    output = [
        '<w:tbl><w:tblPr><w:tblStyle w:val="TableGrid"/>'
        '<w:tblW w:w="5000" w:type="pct"/><w:tblLayout w:type="autofit"/>'
        f'<w:tblCellMar><w:top w:w="{cell_top_bottom}" w:type="dxa"/>'
        f'<w:left w:w="{cell_left_right}" w:type="dxa"/><w:bottom w:w="{cell_top_bottom}" w:type="dxa"/>'
        f'<w:right w:w="{cell_left_right}" w:type="dxa"/></w:tblCellMar></w:tblPr>'
    ]
    width = max((len(row) for row in rows), default=0)
    column_widths = [1500, 3300, 1500, 3300] if compact and width == 4 else []
    if column_widths:
        output.append('<w:tblGrid>' + ''.join(
            f'<w:gridCol w:w="{value}"/>' for value in column_widths) +
                      '</w:tblGrid>')
    for row_index, row in enumerate(rows):
        row_props = '<w:trPr><w:cantSplit/>'
        if row_index == 0:
            row_props += '<w:tblHeader/>'
        row_props += '</w:trPr>'
        output.append(f"<w:tr>{row_props}")
        for cell_index, cell in enumerate(row + [""] * (width - len(row))):
            cell_text = plain(cell)
            cell_font_size = (16 if not compact and any(
                identifier in cell_text for identifier in NARROW_TABLE_IDENTIFIERS)
                              else font_size)
            shading = '<w:shd w:val="clear" w:color="auto" w:fill="D9E2F3"/>' if row_index == 0 else ""
            cell_width = (f'<w:tcW w:w="{column_widths[cell_index]}" w:type="dxa"/>'
                          if column_widths else "")
            output.append(
                f'<w:tc><w:tcPr>{cell_width}{shading}</w:tcPr><w:p><w:pPr>'
                f'<w:pStyle w:val="{paragraph_style}"/><w:jc w:val="left"/>'
                f'<w:spacing w:before="0" w:after="0" w:line="{line_height}" w:lineRule="exact"/>'
                '</w:pPr>' + run(cell_text, row_index == 0,
                                 heading=row_index == 0, size=cell_font_size,
                                 nonbreaking_identifiers=True) + '</w:p></w:tc>')
        output.append("</w:tr>")
    output.append("</w:tbl>")
    return "".join(output)


def compact_glossary_rows(rows):
    """Lay out a two-column glossary as two term/description pairs per row."""
    if not rows or len(rows[0]) != 2:
        return rows
    header = rows[0]
    output = [header + header]
    entries = rows[1:]
    midpoint = (len(entries) + 1) // 2
    left_entries = entries[:midpoint]
    right_entries = entries[midpoint:]
    for index, entry in enumerate(left_entries):
        left = entry + [""] * (2 - len(entry))
        right = right_entries[index] if index < len(right_entries) else ["", ""]
        right = right + [""] * (2 - len(right))
        output.append(left[:2] + right[:2])
    return output


def png_size(path):
    data = path.read_bytes()[:24]
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError(f"unsupported image type: {path}")
    return struct.unpack(">II", data[16:24])


def length_to_emu(value):
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)(cm|mm|in|pt|px)?", value,
                         re.IGNORECASE)
    if not match:
        return None
    amount = float(match.group(1))
    unit = (match.group(2) or "px").lower()
    factors = {"cm": EMU_PER_CM, "mm": EMU_PER_CM / 10,
               "in": EMU_PER_INCH, "pt": EMU_PER_POINT,
               "px": EMU_PER_PIXEL}
    return int(amount * factors[unit])


def parse_image(line):
    """Return alt, target, hinted width and height for a Markdown image."""
    match = re.fullmatch(r"!\[([^]]*)\]\(([^)]+)\)\s*(?:\{([^}]*)\})?",
                         line.strip())
    if not match:
        return None
    alt, target, attributes = match.groups()
    width = height = None
    size_match = re.search(r"\s+=([0-9.]+(?:cm|mm|in|pt|px)?)x([0-9.]+(?:cm|mm|in|pt|px)?)\s*$",
                           target, re.IGNORECASE)
    if size_match:
        width = length_to_emu(size_match.group(1))
        height = length_to_emu(size_match.group(2))
        target = target[:size_match.start()].strip()
    if attributes:
        for key, value in re.findall(r"(width|height)\s*=\s*[\"']?([0-9.]+(?:cm|mm|in|pt|px)?)[\"']?",
                                     attributes, re.IGNORECASE):
            if key.lower() == "width":
                width = length_to_emu(value)
            else:
                height = length_to_emu(value)
    # A quoted Markdown title is not part of the image path.
    target = re.sub(r'\s+["\'][^"\']*["\']\s*$', "", target).strip()
    return alt, target, width, height


def image_extent(width_px, height_px, hinted_width=None, hinted_height=None):
    natural_width = width_px * EMU_PER_PIXEL
    natural_height = height_px * EMU_PER_PIXEL
    if hinted_width and hinted_height:
        scale = min(hinted_width / natural_width,
                    hinted_height / natural_height)
    elif hinted_width:
        scale = hinted_width / natural_width
    elif hinted_height:
        scale = hinted_height / natural_height
    else:
        scale = 1.0
    scale = min(scale, DEFAULT_IMAGE_MAX_WIDTH / natural_width,
                DEFAULT_IMAGE_MAX_HEIGHT / natural_height)
    return max(1, int(natural_width * scale)), max(1, int(natural_height * scale))


def image_paragraph(rel_id, name, width_px, height_px, drawing_id,
                    hinted_width=None, hinted_height=None):
    cx, cy = image_extent(width_px, height_px, hinted_width, hinted_height)
    drawing = f'''<w:r><w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="{cx}" cy="{cy}"/><wp:effectExtent l="0" t="0" r="0" b="0"/><wp:docPr id="{drawing_id}" name="{escape(name)}" descr="{escape(name)}"/><wp:cNvGraphicFramePr><a:graphicFrameLocks xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" noChangeAspect="1"/></wp:cNvGraphicFramePr><a:graphic xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:nvPicPr><pic:cNvPr id="{drawing_id}" name="{escape(name)}"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" r:embed="{rel_id}"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing></w:r>'''
    return (f'<w:p><w:pPr><w:jc w:val="center"/>'
            f'<w:spacing w:before="100" w:after="40" w:line="240" '
            f'w:lineRule="auto"/></w:pPr>{drawing}</w:p>')


def page_break():
    return '<w:p><w:r><w:br w:type="page"/></w:r></w:p>'


def convert_markdown(text, md_path):
    lines = text.splitlines()
    output = []
    images = []
    index = 0
    code = None
    first_heading = True
    current_heading = ""
    while index < len(lines):
        line = lines[index]
        if line.strip() == "<!-- PAGEBREAK -->":
            output.append(page_break())
            index += 1
            continue
        if re.fullmatch(r"<!--.*-->", line.strip()):
            index += 1
            continue
        if line.startswith("```"):
            if code is None:
                code = []
            else:
                for code_line in code or [""]:
                    output.append(paragraph(code_line, "Code", mono=True))
                code = None
            index += 1
            continue
        if code is not None:
            code.append(line)
            index += 1
            continue
        image = parse_image(line)
        if image:
            alt, target, hinted_width, hinted_height = image
            path = (md_path.parent / target).resolve()
            width, height = png_size(path)
            rel_id = f"rIdImage{len(images) + 1}"
            media_name = f"image{len(images) + 1}.png"
            images.append((rel_id, media_name, path))
            caption = plain(alt) or media_name
            output.append(image_paragraph(rel_id, caption, width, height,
                                           len(images), hinted_width,
                                           hinted_height))
            next_index = index + 1
            while next_index < len(lines) and not lines[next_index].strip():
                next_index += 1
            next_line_is_caption = (
                next_index < len(lines) and
                re.match(r"^\s*图\s*\d+(?:[-－]\d+)?", lines[next_index]))
            if alt.strip() and not next_line_is_caption:
                output.append(paragraph(plain(alt), "FigureCaption", center=True))
            index += 1
            continue
        if line.startswith("|") and line.rstrip().endswith("|"):
            rows = []
            while index < len(lines) and lines[index].startswith("|") and lines[index].rstrip().endswith("|"):
                cells = [item.strip() for item in lines[index].strip().strip("|").split("|")]
                if not all(re.fullmatch(r":?-{3,}:?", item) for item in cells):
                    rows.append(cells)
                index += 1
            compact = "术语表" in current_heading
            if compact:
                rows = compact_glossary_rows(rows)
            output.append(table(rows, compact=compact))
            if index < len(lines):
                caption = re.fullmatch(r"\s*(?:(?:Table|表)\s*[:：]|:)\s*(.+?)\s*",
                                       lines[index], re.IGNORECASE)
                if caption:
                    output.append(paragraph(plain(caption.group(1)), "TableCaption",
                                            center=True))
                    index += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            value = plain(heading.group(2))
            current_heading = value
            style = "Title" if first_heading else f"Heading{min(len(heading.group(1)), 3)}"
            output.append(paragraph(value, style, center=first_heading,
                                    keep_next=True))
            first_heading = False
        elif re.match(r"^\*\*(文档版本|交付阶段|项目名称|甲方单位)：", line):
            output.append(paragraph(plain(line), center=True))
        elif re.match(r"^\s*[-*+]\s+", line):
            output.append(paragraph("• " + plain(re.sub(r"^\s*[-*+]\s+", "", line)), indent=360))
        elif line.startswith(">"):
            output.append(paragraph(plain(line.lstrip("> ")), "Quote", indent=360))
        elif re.match(r"^\s*图\s*\d+(?:[-－]\d+)?", line):
            output.append(paragraph(plain(line), "FigureCaption", center=True))
        elif line.strip() and not re.fullmatch(r"\s*[-*_]{3,}\s*", line):
            output.append(paragraph(plain(line), first_line=420,
                                    protect_tail=True))
        index += 1
    if code is not None:
        for code_line in code or [""]:
            output.append(paragraph(code_line, "Code", mono=True))
    return "".join(output), images


def styles():
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:pPr><w:widowControl/><w:kinsoku/><w:spacing w:before="0" w:after="0" w:line="400" w:lineRule="exact"/><w:jc w:val="both"/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei" w:cs="Calibri"/><w:sz w:val="21"/><w:szCs w:val="21"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Title"><w:name w:val="Title"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/><w:spacing w:before="240" w:after="180" w:line="480" w:lineRule="exact"/><w:keepNext/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="SimHei" w:cs="Calibri"/><w:b/><w:color w:val="17365D"/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80" w:line="400" w:lineRule="exact"/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="SimHei" w:cs="Calibri"/><w:b/><w:color w:val="17365D"/><w:sz w:val="30"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="180" w:after="60" w:line="400" w:lineRule="exact"/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="SimHei" w:cs="Calibri"/><w:b/><w:color w:val="365F91"/><w:sz w:val="26"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:pPr><w:keepNext/><w:spacing w:before="120" w:after="40" w:line="400" w:lineRule="exact"/><w:outlineLvl w:val="2"/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="SimHei" w:cs="Calibri"/><w:b/><w:color w:val="4F81BD"/><w:sz w:val="23"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Code"><w:name w:val="Code"/><w:basedOn w:val="Normal"/><w:pPr><w:spacing w:before="0" w:after="0" w:line="300" w:lineRule="exact"/><w:shd w:fill="F2F2F2"/><w:ind w:left="240"/></w:pPr><w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas" w:eastAsia="Microsoft YaHei" w:cs="Consolas"/><w:sz w:val="18"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="Quote"><w:name w:val="Quote"/><w:basedOn w:val="Normal"/><w:rPr><w:i/><w:color w:val="555555"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="TableText"><w:name w:val="Table Text"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="left"/><w:spacing w:before="0" w:after="0" w:line="320" w:lineRule="exact"/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei" w:cs="Calibri"/><w:sz w:val="18"/><w:szCs w:val="18"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="CompactGlossaryText"><w:name w:val="Compact Glossary Text"/><w:basedOn w:val="TableText"/><w:pPr><w:jc w:val="left"/><w:spacing w:before="0" w:after="0" w:line="160" w:lineRule="exact"/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei" w:cs="Calibri"/><w:sz w:val="16"/><w:szCs w:val="16"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="FigureCaption"><w:name w:val="Figure Caption"/><w:basedOn w:val="Normal"/><w:pPr><w:jc w:val="center"/><w:keepLines/><w:spacing w:before="0" w:after="120" w:line="320" w:lineRule="exact"/></w:pPr><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:eastAsia="Microsoft YaHei" w:cs="Calibri"/><w:sz w:val="18"/><w:color w:val="555555"/></w:rPr></w:style>
<w:style w:type="paragraph" w:styleId="TableCaption"><w:name w:val="Table Caption"/><w:basedOn w:val="FigureCaption"/><w:pPr><w:jc w:val="center"/><w:spacing w:before="40" w:after="100" w:line="320" w:lineRule="exact"/></w:pPr></w:style>
<w:style w:type="table" w:styleId="TableGrid"><w:name w:val="Table Grid"/><w:tblPr><w:tblBorders><w:top w:val="single" w:color="B7C9E2" w:sz="4"/><w:left w:val="single" w:color="B7C9E2" w:sz="4"/><w:bottom w:val="single" w:color="B7C9E2" w:sz="4"/><w:right w:val="single" w:color="B7C9E2" w:sz="4"/><w:insideH w:val="single" w:color="D9E2F3" w:sz="4"/><w:insideV w:val="single" w:color="D9E2F3" w:sz="4"/></w:tblBorders></w:tblPr></w:style>
</w:styles>'''


def build_docx(md_path, docx_path):
    source_hash = sha256(md_path)
    source = md_path.read_text(encoding="utf-8")
    title = plain(source.splitlines()[0].lstrip("# "))
    body, images = convert_markdown(source, md_path)
    document = ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
                'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
                'xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" '
                'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                'xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>'
                + body
                + '<w:sectPr><w:footerReference w:type="default" r:id="rIdFooter"/>'
                '<w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1417" w:right="1134" '
                'w:bottom="1417" w:left="1134" w:header="567" w:footer="567"/></w:sectPr>'
                '</w:body></w:document>')
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    core = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            f'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>{escape(title)}</dc:title><dc:creator>CC-EP project</dc:creator><dc:description>source_sha256={source_hash}</dc:description><dcterms:created xsi:type="dcterms:W3CDTF">{now}</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">{now}</dcterms:modified></cp:coreProperties>')
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Default Extension="png" ContentType="image/png"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/><Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/><Override PartName="/word/footer1.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml"/><Override PartName="/word/settings.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>',
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>',
        "word/_rels/document.xml.rels": ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
            '<Relationship Id="rIdFooter" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/footer" Target="footer1.xml"/>'
            '<Relationship Id="rIdSettings" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/settings" Target="settings.xml"/>'
            + ''.join(f'<Relationship Id="{rel_id}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="media/{media_name}"/>'
                      for rel_id, media_name, _ in images)
            + '</Relationships>'),
        "word/document.xml": document,
        "word/styles.xml": styles(),
        "word/settings.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:settings xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:updateFields w:val="true"/><w:defaultTabStop w:val="420"/></w:settings>',
        "word/footer1.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:ftr xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:p><w:pPr><w:jc w:val="center"/></w:pPr><w:r><w:t>第 </w:t></w:r><w:r><w:fldChar w:fldCharType="begin"/></w:r><w:r><w:instrText> PAGE </w:instrText></w:r><w:r><w:fldChar w:fldCharType="end"/></w:r><w:r><w:t> 页</w:t></w:r></w:p></w:ftr>',
        "docProps/core.xml": core,
        "docProps/app.xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>CC-EP Markdown DOCX Sync</Application></Properties>',
    }
    with zipfile.ZipFile(docx_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value.encode("utf-8"))
        for _, media_name, image_path in images:
            archive.write(image_path, f"word/media/{media_name}")


def embedded_hash(docx_path):
    try:
        with zipfile.ZipFile(docx_path) as archive:
            core = archive.read("docProps/core.xml").decode("utf-8")
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    match = re.search(r"source_sha256=([0-9a-f]{64})", core)
    return match.group(1) if match else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    entries = []
    errors = []
    for relative in PAIRS:
        md_path = ROOT / relative
        docx_path = md_path.with_suffix(".docx")
        expected = sha256(md_path)
        if args.check:
            if embedded_hash(docx_path) != expected:
                errors.append(f"out of sync: {relative}")
        else:
            build_docx(md_path, docx_path)
        entries.append({"markdown": relative, "docx": str(docx_path.relative_to(ROOT)),
                        "markdown_sha256": expected,
                        "docx_sha256": sha256(docx_path) if docx_path.exists() else None,
                        "embedded_source_sha256": embedded_hash(docx_path)})
    if errors:
        print("\n".join(errors))
        return 1
    if args.check:
        print(f"delivery document pairs synchronized: {len(entries)}")
        return 0
    MANIFEST.write_text(json.dumps({"schema_version": 1, "pairs": entries},
                                   indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"generated delivery document pairs: {len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
