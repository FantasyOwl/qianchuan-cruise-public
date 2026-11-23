import copy
import unittest
from datetime import timedelta

from monitor.analytics import analyze_monitoring
from monitor.evaluation import evaluate_all
from monitor.reporting import build_report
from monitor.ai import make_package
from test_engine import NOW, series, settings


class ObservationClockTests(unittest.TestCase):
    def midnight(self,scope='account'):
        now=NOW.replace(day=22,hour=0,minute=2)
        rows=series(scope=scope,now=now-timedelta(minutes=3))
        return rows,now

    def test_previous_day_just_before_midnight_is_historical_even_inside_ten_minute_age_limit(self):
        rows,now=self.midnight()
        item=analyze_monitoring(rows,settings(),now)['entities'][0]
        self.assertEqual(item['data_status'],'stale')
        self.assertIn('日累计统计日期不是今天',item['data_status_reason'])
        self.assertFalse(item['spend_trend']['is_current'])
        self.assertEqual(sum(w['complete'] for w in item['hourly']),3)
        self.assertEqual(sum(w['complete'] for w in item['spend_trend']['windows']),3)
        self.assertEqual(rows[-1]['source']['metric_date'],'2026-10-21')

    def test_recent_previous_day_stock_zero_does_not_raise_a_current_stock_alert(self):
        rows,now=self.midnight('product');rows[-1]['metrics']['stock']=0
        result=analyze_monitoring(rows,settings(),now)
        self.assertEqual(result['alerts'],[])
        self.assertEqual(result['entities'][0]['product']['stock'],0)

    def test_live_session_starting_yesterday_can_still_have_current_verified_data(self):
        now=NOW.replace(day=22,hour=0,minute=2);rows=series(scope='live',now=now)
        start=rows[0]['source']['data_at']
        for row in rows:row['source'].update(metric_period='live_session',period_start=start,metric_date=start[:10])
        item=analyze_monitoring(rows,settings(),now)['entities'][0]
        self.assertEqual(item['data_status'],'fresh')
        self.assertTrue(item['spend_trend']['is_current'])
        self.assertEqual(sum(w['complete'] for w in item['hourly']),3)

    def test_latest_missing_time_selects_the_latest_capture_instead_of_an_older_good_clock(self):
        rows=series();row=copy.deepcopy(rows[-1]);row['id']='latest-missing'
        now=NOW+timedelta(seconds=20)
        row['source'].update(captured_at=now.isoformat(),data_at=None);rows.append(row)
        item=analyze_monitoring(rows,settings(),now)['entities'][0]
        self.assertEqual(item['snapshot_id'],'latest-missing')
        self.assertIsNone(item['data_at']);self.assertEqual(item['data_status'],'unverified')
        self.assertFalse(item['spend_trend']['is_current']);self.assertEqual(item['hourly'],[])

    def test_latest_regressed_time_is_not_hidden_by_a_later_old_data_clock(self):
        rows=series();row=copy.deepcopy(rows[-1]);row['id']='latest-regressed';now=NOW+timedelta(seconds=20)
        row['source'].update(captured_at=now.isoformat(),data_at=(NOW-timedelta(minutes=20)).isoformat());rows.append(row)
        item=analyze_monitoring(rows,settings(),now)['entities'][0]
        self.assertEqual(item['snapshot_id'],'latest-regressed')
        self.assertEqual(item['data_status'],'unverified');self.assertIn('时间回退',item['data_status_reason'])
        self.assertFalse(item['spend_trend']['is_current'])

    def test_coincident_captures_with_different_clocks_are_unverified_in_both_input_orders(self):
        for clock in [None,(NOW-timedelta(minutes=1)).isoformat()]:
            rows=series();row=copy.deepcopy(rows[-1]);row['id']='coincident';row['source']['data_at']=clock
            for values in [rows+[row],[row]+rows]:
                item=analyze_monitoring(values,settings(),NOW)['entities'][0]
                self.assertEqual(item['data_status'],'unverified')
                self.assertFalse(item['spend_trend']['is_current'])

    def test_tomorrows_daily_period_is_not_current_even_within_small_clock_tolerance(self):
        now=NOW.replace(hour=23,minute=59);row=series()[-1]
        dt=now+timedelta(minutes=1)
        row['source'].update(captured_at=dt.isoformat(),data_at=dt.isoformat(),metric_date=dt.date().isoformat())
        item=analyze_monitoring([row],settings(),now)['entities'][0]
        self.assertEqual(item['data_status'],'unverified');self.assertIn('统计日期在未来',item['data_status_reason'])
        self.assertFalse(item['spend_trend']['is_current'])

    def test_reports_and_anonymous_ai_preserve_historical_reason_and_keep_execution_blocked(self):
        rows,now=self.midnight();cfg=settings();e=evaluate_all(rows,cfg,now)
        report=build_report(rows,cfg,e,'123',now)
        evidence=report['evidence_and_risks']['spend_observations'][0]
        self.assertEqual(evidence['data_status'],'stale');self.assertIn('不是今天',evidence['data_status_reason'])
        self.assertFalse(evidence['trend']['is_current']);self.assertFalse(report['executed'])
        self.assertFalse(any(r['eligible'] for r in e['recommendations']))
        item=make_package(rows,e)['spend_observations'][0]
        self.assertEqual(item['data_status_reason'],evidence['data_status_reason'])
        self.assertFalse(item['trend']['is_current'])


if __name__=='__main__':unittest.main()
