import copy
import json
import unittest
from datetime import datetime,timedelta,timezone

from monitor.chase import evaluate_chase

NOW=datetime(2026,10,22,18,tzinfo=timezone(timedelta(hours=8)))


def fixture(parent_roi=3,account_roi=3):
    cfg={'accounts':[{'id':'123','scene':'live','roi_basis':'overall_payment','target_roi':2}],'rules':{}}
    start=NOW-timedelta(hours=3);rows=[]
    totals={'parent_spend':2000,'parent_gmv':6000,'parent_impressions':10000,'parent_orders':100,
            'account_spend':10000,'account_gmv':30000,'account_control':500,'task_spend':0,'task_gmv':0,'task_orders':0}
    context={'control_type':'material_chase','target_metric_basis':'overall_payment','parent_plan_id':'parent1','material_id':'mat1',
             'parent_source_key':'parent-report','account_source_key':'account-report','control_started_at':start.isoformat(),
             'last_chase_id':'task1','last_chase_at':start.isoformat(),'chase_history_known':True,'manual_reviewed':True,
             'model_stable':True,'compliance_ok':True,'latest_budget_verified':True,'parent_control_inventory_verified':True}
    for n in range(25):
        at=NOW-timedelta(hours=4)+timedelta(minutes=n*10)
        if n:
            totals['parent_spend']+=80;totals['parent_gmv']+=80*(parent_roi if at>start else 3)
            totals['parent_impressions']+=400 if parent_roi<2 and at>start else 1000;totals['parent_orders']+=3
            totals['account_spend']+=100;totals['account_gmv']+=100*(account_roi if at>start else 3)
            if at>start:
                totals['task_spend']+=10;totals['task_gmv']+=30;totals['task_orders']+=2;totals['account_control']+=10
        source={'kind':'import','captured_at':at.isoformat(),'data_at':at.isoformat(),'metric_date':at.date().isoformat(),'metric_period':'day','coverage':'report'}
        common={'account_id':'123','scene':'live','promotion_type':'universal','quality':{'issues':[]},
                'control':{'history_known':True,'guarantee_verified':True,'running':True,'changes_today':0,'lifecycle':'mature'}}
        parent={**copy.deepcopy(common),'id':f'p-{n}','entity_id':'parent1','scope':'plan','metric_basis':'overall_payment',
                'source':{**source,'series_key':'parent-report'},'metrics':{'budget':5000,'base_spend':totals['parent_spend'],
                'base_gmv':totals['parent_gmv'],'base_orders':totals['parent_orders'],'base_impressions':totals['parent_impressions'],
                'base_clicks':totals['parent_orders']*5,'spend':totals['parent_spend']+totals['task_spend'],
                'gmv':totals['parent_gmv']+totals['task_gmv'],'orders':totals['parent_orders']+totals['task_orders']}}
        account={**copy.deepcopy(common),'id':f'a-{n}','entity_id':'123','scope':'account','metric_basis':'overall_payment',
                 'source':{**source,'series_key':'account-report'},'metrics':{'spend':totals['account_spend'],'gmv':totals['account_gmv'],
                 'orders':100+n*3,'control_spend':totals['account_control'],'roi':99}}
        rows.extend((parent,account))
        if at>=start:
            rows.append({**copy.deepcopy(common),'id':f'c-{n}','entity_id':'task1','scope':'control','metric_basis':'control_payment',
                'source':{**source,'series_key':'control-report'},'context':copy.deepcopy(context),
                'metrics':{'control_spend':totals['task_spend'],'control_gmv':totals['task_gmv'],'orders':totals['task_orders'],
                           'budget':400,'parent_chase_budget_total':400}})
    return rows,cfg


def run(rows,cfg):return evaluate_chase(rows,cfg,NOW)
def controls(rows):return [r for r in rows if r['scope']=='control']
def recs(result,rule):return [r for r in result['recommendations'] if r['rule']==rule]


class ChaseReviewTests(unittest.TestCase):
    def test_verified_two_hour_test_adds_quarter_with_all_caps_and_no_execution(self):
        rows,cfg=fixture();r=recs(run(rows,cfg),'chase_budget_topup')[0]
        self.assertTrue(r['eligible']);self.assertEqual((r['before'],r['after'],r['change_pct']),(400,500,.25))
        self.assertEqual(r['mode'],'simulation_only');self.assertLessEqual(r['change_pct'],.5)
        self.assertFalse(run(rows,cfg)['coverage']['execution_enabled'])

    def test_null_target_never_inherits_platform_target_or_implicit_basis(self):
        rows,cfg=fixture()
        for r in controls(rows):r['metrics']['target_roi']=4
        cfg['accounts'][0]['target_roi']=None
        self.assertEqual(recs(run(rows,cfg),'chase_budget_topup'),[])
        cfg['accounts'][0]['target_roi']=2
        for r in controls(rows):r['context'].pop('target_metric_basis')
        self.assertEqual(recs(run(rows,cfg),'chase_budget_topup'),[])

    def test_parent_budget_total_and_projected_account_share_block_growth(self):
        mutations=(lambda rs:controls(rs)[-1]['metrics'].update(parent_chase_budget_total=1490),
                   lambda rs:controls(rs)[-1]['metrics'].update(parent_chase_budget_total=300),
                   lambda rs:next(r for r in reversed(rs) if r['scope']=='account')['metrics'].update(control_spend=2440))
        for mutate in mutations:
            rows,cfg=fixture();mutate(rows)
            self.assertFalse(recs(run(rows,cfg),'chase_budget_topup'))

    def test_exact_parent_identity_source_and_account_must_match(self):
        for variant in ('parent_id','source','account'):
            rows,cfg=fixture()
            for r in rows:
                if r['scope']=='plan' and variant=='parent_id':
                    r['entity_id']='wrong';r['context']={'plan_id':'parent1'}
                if r['scope']=='plan' and variant=='source':r['source']['series_key']='other'
                if r['scope']=='account' and variant=='account':r['account_id']='other'
            self.assertFalse(recs(run(rows,cfg),'chase_budget_topup'))

    def test_missing_samples_and_changed_relation_do_not_complete_the_test(self):
        rows,cfg=fixture();rows=[r for r in rows if not(r['scope']=='control' and 'T16:10'<=r['source']['data_at'][10:16]<='T16:30')]
        self.assertFalse(recs(run(rows,cfg),'chase_budget_topup'))
        rows,cfg=fixture();controls(rows)[0]['context']['material_id']='other'
        self.assertFalse(recs(run(rows,cfg),'chase_budget_topup'))

    def test_cooldown_unknown_guarantee_and_compliance_are_not_ready(self):
        for mutate in (lambda r:r['control'].update(last_change_at=(NOW-timedelta(minutes=10)).isoformat()),
                       lambda r:r['control'].update(guarantee_verified=False),lambda r:r['context'].update(compliance_ok=None)):
            rows,cfg=fixture();mutate(controls(rows)[-1]);rs=recs(run(rows,cfg),'chase_budget_topup')
            self.assertFalse(any(r['eligible'] for r in rs))

    def test_current_task_started_within_24h_is_not_a_new_rechase(self):
        rows,cfg=fixture();result=run(rows,cfg)
        self.assertFalse(any(a['rule']=='material_rechase_24h' for a in result['alerts']))
        self.assertTrue(recs(result,'chase_budget_topup')[0]['eligible'])

    def test_other_active_same_material_and_historical_pause_trigger_protection(self):
        for historical in (False,True):
            rows,cfg=fixture();other=copy.deepcopy(controls(rows)[-1]);other.update(id='other',entity_id='task-old')
            if historical:
                pause=NOW-timedelta(hours=26)  # 23h before this task started.
                other['source'].update(captured_at=pause.isoformat(),data_at=pause.isoformat(),metric_date=pause.date().isoformat())
                other['context'].update(last_pause_at=pause.isoformat(),last_pause_control_id='task-old',control_started_at=(pause-timedelta(hours=2)).isoformat())
                other['context'].update(last_chase_id='task-old',last_chase_at=(pause-timedelta(hours=2)).isoformat())
                other['control']['running']=False
            rows.append(other);result=run(rows,cfg)
            self.assertTrue(any(a['rule']=='material_rechase_24h' for a in result['alerts']))
            self.assertFalse(any(r['eligible'] for r in recs(result,'chase_budget_topup')))

    def test_parent_decline_is_suspected_until_causal_review_is_linked(self):
        rows,cfg=fixture(parent_roi=1);result=run(rows,cfg)
        self.assertTrue(any(a['rule']=='suspected_parent_crowding' for a in result['alerts']))
        self.assertFalse(recs(result,'suspected_parent_crowding')[0]['eligible'])
        self.assertFalse(recs(result,'chase_budget_topup'))
        controls(rows)[-1]['context'].update(crowding_causality_verified=True,causal_review_at=NOW.isoformat(),causal_review_control_id='task1')
        self.assertTrue(recs(run(rows,cfg),'suspected_parent_crowding')[0]['eligible'])

    def test_wrong_or_stale_causal_review_cannot_authorize_pause(self):
        for update in ({'causal_review_control_id':'wrong'}, {'causal_review_at':(NOW-timedelta(hours=1)).isoformat()}):
            rows,cfg=fixture(parent_roi=1)
            controls(rows)[-1]['context'].update(crowding_causality_verified=True,causal_review_at=NOW.isoformat(),causal_review_control_id='task1')
            controls(rows)[-1]['context'].update(update)
            self.assertFalse(recs(run(rows,cfg),'suspected_parent_crowding')[0]['eligible'])

    def test_account_protection_uses_hour_difference_even_if_day_roi_label_is_high(self):
        rows,cfg=fixture(account_roi=1);result=run(rows,cfg)
        r=recs(result,'suspected_account_roi_impact')[0]
        self.assertEqual(r['evidence']['during_account_hour']['roi'],1)
        self.assertFalse(r['eligible']);self.assertFalse(recs(result,'chase_budget_topup'))
        controls(rows)[-1]['context'].update(account_roi_causality_verified=True,causal_review_at=NOW.isoformat(),causal_review_control_id='task1')
        self.assertTrue(recs(run(rows,cfg),'suspected_account_roi_impact')[0]['eligible'])

    def test_unknown_control_type_is_not_inferred_from_task_name(self):
        rows,cfg=fixture()
        for r in controls(rows):r['context']['control_type']='unknown';r['display_name']='素材追投'
        result=run(rows,cfg);self.assertEqual(result['recommendations'],[])
        json.dumps(result,allow_nan=False)
        invalid={'action':'pause_control','scope':'control','source':'manual_verified_report','status':'reported_applied',
                 'before_at':(NOW-timedelta(minutes=10)).isoformat(),'after_at':NOW.isoformat(),'before':None,'after':{}}
        json.dumps(evaluate_chase(rows,cfg,NOW,events=[invalid,None]),allow_nan=False)

    def test_locally_crosschecked_pause_report_feeds_24_hour_rechase_guard(self):
        rows,cfg=fixture();old=copy.deepcopy(controls(rows)[0])
        before=NOW-timedelta(hours=3,minutes=20);after=before+timedelta(minutes=10)
        old.update(id='old-before',entity_id='old-control')
        old['source'].update(series_key='old-source',captured_at=before.isoformat(),data_at=before.isoformat())
        stopped=copy.deepcopy(old);stopped['id']='old-after';stopped['control']['running']=False
        stopped['source'].update(captured_at=after.isoformat(),data_at=after.isoformat())
        rows.extend([old,stopped])
        self.assertTrue(recs(run(rows,cfg),'chase_budget_topup')[0]['eligible'])
        event={'id':'pause-report','account_id':'123','entity_id':'old-control','scope':'control','action':'pause_control',
               'metric_basis':'control_payment','source_key':'old-source','source':'manual_verified_report','status':'reported_applied',
               'before_at':before.isoformat(),'after_at':after.isoformat(),'before':{'running':True},'after':{'running':False}}
        result=evaluate_chase(rows,cfg,NOW,events=[event])
        self.assertFalse(recs(result,'chase_budget_topup'))
        self.assertTrue(any(a['rule']=='material_rechase_24h' for a in result['alerts']))


if __name__=='__main__':unittest.main()
