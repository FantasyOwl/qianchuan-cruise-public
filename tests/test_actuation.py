import copy
import json
import unittest
from datetime import datetime, timedelta, timezone

from monitor.actuation import exact_id, amount, prepare_action, interpret_budget_response, execute

NOW=datetime(2026,10,22,16,tzinfo=timezone(timedelta(hours=8)))


def fixture():
    account='1999999999999002';plan='8012367229158357'
    settings={'accounts':[{'id':account,'target_roi':2.5,'roi_basis':'overall_payment'}],'rules':{}}
    snapshot={'account_id':account,'entity_id':plan,'scope':'plan','promotion_type':'universal','metric_basis':'overall_payment',
              'source':{'kind':'import','data_at':NOW.isoformat(),'captured_at':NOW.isoformat(),'metric_date':'2026-10-22','coverage':'report'},
              'metrics':{'budget':1000},'control':{'history_known':True,'guarantee_verified':True},'context':{'budget_mode':'specified'}}
    proposal={'id':'p1','account_id':account,'entity_id':plan,'scope':'plan','metric_basis':'overall_payment','action':'budget_up',
              'before':1000,'after':1200,'eligible':True,'mode':'simulation_only'}
    return settings,snapshot,proposal


class ActuationTests(unittest.TestCase):
    def test_verified_budget_preview_preserves_integer_precision_without_token(self):
        cfg,s,p=fixture();r=prepare_action(p,s,cfg,NOW)
        self.assertTrue(r['supported_for_preview']);self.assertFalse(r['live_execution_allowed'])
        body=json.loads(r['request']['body_json'])
        self.assertEqual(body['update_budget_infos'][0]['ad_id'],8012367229158357)
        self.assertFalse(r['request']['submitted']);self.assertNotIn('Authorization',r['request'])

    def test_unknown_or_suggested_budget_requires_conditional_fields(self):
        cfg,s,p=fixture();s['context']={}
        self.assertFalse(prepare_action(p,s,cfg,NOW)['supported_for_preview'])
        s['context']={'budget_mode':'suggested'}
        self.assertFalse(prepare_action(p,s,cfg,NOW)['supported_for_preview'])
        s['metrics'].update(min_estimate_convert=10,estimate_convert=20,estimate_roi_goal=2.5,min_estimate_roi_goal=2)
        self.assertTrue(prepare_action(p,s,cfg,NOW)['supported_for_preview'])

    def test_unknown_write_contract_is_not_guessed(self):
        cfg,s,p=fixture()
        for action in ['bid_down','pause_control','create_chase','delete_plan']:
            p['action']=action;r=prepare_action(p,s,cfg,NOW)
            self.assertFalse(r['supported_for_preview']);self.assertIsNone(r['request'])

    def test_plan_state_contract_cannot_target_a_task(self):
        cfg,s,p=fixture();p.update(action='pause_plan',before=True,after=False);s['control']['running']=True
        r=prepare_action(p,s,cfg,NOW)
        self.assertEqual(json.loads(r['request']['body_json'])['opt_status'],'DISABLE')
        p['scope']=s['scope']='control'
        self.assertFalse(prepare_action(p,s,cfg,NOW)['supported_for_preview'])

    def test_targets_readonly_unknown_source_and_stale_values_fail_closed(self):
        cfg,s,p=fixture()
        mutations=[lambda c,x,y:c['accounts'][0].update(target_roi=None),
                   lambda c,x,y:x['source'].update(data_at=(NOW-timedelta(hours=2)).isoformat()),
                   lambda c,x,y:x['source'].update(coverage='visible_only'),
                   lambda c,x,y:x['source'].update(kind='demo'),
                   lambda c,x,y:y.update(account_id='999'),
                   lambda c,x,y:y.update(eligible=False),
                   lambda c,x,y:y.update(after=1500),
                   lambda c,x,y:y.update(before=999),
                   lambda c,x,y:y.update(target_id='another-plan'),
                   lambda c,x,y:y.update(metric_basis='marketing'),
                   lambda c,x,y:x.update(quality={'issues':['金额范围不一致']}),
                   lambda c,x,y:x.update(promotion_type='standard')]
        for mutate in mutations:
            c,x,y=copy.deepcopy((cfg,s,p));mutate(c,x,y)
            self.assertFalse(prepare_action(y,x,c,NOW)['supported_for_preview'])

    def test_invalid_money_and_ids_are_rejected(self):
        for value in [True,-1,float('nan'),'0','1.001','1e100']:
            with self.assertRaises(ValueError):amount(value)
        for value in [1999999999999002,'1.0','0','-1','1e15','x']:
            with self.assertRaises(ValueError):exact_id(value)

    def test_partial_and_duplicate_results_are_not_whole_batch_success(self):
        value={'code':0,'data':{'results':[{'ad_id':1,'status':'SUCCESS'},{'ad_id':2,'status':'FAILED'}]}}
        r=interpret_budget_response(value,['1','2','3'])
        self.assertFalse(r['success']);self.assertEqual(r['unknown'],['3'])
        value['data']['results'].append({'ad_id':1,'status':'SUCCESS'})
        self.assertIn('1',interpret_budget_response(value,['1','2','3'])['unknown'])

    def test_no_configuration_can_enable_real_execution(self):
        with self.assertRaises(PermissionError):execute(live=True,approved=True)


if __name__=='__main__':unittest.main()
