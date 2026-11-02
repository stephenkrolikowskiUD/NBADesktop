"""Offline tests of symmetric, market-anchored eligibility."""
import ast
from pathlib import Path
import unittest

from nba_readiness import side_estimates, qualify_model_picks


class SelectionTests(unittest.TestCase):
    def test_sides_are_symmetric(self):
        over = side_estimates([2] * 9 + [0], 1, -110, -110)
        under = side_estimates([0] * 9 + [2], 1, -110, -110)
        self.assertAlmostEqual(over['OVER']['ev'], under['UNDER']['ev'])
        self.assertAlmostEqual(sum(v['probability'] for v in over.values()), 1)
        self.assertLess(over['OVER']['probability'], .9)

    def test_reliability_shrinks_probability(self):
        full = side_estimates([2] * 10, 1, -110, -110)
        prior = side_estimates([2] * 10, 1, -110, -110, .35)
        self.assertLess(prior['OVER']['probability'], full['OVER']['probability'])

    def test_pushes_reduce_return_not_conditional_probability(self):
        base = side_estimates([2] * 10, 1, -110, -110)
        pushed = side_estimates([2] * 10 + [1] * 10, 1, -110, -110)
        self.assertEqual(base['OVER']['probability'], pushed['OVER']['probability'])
        self.assertAlmostEqual(base['OVER']['ev'] / 2, pushed['OVER']['ev'])

    def test_missing_prices_and_small_samples_fail_closed(self):
        for odds in [None, float('nan'), 0]:
            self.assertEqual(side_estimates([2] * 10, 1, -110, odds), {})
        self.assertEqual(side_estimates([2] * 4, 1, -110, -110), {})

    def test_under_pra_uses_under_evidence_without_cap(self):
        estimates = side_estimates([0] * 20, 1, -110, -110)
        picks = [{'player': str(i), 'prop_type': 'PRA', 'line': 1,
                  'lean': 'UNDER', 'confidence': 'SMASH'} for i in range(3)]
        evidence = {(str(i), 'PRA', 1): estimates for i in range(3)}
        result = qualify_model_picks(picks, evidence, str)
        self.assertEqual(len(result), 3)
        self.assertTrue(all(p['confidence'] == 'LEAN' for p in result))
        picks[0]['lean'] = 'OVER'
        self.assertEqual(len(qualify_model_picks(picks, evidence, str)), 2)

    def test_exact_line_and_ev_floor(self):
        pick = {'player': 'A', 'prop_type': 'PTS', 'line': 1, 'lean': 'OVER'}
        evidence = {('A', 'PTS', 1): {'OVER': {'ev': 4.99, 'edge': 2, 'probability': .55, 'games': 10}}}
        self.assertEqual(qualify_model_picks([pick], evidence, str), [])
        evidence[('A', 'PTS', 1)]['OVER']['ev'] = 5
        self.assertEqual(len(qualify_model_picks([pick], evidence, str)), 1)
        pick['line'] = 2
        self.assertEqual(qualify_model_picks([pick], evidence, str), [])

    def test_consensus_does_not_promote(self):
        source = Path(__file__).resolve().parents[1] / 'NBAEnginev5-4.py'
        tree = ast.parse(source.read_text())
        funcs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in
                 {'normalize_confidence', 'promote_consensus_confidence'}]
        scope = {}
        exec(compile(ast.Module(body=funcs, type_ignores=[]), str(source), 'exec'), scope)
        self.assertEqual(scope['promote_consensus_confidence']('LEAN', 3), 'LEAN')


if __name__ == '__main__':
    unittest.main()
