"""Recent, fully covered publication panels and auditable hierarchical weights."""
import json
import math
from pathlib import Path
import statistics

TOPOLOGIES = ('3n1s', '3n2s', '8n1s', '8n2s', '16n1s')
SOURCE = 'docs/design/performance_extension_data.json'
APPLICATION_TOPOLOGIES = ('3n1s', '8n1s', '16n1s')
REFERENCES = {'capacity_ratio': [1.5], 'outer_delta_cycles_2ghz': [0, 50],
              'speedup': [1, 1 / .9]}


def geomean(values):
    values = list(values)
    if not values or any(v is None or not math.isfinite(v) or v <= 0 for v in values):
        raise ValueError('geometric mean requires finite strictly positive observations')
    return math.exp(statistics.mean(math.log(v) for v in values))


def e2e_ns(arm):
    # Same mean-plane ns/op definition as summarize_database_perf_matrix.py.
    rows = arm['timers']
    if not rows or len({r['plane'] for r in rows}) != len(rows):
        raise ValueError('missing/duplicate E2E planes')
    values = []
    for r in rows:
        if min(r['operations'], r['counter_ticks'], r['counter_frequency_hz']) <= 0:
            raise ValueError('invalid E2E timer')
        values.append(r['counter_ticks'] * 1e9 / r['counter_frequency_hz'] / r['operations'])
    return statistics.mean(values)


def coordinates(data):
    arms = {}
    for row in data['selected_arms']:
        key = (row['pressure_pct'], row['tc'], row['topology'], row['role'])
        if key in arms or row['result']['status'] != 'PASS':
            raise ValueError('duplicate or non-PASS selected arm')
        hist = row['outer_histogram_ps']
        if (not hist or any(v < 0 or count <= 0 for v, count in hist)
                or len({v for v, count in hist}) != len(hist)
                or sum(count for v, count in hist) != row['outer_count']
                or sum(v * count for v, count in hist) != row['outer_sum_ps']):
            raise ValueError('invalid completed-Outer histogram')
        if sum(p['outer_events'] for p in row['outer_processes']) != row['outer_count']:
            raise ValueError('Outer process coverage mismatch')
        arms[key] = row
    expected = {(p, tc, t, role) for p in (175, 200) for tc in range(142, 148)
                for t in TOPOLOGIES for role in ('naive', 'spill', 'ideal')}
    if set(arms) != expected:
        raise ValueError('extension requires exactly 60 coordinates / 180 selected arms')
    rows = []
    for pressure in (175, 200):
        for tc in range(142, 148):
            for topology in TOPOLOGIES:
                naive, spill, ideal = (arms[pressure, tc, topology, role]
                                       for role in ('naive', 'spill', 'ideal'))
                if naive['resident_capacity'] <= 0 or spill['exact_live'] is None:
                    raise ValueError('missing positive capacity evidence')
                means = [a['outer_sum_ps'] / a['outer_count'] / 1000 for a in (spill, ideal)]
                rows.append({'pressure_pct': pressure, 'tc': tc, 'topology': topology,
                             'capacity_ratio': max(spill['resident_capacity'], spill['exact_live']) / naive['resident_capacity'],
                             'outer_delta_cycles_2ghz': (means[0] - means[1]) * 2,
                             'naive_e2e_ns_per_op': e2e_ns(naive),
                              'spill_noopt_e2e_ns_per_op': e2e_ns(spill),
                              'reduction_pct': 100 * (1 - e2e_ns(spill) / e2e_ns(naive)),
                             'speedup': e2e_ns(naive) / e2e_ns(spill)})
    return rows


def hierarchy(rows, pressure, field):
    aggregate = statistics.mean if field in ('outer_delta_cycles_2ghz', 'reduction_pct') else geomean
    topologies = APPLICATION_TOPOLOGIES if field == 'reduction_pct' else TOPOLOGIES
    groups = []
    for tc in range(142, 148):
        selected = [r for r in rows if r['pressure_pct'] == pressure and r['tc'] == tc and r['topology'] in topologies]
        if len(selected) != len(topologies) or {r['topology'] for r in selected} != set(topologies):
            raise ValueError('no available-case reweighting: incomplete topology coverage')
        values = [next(r[field] for r in selected if r['topology'] == t) for t in topologies]
        if any(v is None or not math.isfinite(v) for v in values):
            raise ValueError('non-finite observation')
        groups.append({'tc': tc, 'values': values, 'summary': aggregate(values)})
    return groups, aggregate(g['summary'] for g in groups)


def lineage(data):
    rows = coordinates(data)
    specs = [('ubcc-metric1-extension-matrix', ('capacity_ratio', 'outer_delta_cycles_2ghz'), '图 3-3'),
              ('ubcc-tc142-147-applications', ('reduction_pct',), '图 4-3')]
    output = []
    for name, fields, figure in specs:
        output.append({'name': name, 'source_artifacts': [SOURCE],
                       'generator': 'scripts/publication_extension_charts.py::render',
                       'metric_definition': 'Selected PASS extension; capacity uses five-topology GM; completed Outer Delta uses five-topology AM in cycles@2GHz. Application reduction=100*(1-spill-noopt/naive), using mean-plane E2E ns/op, AM across 3N1S/8N1S/16N1S then AM across TC. Directory pressure P175/P200; provenance retained in source JSON.',
                       'derived_values': {'rows': rows, 'summaries': {
                           str(p): {f: hierarchy(rows, p, f)[1] for f in fields} for p in (175, 200)}},
                       'reference_lines': {f: ([0, 10] if f == 'reduction_pct' else REFERENCES[f]) for f in fields},
                       'document_references': [{'document': 'docs/design/cc_ep_deliverable3_performance_api.' + ext,
                                                'figure': figure} for ext in ('md', 'docx')]})
    original = data['metric2_original']
    output.append({'name': 'ubcc-metric2-reductions', 'source_artifacts': [SOURCE],
                   'generator': 'scripts/publication_extension_charts.py::render',
                   'metric_definition': 'Single round, 21 runs; applicable naive/optimized ratios. TC140 excluded from plot and reduction AM; no speedup GM summary.',
                   'derived_values': original, 'reference_lines': REFERENCES['speedup'],
                   'document_references': [{'document': 'docs/design/cc_ep_deliverable3_performance_api.' + ext,
                                            'figure': '图 4-1'} for ext in ('md', 'docx')]})
    return output


def render(api, data):
    return render_stage2(api, data)


def render_stage2(api, data):
    """Fixed-weight panels; all summaries remain in the accompanying tables."""
    rows = coordinates(data)
    palette = [api.BLUE, api.TEAL, '#9986A8', '#A58B65', '#718573']
    specs = [('ubcc-metric1-extension-matrix', ('capacity_ratio', 'outer_delta_cycles_2ghz')),
             ('ubcc-tc142-147-applications', ('reduction_pct',))]
    for stem, fields in specs:
        fig, axes = api.plt.subplots(2, len(fields), figsize=(7, 5.1), squeeze=False)
        for fi, field in enumerate(fields):
            topologies = APPLICATION_TOPOLOGIES if field == 'reduction_pct' else TOPOLOGIES
            for pi, pressure in enumerate((175, 200)):
                ax = axes[pi, fi]
                groups, overall = hierarchy(rows, pressure, field)
                width = .72 / len(topologies)
                for ti, topo in enumerate(topologies):
                    xs = [i + (ti - (len(topologies)-1)/2) * width for i in range(6)]
                    values = [g['values'][ti] for g in groups]
                    ax.bar(xs, values, width=width*.88, color=palette[ti], label=topo.upper())
                ax.set_xticks(range(6), [f'TC{g["tc"]}' for g in groups], fontsize=11)
                refs = [0, 10] if field == 'reduction_pct' else REFERENCES[field]
                for ref in refs:
                    ax.axhline(ref, color=api.ORANGE, ls='--', lw=.9,
                               label='10% reduction' if field == 'reduction_pct' and ref == 10 else '_nolegend_')
                ylabel = {'capacity_ratio': 'Capacity ratio', 'outer_delta_cycles_2ghz': 'Outer delta (ns)',
                          'reduction_pct': 'E2E reduction (%)'}[field]
                if field == 'outer_delta_cycles_2ghz':
                    # Transform both observations and reference lines to ns, not counter ticks.
                    for patch in ax.patches:
                        patch.set_height(patch.get_height()/2)
                    for line in ax.lines:
                        line.set_ydata([v/2 for v in line.get_ydata()])
                    ax.relim(); ax.autoscale_view()
                ax.set_ylabel(ylabel, fontsize=11)
                ax.set_title(f'P{pressure}', fontsize=12)
                ax.tick_params(axis='y', labelsize=11)
                ax.grid(axis='y', alpha=.18); ax.set_axisbelow(True)
                ax.legend(loc='upper left', bbox_to_anchor=(0, 1.02), ncol=2 if len(fields) == 2 else 3, fontsize=10, frameon=False)
                lo, hi = ax.get_ylim(); ax.set_ylim(lo, hi + (hi-lo)*.24)
        fig.tight_layout(h_pad=1.5)
        api.save_chart(fig, stem)
    cases = [r for r in data['metric2_original']['comparisons'] if r['applicable']]
    values = [r['means_ns']['naive']/r['means_ns']['optimized'] for r in cases]
    fig, ax = api.plt.subplots(figsize=(7, 3.7))
    ax.bar([r['case'] for r in cases], values, width=.5, color=api.BLUE)
    ax.set_yscale('log')
    ax.axhline(1, color=api.GRAY, lw=.8)
    ax.axhline(1/.9, color=api.TEAL, ls='--', label='10% reduction')
    ax.set_ylabel('naive / optimized', fontsize=11)
    ax.tick_params(labelsize=11)
    ax.legend(fontsize=11, frameon=False)
    ax.grid(axis='y', alpha=.18); ax.set_axisbelow(True)
    fig.tight_layout()
    api.save_chart(fig, 'ubcc-metric2-reductions')
    return lineage(data)


def render_legacy(api, data):
    rows = coordinates(data)
    for stem, fields in [('ubcc-metric1-extension-matrix', ('capacity_ratio', 'outer_delta_cycles_2ghz')),
                         ('ubcc-tc142-147-applications', ('speedup',))]:
        fig, axes = api.plt.subplots(len(fields) * 2, 1, figsize=(23 if len(fields) == 2 else 16, 3.5 * len(fields) * 2), squeeze=False)
        for i, field in enumerate(fields):
            for j, pressure in enumerate((175, 200)):
                ax = axes[i*2+j, 0]
                groups, overall = hierarchy(rows, pressure, field)
                labels, values, colors = [], [], []
                summary_name = 'AM' if field == 'outer_delta_cycles_2ghz' else 'GM'
                for group in groups:
                    labels += [t.upper() for t in TOPOLOGIES] + [f'TC{group["tc"]}\n{summary_name}']
                    values += group['values'] + [group['summary']]
                    colors += [api.BLUE] * 5 + [api.TEAL]
                labels += [f'Overall\n{summary_name}']; values += [overall]; colors += [api.ORANGE]
                bars = ax.bar(range(len(values)), values, color=colors, width=.75)
                ax.set_xticks(range(len(values)), labels, rotation=60, ha='right', fontsize=10)
                for k, group in enumerate(groups):
                    ax.text((k*6+2.5)/len(values), .98, f'TC{group["tc"]}', transform=ax.transAxes,
                            ha='center', va='top', fontsize=11)
                    if k:
                        ax.axvline(k*6-.5, color='#cccccc', lw=.6)
                for reference in REFERENCES[field]:
                    ax.axhline(reference, color=api.ORANGE, linestyle='--', lw=1,
                               label=f'{reference:g}' if reference != 1/.9 else '1/0.9 (10% reduction)')
                for k in list(range(5, 36, 6)) + [36]:
                    ax.annotate(f'{values[k]:.3f}', (k, values[k]), xytext=(0, 4 if values[k] >= 0 else -12),
                                textcoords='offset points', ha='center', fontsize=10)
                ylabel = {'capacity_ratio': 'Capacity ratio (×)', 'outer_delta_cycles_2ghz': 'Delta (cycles @ 2 GHz)',
                          'speedup': 'Speedup: naive / spill-noopt (×)'}[field]
                ax.set_ylabel(ylabel)
                ax.set_title(f'P{pressure} directory pressure · 30/30 coordinates · 2026-09-06–08', fontsize=13)
                ax.grid(axis='y', alpha=.2); ax.set_axisbelow(True)
                lo, hi = min(values + REFERENCES[field] + [0]), max(values + REFERENCES[field])
                span = max(hi-lo, .1)
                ax.set_ylim(lo - (.12*span if lo < 0 else 0), hi + .24*span)
                ax.legend(loc='upper right', frameon=False, fontsize=10)
        fig.tight_layout(h_pad=2)
        api.save_chart(fig, stem)
    cases = data['metric2_original']['comparisons']
    values = [r['means_ns']['naive'] / r['means_ns']['optimized'] for r in cases]
    gm = geomean(v for r, v in zip(cases, values) if r['applicable'])
    fig, ax = api.plt.subplots(figsize=(10.5, 4))
    labels = [r['case'] + ('' if r['applicable'] else '\nexcluded') for r in cases] + ['Applicable\nGM']
    ax.bar(labels, values+[gm], color=[api.BLUE if r['applicable'] else api.GRAY for r in cases]+[api.ORANGE])
    ax.set_yscale('log')
    from matplotlib.ticker import NullFormatter
    ax.set_yticks([1, 2, 5, 10, 20, 50, 100], ['1', '2', '5', '10', '20', '50', '100'])
    ax.yaxis.set_minor_formatter(NullFormatter())
    ax.tick_params(axis='y', which='both', labelsize=12)
    for value in REFERENCES['speedup']:
        ax.axhline(value, color=api.TEAL, linestyle='--', label=f'{value:.6g}×')
    for i, value in enumerate(values+[gm]):
        ax.text(i, value*1.12, f'{value:.3f}×', ha='center', fontsize=10)
    ax.set_ylim(.7, max(values)*2.5)
    ax.set_ylabel('Speedup: naive / optimized (×, log scale)')
    ax.set_title('2026-09-07 · original scenarios · one round / 21 runs\nApplicable AM reduction = 65.294%; GM is not the formal criterion', fontsize=13)
    ax.grid(axis='y', alpha=.2); ax.legend(frameon=False)
    api.save_chart(fig, 'ubcc-metric2-reductions')
    return lineage(data)
