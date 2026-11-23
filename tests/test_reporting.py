import copy
import json
import unittest
from datetime import timedelta

from monitor.demo import outcome_scenario, migration_scenario
from monitor.evaluation import evaluate_all
from monitor.references import provenance
from monitor.reporting import build_report, report_markdown
from test_engine import NOW, settings, series


def fixture():
    rows,cfg=series(roi=3),settings()
    result=evaluate_all(rows,cfg,NOW)
    return rows,cfg,result,build_report(rows,cfg,result,'123',NOW)


class ReportingTests(unittest.TestCase):
    def test_three_sections_include_concrete_action_source_goal_and_risks(self):
        rows,cfg,e,r=fixture();action=r['prioritized_actions'][0]
        self.assertEqual(action['action']['after'],12000)
        self.assertEqual(action['source_snapshot_ids'],['18']);self.assertEqual(action['source_resolution'],'exact')
        self.assertTrue(action['expected_goal']);self.assertTrue(action['risks']);self.assertFalse(action['execution_eligible'])
        md=report_markdown(r)
        self.assertLess(md.index('## 核心结论'),md.index('## 分级行动指令'))
        self.assertLess(md.index('## 分级行动指令'),md.index('## 数据佐证与风险提示'))
        self.assertIn('20%',md);self.assertIn('12000',md)

    def test_other_account_metrics_names_and_actions_are_excluded(self):
        rows,cfg,e,r=fixture();other=copy.deepcopy(rows)
        cfg['accounts'].append({'id':'456','name':'PRIVATE_OTHER_ACCOUNT','roi_basis':'overall_payment','target_roi':2})
        for row in other:
            row.update(account_id='456',entity_id='PRIVATE_OTHER_PLAN',display_name='PRIVATE_OTHER_NAME',id='other-'+row['id'])
            row['source']['series_key']='PRIVATE_OTHER_SERIES'
        rows+=other;e=evaluate_all(rows,cfg,NOW);r=build_report(rows,cfg,e,'123',NOW)
        text=json.dumps(r,ensure_ascii=False)
        self.assertNotIn('PRIVATE_OTHER',text);self.assertEqual(r['evidence_summary']['source_count'],1)

    def test_source_series_and_metric_basis_remain_separate(self):
        rows,cfg,e,r=fixture();other=copy.deepcopy(rows)
        for row in other:
            row.update(id='m-'+row['id'],metric_basis='marketing');row['source']['series_key']='marketing-report'
        rows+=other;r=build_report(rows,cfg,evaluate_all(rows,cfg,NOW),'123',NOW)
        self.assertEqual({s['metric_basis'] for s in r['evidence_and_risks']['sources']},{'overall_payment','marketing'})
        self.assertTrue(all(a['object']['metric_basis']=='overall_payment' for a in r['prioritized_actions']))

    def test_null_user_target_does_not_inherit_plan_target(self):
        rows,cfg,e,r=fixture();cfg['accounts'][0]['target_roi']=None
        for row in rows:row['metrics']['target_roi']=2
        r=build_report(rows,cfg,evaluate_all(rows,cfg,NOW),'123',NOW)
        self.assertIsNone(r['account']['target_roi']);self.assertFalse(r['prioritized_actions'])
        self.assertTrue(any('目标仍为空' in s for s in r['core_conclusions']))

    def test_empty_or_stale_evidence_does_not_claim_healthy_account(self):
        cfg=settings();r=build_report([],cfg,evaluate_all([],cfg,NOW),'123',NOW)
        self.assertEqual(r['data_mode'],'empty');self.assertIn('不表示账户健康',report_markdown(r))
        rows=series(now=NOW-timedelta(hours=2));r=build_report(rows,cfg,evaluate_all(rows,cfg,NOW),'123',NOW)
        self.assertEqual(r['evidence_summary']['source_states']['stale'],1)
        self.assertIn('不能由没有投放告警判定账户健康',''.join(r['core_conclusions']))

    def test_unconfigured_account_is_rejected(self):
        with self.assertRaises(ValueError):build_report([],settings(),{},'999',NOW)

    def test_user_thresholds_and_incomplete_guarantee_are_not_official_promises(self):
        rows,cfg,e,r=fixture();basis=r['prioritized_actions'][0]['policy_basis']
        self.assertEqual(basis['strategy']['authority'],'user_strategy')
        refs={v['id']:v for v in basis['official_references']}
        self.assertIn('未核验完整原文',refs['universal_guarantee']['verification'])
        self.assertIn('contract_budget',refs);self.assertEqual(refs['contract_budget']['checked_at'],'2026-10-22')
        self.assertTrue(all('不证明' in v['application'] for v in refs.values()))

    def test_boost_duration_goal_parent_and_risk_survive_report(self):
        rows,cfg,e,r=fixture();rec=copy.deepcopy(e['recommendations'][0])
        rec.update(action='one_click_boost',target_id='boost-task',target_type='boost_task',
                   parameters={'budget':100,'duration_hours':1,'optimization_goal':'live_purchase','parent_plan_id':'parent'})
        rec['policy_basis']=provenance(rec,{'scope':'plan','scene':'live','promotion_type':'universal'})
        e['recommendations']=[rec];r=build_report(rows,cfg,e,'123',NOW);action=r['prioritized_actions'][0]
        self.assertEqual(action['action']['parameters']['duration_hours'],1)
        self.assertEqual(action['object']['target_id'],'boost-task')
        self.assertTrue(any('盈亏自负' in s for s in action['risks']))
        self.assertTrue(any(ref['id']=='live_targeting' for ref in action['policy_basis']['official_references']))

    def test_report_does_not_export_configuration_keys_pairing_or_raw_snapshot_payload(self):
        rows,cfg,e,r=fixture();cfg.update(ai={'api_key':'PRIVATE_AI_KEY','endpoint':'PRIVATE_ENDPOINT'},pairing_token='PRIVATE_PAIRING')
        rows[-1].update(raw_html='PRIVATE_HTML',cookie='PRIVATE_COOKIE')
        r=build_report(rows,cfg,e,'123',NOW);text=json.dumps(r)
        self.assertNotIn('PRIVATE_',text)

    def test_markdown_escapes_names_and_uses_unbreakable_json_fences(self):
        rows,cfg,e,r=fixture();cfg['accounts'][0]['name']='[click](https://evil.invalid)<script>bad</script>'
        e['recommendations'][0]['evidence']['text']='```\n<script>do_bad()</script>\n```'
        r=build_report(rows,cfg,e,'123',NOW);text=report_markdown(r)
        self.assertNotIn('账户：[click]',text);self.assertIn('&lt;script&gt;',text)
        self.assertIn('````json',text)

    def test_outcome_restore_candidates_stay_in_observations_without_action_eligibility(self):
        rows,cfg,now,events=outcome_scenario(settings());e=evaluate_all(rows,cfg,now,events=events)
        r=build_report(rows,cfg,e,'123',now)
        self.assertTrue(r['evidence_and_risks']['outcome_reviews'][0]['candidates'])
        self.assertFalse(any(a['action']['type']=='bid_restore' for a in r['prioritized_actions']))
        self.assertTrue(r['synthetic']);self.assertFalse(r['executed'])

    def test_complete_synthetic_migration_keeps_its_evidence_label(self):
        rows,cfg,now,events,plans=migration_scenario(settings());e=evaluate_all(rows,cfg,now,events=events,migrations=plans)
        r=build_report(rows,cfg,e,'123',now)
        flow=r['evidence_and_risks']['budget_migrations'][0]
        self.assertEqual(flow['state'],'completed_reported');self.assertEqual(flow['evidence_kind'],'synthetic')
        self.assertEqual(r['evidence_summary']['real_executions_by_this_program'],0)

    def test_report_does_not_mutate_rules_or_observation_evidence(self):
        rows,cfg,e,r=fixture();before=copy.deepcopy((rows,cfg,e));build_report(rows,cfg,e,'123',NOW)
        self.assertEqual(before,(rows,cfg,e))

    def test_priority_order_is_independent_of_insertion_order(self):
        rows,cfg,e,r=fixture();rec=e['recommendations'][0]
        e['recommendations']=[dict(rec,id='p2',priority='P2'),dict(rec,id='p0',priority='P0'),dict(rec,id='p1',priority='P1')]
        r=build_report(rows,cfg,e,'123',NOW)
        self.assertEqual([a['priority'] for a in r['prioritized_actions']],['P0','P1','P2'])

    def test_mixed_evidence_is_not_described_as_only_real_observations(self):
        rows,cfg,e,r=fixture();other=copy.deepcopy(rows)
        for row in other:row['id']='demo-'+row['id'];row['source'].update(kind='demo',series_key='synthetic')
        rows+=other;r=build_report(rows,cfg,evaluate_all(rows,cfg,NOW),'123',NOW)
        self.assertEqual(r['data_mode'],'mixed_evidence');self.assertIn('合成与观察混合证据',report_markdown(r))

    def test_cross_account_exclusion_conflict_is_relevant_but_foreign_ids_are_redacted(self):
        rows,cfg,e,r=fixture();other=copy.deepcopy(rows)
        cfg['accounts'].append({'id':'456','roi_basis':'overall_payment','target_roi':2})
        for row in rows:row['context']={'aweme_id':'shared-aweme','plan_marketing_goal':'LIVE_PROM_GOODS'}
        for row in other:
            row.update(account_id='456',entity_id='FOREIGN_PLAN',id='other-'+row['id'])
            row['context']={'aweme_id':'shared-aweme','plan_marketing_goal':'LIVE_PROM_GOODS'}
        rows+=other;e=evaluate_all(rows,cfg,NOW);r=build_report(rows,cfg,e,'123',NOW)
        alert=next(a for a in r['evidence_and_risks']['alerts'] if a['rule']=='aweme_universal_conflict')
        self.assertEqual(alert['evidence']['other_account_conflicting_plan_count'],1)
        self.assertNotIn('FOREIGN_PLAN',json.dumps(r))
        self.assertFalse(any(a['offline_eligible'] and a['action']['type']=='budget_up' for a in r['prioritized_actions']))
        other_report=build_report(rows,cfg,e,'456',NOW)
        self.assertTrue(any(a['rule']=='aweme_universal_conflict' for a in other_report['evidence_and_risks']['alerts']))
        self.assertFalse(any(a['eligible'] and a['action']=='budget_up' for a in e['recommendations']))


if __name__=='__main__':unittest.main()
