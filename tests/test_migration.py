import copy
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from monitor.evaluation import evaluate_all
from monitor.migration import create_migration, review_migration
from monitor.schema import normalize_context
from monitor.store import Store
from monitor.demo import synthetic_maintenance_history
from test_engine import NOW, series, settings


def fixture():
    cfg = settings()
    source, target = series(roi=1), series(roi=3)
    for row in source:
        row['entity_id'] = 'old'; row['id'] = 's'+row['id']; row['source']['series_key'] = 'old-report'
        row['scene'] = 'product'; row['promotion_type'] = 'chengfang'
        row['control']['lifecycle'] = 'declining'
        row['context'] = {'model_stable': True, 'creative_replacement_failed': True, 'bid_maintenance_reviewed': True}
    for row in target:
        row['entity_id'] = 'new'; row['id'] = 't'+row['id']; row['source']['series_key'] = 'new-report'
        row['scene'] = 'product'; row['promotion_type'] = 'chengfang'
        row['metrics']['budget'] = 5000
        row['control']['budget_original'] = 5000
        row['context'] = {'model_stable': True}
    source,refresh=synthetic_maintenance_history(source)
    rows = source+target
    evaluation = evaluate_all(rows, cfg, NOW,events=[refresh])
    plan = create_migration(source[-1]['id'], target[-1]['id'], rows, cfg, evaluation, NOW)
    return rows, cfg, evaluation, plan


def evaluate_pair(rows,cfg,now,**kwargs):
    # This fixture's refresh is explicit synthetic test data represented as an
    # operator report; production evaluations obtain reports only from imports.
    source=[r for r in rows if r['entity_id']=='old']
    events=list(kwargs.pop('events',[]))
    if source:
        first=min(source,key=lambda r:r['source']['data_at'])
        from datetime import datetime
        before=datetime.fromisoformat(first['source']['data_at']);after=before+timedelta(minutes=10)
        events.insert(0,{'id':'demo-maintenance-old','account_id':'123','entity_id':'old','scope':'plan',
            'action':'refresh_creatives','metric_basis':'overall_payment','source_key':'old-report',
            'source':'manual_verified_report','status':'reported_applied','before_at':before.isoformat(),'after_at':after.isoformat(),
            'before':{'material_ids':['old-material']},'after':{'material_ids':['old-material','new-material']}})
    return evaluate_all(rows,cfg,now,events=events,**kwargs)


def add_report(rows, plan, name, when):
    leg = plan[name]
    before = when-timedelta(minutes=10)
    event = {'id': name+'-event', 'account_id':plan['account_id'], 'entity_id':leg['entity_id'], 'scope':'plan',
             'action':'budget_down' if name=='source' else 'budget_up', 'metric_basis':plan['metric_basis'],
             'source_key':leg['series_key'], 'source':'manual_verified_report', 'status':'reported_applied',
             'before_at':before.isoformat(), 'after_at':when.isoformat(),
             'before':{'budget':leg['before']}, 'after':{'budget':leg['after']}}
    last = max((r for r in rows if r['entity_id']==leg['entity_id']),key=lambda r:r['source']['data_at'])
    last_time = NOW if last['source']['data_at']==NOW.isoformat() else __import__('datetime').datetime.fromisoformat(last['source']['data_at'])
    for n in range(1,int((when+timedelta(hours=1)-last_time).total_seconds()/600)+1):
        row=copy.deepcopy(last);at=last_time+timedelta(minutes=10*n)
        row['id']+=f'-post-{n}';row['source'].update(data_at=at.isoformat(),captured_at=at.isoformat())
        if 'creative_ids' in row.get('context',{}):row['context']['creative_inventory_at']=at.isoformat()
        row['metrics'].update(budget=leg['before'] if at<=before else leg['after'],
                              spend=last['metrics']['spend']+n*100,gmv=last['metrics']['gmv']+n*100*(1 if name=='source' else 3),
                              orders=last['metrics']['orders']+n*2,clicks=last['metrics']['clicks']+n*40,impressions=last['metrics']['impressions']+n*1000)
        if at>=when:row['control'].update(last_change_at=when.isoformat(),changes_today=1)
        rows.append(row)
    return event


def extend_unchanged(rows, entity, until):
    last=max((r for r in rows if r['entity_id']==entity), key=lambda r:r['source']['data_at'])
    from datetime import datetime
    at=datetime.fromisoformat(last['source']['data_at'])
    for n in range(1,int((until-at).total_seconds()/600)+1):
        row=copy.deepcopy(last);t=at+timedelta(minutes=10*n);row['id']+=f'-ext-{n}'
        row['source'].update(data_at=t.isoformat(),captured_at=t.isoformat())
        if 'creative_ids' in row.get('context',{}):row['context']['creative_inventory_at']=t.isoformat()
        row['metrics'].update(spend=last['metrics']['spend']+n*100,gmv=last['metrics']['gmv']+n*300,
                              orders=last['metrics']['orders']+n*2,clicks=last['metrics']['clicks']+n*40,impressions=last['metrics']['impressions']+n*1000)
        rows.append(row)


class MigrationTests(unittest.TestCase):
    def test_budget_neutral_pair_and_first_step_only(self):
        rows,cfg,e,p=fixture()
        self.assertEqual(p['state'],'ready_reduce',p)
        self.assertEqual((p['source']['after'],p['target']['after'],p['amount']),(9000,6000,1000))
        self.assertEqual(p['total_before'],p['total_after'])
        r=review_migration(p,rows,cfg,e,[],NOW)
        self.assertEqual(r['next_step']['action'],'budget_down');self.assertFalse(r['execution_eligible'])

    def test_empty_target_and_first_two_maintenance_priorities_block(self):
        for mutate in (lambda rows,cfg:cfg['accounts'][0].update(target_roi=None),
                       lambda rows,cfg:rows[18]['context'].update(creative_replacement_failed=False),
                       lambda rows,cfg:rows[18]['context'].update(bid_maintenance_reviewed=False)):
            rows,cfg,e,p=fixture();mutate(rows,cfg)
            p=create_migration('s18','t18',rows,cfg,evaluate_pair(rows,cfg,NOW),NOW)
            self.assertEqual(p['state'],'blocked')

    def test_other_account_or_product_or_same_plan_never_transfers(self):
        rows,cfg,e,p=fixture()
        for key,val in [('scene','live'),('promotion_type','standard'),('metric_basis','marketing')]:
            changed=copy.deepcopy(rows);changed[-1][key]=val
            self.assertEqual(create_migration('s18','t18',changed,cfg,e,NOW)['state'],'blocked')
        self.assertEqual(create_migration('s18','s18',rows,cfg,e,NOW)['state'],'blocked')

    def test_live_universal_aweme_exclusion(self):
        rows,cfg,e,p=fixture()
        for r in rows:r.update(scene='live',promotion_type='universal');r['context']['aweme_id']='same'
        p=create_migration('s18','t18',rows,cfg,evaluate_pair(rows,cfg,NOW),NOW)
        self.assertTrue(any('抖音号' in b for b in p['blockers']))

    def test_small_receiver_does_not_force_below_ten_percent_donor_step(self):
        rows,cfg,e,p=fixture()
        for r in rows:
            if r['entity_id']=='new':r['metrics']['budget']=1000;r['control']['budget_original']=1000
        p=create_migration('s18','t18',rows,cfg,evaluate_pair(rows,cfg,NOW),NOW)
        self.assertEqual(p['state'],'blocked')

    def test_budget_change_without_report_is_held(self):
        rows,cfg,e,p=fixture();rows[18]['metrics']['budget']=9000
        r=review_migration(p,rows,cfg,e,[],NOW)
        self.assertEqual(r['state'],'held');self.assertIsNone(r['next_step'])

    def test_cross_day_expires_and_cancel_is_only_local(self):
        rows,cfg,e,p=fixture()
        self.assertEqual(review_migration(p,rows,cfg,e,[],NOW+timedelta(days=1))['state'],'expired')
        p['cancelled']=True
        self.assertEqual(review_migration(p,rows,cfg,e,[],NOW)['state'],'cancelled')

    def test_source_report_needs_complete_hour_then_allows_only_target_step(self):
        rows,cfg,e,p=fixture();at=NOW+timedelta(minutes=10)
        event=add_report(rows,p,'source',at);end=at+timedelta(hours=1)
        extend_unchanged(rows,'new',end)
        evaluation=evaluate_pair(rows,cfg,end)
        r=review_migration(p,rows,cfg,evaluation,[event],end)
        self.assertEqual(r['state'],'ready_raise',r)
        self.assertEqual(r['next_step']['action'],'budget_up');self.assertFalse(r['next_step']['execution_eligible'])
        partial=[r for r in rows if r['source']['data_at']<=(at+timedelta(minutes=20)).isoformat()]
        r=review_migration(p,partial,cfg,evaluate_pair(partial,cfg,at+timedelta(minutes=20)),[event],at+timedelta(minutes=20))
        self.assertEqual(r['state'],'pending_source_review')

    def test_early_receiver_and_ambiguous_reports_hold(self):
        rows,cfg,e,p=fixture();at=NOW+timedelta(minutes=10)
        source=add_report(rows,p,'source',at);target=add_report(rows,p,'target',at)
        end=at+timedelta(hours=1)
        r=review_migration(p,rows,cfg,evaluate_pair(rows,cfg,end),[source,target],end)
        self.assertEqual(r['state'],'held');self.assertTrue(any('顺序' in b for b in r['blockers']))
        target=copy.deepcopy(source);target['id']='duplicate'
        self.assertEqual(review_migration(p,rows,cfg,e,[source,target],end)['state'],'held')

    def test_two_reported_stages_and_observations_are_never_platform_certification(self):
        rows,cfg,e,p=fixture();at=NOW+timedelta(minutes=10)
        first=add_report(rows,p,'source',at)
        second=add_report(rows,p,'target',at+timedelta(hours=1,minutes=10))
        end=at+timedelta(hours=2,minutes=10);extend_unchanged(rows,'old',end)
        # Extending donor uses its observed ROI, not recipient's ROI.
        for r in rows:
            if r['entity_id']=='old' and '-ext-' in r['id']:r['metrics']['gmv']=r['metrics']['spend']
        r=review_migration(p,rows,cfg,evaluate_pair(rows,cfg,end),[first,second],end)
        self.assertEqual(r['state'],'completed_reported',r)
        self.assertFalse(r['executed']);self.assertFalse(r['execution_eligible'])

    def test_active_migration_reserves_both_plan_budgets(self):
        rows,cfg,e,p=fixture();e=evaluate_pair(rows,cfg,NOW,migrations=[p])
        self.assertEqual(e['budget_migrations'][0]['state'],'ready_reduce')
        self.assertFalse(any(r['eligible'] and r['action'] in {'budget_up','budget_down'} for r in e['recommendations']))

    def test_account_risk_veto_also_blocks_base_engine_growth(self):
        rows,cfg,e,p=fixture();account=copy.deepcopy(rows[-1]);account.update(id='account',entity_id='123',scope='account')
        account['context']={'account_blocked':True};account['metrics']['balance']=0
        rows.append(account);e=evaluate_pair(rows,cfg,NOW)
        self.assertTrue(any(a['priority']=='P0' for a in e['alerts']))
        self.assertFalse(any(r['eligible'] and r['action']=='budget_up' for r in e['recommendations']))

    def test_browser_does_not_certify_maintenance_review(self):
        self.assertIsNone(normalize_context({'bid_maintenance_reviewed':True},'browser_dom')['bid_maintenance_reviewed'])

    def test_local_persistence_and_cancel_survive_restart(self):
        rows,cfg,e,p=fixture()
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root)/'monitor.db');store.add_migration(p)
            self.assertEqual(store.migrations()[0]['id'],p['id'])
            store.cancel_migration(p['id'])
            self.assertTrue(Store(Path(root)/'monitor.db').migrations()[0]['cancelled'])

    def test_archived_review_stays_historical_without_reopening_after_stale_data(self):
        rows,cfg,e,p=fixture()
        final={**p,'state':'completed_reported','next_step':None,'executed':False}
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root)/'monitor.db');store.add_migration(p);store.finalize_migration(final)
            frozen=store.migrations()[0]
            r=review_migration(frozen,[],cfg,e,[],NOW+timedelta(days=1))
            self.assertEqual(r['state'],'completed_reported');self.assertTrue(r['historical'])
            self.assertFalse(r['execution_eligible'])

    def test_non_latest_or_conflicting_snapshot_is_rejected(self):
        rows,cfg,e,p=fixture()
        with self.assertRaises(ValueError):create_migration('s17','t18',rows,cfg,e,NOW)
        other=copy.deepcopy(rows[18]);other['id']='conflict';other['metrics']['spend']+=1;rows.append(other)
        with self.assertRaises(ValueError):create_migration('s18','t18',rows,cfg,e,NOW)

    def test_real_and_synthetic_or_changed_source_types_cannot_mix(self):
        rows,cfg,e,p=fixture();rows[-1]['source']['kind']='demo'
        self.assertEqual(create_migration('s18','t18',rows,cfg,e,NOW)['state'],'blocked')
        rows,cfg,e,p=fixture()
        for row in rows:row['source']['kind']='demo'
        self.assertEqual(review_migration(p,rows,cfg,e,[],NOW)['state'],'held')

    def test_sharp_donor_spend_drop_prevents_handover(self):
        rows,cfg,e,p=fixture();at=NOW+timedelta(minutes=10)
        event=add_report(rows,p,'source',at);end=at+timedelta(hours=1);extend_unchanged(rows,'new',end)
        base=next(r for r in rows if r['entity_id']=='old' and r['source']['data_at']==at.isoformat())['metrics']['spend']
        for row in rows:
            if row['entity_id']=='old' and row['source']['data_at']>at.isoformat():
                row['metrics']['spend']=base+(row['metrics']['spend']-base)*.2
                row['metrics']['gmv']=row['metrics']['spend']
        r=review_migration(p,rows,cfg,evaluate_pair(rows,cfg,end),[event],end)
        self.assertEqual(r['state'],'held');self.assertTrue(any('断层' in b for b in r['blockers']))

    def test_replay_complete_handover_is_synthetic_only(self):
        from monitor.demo import migration_scenario
        rows,cfg,now,events,plans=migration_scenario(settings())
        e=evaluate_pair(rows,cfg,now,events=events,migrations=plans)
        self.assertEqual(e['budget_migrations'][0]['state'],'completed_reported')
        self.assertEqual(e['outcome_review']['summary']['manual_reported_applied'],0)
        self.assertEqual(e['outcome_review']['summary']['simulated'],3)


if __name__=='__main__':unittest.main()
