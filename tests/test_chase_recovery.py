import json
import unittest
from datetime import timedelta
from decimal import Decimal
from tests import test_hybrid_chase as fixtures
from monitor.chase_recovery import reservations
from monitor.hybrid_chase import HybridChase

class RecoveryTests(unittest.TestCase):
    def test_start_does_not_report_an_isolated_old_receipt_as_a_new_f5_sweep(self):
        c,r,d=self.isolate()
        self.hybrid.worker(self.h.worker)
        result=self.hybrid.start({'account_id':r['account_id']})
        self.assertFalse(result['already_running']);self.assertNotEqual(result['run_id'],r['id'])
        self.assertTrue(self.hybrid.active(r['account_id']))
        self.assertEqual(self.a.isolated_budget(r['account_id'],r['plan_id']),Decimal('200.0'))
        self.assertEqual(next(x for x in self.a.status() if x['id']==c['action']['id'])['state'],'unknown')
        self.assertEqual(self.h.posts,[])

    def test_f5_checks_fresh_native_quota_before_opening_a_cost_control_form(self):
        c,r,d=self.isolate();self.native_rule_row(r,c)
        with self.a.db() as db:
            row=db.execute("SELECT data FROM hybrid_rule_runs WHERE id='native-proof'").fetchone();v=json.loads(row['data'])
            v['rows']['10000020']['context']['native_available_budget']=0
            db.execute("UPDATE hybrid_rule_runs SET data=? WHERE id='native-proof'",(json.dumps(v),))
        inventory={'tasks':{},'lookups':{},'parent_budget':'10000'}
        with self.assertRaisesRegex(ValueError,'本批未开追投表单'):self.hybrid.budget_check(r,inventory,Decimal('100'),cost_control=True)
        self.hybrid.budget_check(r,inventory,Decimal('100'),cost_control=False)
        self.h.at+=timedelta(seconds=91)
        self.hybrid.budget_check(r,inventory,Decimal('100'),cost_control=True)
        self.assertEqual(self.h.posts,[])

    def native_rule_row(self,r,c,**changes):
        from monitor.hybrid_rules import HybridRules
        self.a.hybrid_rules=HybridRules(self.a)
        s={'account_id':r['account_id'],'plan_id':r['plan_id'],'task_id':'10000020',
           'name':'Later manual task','material_count':8,'captured_at':self.h.at.isoformat(),
           'context':{'created_at':(self.h.at-timedelta(minutes=5)).isoformat(),
                      'control_type':'material_chase','control_delivery_mode':'cost_control'}}
        s.update(changes);d={'preview':False,'rows':{'10000020':s}}
        with self.a.db() as db:db.execute('INSERT OR REPLACE INTO hybrid_rule_runs VALUES(?,?,?,?,?,?)',('native-proof',r['account_id'],r['plan_id'],'waiting',self.h.at.isoformat(),json.dumps(d)))
        return {'account_id':r['account_id'],'plan_id':r['plan_id'],'action':'pause_task','task_id':'10000020','execution_channel':'browser'}

    def test_later_manual_task_requires_incompatible_native_creation_evidence(self):
        c,r,d=self.isolate();i=self.native_rule_row(r,c)
        with self.a.db() as db:
            for kind in ('pause_task','task_budget','task_duration'):
                self.a._pending(db,{**i,'action':kind},hybrid_rule_run='native-proof')
            # Public API intents / unbound row claims cannot use this proof.
            with self.assertRaises(ValueError):self.a._pending(db,{**i,'execution_channel':'api'},hybrid_rule_run='native-proof')
        self.assertEqual(self.a.isolated_budget(r['account_id'],r['plan_id']),Decimal('200.0'))
        self.assertEqual(next(x for x in self.a.status() if x['id']==c['action']['id'])['state'],'unknown')
        self.assertEqual(self.h.posts,[])

    def test_single_page_native_inventory_is_published_before_draft_and_final_checks(self):
        c,r,d=self.isolate();i=self.native_rule_row(r,c)
        with self.a.db() as db:
            run=db.execute("SELECT * FROM hybrid_rule_runs WHERE id='native-proof'").fetchone()
            fresh=json.loads(run['data'])
            db.execute("UPDATE hybrid_rule_runs SET data=? WHERE id='native-proof'",(json.dumps({**fresh,'rows':{}}),))
            with self.assertRaises(ValueError):self.a._pending(db,i,hybrid_rule_run='native-proof')
            self.a.hybrid_rules.pending(db,run,fresh,i)
            # The next guard must use the new observation, never renew an old
            # timestamp or accept just a client-supplied browser_surface.
            fresh['rows']['10000020']['captured_at']=(self.h.at-timedelta(seconds=91)).isoformat()
            with self.assertRaises(ValueError):self.a.hybrid_rules.pending(db,run,fresh,i)
        self.assertEqual(self.h.posts,[])

    def test_native_name_alone_or_stale_foreign_or_ambiguous_rows_do_not_release_lock(self):
        c,r,d=self.isolate()
        changes=[{'material_count':len(c['action']['intent']['material_ids'])},
                 {'name':c['action']['intent']['browser_batch']['task_name']},
                 {'captured_at':(self.h.at-timedelta(seconds=91)).isoformat()},
                 {'account_id':'other'},{'plan_id':'99999999'},
                 {'context':{'control_type':'material_chase','control_delivery_mode':'cost_control','created_at':d['started_at']}},
                 {'material_count':0},{'material_count':True}]
        for change in changes:
            with self.subTest(change=change):
                i=self.native_rule_row(r,c,**change)
                with self.a.db() as db:
                    with self.assertRaises(ValueError):self.a._pending(db,i,hybrid_rule_run='native-proof')

    def test_volume_task_can_be_distinguished_even_without_material_count(self):
        c,r,d=self.isolate();i=self.native_rule_row(r,c,material_count=0,context={
            'control_type':'material_chase','control_delivery_mode':'volume',
            'created_at':(self.h.at-timedelta(minutes=5)).isoformat()})
        with self.a.db() as db:self.a._pending(db,i,hybrid_rule_run='native-proof')

    def add_verified_creation(self,old,*,name='QCX_later_independent',state='verified',plan=None,proof=True):
        import copy
        intent=copy.deepcopy(old['action']['intent']);intent['browser_batch']['task_name']=name
        if plan:intent['plan_id']=plan
        receipt={'task_id':'10000019','independently_verified':proof,'ad_changes':1 if proof else 0}
        with self.a.db() as db:db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',('verified-later',state,json.dumps(intent),'known',self.h.at.isoformat(),self.h.at.isoformat(),self.h.at.isoformat(),json.dumps(receipt)))

    def test_later_verified_creation_can_be_controlled_while_original_reservation_remains(self):
        c,r,d=self.isolate();self.add_verified_creation(c)
        with self.a.db() as db:
            for action in ('pause_task','task_budget','task_duration'):
                self.a._pending(db,{'account_id':r['account_id'],'plan_id':r['plan_id'],'action':action,'task_id':'10000019'})
            self.assertIn('10000019',reservations(db,r['account_id'],r['plan_id'])[0]['existing_task_ids'])
        self.assertEqual(self.a.isolated_budget(r['account_id'],r['plan_id']),Decimal('200.0'))
        self.assertEqual(next(x for x in self.a.status() if x['id']==c['action']['id'])['state'],'unknown')
        self.assertEqual(self.h.posts,[])

    def test_ambiguous_or_foreign_creation_never_extends_independent_task_scope(self):
        c,r,d=self.isolate()
        for opts in ({'name':c['action']['intent']['browser_batch']['task_name']},{'state':'unknown'},{'plan':'99999999'},{'proof':False}):
            with self.a.db() as db:db.execute("DELETE FROM api_actions WHERE id='verified-later'")
            self.add_verified_creation(c,**opts)
            with self.a.db() as db:
                with self.assertRaises(ValueError):self.a._pending(db,{'account_id':r['account_id'],'plan_id':r['plan_id'],'action':'pause_task','task_id':'10000019'})
        self.assertEqual(self.h.posts,[])

    def setUp(self):
        self.h=fixtures.HybridTests('runTest');self.h.setUp();self.a=self.h.queue;self.hybrid=self.h.hybrid
    def tearDown(self):self.h.tearDown()
    def isolate(self):
        c=self.h.ready();permit=self.h.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        self.h.at+=timedelta(minutes=11);self.hybrid.tick()
        with self.a.db() as db:
            r=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone();d=json.loads(r['data'])
            self.assertTrue(d['isolated_readback']);self.assertEqual(self.a.status()[0]['state'],'unknown')
        return c,r,d

    def test_unknown_write_is_isolated_without_releasing_budget_materials_or_daily_batch(self):
        c,r,d=self.isolate();aid=r['account_id'];pid=r['plan_id'];budget=Decimal(str(c['action']['intent']['body']['budget']))
        self.assertFalse(self.hybrid.active(aid));self.assertFalse(self.hybrid.active(aid,writes_only=True))
        self.assertEqual(self.a.isolated_budget(aid,pid),budget);self.assertEqual(self.a.auto.daily(aid),1)
        self.assertEqual(self.a.isolated_materials(aid,pid),set(c['action']['intent']['material_ids']))
        with self.assertRaisesRegex(ValueError,'后台核验中'):self.a.require_allowed_materials(aid,c['action']['intent']['material_ids'])
        with self.a.db() as db:
            self.assertEqual(reservations(db,'other',pid),[])
            self.assertEqual(self.hybrid.pending(db,aid,pid),[])
            self.a._pending(db,{'account_id':aid,'plan_id':pid,'action':'pause_task','task_id':'10001'})
            for intent in [{'action':'pause_task','task_id':'99999'},{'action':'plan_roi'},{'action':'material_chase','material_ids':c['action']['intent']['material_ids']}]:
                with self.assertRaises(ValueError):self.a._pending(db,{'account_id':aid,'plan_id':pid,**intent})
        self.assertEqual(self.h.posts,[])

    def test_api_and_browser_caps_include_unknown_reservation(self):
        c,r,d=self.isolate();aid=r['account_id'];pid=r['plan_id'];reserve=self.a.isolated_budget(aid,pid)
        budget=Decimal(self.a.auto.row(aid)['budget_cap'])-reserve+1
        with self.assertRaisesRegex(ValueError,'在投预算上限'):self.hybrid.budget_check(r,d,budget)
        i={'account_id':aid,'plan_id':pid,'body':{'budget':str(reserve)}}
        with self.assertRaises(ValueError):self.a.auto.check_budget(i,[],str(reserve*2-1),allow_lookup=False)
        with self.assertRaises(ValueError):self.a._budget_guard({'budget':str(reserve*3)},[],reserve*3,aid,pid)

    def test_new_sweep_and_rule_yield_never_cancel_the_background_original(self):
        c,r,d=self.isolate();aid=r['account_id'];self.hybrid.worker(self.h.worker)
        self.assertTrue(self.hybrid.request(aid,reason='自动恢复其他独立批次'))
        self.assertTrue(self.hybrid.active(aid));self.assertTrue(self.hybrid.read_only_active(aid))
        self.assertTrue(self.hybrid.yield_readonly(aid))
        with self.a.db() as db:
            self.assertEqual(db.execute('SELECT state FROM hybrid_runs WHERE id=?',(r['id'],)).fetchone()[0],'waiting')
        self.assertEqual(len(self.a.status()),1);self.assertEqual(self.a.status()[0]['state'],'unknown');self.assertEqual(self.h.posts,[])

    def test_restart_preserves_original_reservation_and_a_later_receipt_only_closes_original(self):
        c,r,d=self.isolate();self.hybrid=HybridChase(self.a,lambda:self.h.policy);self.a.hybrid=self.hybrid
        self.assertGreater(self.a.isolated_budget(r['account_id'],r['plan_id']),0)
        self.h.at+=timedelta(minutes=5);self.hybrid.worker(self.h.worker)
        verify=self.hybrid.next(self.h.worker)['command'];self.assertEqual(verify['phase'],'verify')
        batch=c['action']['intent']['browser_batch']
        self.hybrid.result({'run_id':verify['run_id'],'id':verify['id'],'lease':verify['lease'],'ok':True,'message':'真实原单回读',
            'observation':self.h.observed(verify,[{'id':'10000009','name':batch['task_name'],'material_ids':batch['material_ids'],'after':batch['after'],'status':'调控中'}],document='independent-new-document')})
        self.assertEqual(self.a.status()[0]['state'],'verified');self.assertEqual(self.a.isolated_budget(r['account_id'],r['plan_id']),0)
        self.assertEqual(self.a.auto.daily(r['account_id']),1)
        with self.a.db() as db:self.assertEqual(db.execute('SELECT state FROM hybrid_runs WHERE id=?',(r['id'],)).fetchone()[0],'completed')
        self.assertEqual(self.h.posts,[])

    def test_invalid_page_cursor_is_reset_without_replaying_original(self):
        c=self.h.ready();p=self.h.begin_web(c);self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':p['submit_token']})
        self.h.at+=timedelta(seconds=2)
        with self.a.db() as db:
            r=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone();d=json.loads(r['data']);d['page']=49;self.hybrid.save(db,r,d)
        verify=self.h.command();self.hybrid.result({'run_id':verify['run_id'],'id':verify['id'],'lease':verify['lease'],'ok':False,'message':'无法翻到指定页','observation':None})
        with self.a.db() as db:
            d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(r['id'],)).fetchone()[0]);self.assertEqual(d['page'],1);self.assertTrue(d['permit']['consumed'])
        self.assertEqual(self.a.status()[0]['state'],'unknown');self.assertEqual(self.a.auto.daily(r['account_id']),1);self.assertEqual(self.h.posts,[])

    def test_a_current_read_lease_is_not_preempted_by_background_isolation(self):
        c=self.h.ready();p=self.h.begin_web(c);self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':p['submit_token']})
        self.h.at+=timedelta(minutes=11);self.h.command();self.hybrid.tick()
        with self.a.db() as db:self.assertFalse(json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()[0]).get('isolated_readback',False))
        self.h.at+=timedelta(minutes=3);self.hybrid.tick()
        self.assertGreater(self.a.isolated_budget(self.h.account['id'],self.h.pid),0)

    def test_rule_fallback_can_start_while_original_chase_remains_unknown(self):
        from monitor.hybrid_rules import HybridRules
        aid=self.h.account['id'];_,revision=self.a.profile(aid)
        self.a.rule_auto.configure({'account_id':aid,'plan_id':self.h.pid,'profile_revision':revision,'features':['f'+str(n) for n in range(1,7)],'enabled':True,'confirmed':True})
        self.a.hybrid_rules=HybridRules(self.a)
        c,r,d=self.isolate()
        self.assertTrue(self.a.hybrid_rules.request(aid,reason='核验异常不阻塞已有任务规则'))
        self.assertTrue(self.a.hybrid_rules.active(aid));self.assertEqual(self.a.status()[0]['state'],'unknown');self.assertEqual(self.h.posts,[])

    def test_missing_receipt_full_list_scan_is_bounded_to_three_pages_per_cycle(self):
        c=self.h.ready();p=self.h.begin_web(c);self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':p['submit_token']});self.h.at+=timedelta(seconds=2)
        for n in range(3):
            command=self.h.command();rows=[{'id':str(20000+n*50+i),'budget':'10','status':'已暂停'} for i in range(50)]
            self.h.feed(command,rows,total=200,page_size=50,document='new-document')
        with self.a.db() as db:
            d=json.loads(db.execute('SELECT data FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone()[0]);self.assertEqual(d['page'],1);self.assertIsNotNone(d['retry_at'])
        self.assertIsNone(self.hybrid.next(self.h.worker)['command']);self.assertEqual(len(self.a.status()),1);self.assertEqual(self.h.posts,[])
