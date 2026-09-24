#!/usr/bin/env python3
"""Measure embedded SVG text at actual DOCX and Writer PDF image dimensions."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from sync_delivery_documents import PAIRS

ROOT=Path(__file__).resolve().parents[1]
NS={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main',
    'wp':'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
    'a':'http://schemas.openxmlformats.org/drawingml/2006/main',
    'r':'http://schemas.openxmlformats.org/officeDocument/2006/relationships'}


def sizes(svg):
    root=ET.parse(svg).getroot()
    width,height=map(float,root.get('viewBox').split()[2:])
    output=[]
    def walk(element, font=None, scale=1):
        style=element.get('style','')
        match=re.search(r'(?:font-size\s*:|font\s*:[^;]*?)\s*([\d.]+)(?:px|pt)',style)
        if match:font=float(match[1])
        transforms=element.get('transform','')
        for sx,sy in re.findall(r'scale\(\s*([\d.]+)(?:[, ]+([\d.]+))?\s*\)',transforms):
            scale*=min(float(sx),float(sy or sx))
        if element.tag.endswith(('text','tspan')) and element.text and element.text.strip():
            assert font is not None, (svg,style)
            output.append(font*scale)
        for child in element:walk(child,font,scale)
    walk(root)
    assert output,svg
    return width,height,min(output)


def main():
    rows=[]
    for relative in PAIRS:
        md=ROOT/relative
        refs=re.findall(r'!\[[^]]*\]\(figures/([\w-]+)\.png[^)]*\)',md.read_text())
        with zipfile.ZipFile(md.with_suffix('.docx')) as z:
            xml=ET.fromstring(z.read('word/document.xml'))
            images=xml.findall('.//wp:inline',NS)
            assert len(images)==len(refs)
            for stem,image in zip(refs,images):
                extent=image.find('wp:extent',NS)
                w,h=[int(extent.get(k))/12700 for k in ('cx','cy')]
                sw,sh,minimum=sizes(md.parent/'figures'/f'{stem}.svg')
                final=minimum*min(w/sw,h/sh)
                rows.append(dict(document=md.stem,figure=stem,docx_width_pt=w,docx_height_pt=h,
                                 svg_width=sw,svg_height=sh,svg_min_font=minimum,final_min_pt=final))
        listing=subprocess.check_output(['pdfimages','-list',str(md.with_suffix('.pdf'))],text=True)
        actual=[]
        for line in listing.splitlines()[2:]:
            f=line.split()
            if len(f)>13 and f[2]=='image':
                actual.append((int(f[0]),float(f[3])*72/float(f[12]),float(f[4])*72/float(f[13])))
        docrows=[r for r in rows if r['document']==md.stem]
        assert len(actual)==len(docrows),(md,len(actual),len(docrows))
        for row,(page,w,h) in zip(docrows,actual):
            row.update(pdf_page=page,pdf_width_pt=w,pdf_height_pt=h,
                       pdf_min_pt=row['svg_min_font']*min(w/row['svg_width'],h/row['svg_height']))
            assert row['final_min_pt']>=8 and row['pdf_min_pt']>=8,row
    output=ROOT/'docs/design/figures/physical_font_qa.json'
    output.write_text(json.dumps(dict(method='SVG inherited font size and viewBox scaled by DOCX EMU extent; independently cross-check Writer PDF embedded image pixel size and reported PPI.',
                                    threshold_pt=8,placements=rows,pass_all=True),ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'figures':len(rows),'minimum_docx_pt':min(r['final_min_pt'] for r in rows),
                      'minimum_pdf_pt':min(r['pdf_min_pt'] for r in rows)},indent=2))


if __name__=='__main__':main()
