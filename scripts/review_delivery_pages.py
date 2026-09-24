#!/usr/bin/env python3
"""Render every Writer PDF page and retain text/font geometry for visual review."""
import json
from pathlib import Path
import subprocess
import xml.etree.ElementTree as ET
from PIL import Image, ImageDraw

ROOT = Path('/work/docs/design')
OUT = Path('/review/pages')
OUT.mkdir(parents=True, exist_ok=True)
report = []
for di, stem in enumerate(('cc_ep_protocol_overview', 'cc_ep_deliverable2_verification_reliability_ha', 'cc_ep_deliverable3_performance_api'), 1):
    pdf = ROOT / (stem+'.pdf')
    subprocess.run(['pdftoppm', '-scale-to', '1180', '-png', str(pdf), str(OUT/f'd{di}')], check=True)
    subprocess.run(['pdftotext', '-layout', str(pdf), str(OUT/f'd{di}-fulltext.txt')], check=True)
    subprocess.run(['pdftotext', '-bbox', str(pdf), str(OUT/f'd{di}-bbox.html')], check=True)
    pages = ET.parse(OUT/f'd{di}-bbox.html').getroot().findall('.//{*}page')
    texts, thumbs = [], []
    for pi, page in enumerate(pages, 1):
        filename = OUT / f'd{di}-{pi:02d}.png'
        if not filename.exists():
            filename = OUT / f'd{di}-{pi}.png'
        words = page.findall('.//{*}word')
        text = ' '.join(w.text or '' for w in words)
        texts.append(text)
        report.append(dict(document=di, page=pi, image=str(filename), chars=len(text),
                           outside=[w.text for w in words if float(w.get('xMin'))<35 or float(w.get('xMax'))>float(page.get('width'))-35]))
        image = Image.open(filename).convert('RGB')
        image.thumbnail((417, 590))
        tile = Image.new('RGB', (437, 620), 'white')
        tile.paste(image, (10, 25))
        ImageDraw.Draw(tile).text((12, 5), f'D{di} page {pi}', fill='black')
        thumbs.append(tile)
    for batch in range(0, len(thumbs), 6):
        sheet = Image.new('RGB', (437*3, 620*2), '#cccccc')
        for k, tile in enumerate(thumbs[batch:batch+6]):
            sheet.paste(tile, ((k%3)*437, (k//3)*620))
        sheet.save(OUT / f'd{di}-sheet-{batch//6+1}.png')
(OUT/'scan.json').write_text(json.dumps(report, ensure_ascii=False, indent=2))
print(json.dumps({'pages': len(report), 'outside': [r for r in report if r['outside']]}, ensure_ascii=False, indent=2))
