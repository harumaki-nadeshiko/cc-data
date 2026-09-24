#!/usr/bin/env python3
"""Reduce immutable v4 evidence inside Docker; never use stored primary_ns."""
import argparse
import hashlib
import json
import re
from pathlib import Path
from statistics import mean

TOPOS = ('3n1s', '3n2s', '8n1s', '8n2s', '16n1s')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('evidence', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    rows = []
    metric_names = {}
    for path in sorted((args.evidence / 'results/m3').glob('*/*/*/*/result.json')):
        d = json.loads(path.read_text())
        assert d['status'] == 'PASS' and d['return_code'] == 0, path
        assert all(str(v) == '0' for v in d['child_exits'].values()), path
        metrics = d['parsed']['metrics']
        for name, digest in d['evidence_sha256'].items():
            if name.startswith('simout') and name.endswith('.log'):
                assert hashlib.sha256((path.parent/name).read_bytes()).hexdigest() == digest, (path, name)
        metric_names[d['tc']] = list(metrics)
        values = {}
        for name, metric in metrics.items():
            sources = metric['sources']
            assert all(s['frequency_hz'] == 25165824 for s in sources)
            for source in sources:
                lines = (path.parent/Path(source['file']).name).read_text().splitlines()
                fields = dict(re.findall(r'(\w+)=([^\s]+)', lines[source['line']-1]))
                assert fields['phase'] == source['phase'], (path, source)
                if 'operations' not in fields:
                    candidates = [dict(re.findall(r'(\w+)=([^\s]+)', line)) for line in lines if '[GUEST-TIMER]' in line]
                    matches = [f for f in candidates if f.get('phase') == source['phase']]
                    if not matches:
                        assert name in ('producer_consumer_load', 'queued_token_store'), (path, source, fields)
                        assert int(fields['samples']) == source['count']
                        assert int(fields['mean']) == source['ticks']
                        continue  # Auxiliary PERF-LATENCY mean, not a summed timer.
                    fields = matches[0]
                assert int(fields['operations']) == source['count'], (path, source, fields)
                assert int(fields['counter_ticks']) == source['ticks'], (path, source, fields)
                assert int(fields['counter_frequency_hz']) == source['frequency_hz']
            if name in ('producer_consumer_load', 'queued_token_store'):
                value = sum(s['ticks'] * s['count'] * 1e9 / s['frequency_hz'] for s in sources) / sum(s['count'] for s in sources)
            elif 'end_to_end' in name:
                value = max(s['ticks'] / s['count'] * 1e9 / s['frequency_hz'] for s in sources)
            else:
                value = sum(s['ticks'] * 1e9 / s['frequency_hz'] for s in sources) / sum(s['count'] for s in sources)
            values[name] = value
        if d['tc'] == 232:
            primary = (2 * values['hot_key_read'] + values['hot_key_write']) / 3
        elif d['tc'] == 233:
            primary = values['producer_consumer_service']
        else:
            primary = values[next(iter(metrics))]
        rows.append(dict(tc=d['tc'], topology=d['topology'], pressure=d['pct'], arm=d['arm'],
                         ns=primary, metrics=values, source=str(path.relative_to(args.evidence)),
                         sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                         workload_sha256=d['workload_sha256']))
    assert len(rows) == 240
    index = {(r['topology'], r['tc'], r['pressure'], r['arm']): r for r in rows}
    assert len(index) == 240
    pairs = []
    for topo in TOPOS:
        for tc in range(228, 236):
            for pressure in (0, 50, 100):
                u, h = [index[topo, tc, pressure, arm] for arm in ('ubcc', 'ha-vi')]
                assert u['workload_sha256'] == h['workload_sha256']
                pairs.append(dict(topology=topo, tc=tc, pressure=pressure, ubcc_ns=u['ns'],
                                  ha_ns=h['ns'], reduction_pct=100 * (1-u['ns']/h['ns'])))
    groups = []
    for topo in TOPOS:
        for pressure in (0, 50, 100):
            for name, cases in [('core', range(228, 231)), ('representative', range(231, 236))]:
                subset = [p for p in pairs if p['topology'] == topo and p['pressure'] == pressure and p['tc'] in cases]
                u, h = [mean(p[k] for p in subset) for k in ('ubcc_ns', 'ha_ns')]
                groups.append(dict(topology=topo, pressure=pressure, group=name, ubcc_ns=u, ha_ns=h,
                                   reduction_pct=100*(1-u/h), lower=u<h))
    output = dict(schema='boundary-v4-publication-1', runs=rows, pairs=pairs, groups=groups,
                  method='240 runs = 5 topologies x 8 TC x 3 pressures x 2 arms; NOT three repetitions. TC232 fixed 2:1; service pooled operations; E2E maximum normalized plane duration.')
    output['overall'] = {name: {
        'ratio_of_equal_weight_absolute_means_reduction_pct': 100*(1-mean(g['ubcc_ns'] for g in groups if g['group']==name)/mean(g['ha_ns'] for g in groups if g['group']==name)),
        'coordinate_reduction_am_pct': mean(g['reduction_pct'] for g in groups if g['group']==name),
        'passed_coordinates': sum(g['lower'] for g in groups if g['group']==name)}
        for name in ('core', 'representative')}
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps(metric_names, indent=2))
    print(json.dumps(groups, indent=2))
    table = ['| 拓扑 | L3 压力 | 核心 UBCC / HA-VI（ns/op） | 核心降幅 | 代表 UBCC / HA-VI（ns/op） | 代表降幅 |',
             '|---|---:|---:|---:|---:|---:|']
    for topo in TOPOS:
        for pressure in (0, 50, 100):
            c, r = [next(g for g in groups if g['topology'] == topo and g['pressure'] == pressure and g['group'] == name) for name in ('core', 'representative')]
            table.append(f"| {topo.upper()} | {pressure}% | {c['ubcc_ns']:.3f} / {c['ha_ns']:.3f} | {c['reduction_pct']:.3f}% | {r['ubcc_ns']:.3f} / {r['ha_ns']:.3f} | {r['reduction_pct']:.3f}% |")
    args.output.with_suffix('.md').write_text('\n'.join(table)+'\n')
    if args.output.parent.name == 'design':
        document = args.output.parent / 'cc_ep_deliverable3_performance_api.md'
        text = document.read_text()
        start, end = '<!-- BEGIN V4 GROUP TABLE -->', '<!-- END V4 GROUP TABLE -->'
        before, rest = text.split(start)
        _, after = rest.split(end)
        document.write_text(before + start + '\n' + '\n'.join(table) + '\n' + end + after)
        charts(pairs, args.output.parent / 'figures')


def charts(pairs, directory):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for path in Path('/usr/local/share/fonts').rglob('*'):
        if path.suffix.lower() in ('.ttf', '.otf', '.ttc'):
            font_manager.fontManager.addfont(str(path))
    plt.rcParams.update({'font.family': 'Microsoft YaHei', 'font.size': 9,
                         'svg.fonttype': 'none', 'axes.unicode_minus': False})
    colors = ['#284e76', '#428d9e', '#76914d', '#ae8c4e', '#8f738e']
    for cases, stem in [(range(228, 231), 'ubcc-ha-vi-comparison'),
                        (range(231, 236), 'ubcc-metric3-per-tc-reductions')]:
        fig, axes = plt.subplots(3, 1, figsize=(6.1, 6.4), sharex=True)
        for ax, pressure in zip(axes, (0, 50, 100)):
            for ti, topo in enumerate(TOPOS):
                values = [next(p['reduction_pct'] for p in pairs if p['topology'] == topo and p['tc'] == tc and p['pressure'] == pressure) for tc in cases]
                ax.bar([i+(ti-2)*.15 for i in range(len(cases))], values, width=.14, color=colors[ti], label=topo.upper(), zorder=3)
            ax.axhline(0, color='#777777', linewidth=.7)
            ax.set_ylabel('降幅（%）', fontsize=9)
            ax.text(.99, .9, f'P{pressure}', ha='right', transform=ax.transAxes, fontsize=10)
            ax.grid(axis='y', color='#e4e4e4', linewidth=.5, zorder=0)
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.tick_params(labelsize=9)
        axes[-1].set_xticks(range(len(cases)))
        axes[-1].set_xticklabels([f'TC{tc}' for tc in cases])
        handles, labels = axes[0].get_legend_handles_labels()
        fig.legend(handles, labels, loc='upper center', ncol=5, frameon=False, fontsize=9,
                   handlelength=1, columnspacing=.9)
        fig.subplots_adjust(left=.13, right=.98, bottom=.06, top=.92, hspace=.2)
        for suffix in ('svg', 'png'):
            fig.savefig(directory / f'{stem}.{suffix}', dpi=300)
        plt.close(fig)


if __name__ == '__main__':
    main()
