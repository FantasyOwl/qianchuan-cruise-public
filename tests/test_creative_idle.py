import copy
import unittest
from datetime import timedelta

from monitor.evaluation import evaluate_all
from monitor.strategies import extend_evaluation
from test_engine import NOW, settings
from test_strategies import idle_rows


def pauses(rows,*,full=True,cfg=None):
    result=evaluate_all(rows,cfg or settings(),NOW) if full else extend_evaluation(rows,cfg or settings(),{},NOW)
    return [r for r in result['recommendations'] if r['action']=='pause_creative']


class CreativeIdleTests(unittest.TestCase):
    def test_complete_daily_ledger_produces_exact_local_pause_with_parent_scope(self):
        r=pauses(idle_rows())[0]
        self.assertTrue(r['eligible']);self.assertIs(r['before'],True);self.assertIs(r['after'],False)
        self.assertEqual(r['parameters']['parent_plan_id'],'parent1')
        self.assertGreaterEqual(r['evidence']['observed_seconds'],86400)
        self.assertFalse(r['evidence']['platform_verified'])

    def test_activity_before_window_is_not_mistaken_for_activity_during_window(self):
        r=pauses(idle_rows(prior_activity=True))[0]
        self.assertTrue(r['eligible']);self.assertEqual(r['evidence']['spend_delta'],0)
        self.assertEqual(r['evidence']['clicks_delta'],0)

    def test_cross_midnight_zeros_without_explicit_close_open_pair_are_insufficient(self):
        self.assertFalse(pauses(idle_rows(boundaries=False)))

    def test_23_hours_55_minutes_is_not_24_hours(self):
        rows=idle_rows();first=rows[0]
        from monitor.schema import parse_time
        t=parse_time(first['source']['data_at'])+timedelta(minutes=5)
        first['source'].update(data_at=t.isoformat(),captured_at=t.isoformat())
        self.assertFalse(pauses(rows))

    def test_newest_capture_missing_or_regressed_clock_cannot_select_older_favorable_row(self):
        for mode in ('missing','regressed','conflicting_capture'):
            with self.subTest(mode=mode):
                rows=idle_rows();bad=copy.deepcopy(rows[-2]);bad['id']='latest-bad'
                bad['source'].update(metric_date=NOW.date().isoformat(),captured_at=(NOW+timedelta(seconds=1)).isoformat(),
                    data_at=None if mode=='missing' else (NOW-timedelta(minutes=10)).isoformat())
                if mode=='conflicting_capture':bad['source']['captured_at']=NOW.isoformat()
                rows.append(bad)
                self.assertFalse(pauses(rows));self.assertFalse(pauses(rows,full=False))

    def test_same_capture_conflicting_governance_is_not_resolved_by_input_order(self):
        rows=idle_rows();last=next(r for r in rows if r['source']['data_at']==NOW.isoformat())
        bad=copy.deepcopy(last);bad['id']='same-capture-conflict';bad['control']['guarantee_verified']=False
        for values in (rows+[bad],[bad]+rows):
            self.assertFalse(any(r['eligible'] for r in pauses(values)))

    def test_parent_or_creative_type_or_creation_identity_change_cannot_stitch_24_hours(self):
        for mode in ('parent','parent_unhashable','type','created'):
            rows=idle_rows()
            if mode=='parent':rows[15]['context']['parent_plan_id']='other-parent'
            if mode=='parent_unhashable':rows[15]['context']['parent_plan_id']=[]
            if mode=='type':rows[15]['context']['creative_type']='programmatic'
            if mode=='created':rows[15]['context']['created_at']=(NOW-timedelta(days=3)).isoformat()
            with self.subTest(mode=mode):self.assertFalse(pauses(rows))

    def test_any_window_activity_missing_counters_or_gap_blocks_zero_claim(self):
        for mode in ('spend','tiny_regression','click','missing','gap','fractional_click'):
            rows=idle_rows()
            if mode=='spend':rows[100]['metrics']['spend']=.01
            if mode=='tiny_regression':rows[100]['metrics']['spend']=.0000000001
            if mode=='click':rows[100]['metrics']['clicks']=1
            if mode=='missing':rows[100]['metrics'].pop('clicks')
            if mode=='gap':rows=rows[:50]+rows[55:]
            if mode=='fractional_click':rows[100]['metrics']['clicks']=.5
            with self.subTest(mode=mode):self.assertFalse(pauses(rows))

    def test_positive_closing_counter_is_not_erased_by_midnight_reset(self):
        rows=idle_rows();close=next(r for r in rows if r['id']=='prior-day-close')
        close['metrics']['spend']=1
        self.assertFalse(pauses(rows))

    def test_primary_role_does_not_turn_creative_pause_into_plan_shutdown(self):
        rows=idle_rows()
        for r in rows:r['context']['is_primary_plan']=True
        for r in pauses(rows):
            self.assertEqual(r['scope'],'creative');self.assertEqual(r['target_type'],'creative_status')

    def test_no_target_roi_still_allows_only_nonfinancial_idle_assessment(self):
        cfg=settings();cfg['accounts'][0]['target_roi']=None
        self.assertTrue(pauses(idle_rows(),cfg=cfg)[0]['eligible'])

    def test_exact_source_clock_identity_and_material_limits_reach_report_and_ai(self):
        from monitor.reporting import build_report,report_markdown
        from monitor.ai import make_package
        rows=idle_rows();private='private-parent-material-7842'
        for row in rows:row['context']['parent_plan_id']=private
        result=evaluate_all(rows,settings(),NOW);rec=next(r for r in result['recommendations'] if r['action']=='pause_creative')
        health=next(x for x in result['collection_health']['sources'] if x['account_id']=='123' and x['scope']=='creative' and x['metric_date']==NOW.date().isoformat())
        self.assertEqual(rec['source_key'],health['source_key'])
        refs={r['id'] for r in rec['policy_basis']['official_references']}
        self.assertTrue({'material_read','material_add','material_delete'}<=refs)
        report=build_report(rows,settings(),result,'123',NOW);text=report_markdown(report)
        self.assertIn('前后值：开启 → 关闭',text);self.assertIn('删除素材不等同暂停',text)
        self.assertNotIn(private,json_text(make_package(rows,result)))

    def test_pause_preview_cannot_be_reinterpreted_as_material_deletion(self):
        from monitor.actuation import prepare_action
        rows=idle_rows();rec=pauses(rows)[0];last=next(r for r in rows if r['source']['data_at']==NOW.isoformat())
        result=prepare_action(rec,last,settings(),NOW)
        self.assertFalse(result['supported_for_preview']);self.assertFalse(result['live_execution_allowed'])
        self.assertIsNone(result['request']);self.assertEqual(len(result['official_references']),3)
        self.assertIn('不能用删除接口代替暂停','；'.join(result['blockers']))

    def test_account_reports_do_not_mix_other_accounts_creative_ledgers(self):
        from monitor.reporting import build_report
        rows=idle_rows();other=copy.deepcopy(rows);cfg=settings()
        cfg['accounts'].append({**cfg['accounts'][0],'id':'456'})
        for row in other:row['account_id']='456';row['entity_id']='other-private-creative';row['context']['parent_plan_id']='other-private-parent'
        result=evaluate_all(rows+other,cfg,NOW);report=build_report(rows+other,cfg,result,'123',NOW)
        self.assertNotIn('other-private-',json_text(report))


def json_text(value):
    import json
    return json.dumps(value,ensure_ascii=False)


if __name__=='__main__':unittest.main()
