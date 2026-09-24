#!/usr/bin/env python3
"""Generate publication ns tables from retained observations, without new runs."""
import hashlib
import json
from pathlib import Path
from statistics import mean
from publication_extension_charts import coordinates, hierarchy

ROOT = Path(__file__).resolve().parents[1] / 'docs/design'
FREQUENCY = 25165824


def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |', '| '+' | '.join(['---']*len(headers))+' |'] +
                     ['| '+' | '.join(str(v) for v in row)+' |' for row in rows])


def main():
    preview_path = ROOT/'performance_preview_data.json'
    data = json.loads(preview_path.read_text())['testcases']
    records = []
    def ns(tc, key, value, unit):
        result = value * 1e9/FREQUENCY
        records.append(dict(tc=tc, key=key, retained_counter_value=value, frequency_hz=FREQUENCY,
                            ns=result, display=f'{result:,.3f}', unit=unit))
        return f'{result:,.3f}'
    totals = []
    for tc in list(range(121,125))+list(range(130,135)):
        m=data[f'TC{tc}']['measurements']
        values = m.get('scenario_total_ticks') or {role:m[role+'_total_ticks'] for role in ('naive','spill_noopt','optimized')}
        totals.append([f'TC{tc}']+[ns(tc, role+'_total_ticks',values[role],'ns/scenario') for role in ('naive','spill_noopt','optimized')])
    phase_keys = {130:[('post_pressure_hot_reuse','热点复用（96 次）')],
                  131:[('catalog_reuse','catalog 复用（8,192 次）'),('exclusive_upgrade','独占升级（256 次）')],
                  132:[('checkpoint_recover','检查点恢复（8,192 次）')],
                  133:[('frontier_reuse','图前沿复用（4,096 次）')],
                  134:[('window_reuse','窗口复用（4,096 次）')]}
    phases=[]
    for tc, entries in phase_keys.items():
        m=data[f'TC{tc}']['measurements']
        for key,label in entries:
            values=m[key+'_ticks_per_op']
            phases.append([f'TC{tc} · {label}']+[ns(tc, role+'_'+key,values[role],'ns/op') for role in ('naive','spill_noopt','optimized')])
    paths=[]
    keys={120:[('retained_path','共享热点读')],121:[('retained_path','冷流读取')],
          122:[('retained_path','热点重用'),('retained_hot_share','热点共享')],
          123:[('retained_node1','节点 1 共享读'),('retained_node2','节点 2 共享读'),('retained_mean','两节点等权均值')],
          124:[('retained_path','请求者读取')],125:[('retained_spill_path','读换入')],
          126:[('retained_spill_path','升级写入')],128:[('retained_spill_path','验证读取')],
          129:[('retained_read_v0','V0 换入读'),('retained_read_v1','V1 换入读')]}
    for tc,entries in keys.items():
        for key,label in entries:
            m=data[f'TC{tc}']['measurements']
            paths.append([f'TC{tc}',label,ns(tc,key,m[key+'_ticks_per_op'],'ns/op')])
    m=data['TC127']['measurements']
    flush=ns(127,'retained_flush_ticks',m['retained_flush_ticks'],'ns/flush')
    extension=json.loads((ROOT/'performance_extension_data.json').read_text())
    coords=coordinates(extension)
    extension_rows=[[f'{p}%', '30 / 90',f'{hierarchy(coords,p,"capacity_ratio")[1]:.3f}×',
                     f'{hierarchy(coords,p,"outer_delta_cycles_2ghz")[1]/2:.3f}'] for p in (175,200)]
    fragments={
        'TOTAL_NS':table(['场景','naive（ns/场景）','spill-noopt（ns/场景）','optimized（ns/场景）'],totals),
        'PHASE_NS':table(['场景与计量操作','naive（ns/op）','spill-noopt（ns/op）','optimized（ns/op）'],phases),
        'PATH_NS':table(['场景','路径','时延（ns/op）'],paths)+f'\n\nTC127 的完整写回刷新耗时为 **{flush} ns**，不是每次操作时延。',
        'EXTENSION_NS':table(['目录目标压力','坐标数 / 运行数','容量 GM','附加时延 AM（ns）'],extension_rows)}
    document=ROOT/'cc_ep_deliverable3_performance_api.md'
    text=document.read_text()
    for name, fragment in fragments.items():
        begin,end=f'<!-- BEGIN {name} -->',f'<!-- END {name} -->'
        before,rest=text.split(begin)
        _,after=rest.split(end)
        text=before+begin+'\n'+fragment+'\n'+end+after
    document.write_text(text)
    (ROOT/'delivery_timing_ns.json').write_text(json.dumps(dict(
        source=preview_path.name, source_sha256=hashlib.sha256(preview_path.read_bytes()).hexdigest(),
        method='CNTVCT retained numeric precision; ns = counter value * 1e9 / 25165824. Scenario totals not divided by phase operation counts. No rerun or added precision implied.',
        observations=records, extension_ns=extension_rows),ensure_ascii=False,indent=2)+'\n')
    print(f'generated {len(records)} ns observations; extension values reduced from source histograms')


if __name__=='__main__':
    main()
