#!/usr/bin/env python3
"""Recompute Stage2 questions from recorded timers, without selecting new runs."""
import json
import statistics
from pathlib import Path
from publication_extension_charts import coordinates

ROOT = Path(__file__).resolve().parents[1]


def main():
    rows = coordinates(json.loads((ROOT / 'docs/design/performance_extension_data.json').read_text()))
    subsets = {'single_socket': ('3n1s', '8n1s', '16n1s'),
               'at_least_8_nodes': ('8n1s', '8n2s', '16n1s')}
    output = {'definition': 'mean-plane ns/op; reduction AM; equal topology and pressure weights',
              'measured_coordinates': rows, 'subset_reductions': [], 'sensitivity': []}
    for tc in range(142, 148):
        for name, topologies in subsets.items():
            result = {'tc': tc, 'subset': name}
            for label, pressures in [('P175', (175,)), ('P200', (200,)), ('combined', (175, 200))]:
                result[label] = statistics.mean(r['reduction_pct'] for r in rows
                    if r['tc'] == tc and r['pressure_pct'] in pressures and r['topology'] in topologies)
            output['subset_reductions'].append(result)
        for pressure in (175, 200):
            d = {r['topology']: r['reduction_pct'] for r in rows
                 if r['tc'] == tc and r['pressure_pct'] == pressure}
            base = statistics.mean(d.values())
            delta = sum(d['8n'+s] - d['3n'+s] for s in ('1s', '2s')) / 5
            output['sensitivity'].append({'tc': tc, 'pressure': pressure,
                'measured_five_topology_am': base, 'estimated_replacement_am': base + .2*delta,
                'sensitivity_endpoints': sorted((base, base + delta)), 'measured': False})
    output['sensitivity_model'] = (
        'r(4NS)=r(3NS)+alpha*(r(8NS)-r(3NS)); alpha=(4-3)/(8-3)=0.2; replace both 3N cells. '
        'Sensitivity alpha in [0,1] is not a confidence interval or bound: power-of-two stride, '
        'HNF sharing and resident budgets can break interpolation.')
    (ROOT / 'docs/design/stage2_recomputed_data.json').write_text(json.dumps(output, indent=2) + '\n')
    print(json.dumps({k: v for k, v in output.items() if k != 'measured_coordinates'}, indent=2))


if __name__ == '__main__':
    main()
