import copy
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from unittest.mock import patch

from monitor import engine
from monitor.evaluation import evaluate_all
from test_engine import NOW, series, settings


class EvaluationCacheTests(unittest.TestCase):
    def test_full_pipeline_matches_uncached_result_and_does_not_change_inputs(self):
        for mode in ('fresh', 'missing_clock', 'conflicting_values', 'detail_header', 'base'):
            rows = series()
            if mode == 'missing_clock':
                rows[-1]['source']['data_at'] = None
            elif mode == 'conflicting_values':
                peer = copy.deepcopy(rows[-1]); peer['metrics']['spend'] += 10
                rows.append(peer)
            elif mode == 'detail_header':
                for row in rows:
                    row['source'].update(kind='browser_dom', url='https://qianchuan.jinritemai.com/uni-prom/detail?aavid=123')
            elif mode == 'base':
                for row in rows:
                    row['metrics'].update(base_spend=row['metrics']['spend'],
                        base_gmv=row['metrics']['gmv'], base_orders=row['metrics']['orders'])
            before = copy.deepcopy(rows)
            with self.subTest(mode=mode):
                self.assertEqual(evaluate_all(rows, settings(), NOW),
                                 evaluate_all.__wrapped__(rows, settings(), NOW))
                self.assertEqual(rows, before)
        self.assertIsNone(engine._evaluation_records.get())

    def test_consumers_share_parsing_but_not_working_metrics_or_issues(self):
        row = series()[-1]; accounts = engine._settings(settings())[1]
        with patch.object(engine, '_parse_record', wraps=engine._parse_record) as parse:
            with engine._record_cache_scope():
                first = engine._record(row, accounts, NOW)
                first['metrics']['spend'] = 999999
                first['issues'].append('consumer-local')
                first['source']['data_at'] = None
                first['control']['history_known'] = False
                second = engine._record(row, accounts, NOW)
            self.assertEqual(parse.call_count, 1)
        self.assertEqual(second['metrics']['spend'], row['metrics']['spend'])
        self.assertNotIn('consumer-local', second['issues'])
        self.assertEqual(second['source']['data_at'], row['source']['data_at'])
        self.assertEqual(second['control']['history_known'], row['control']['history_known'])

    def test_account_membership_and_evaluation_time_are_not_reused(self):
        row = series()[-1]; accounts = engine._settings(settings())[1]
        with engine._record_cache_scope():
            self.assertIsNotNone(engine._record(row, accounts, NOW))
            self.assertIsNone(engine._record(row, {}, NOW))
            later = engine._record(row, accounts, NOW + timedelta(days=1))
            self.assertIn('统计日期不是今日，仅供历史观察', later['issues'])
        row['metrics']['spend'] = 321
        with engine._record_cache_scope():
            self.assertEqual(engine._record(row, accounts, NOW)['metrics']['spend'], 321)

    def test_exception_and_nested_evaluations_restore_prior_scope(self):
        self.assertIsNone(engine._evaluation_records.get())
        with engine._record_cache_scope():
            outer = engine._evaluation_records.get()
            with self.assertRaises(RuntimeError):
                with engine._record_cache_scope():
                    self.assertIsNot(engine._evaluation_records.get(), outer)
                    raise RuntimeError('test interruption')
            self.assertIs(engine._evaluation_records.get(), outer)
        self.assertIsNone(engine._evaluation_records.get())

    def test_parallel_pipelines_and_changed_targets_match_independent_results(self):
        rows = series(); cfg = settings(); other = copy.deepcopy(cfg)
        other['accounts'][0]['target_roi'] = None
        cases = [(rows, cfg, NOW), (rows, other, NOW + timedelta(days=1))]
        expected = [evaluate_all.__wrapped__(*case) for case in cases]
        with ThreadPoolExecutor(max_workers=2) as pool:
            actual = list(pool.map(lambda case: evaluate_all(*case), cases))
        self.assertEqual(actual, expected)
        self.assertIsNone(engine._evaluation_records.get())


if __name__ == '__main__':
    unittest.main()
