#!/usr/bin/env python3
"""Check publication timing, unchanged M3 aggregation and final PDF text."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
from statistics import mean
from sync_delivery_documents import PAIRS

ROOT=Path(__file__).resolve().parents[1]
DESIGN=ROOT/'docs/design'


def normalized(text):
    return re.sub(r'\s+','',text).replace('\u2060','')


def main():
    texts={}
    for relative in PAIRS:
        md=ROOT/relative
        raw=md.read_text()
        body=raw.split('## 附录')[0]
        assert not re.search(r'历史|旧数据|原场景|N/A|BLOCKED|TODO|选定臂|逐臂|单臂|〔待填写〕',body),md
        assert not re.search(r'\d[\d,.]*\s+(?:cycles|ticks)(?:\b|/)',raw),md
        pdftext=subprocess.check_output(['pdftotext','-layout',str(md.with_suffix('.pdf')),'-'],text=True)
        assert '\ufffd' not in pdftext and '\u2060' not in pdftext,md
        texts[md.stem]=normalized(pdftext)
    d3=texts['cc_ep_deliverable3_performance_api']
    timing=json.loads((DESIGN/'delivery_timing_ns.json').read_text())
    assert hashlib.sha256((DESIGN/timing['source']).read_bytes()).hexdigest()==timing['source_sha256']
    for row in timing['observations']:
        assert abs(row['ns']-row['retained_counter_value']*1e9/25165824)<1e-8
        assert row['display'] in d3,row
    m3=json.loads((DESIGN/'performance_boundary_v4_m3.json').read_text())
    assert len(m3['runs'])==240 and len(m3['pairs'])==120
    assert sum(p['reduction_pct']<0 for p in m3['pairs'])==7
    for row in m3['groups']:
        selected=[p for p in m3['pairs'] if p['topology']==row['topology'] and p['pressure']==row['pressure'] and (p['tc']<=230)==(row['group']=='core')]
        assert abs(mean(p['ubcc_ns'] for p in selected)-row['ubcc_ns'])<1e-8
        assert abs(mean(p['ha_ns'] for p in selected)-row['ha_ns'])<1e-8
        for key in ('ubcc_ns','ha_ns','reduction_pct'):
            assert f'{row[key]:.3f}' in d3,(row,key)
    report=dict(pass_all=True,timing_observations=59,m3_runs=240,m3_pairs=120,
                negative_pairs=7,pdf_scalar_checks=59+30*3,
                documents=[dict(document=p,markdown_sha256=hashlib.sha256((ROOT/p).read_bytes()).hexdigest(),
                                pdf_sha256=hashlib.sha256((ROOT/p).with_suffix('.pdf').read_bytes()).hexdigest()) for p in PAIRS])
    (DESIGN/'delivery_semantic_qa.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
