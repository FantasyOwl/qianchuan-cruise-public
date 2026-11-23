import copy
import unittest
from datetime import datetime, timedelta, timezone

from monitor.cruise_rules import FEATURE_CATALOG, default_profile, evaluate_account, validate_profile
from monitor.cruise_rules import profile_for_entry


class EntryMetricTests(unittest.TestCase):
    def test_all_domain_save_normalizes_only_metric_fields(self):
        original=default_profile();original['features']['f2']['rules']=[{'cost_metric':'control_cost','target_control_budget':500}]
        result=profile_for_entry(original,{'scene':'live','delivery_entry':'uni_promotion'})
        self.assertEqual(result['features']['f1']['cost_metric'],'control_spend')
        self.assertEqual(result['features']['f4']['cost_metric'],'control_spend')
        self.assertEqual(result['features']['f2']['rules'][0]['target_control_budget'],500)
        self.assertEqual(original['features']['f1']['cost_metric'],'control_cost')
        for entry in ('chengfang','both'):
            self.assertEqual(profile_for_entry(original,{'scene':'live','delivery_entry':entry}),original)
        self.assertEqual(profile_for_entry(original,{'scene':'product','delivery_entry':'uni_promotion'}),original)

NOW=datetime.fromisoformat('2026-10-25T12:00:00+08:00')
ACCOUNT='1999999999999002'
PLAN='8218616704149013'
TASK='8339456325564332585'


def task(**updates):
    row={'id':'snapshot-1','account_id':ACCOUNT,'entity_id':TASK,'scope':'control','scene':'live',
        'metric_basis':'control_payment','metrics':{'spend':100,'comprehensive_cost':100,'roi':2,'budget':100,'orders':1},
        'source':{'kind':'browser_dom','captured_at':NOW.isoformat(),'data_at':None,'metric_date':'2026-10-25','parent_entity_id':PLAN},
        'context':{'parent_plan_id':PLAN,'control_delivery_mode':'cost_control','created_at':(NOW-timedelta(minutes=45)).isoformat(),
                   'observed_duration_mode':'fixed','observed_duration_hours':2},'control':{'running':True}}
    for k,v in updates.items():
        if isinstance(v,dict):row[k].update(v)
        else:row[k]=v
    return row


class CruiseRulesTests(unittest.TestCase):
    def test_pause_is_selected_before_budget_increase_even_with_one_action_per_round(self):
        p=self.profile(min_control_cost=80,max_roi=2);p['safety']['max_actions_per_round']=1
        p['features']['f2'].update(enabled=True,rules=[{'cost_metric':'control_spend','min_control_cost':100,'max_control_cost':300,'min_roi':2.3,'target_control_budget':600,'cooldown_minutes':5}])
        add=task(metrics={'spend':150,'comprehensive_cost':150,'roi':3,'budget':300})
        stop=task(entity_id='8618530283142212632',metrics={'spend':150,'comprehensive_cost':150,'roi':1,'budget':300})
        out=self.run_rule(p,[add,stop]);self.assertEqual(len(out['proposals']),1)
        self.assertEqual(out['proposals'][0]['action'],'control_pause');self.assertEqual(out['proposals'][0]['entity_id'],stop['entity_id'])

    def profile(self,fid='f1',**values):
        p=default_profile();p['enabled']=True;p['features'][fid]['enabled']=True;p['features'][fid].update(values)
        if fid=='f1' and 'max_roi' not in values:p['features'][fid]['max_roi']=2.25
        return p

    def run_rule(self,p=None,rows=None,now=NOW,history=None):
        return evaluate_account(ACCOUNT,p or self.profile(),[task()] if rows is None else rows,now=now,history=history)

    def assert_none(self,result):self.assertEqual(result['proposals'],[])

    def test_default_six_features_are_disabled_and_account_thresholds_are_not_inherited(self):
        p=default_profile()
        self.assertEqual([f['id'] for f in FEATURE_CATALOG],['f1','f2','f3','f4','f5','f6'])
        self.assertFalse(p['enabled']);self.assertTrue(all(not f['enabled'] for f in p['features'].values()))
        self.assertIsNone(p['features']['f1']['max_roi']);self.assertIsNone(p['features']['f6']['min_overall_roi'])
        self.assertEqual(validate_profile(p),p)
        p['features']['f1']['max_roi']=9
        self.assertIsNone(default_profile()['features']['f1']['max_roi'])

    def test_profile_rejects_bad_switches_unknown_fields_and_invalid_intervals(self):
        for patch in ({'enabled':1},{'unknown':True},{'main_interval_seconds':0},{'operation_start':'25:00'},
                      {'features':{'bad':{}}},{'features':{'f1':{'max_roi':float('nan')}}},
                      {'features':{'f2':{'rules':[{'min_control_cost':100,'max_control_cost':50}]}}}):
            with self.subTest(patch=patch),self.assertRaises(ValueError):validate_profile(patch)

    def test_repeat_running_material_switch_defaults_off_and_requires_a_boolean(self):
        self.assertFalse(default_profile()['features']['f5']['allow_running_materials'])
        for value in (True,False):self.assertIs(validate_profile({'features':{'f5':{'allow_running_materials':value}}})['features']['f5']['allow_running_materials'],value)
        for bad in (1,'true',None):
            with self.assertRaises(ValueError):validate_profile({'features':{'f5':{'allow_running_materials':bad}}})

    def test_f1_current_task_roi_matches_without_unrelated_hourly_or_account_target_gates(self):
        r=self.run_rule();self.assertEqual(len(r['proposals']),1)
        q=r['proposals'][0]
        self.assertEqual(q['action'],'control_pause');self.assertIs(q['before'],True);self.assertIs(q['after'],False)
        self.assertEqual(q['entity_id'],TASK);self.assertEqual(q['parent_plan_id'],PLAN)
        self.assertEqual(q['metric_basis'],'control_payment');self.assertTrue(q['requires_confirmation'])
        self.assertFalse(r['executed'])

    def test_roi_threshold_unconfigured_does_not_become_a_zero_or_old_account_target(self):
        self.assert_none(self.run_rule(self.profile(max_roi=None)))
        row=task();del row['metrics']['roi']
        r=self.run_rule(rows=[row]);self.assert_none(r);self.assertIn('缺少明确任务支付ROI口径',r['skipped'][0]['reasons'])

    def test_f1_strict_threshold_boundaries_and_explicit_spend_metric(self):
        for values in ({'comprehensive_cost':50},{'roi':2.25}):
            with self.subTest(values=values):self.assert_none(self.run_rule(rows=[task(metrics=values)]))
        row=task();del row['metrics']['comprehensive_cost']
        self.assert_none(self.run_rule(rows=[row]))
        self.assertEqual(len(self.run_rule(self.profile(cost_metric='control_spend'),[row])['proposals']),1)

    def test_wrong_roi_basis_cannot_be_substituted_for_task_payment_roi(self):
        for basis in ('net','marketing','overall_payment','unknown'):
            with self.subTest(basis=basis):self.assert_none(self.run_rule(rows=[task(metric_basis=basis)]))

    def test_f2_absolute_budget_and_strict_cost_roi_interval(self):
        p=self.profile('f2',rules=[{'min_control_cost':50,'max_control_cost':150,'min_roi':3,'target_control_budget':600}])
        r=self.run_rule(p,[task(metrics={'roi':4})]);self.assertEqual(r['proposals'][0]['after'],'600.00')
        self.assertEqual(r['proposals'][0]['before'],'100.00')
        for values in ({'comprehensive_cost':50,'roi':4},{'comprehensive_cost':150,'roi':4},{'roi':3},{'roi':4,'budget':600}):
            with self.subTest(values=values):self.assert_none(self.run_rule(p,[task(metrics=values)]))
        self.assert_none(self.run_rule(p,[task(metrics={'roi':4},context={'control_delivery_mode':'volume'})]))

    def test_first_matching_feature_and_first_matching_row_win(self):
        p=self.profile(max_roi=5)
        p['features']['f2']={'enabled':True,'rules':[{'min_control_cost':50,'max_control_cost':150,'min_roi':3,'target_control_budget':600}]}
        r=self.run_rule(p,[task(metrics={'roi':4})]);self.assertEqual([q['feature_id'] for q in r['proposals']],['f1'])
        p['features']['f1']['enabled']=False
        p['features']['f2']['rules'].append({'min_control_cost':50,'max_control_cost':150,'min_roi':3,'target_control_budget':800})
        self.assertEqual(self.run_rule(p,[task(metrics={'roi':4})])['proposals'][0]['after'],'600.00')

    def test_f3_splits_budget_and_duration_into_two_immutable_candidates(self):
        p=self.profile('f3',rules=[{'min_control_cost':50,'max_control_cost':150,'min_roi':3,'target_control_budget':600,'increase_control_duration':1.5}])
        row=task(metrics={'roi':4},context={'control_delivery_mode':'volume'})
        proposals=self.run_rule(p,[row])['proposals']
        self.assertEqual([q['action'] for q in proposals],['control_budget_up','control_duration_up'])
        self.assertEqual([q['after'] for q in proposals],['600.00','210.00'])
        self.assertEqual(proposals[0]['group_id'],proposals[1]['group_id'])
        p['safety']['max_actions_per_round']=1
        self.assert_none(self.run_rule(p,[row]))

    def test_f3_duration_remains_reviewable_after_separate_budget_write(self):
        p=self.profile('f3',rules=[{'min_control_cost':50,'max_control_cost':150,'min_roi':3,'target_control_budget':600,'increase_control_duration':1.5}])
        row=task(metrics={'roi':4,'budget':600},context={'control_delivery_mode':'volume'})
        history=[{'account_id':ACCOUNT,'parent_plan_id':PLAN,'entity_id':TASK,'action':'control_budget_up','state':'verified','at':NOW.isoformat()}]
        q=self.run_rule(p,[row],history=history)['proposals']
        self.assertEqual([x['action'] for x in q],['control_duration_up'])
        self.assertEqual((q[0]['before'],q[0]['after']),('120.00','210.00'))

    def test_f3_unknown_or_unlimited_duration_does_not_invent_extension_value(self):
        p=self.profile('f3',rules=[{'min_control_cost':50,'max_control_cost':150,'min_roi':3,'target_control_budget':600,'increase_control_duration':1}])
        for mode in ('unknown','unlimited'):
            with self.subTest(mode=mode):self.assert_none(self.run_rule(p,[task(metrics={'roi':4},context={'control_delivery_mode':'volume','observed_duration_mode':mode})]))

    def test_budget_and_duration_are_never_silently_rounded(self):
        p=self.profile('f3',rules=[{'min_control_cost':50,'max_control_cost':150,'min_roi':3,'target_control_budget':600,'increase_control_duration':1}])
        for update in ({'metrics':{'roi':4,'budget':100.001},'context':{'control_delivery_mode':'volume'}},
                       {'metrics':{'roi':4},'context':{'control_delivery_mode':'volume','observed_duration_hours':1.333}}):
            with self.subTest(update=update):self.assert_none(self.run_rule(p,[task(**update)]))
        p['features']['f3']['rules'][0]['target_control_budget']=600.001
        with self.assertRaises(ValueError):validate_profile(p)

    def test_f4_explicit_zero_cost_and_runtime_match_without_roi_requirement(self):
        p=self.profile('f4');row=task(metric_basis='unknown',metrics={'comprehensive_cost':0},context={'control_delivery_mode':'volume'})
        del row['metrics']['roi']
        self.assertEqual(self.run_rule(p,[row])['proposals'][0]['action'],'control_pause')
        del row['metrics']['comprehensive_cost']
        self.assert_none(self.run_rule(p,[row]))

    def low_spend_task(self,hours=2,spend=19,**kwargs):
        created=NOW-timedelta(hours=hours)
        row=task(context={'control_type':'material_chase','created_at':created.isoformat(),'lifetime_spend_verified':True,
            'lifetime_spend_start':created.isoformat(),'lifetime_spend_end':NOW.isoformat()},metrics={'lifetime_spend':spend})
        for key,value in kwargs.items():row[key].update(value)
        return row

    def test_low_spend_f4_uses_reached_hours_and_strictly_below_lifetime_spend_for_both_modes(self):
        p=self.profile('f4',pause_rule='low_spend',low_spend_hours=2,low_spend_max=20,low_spend_scope='both')
        for mode in ('cost_control','volume'):
            row=self.low_spend_task(context={'control_delivery_mode':mode})
            self.assertEqual(self.run_rule(p,[row])['proposals'][0]['action'],'control_pause')
        for row in (self.low_spend_task(hours=1.99),self.low_spend_task(spend=20),self.low_spend_task(spend=25)):
            self.assert_none(self.run_rule(p,[row]))

    def test_f4_requires_today_time_spend_and_payment_roi(self):
        p=self.profile('f4',pause_rule='daily_spend_roi',daily_runtime_hours=2,daily_spend_max=20,daily_roi_max=1.5)
        matching=self.low_spend_task(hours=27,spend=99,metrics={'spend':19,'roi':0})
        proposal=self.run_rule(p,[matching])['proposals'][0]
        self.assertEqual(proposal['action'],'control_pause')
        self.assertIn('今日经过时长',proposal['reason'])
        for update in ({'metrics':{'spend':20}},{'metrics':{'roi':1.5}},{'metrics':{'roi':None}},
                       {'source':{'metric_date':None}},{'source':{'data_at':(NOW-timedelta(minutes=4)).isoformat()}},
                       {'context':{'created_at':(NOW-timedelta(hours=1.99)).isoformat()}}):
            row=copy.deepcopy(matching)
            for key, value in update.items():row[key].update(value)
            with self.subTest(update=update):self.assert_none(self.run_rule(p,[row]))
        row=copy.deepcopy(matching);row['metric_basis']='unknown'
        self.assert_none(self.run_rule(p,[row]))

    def test_f4_today_clock_resets_for_tasks_created_yesterday(self):
        p=self.profile('f4',pause_rule='daily_spend_roi',daily_runtime_hours=2,daily_spend_max=20,daily_roi_max=1.5)
        p['operation_start']='00:00';p['operation_end']='23:59'
        row=self.low_spend_task(hours=27,metrics={'spend':19,'roi':1})
        early=NOW.replace(hour=1,minute=59);row['source']['captured_at']=early.isoformat()
        self.assert_none(self.run_rule(p,[row],now=early))
        reached=early+timedelta(minutes=1);row['source']['captured_at']=reached.isoformat()
        self.assertEqual(self.run_rule(p,[row],now=reached)['proposals'][0]['action'],'control_pause')
        p['features']['f4']['daily_roi_max']=None
        self.assert_none(self.run_rule(p,[row],now=reached))

    def test_f4_today_pause_wins_over_budget_increase_for_the_same_task(self):
        p=self.profile('f4',pause_rule='daily_spend_roi',daily_runtime_hours=2,daily_spend_max=20,daily_roi_max=1.5)
        p['features']['f2'].update(enabled=True,rules=[{'cost_metric':'control_spend','min_control_cost':5,'max_control_cost':30,'min_roi':.5,'target_control_budget':150}])
        row=self.low_spend_task(hours=27,metrics={'spend':19,'roi':1})
        self.assertEqual([q['action'] for q in self.run_rule(p,[row])['proposals']],['control_pause'])

    def test_low_spend_never_uses_daily_zero_or_missing_lifetime_window(self):
        p=self.profile('f4',pause_rule='low_spend',low_spend_hours=2,low_spend_max=20)
        row=self.low_spend_task(hours=26,spend=900,metrics={'spend':0});self.assert_none(self.run_rule(p,[row]))
        row=self.low_spend_task();row['metrics'].pop('lifetime_spend');self.assert_none(self.run_rule(p,[row]))
        row=self.low_spend_task(context={'lifetime_spend_start':(NOW-timedelta(hours=1)).isoformat()});self.assert_none(self.run_rule(p,[row]))
        row=self.low_spend_task(context={'control_type':'one_click_boost'});self.assert_none(self.run_rule(p,[row]))

    def test_low_spend_defaults_blank_and_scope_filter_is_explicit(self):
        self.assertIsNone(default_profile()['features']['f4']['low_spend_hours'])
        self.assertIsNone(default_profile()['features']['f4']['low_spend_max'])
        p=self.profile('f4',pause_rule='low_spend');self.assert_none(self.run_rule(p,[self.low_spend_task()]))
        p['features']['f4'].update(low_spend_hours=2,low_spend_max=20,low_spend_scope='volume')
        self.assert_none(self.run_rule(p,[self.low_spend_task(context={'control_delivery_mode':'cost_control'})]))

    def test_low_spend_thresholds_reject_zero_nonfinite_and_silent_rounding(self):
        for values in ({'low_spend_hours':0},{'low_spend_max':0},{'low_spend_hours':float('nan')},{'low_spend_max':1.001}):
            with self.subTest(values=values),self.assertRaises(ValueError):validate_profile(self.profile('f4',pause_rule='low_spend',**values))

    def test_high_spend_alert_does_not_suppress_explicit_new_low_spend_stop_rule(self):
        p=self.profile('f4',pause_rule='low_spend',low_spend_hours=2,low_spend_max=1000)
        row=self.low_spend_task(spend=600,metrics={'spend':600,'orders':0})
        result=self.run_rule(p,[row]);self.assertEqual(len(result['proposals']),1);self.assertEqual(len(result['alerts']),1)
        row=task(metrics={'comprehensive_cost':0},context={'control_delivery_mode':'volume','created_at':(NOW-timedelta(minutes=29)).isoformat()})
        self.assert_none(self.run_rule(p,[row]))

    def test_high_spend_zero_orders_is_alert_only_as_original_engine(self):
        row=task(metrics={'spend':501,'orders':0})
        r=self.run_rule(rows=[row]);self.assert_none(r);self.assertEqual(len(r['alerts']),1)
        row['metrics'].pop('orders')
        self.assertEqual(len(self.run_rule(rows=[row])['proposals']),1)

    def test_account_parent_status_and_freshness_are_not_guessed(self):
        for patch in ({'account_id':'456'},{'control':{'running':False}},{'control':{'running':None}},
                      {'context':{'parent_plan_id':'123'}},{'source':{'captured_at':(NOW-timedelta(seconds=181)).isoformat()}},
                      {'source':{'metric_date':'2026-10-24'}},{'scene':'product'}):
            with self.subTest(patch=patch):self.assert_none(self.run_rule(rows=[task(**patch)]))
        row=task();row['context'].pop('parent_plan_id');row['source'].pop('parent_entity_id')
        self.assert_none(self.run_rule(rows=[row]))

    def test_actual_capture_instants_are_compared_across_z_and_plus_eight(self):
        old=task(source={'captured_at':(NOW-timedelta(minutes=1)).isoformat()},metrics={'roi':4})
        fresh=task(source={'captured_at':NOW.astimezone(timezone.utc).isoformat().replace('+00:00','Z')})
        self.assertEqual(len(self.run_rule(rows=[fresh,old])['proposals']),1)
        same=copy.deepcopy(fresh);same['source']['captured_at']=NOW.isoformat()
        self.assertEqual(len(self.run_rule(rows=[fresh,same])['proposals']),1)
        same['metrics']['roi']=4
        self.assert_none(self.run_rule(rows=[fresh,same]))

    def test_pending_unknown_cooldown_and_daily_limit_are_account_scoped(self):
        event={'account_id':ACCOUNT,'parent_plan_id':PLAN,'entity_id':TASK,'action':'control_pause','state':'unknown','at':NOW.isoformat()}
        self.assert_none(self.run_rule(history=[event]))
        event['state']='verified';event['at']=(NOW-timedelta(minutes=14)).isoformat()
        self.assert_none(self.run_rule(history=[event]))
        event['at']=(NOW-timedelta(minutes=15)).isoformat()
        self.assertEqual(len(self.run_rule(history=[event])['proposals']),1)
        event['account_id']='456';event['state']='unknown'
        self.assertEqual(len(self.run_rule(history=[event])['proposals']),1)
        p=self.profile();p['safety']['max_actions_per_day']=1
        event.update(account_id=ACCOUNT,entity_id='123',state='verified',at=NOW.isoformat())
        self.assert_none(self.run_rule(p,history=[event]))

    def test_operation_and_broadcast_windows_cover_midnight_and_protect_both_lanes(self):
        p=self.profile();p['broadcast_protection']={'enabled':True,'start':'23:00','end':'08:00'}
        self.assert_none(self.run_rule(p,now=NOW.replace(hour=23)))
        self.assert_none(self.run_rule(p,now=NOW.replace(hour=6)))
        p['operation_start']=p['operation_end']='00:00'
        row=task(source={'captured_at':NOW.replace(hour=3).isoformat()})
        p['broadcast_protection']['enabled']=False
        self.assertEqual(len(self.run_rule(p,[row],now=NOW.replace(hour=3))['proposals']),1)

    def creative(self,status='-',**updates):
        values={'scope':'creative','entity_id':'86436454504101401151','metric_basis':'overall_payment',
            'context':{'chase_status':status},'metrics':{'spend':100,'roi':4}};values.update(updates)
        return task(**values)

    def f5(self,**row):
        rule={'material_spend_min':50,'material_spend_max':500,'material_roi_min':3,'chase_type':'控成本追投',
            'optimization_target':'直播间成交','daily_budget':100,'bid_value':2.5};rule.update(row)
        return self.profile('f5',rules=[rule])

    def test_f5_cost_control_candidate_keeps_parent_identity_and_native_creation_boundary(self):
        p=self.f5();r=self.run_rule(p,[self.creative()]);q=r['proposals'][0]
        self.assertEqual(q['action'],'auto_chase');self.assertEqual(q['after']['daily_budget'],100)
        self.assertEqual(q['native_dispatch_required'],'material_chase_creation');self.assertTrue(q['requires_confirmation'])
        self.assertEqual(q['parent_plan_id'],PLAN);self.assertEqual(q['entity_id'],'86436454504101401151')

    def test_f5_reports_missing_threshold_and_creation_adapter_even_without_materials(self):
        r=self.run_rule(self.f5(material_roi_min=None),[])
        self.assert_none(r)
        self.assertEqual({d['status'] for d in r['diagnostics']},
            {'creation_not_connected','incomplete_rule','missing_current_creatives'})
        self.assertIn('素材ROI下限','；'.join(d['reason'] for d in r['diagnostics']))
        fresh=self.run_rule(self.f5(),[self.creative()])
        self.assertNotIn('missing_current_creatives',[d['status'] for d in fresh['diagnostics']])

    def test_disabled_f5_does_not_add_creation_or_material_diagnostics_to_main_scan(self):
        r=self.run_rule(self.profile(),[])
        self.assertFalse(any(d['feature_id']=='f5' for d in r['diagnostics']))

    def test_f5_active_unknown_status_wrong_basis_and_missing_parameters_do_not_match(self):
        for status in (None,'','追投中','已追投','未知状态'):
            with self.subTest(status=status):self.assert_none(self.run_rule(self.f5(),[self.creative(status)]))
        self.assert_none(self.run_rule(self.f5(),[self.creative(metric_basis='marketing')]))
        self.assert_none(self.run_rule(self.f5(bid_value=None),[self.creative()]))

    def test_f5_volume_uses_total_budget_and_hours_not_disabled_cost_control_fields(self):
        p=self.f5(chase_type='放量追投',total_budget=200,control_duration=2,bid_value=None,daily_budget=None)
        q=self.run_rule(p,[self.creative('已结束')])['proposals'][0]
        self.assertEqual(q['after'],{'chase_type':'放量追投','total_budget':200,'control_duration':2})

    def test_f5_current_active_task_blocks_but_finished_history_and_stale_task_do_not(self):
        c=self.creative();t=task(context={'material_id':c['entity_id']})
        self.assert_none(self.run_rule(self.f5(),[c,t]))
        t['source']['captured_at']=(NOW-timedelta(days=1)).isoformat()
        self.assertEqual(len(self.run_rule(self.f5(),[c,t])['proposals']),1)
        history=[{'account_id':ACCOUNT,'entity_id':c['entity_id'],'parent_plan_id':PLAN,'action':'auto_chase','state':'verified','at':(NOW-timedelta(hours=23)).isoformat()}]
        self.assertEqual(len(self.run_rule(self.f5(),[c],history=history)['proposals']),1)

    def test_f5_running_material_opt_in_keeps_unknown_history_blocked(self):
        p=self.f5();p['features']['f5']['allow_running_materials']=True;c=self.creative('调控中');t=task(context={'material_id':c['entity_id']})
        history=[{'account_id':ACCOUNT,'entity_id':c['entity_id'],'parent_plan_id':PLAN,'action':'auto_chase','state':'verified','at':NOW.isoformat()}]
        result=self.run_rule(p,[c,t],history=history)
        self.assertEqual(len(result['proposals']),1);self.assertTrue(result['proposals'][0]['allow_running_materials'])
        history[0]['state']='unknown';self.assert_none(self.run_rule(p,[c,t],history=history))
        for status in ('','未知状态','已追投'):
            self.assert_none(self.run_rule(p,[self.creative(status)]))

    def test_f6_configuration_diagnostic_does_not_manufacture_a_writer(self):
        r=self.run_rule(self.profile('f6'),[])
        self.assert_none(r);self.assertEqual(r['diagnostics'][0]['status'],'config_only')

    def test_input_objects_are_not_mutated_and_invalid_identifiers_reject(self):
        p=self.profile();rows=[task()];before=copy.deepcopy((p,rows))
        self.run_rule(p,rows);self.assertEqual((p,rows),before)
        with self.assertRaises(ValueError):evaluate_account(int(ACCOUNT),p,rows,now=NOW)
        with self.assertRaises(ValueError):self.run_rule(p,rows,history={})
        self.assert_none(self.run_rule(rows=[task(entity_id=123)]))


if __name__=='__main__':unittest.main()
