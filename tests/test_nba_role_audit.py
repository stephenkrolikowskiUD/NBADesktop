import unittest
from nba_role_audit import role_context, audit, replay_snapshot, score
from nba_readiness import side_estimates


class RoleAuditTests(unittest.TestCase):
    def history(self):
        return [dict(GAME_DATE=f'2026-11-{day:02}', MIN=36 if day > 7 else 24,
                     FGA=12, FTA=2, TOV=1, PRA=30, TEAM_ABBREVIATION='BOS')
                for day in range(1, 11)]

    def test_role_uses_disjoint_windows_and_excludes_same_day(self):
        history = self.history() + [dict(GAME_DATE='2026-11-11', MIN=1)]
        role = role_context(history, '2026-11-11')
        self.assertEqual(role['ROLE_TREND'], 'EXPANDED')
        self.assertEqual(role['ROLE_RECENT_MIN'], 36)
        self.assertEqual(role['ROLE_EARLIER_MIN'], 24)
        self.assertEqual(role, role_context(history + history, '2026-11-11'))

    def test_small_and_missing_history_not_stable(self):
        self.assertEqual(role_context(self.history()[:4], '2026-11-11')['ROLE_TREND'], 'INSUFFICIENT')
        self.assertIsNone(role_context([], '2026-11-11')['ROLE_MIN_DELTA'])

    def test_pushes_and_missing_prices(self):
        rows = [dict(RESULT='YES', ODDS=-110), dict(RESULT='PUSH', ODDS=-110),
                dict(RESULT='NO', ODDS=''), dict(RESULT='DNP', ODDS=-110)]
        result = score(rows)
        self.assertEqual(result['decisive'], 2)
        self.assertEqual(result['priced_settled'], 2)
        self.assertAlmostEqual(result['roi_pct'], 100 * (100 / 110) / 2)

    def snapshot(self):
        return dict(schema='nba-accuracy-inputs-v1', model_policy='nba-symmetric-market-prior-v1',
                    captured_at='2026-11-11T14:00:00Z', slate_date='2026-11-11',
                    candidates=[dict(player='A', metric='PRA', line=29.5, over_odds=-110,
                        under_odds=-110, reliability=1,
                        commence_time='2026-11-11T23:00:00Z', quote_time='2026-11-11T13:50:00Z',
                        history=self.history())])

    def test_replay_both_sides_and_late_capture(self):
        payload = self.snapshot()
        result = replay_snapshot(payload, side_estimates)[0]
        self.assertEqual(set(result['estimates']), {'OVER', 'UNDER'})
        payload['captured_at'] = '2026-11-12T00:00:00Z'
        self.assertEqual(replay_snapshot(payload, side_estimates)[0]['status'], 'excluded')

    def test_replay_rejects_future_history_and_naive_timestamp(self):
        payload = self.snapshot()
        payload['candidates'][0]['history'][0]['GAME_DATE'] = '2026-11-11'
        self.assertEqual(replay_snapshot(payload, side_estimates)[0]['status'], 'excluded')
        payload['captured_at'] = '2026-11-11T14:00:00'
        with self.assertRaises(ValueError):
            replay_snapshot(payload, side_estimates)

    def test_identity_mismatch_never_attaches_probability(self):
        row = dict(POLICY='first-pregame-selection-v1', EVIDENCE='DIRECT', SURFACE='RESEARCH_FEED',
                   GAME_ID='game', PLAYER='A', MARKET='PRA', SIDE='OVER', LINE=20,
                   PUBLISHED_AT='2026-11-11T14:00:00Z', GAME_DATE='2026-11-11', SOURCE_ROW=2,
                   RESULT='YES', ODDS=-110)
        report = audit([row, row], [dict(player='B', MODEL_PROBABILITY=.9)], [])
        self.assertEqual(report['overall']['selected'], 1)
        self.assertEqual(report['calibration'], [])
        self.assertEqual(report['audit_counts']['repeat'], 1)


if __name__ == '__main__':
    unittest.main()
