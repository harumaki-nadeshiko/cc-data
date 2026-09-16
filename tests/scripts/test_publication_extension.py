import copy
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'scripts'))
from publication_extension_charts import coordinates, geomean, hierarchy, REFERENCES
from publication_log_stream import canonical_log, open_log


class ExtensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = json.loads((ROOT / 'docs/design/performance_extension_data.json').read_text())
        cls.rows = coordinates(cls.data)

    def test_complete(self):
        self.assertEqual(len(self.rows), 60)
        self.assertEqual(len(self.data['selected_arms']), 180)

    def test_positive_gm(self):
        for values in ([], [0], [-1], [None], [math.inf], [math.nan]):
            with self.assertRaises(ValueError):
                geomean(values)
        self.assertAlmostEqual(geomean([1, 4]), 2)

    def test_negative_delta_retained(self):
        self.assertTrue(any(r['outer_delta_cycles_2ghz'] < 0 for r in self.rows))

    def test_hierarchical_weights(self):
        for p in (175, 200):
            for field in REFERENCES:
                groups, total = hierarchy(self.rows, p, field)
                aggregate = (lambda v: sum(v)/len(v)) if field == 'outer_delta_cycles_2ghz' else geomean
                self.assertAlmostEqual(total, aggregate([g['summary'] for g in groups]))
                self.assertEqual(len(groups), 6)

    def test_separate_pressure(self):
        self.assertNotEqual(hierarchy(self.rows,175,'capacity_ratio')[1], hierarchy(self.rows,200,'capacity_ratio')[1])

    def test_missing_rejected(self):
        with self.assertRaises(ValueError):
            hierarchy(self.rows[1:],175,'speedup')
        data = dict(self.data, selected_arms=self.data['selected_arms'][:-1])
        with self.assertRaises(ValueError):
            coordinates(data)

    def test_duplicate_rejected(self):
        data = dict(self.data, selected_arms=self.data['selected_arms']+[self.data['selected_arms'][0]])
        with self.assertRaises(ValueError):
            coordinates(data)

    def test_replacement_selection(self):
        selected = self.data['selected_arms']
        self.assertEqual(sum(r['source_id'].startswith('metric1-replacements') for r in selected),6)
        local = [r for r in selected if r['source_id'].startswith('tc143-rmbefore')]
        self.assertEqual(len(local),1)
        self.assertEqual(local[0]['resident_capacity'],65536)

    def test_references(self):
        self.assertEqual(REFERENCES['capacity_ratio'],[1.5])
        self.assertEqual(REFERENCES['outer_delta_cycles_2ghz'],[0,50])
        self.assertEqual(REFERENCES['speedup'],[1,1/.9])

    def test_single_round_am(self):
        m2 = self.data['metric2_original']
        self.assertTrue(m2['single_round'])
        values = [100*(1-r['means_ns']['optimized']/r['means_ns']['naive']) for r in m2['comparisons'] if r['applicable']]
        self.assertAlmostEqual(sum(values)/len(values),65.29371670248797)

    def test_stage2_application_reduction_weights(self):
        groups, total = hierarchy(self.rows, 175, 'reduction_pct')
        self.assertTrue(all(len(g['values']) == 3 for g in groups))
        self.assertAlmostEqual(groups[0]['summary'], 6.2096, places=4)
        self.assertAlmostEqual(total, sum(g['summary'] for g in groups)/6)
        self.assertTrue(any(r['reduction_pct'] < 0 for r in self.rows))
        with self.assertRaises(ValueError):
            hierarchy(self.rows[1:], 175, 'reduction_pct')

    def test_canonical_no_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'stderr.log'
            with self.assertRaises(ValueError): canonical_log(p)
            p.write_text('same event\nsame event\n')
            with open_log(canonical_log(p)) as stream: self.assertEqual(len(list(stream)),2)
            p.with_name(p.name+'.zst').write_bytes(b'bad')
            with self.assertRaises(ValueError): canonical_log(p)

    def test_corrupt_zstd_fails(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'stderr.log.zst';p.write_bytes(b'not zstd')
            with self.assertRaises(ValueError):
                with open_log(p) as stream: list(stream)


if __name__ == '__main__':
    unittest.main()
