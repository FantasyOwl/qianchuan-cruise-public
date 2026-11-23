import copy
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from monitor.demo import roi_recovery_scenario
from monitor.engine import CN, _record, _settings, _time
from monitor.daily_windows import daily_window, inspect_daily_ledger
from monitor.evaluation import evaluate_all
from monitor.roi_recovery import review_roi_recovery
from monitor.schema import normalize_snapshot
from monitor.store import Store
from test_engine import settings, NOW


def overnight(days=1):
    """A real daily reset in synthetic counters, with explicit boundary pairs."""
    rows,cfg,old_now,events=roi_recovery_scenario(settings())
    now=old_now.replace(hour=1,minute=40)+timedelta(days=days)
    shift=now-old_now
    for e in events:
        for key in ('before_at','after_at'):e[key]=(_time(e[key])+shift).isoformat()
    for row in rows:
        for key in ('captured_at','data_at'):row['source'][key]=(_time(row['source'][key])+shift).isoformat()
        at=_time(row['source']['data_at']);row['source']['metric_date']=at.date().isoformat()
        for key in ('created_at','plan_roi_observed_at'):row['context'][key]=(_time(row['context'][key])+shift).isoformat()
        if row['control']['last_change_at']:row['control']['last_change_at']=(_time(row['control']['last_change_at'])+shift).isoformat()
        row['control']['changes_today']=sum(_time(e['after_at']).date()==at.date() and _time(e['after_at'])<=at for e in events)
    midnight=now.replace(hour=0,minute=0,second=0,microsecond=0)
    opening=next(r for r in rows if _time(r['source']['data_at'])==midnight)
    offsets={k:opening['metrics'][k] for k in ('spend','gmv','orders')}
    closing=copy.deepcopy(opening);closing['id']='explicit-daily-close'
    closing['source']['metric_date']=(midnight-timedelta(days=1)).date().isoformat()
    closing['context'].update(day_boundary_role='closing',day_boundary_verified=True)
    for row in rows:
        if _time(row['source']['data_at'])>=midnight:
            for k,v in offsets.items():row['metrics'][k]-=v
    opening['context'].update(day_boundary_role='opening',day_boundary_verified=True)
    rows.append(closing)
    rows.sort(key=lambda r:(_time(r['source']['data_at']),r['source']['metric_date']))
    return rows,cfg,now,events


def review(args):
    rows,cfg,at,events=args
    return review_roi_recovery(rows,cfg,at,events=events)


def extend_daily(args,days=2,complete=False):
    rows,cfg,at,events=args
    if complete:
        event=copy.deepcopy(events[-1]);event.update(id='final-return',before_at=at.isoformat(),
            after_at=(at+timedelta(minutes=10)).isoformat(),before={'target_roi':3.31},after={'target_roi':3.5})
        events.append(event)
    end=at+timedelta(days=days);stamp=at+timedelta(minutes=10);previous=copy.deepcopy(rows[-1]);n=0
    while stamp<=end:
        row=copy.deepcopy(previous);row['id']='extended-'+str(n);n+=1
        row['source'].update(captured_at=stamp.isoformat(),data_at=stamp.isoformat(),metric_date=previous['source']['metric_date'])
        row['context'].pop('day_boundary_role',None);row['context'].pop('day_boundary_verified',None)
        row['context']['plan_roi_observed_at']=stamp.isoformat()
        for k,amount in (('spend',30),('gmv',96),('orders',3)):row['metrics'][k]+=amount
        if complete:
            row['metrics']['target_roi']=3.5;row['control']['last_change_at']=events[-1]['after_at']
        row['control']['changes_today']=sum(_time(e['after_at']).date()==stamp.date() and _time(e['after_at'])<=stamp for e in events)
        if stamp.date().isoformat()!=previous['source']['metric_date']:
            row['context'].update(day_boundary_role='closing',day_boundary_verified=True);rows.append(row)
            row=copy.deepcopy(row);row['id']+='-opening';row['source']['metric_date']=stamp.date().isoformat()
            row['context'].update(day_boundary_role='opening',day_boundary_verified=True)
            for k in ('spend','gmv','orders'):row['metrics'][k]=0
        rows.append(row);previous=row;stamp+=timedelta(minutes=10)
    return rows,cfg,end,events


class DailyRecoveryTests(unittest.TestCase):
    def test_cross_midnight_recovery_uses_independent_day_deltas(self):
        args=overnight();result=review(args);self.assertEqual(len(result['observations']),1)
        item=result['observations'][0];self.assertEqual(item['state'],'ready_return_step')
        self.assertTrue(item['cross_day']);self.assertEqual(len(item['daily_boundaries']),1)
        self.assertTrue(result['recommendations'][0]['eligible']);self.assertEqual(result['recommendations'][0]['after'],3.5)
        self.assertEqual(item['reported_changes_on_current_day'],1)
        self.assertEqual(item['step_history'][0]['first_hour']['ad_spend'],180)
        self.assertEqual(item['current_window']['ad_spend'],180)

    def test_missing_boundary_side_or_verification_blocks_instead_of_guessing_reset(self):
        for side in ('closing','opening'):
            for mode in ('remove','unverify'):
                args=overnight();rows=args[0]
                target=next(r for r in rows if r['context'].get('day_boundary_role')==side)
                if mode=='remove':rows.remove(target)
                else:target['context']['day_boundary_verified']=False
                self.assertEqual(review(args)['recommendations'],[],(side,mode))

    def test_positive_opening_and_nonmidnight_closing_are_not_reset_evidence(self):
        for mode in ('positive','late'):
            args=overnight();row=next(r for r in args[0] if r['context'].get('day_boundary_role')==('opening' if mode=='positive' else 'closing'))
            if mode=='positive':row['metrics']['spend']=1
            else:row['source']['data_at']=(_time(row['source']['data_at'])+timedelta(seconds=1)).isoformat()
            self.assertEqual(review(args)['recommendations'],[])

    def test_day_internal_counter_reset_is_not_allowed_by_boundary_flag(self):
        args=overnight();args[0][-3]['metrics']['gmv']=0
        self.assertEqual(review(args)['recommendations'],[])

    def test_same_timestamp_closing_and_opening_counters_are_not_duplicate_conflicts(self):
        args=overnight();result=review(args);boundary=result['observations'][0]['daily_boundaries'][0]
        self.assertGreater(boundary['closing_counters']['spend'],0);self.assertEqual(boundary['opening_counters']['spend'],0)
        self.assertFalse(boundary['platform_verified'])

    def test_same_day_same_timestamp_conflicting_counters_are_rejected(self):
        args=overnight();duplicate=copy.deepcopy(args[0][-1]);duplicate['id']='conflict';duplicate['metrics']['gmv']+=1
        args[0].append(duplicate);self.assertEqual(review(args)['recommendations'],[])

    def test_today_count_does_not_need_to_include_yesterday_root(self):
        args=overnight();args[0][-1]['control']['changes_today']=1
        self.assertTrue(review(args)['recommendations'][0]['eligible'])
        args[0][-1]['control']['changes_today']=0
        self.assertFalse(review(args)['recommendations'][0]['eligible'])

    def test_root_with_no_changes_today_can_return_after_midnight(self):
        args=overnight();rows,cfg,at,events=args;root=events[0];events.pop()
        for r in rows:
            if _time(r['source']['data_at'])>=_time(root['after_at']):
                r['metrics']['target_roi']=3.15;r['control']['last_change_at']=root['after_at']
            r['control']['changes_today']=int(_time(r['source']['data_at']).date()==_time(root['after_at']).date() and _time(r['source']['data_at'])>=_time(root['after_at']))
        result=review(args);self.assertTrue(result['recommendations'][0]['eligible'])
        self.assertEqual(result['observations'][0]['reported_changes_on_current_day'],0)

    def test_missing_updates_and_unknown_quality_remain_blocking_across_midnight(self):
        for mode in ('missing','quality'):
            args=overnight();target=next(r for r in args[0] if r['context'].get('day_boundary_role')=='closing')
            if mode=='missing':target['source']['data_at']=None
            else:target['quality']['issues']=['unverified report identity']
            self.assertEqual(review(args)['recommendations'],[])

    def test_live_session_totals_cannot_supply_a_daily_reset(self):
        args=overnight()
        for r in args[0]:r['source'].update(metric_period='live_session',period_start=args[0][0]['source']['data_at'])
        self.assertEqual(review(args)['recommendations'],[])

    def test_base_ledger_reset_is_not_borrowed_from_overall_counters(self):
        args=overnight()
        for row in args[0]:
            for k in ('spend','gmv','orders'):row['metrics']['base_'+k]=row['metrics'][k]
            row['metrics']['spend']*=20;row['metrics']['gmv']*=20
        self.assertTrue(review(args)['recommendations'][0]['eligible'])
        opening=next(r for r in args[0] if r['context'].get('day_boundary_role')=='opening')
        opening['metrics'].pop('base_orders');self.assertEqual(review(args)['recommendations'],[])

    def test_schema_preserves_manual_boundary_declaration_but_browser_cannot_certify_it(self):
        rows,cfg,at,_=overnight();closing=next(r for r in rows if r['context'].get('day_boundary_role')=='closing')
        closing['source']['kind']='import';closing['source']['dataset_id']='explicit-daily-ledger'
        self.assertTrue(normalize_snapshot(closing,cfg,at)['context']['day_boundary_verified'])
        closing['source']['kind']='browser_dom';closing['source']['url']='https://qianchuan.jinritemai.com/uni-prom?aavid=123'
        self.assertIsNone(normalize_snapshot(closing,cfg,at)['context']['day_boundary_verified'])

    def test_episode_keeps_risk_veto_and_is_immutable(self):
        args=overnight();before=copy.deepcopy(args);rows,cfg,at,events=args
        result=evaluate_all(rows,cfg,at,events=events);self.assertTrue(next(r for r in result['recommendations'] if r['rule']=='roi_recovery_step')['eligible'])
        self.assertEqual(args,before)
        account=copy.deepcopy(rows[-1]);account.update(id='blocked-account',scope='account',entity_id='123');account['source']['series_key']='risk';account['context']={'account_blocked':True}
        result=evaluate_all(rows+[account],cfg,at,events=events)
        self.assertFalse(next(r for r in result['recommendations'] if r['rule']=='roi_recovery_step')['eligible'])

    def test_active_episode_can_span_multiple_days_with_zero_reported_changes_today(self):
        result=review(extend_daily(overnight()))
        item=result['observations'][0];self.assertEqual(item['state'],'ready_return_step')
        self.assertEqual(len(item['daily_boundaries']),3);self.assertEqual(item['reported_changes_on_current_day'],0)
        self.assertTrue(result['recommendations'][0]['eligible'])

    def test_complete_episode_is_archived_across_later_days_and_cost_changes(self):
        args=extend_daily(overnight(),complete=True);rows,cfg,at,events=args
        rows[-1]['metrics'].update(target_cost=1,budget=1000,target_roi=3.7)
        result=review(args);item=result['observations'][0]
        self.assertEqual(item['state'],'complete');self.assertFalse(item['active']);self.assertTrue(item['historical'])
        self.assertEqual(item['completed_at'],(_time(events[-1]['after_at'])+timedelta(hours=1)).isoformat())
        self.assertEqual(result['recommendations'],[])

    def test_net_and_marketing_ledgers_preserve_their_own_cost_and_revenue(self):
        for basis in ('net','marketing'):
            rows,cfg,at,events=overnight();cfg['accounts'][0]['roi_basis']=basis
            if basis=='net':cfg['accounts'][0]['target_roi']=2
            for e in events:e['metric_basis']=basis
            for row in rows:
                row['metric_basis']=basis;row['context'].update(plan_roi_basis=basis,target_cost_basis=basis)
                row['metrics']['net_gmv']=row['metrics']['gmv']*(.75 if basis=='net' else 2)
                row['metrics']['comprehensive_cost']=row['metrics']['spend']*1.5
            result=review((rows,cfg,at,events));self.assertTrue(result['recommendations'][0]['eligible'])
            self.assertAlmostEqual(result['observations'][0]['current_window']['roi'],2.4 if basis=='net' else 6.4/1.5)


class RecoveryHistoryTests(unittest.TestCase):
    def test_old_interference_is_not_hidden_by_a_newer_operation_head(self):
        rows,cfg,at,events=extend_daily(overnight(),complete=True)
        old=copy.deepcopy(events[0]);old.update(id='old-budget-interference',action='budget_down',source_key='other-budget-source',
            before_at=events[0]['after_at'],after_at=(_time(events[0]['after_at'])+timedelta(minutes=10)).isoformat(),
            before={'budget':2000},after={'budget':1800})
        later=copy.deepcopy(old);later.update(id='new-budget-head',before_at=(at-timedelta(minutes=10)).isoformat(),after_at=at.isoformat())
        events.extend([old,later])
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'monitor.db');store.add(rows);store.add_events(events)
            ordinary=store.evaluation_snapshots(now=at);short=store.events(now=at)
            self.assertNotIn(old['id'],{e['id'] for e in short})
            full,history=store.recovery_history(ordinary,short)
            result=review_roi_recovery(full,cfg,at,events=history)
            self.assertEqual(result['recommendations'],[])
            self.assertIn('其他同计划',''.join(result['observations'][0]['missing']))

    def test_history_fetch_does_not_attach_another_plan_or_account(self):
        rows,cfg,at,events=overnight();other=copy.deepcopy(rows[0]);other.update(id='private-plan',entity_id='PRIVATE_OTHER')
        foreign=copy.deepcopy(rows[0]);foreign.update(id='foreign-account',account_id='999')
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'monitor.db');store.add(rows+[other,foreign]);store.add_events(events)
            full,history=store.recovery_history([rows[-1]],[])
            self.assertTrue(all(r['entity_id']=='demo-roi-recovery' and r['account_id']=='123' for r in full))
            self.assertEqual(len(history),2)

    def test_completed_history_outside_28h_is_recovered_end_to_end_after_reopen(self):
        rows,cfg,at,events=extend_daily(overnight(),complete=True)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'monitor.db';store=Store(path);store.add(rows);store.add_events(events);store=Store(path)
            ordinary=store.evaluation_snapshots(now=at);short=store.events(now=at)
            self.assertNotIn(events[1]['id'],{e['id'] for e in short})
            full,history=store.recovery_history(ordinary,short)
            result=evaluate_all(ordinary,cfg,at,events=short,recovery_snapshots=full,recovery_events=history)
            self.assertEqual(result['roi_recovery_reviews'][0]['state'],'complete')
            self.assertTrue(all(i['review_status']=='roi_recovery_review' for i in result['outcome_review']['items']))
            self.assertGreater(result['coverage']['recovery_history']['input_snapshots'],len(ordinary))
            self.assertEqual(len(store.events(now=at,hours=200)),3)

    def test_explicit_root_loads_old_chain_outside_normal_window(self):
        rows,cfg,at,events=overnight()
        # Move the current observation two days later while preserving actual intervening samples.
        last=copy.deepcopy(rows[-1]);end=at+timedelta(days=2);counter=0;stamp=at+timedelta(minutes=10)
        while stamp<=end:
            row=copy.deepcopy(last);row['id']='later-'+str(counter);counter+=1
            row['source'].update(captured_at=stamp.isoformat(),data_at=stamp.isoformat(),metric_date=stamp.date().isoformat())
            row['context']['plan_roi_observed_at']=stamp.isoformat();row['control']['changes_today']=0
            rows.append(row);stamp+=timedelta(minutes=10)
        later=copy.deepcopy(events[-1]);later.pop('roi_recovery_root_id')
        later.update(id='later-unlinked-change',status='simulated',before_at=(end-timedelta(minutes=10)).isoformat(),
                     after_at=end.isoformat(),before={'target_roi':3.31},after={'target_roi':3.5})
        events.append(later)
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'monitor.db');store.add(rows);store.add_events(events)
            ordinary=store.evaluation_snapshots(now=end);short_events=store.events(now=end)
            full,history=store.recovery_history(ordinary,short_events)
            self.assertTrue(any(r['id']==rows[0]['id'] for r in full))
            self.assertEqual({e['id'] for e in history},{e['id'] for e in events})
            self.assertGreater(len(full),len(ordinary));self.assertLess(len(short_events),len(history))

    def test_latest_head_without_explicit_root_does_not_fetch_old_episode(self):
        rows,cfg,at,events=overnight()
        with tempfile.TemporaryDirectory() as folder:
            store=Store(Path(folder)/'monitor.db');store.add(rows);store.add_events(events)
            current=copy.deepcopy(rows[-1]);current['context'].pop('roi_recovery_event_id');current['source']['captured_at']=(at+timedelta(seconds=1)).isoformat();current['id']='unlinked-current';store.add([current])
            full,history=store.recovery_history([current],[])
            self.assertEqual(full,[current]);self.assertEqual(history,[])
