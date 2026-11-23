import copy
import json
import shutil
import sqlite3
import tempfile
import threading
import unittest
from datetime import timedelta
import urllib.error
import urllib.request
from pathlib import Path

from monitor.ai import make_package, validate_ai_config, validate_proposals
from monitor.demo import scenario
from monitor.engine import evaluate
from monitor.legacy import inspect_legacy
from monitor.schema import normalize_snapshot, validate_settings
from monitor.server import App, Handler, Server, ROOT


def config():
    return json.loads((ROOT/'config.json').read_text('utf-8'))


class IngestionTests(unittest.TestCase):
    def setUp(self):
        self.cfg = config()
        self.rows, self.sim_cfg, self.now = scenario(self.cfg)
        self.row = copy.deepcopy(self.rows[-1])

    def test_account_and_url_must_both_match_allowlist(self):
        self.row['account_id'] = '999'
        with self.assertRaises(ValueError): normalize_snapshot(self.row, self.cfg, self.now)
        self.row['account_id'] = self.cfg['accounts'][0]['id']
        self.row['source']['url'] = 'https://qianchuan.jinritemai.com/uni-prom?aavid=999'
        with self.assertRaises(ValueError): normalize_snapshot(self.row, self.cfg, self.now)

    def test_browsers_cannot_claim_guarantee_verification(self):
        self.row['source']['kind'] = 'browser_dom'
        self.row['source']['url'] = 'https://qianchuan.jinritemai.com/uni-prom?aavid='+self.row['account_id']
        value = normalize_snapshot(self.row, self.cfg, self.now)
        self.assertFalse(value['control']['guarantee_verified'])
        self.assertEqual(value['source']['coverage'], 'visible_only')

    def test_nonfinite_and_boolean_metrics_rejected(self):
        for bad in (True, float('nan'), float('inf'), -1, '0'):
            self.row['metrics']['spend'] = bad
            with self.assertRaises(ValueError): normalize_snapshot(self.row, self.cfg, self.now)

    def test_missing_is_distinct_from_zero_and_sensitive_fields_dropped(self):
        self.row['metrics'].update(balance=0, clicks=None, token='secret')
        self.row['raw_html'] = 'cookie=secret'
        self.row['source']['url'] = f"https://qianchuan.jinritemai.com/uni-prom?aavid={self.row['account_id']}&token=secret#cookie"
        value = normalize_snapshot(self.row, self.cfg, self.now)
        self.assertEqual(value['metrics']['balance'], 0)
        self.assertIsNone(value['metrics']['clicks'])
        self.assertNotIn('secret', json.dumps(value))

    def test_strategy_cannot_remove_cooldown_or_increase_step_cap(self):
        for values in ({'cooldown_minutes': 1}, {'budget_step_up': .8}, {'min_orders': None}):
            with self.assertRaises(ValueError): validate_settings({'rules': values})

    def test_report_identity_is_preserved_and_unknown_imports_do_not_join(self):
        one = copy.deepcopy(self.row); two = copy.deepcopy(self.row)
        one['source']['kind'] = two['source']['kind'] = 'import'
        one['source']['dataset_id'] = 'separate-a'
        two['source']['dataset_id'] = 'separate-b'
        a = normalize_snapshot(one, self.cfg, self.now)
        b = normalize_snapshot(two, self.cfg, self.now)
        self.assertNotEqual(a['source']['series_key'], b['source']['series_key'])
        del one['source']['dataset_id']
        a = normalize_snapshot(one, self.cfg, self.now)
        b = normalize_snapshot(one, self.cfg, self.now)
        self.assertNotEqual(a['source']['series_key'], b['source']['series_key'])

    def test_live_session_never_enters_daily_budget_rules(self):
        rows = copy.deepcopy(self.rows)
        for row in rows:
            row['source']['metric_period'] = 'live_session'
            row['source']['period_start'] = rows[0]['source']['captured_at']
        result = evaluate(rows, self.sim_cfg, self.now)
        self.assertEqual(result['recommendations'], [])
        self.assertTrue(all(x['quality'] != 'fresh' for x in result['entities']))

    def test_ai_payload_has_no_real_identifiers_or_source_urls(self):
        evaluation = evaluate(self.rows, self.sim_cfg, self.now)
        package = make_package(self.rows, evaluation)
        text = json.dumps(package)
        self.assertNotIn(self.row['account_id'], text)
        self.assertNotIn('demo-plan', text)
        self.assertNotIn('qianchuan.jinritemai', text)
        self.assertEqual(package['metrics'][0]['metric_period'],'day')
        self.assertIn('period_start',package['metrics'][0])
        self.assertIn('funnel_created_orders',package['metric_guide'])

    def test_ai_cannot_expand_action_or_use_new_object(self):
        evaluation = evaluate(self.rows, self.sim_cfg, self.now)
        rec = evaluation['recommendations'][0]
        response = {'analysis':'test', 'proposals':[
            {'recommendation_id':rec['id'], 'change_pct':-.05},
            {'recommendation_id':rec['id'], 'change_pct':-.5},
            {'recommendation_id':rec['id'], 'change_pct':.1},
            {'recommendation_id':'new-id', 'change_pct':-.1}]}
        validated = validate_proposals(response, evaluation)
        self.assertEqual(len(validated['accepted_for_simulation']), 1)
        self.assertEqual(len(validated['rejected']), 3)
        self.assertFalse(validated['executed'])
        self.assertEqual(validated['accepted_for_simulation'][0]['mode'], 'simulation_only')

    def test_ai_blocks_local_rejected_and_bad_endpoint(self):
        evaluation = evaluate(self.rows, self.sim_cfg, self.now)
        rec = evaluation['recommendations'][0]
        rec['eligible'] = False
        response = {'analysis':'test', 'proposals':[{'recommendation_id':rec['id'],'change_pct':-.05}]}
        self.assertEqual(len(validate_proposals(response, evaluation)['rejected']), 1)
        for endpoint in ('http://example.com/api', 'https://user:secret@example.com/api', 'https://example.com/api?key=secret'):
            with self.assertRaises(ValueError): validate_ai_config({'endpoint':endpoint})

    def test_ai_duplicate_recommendations_cannot_multiply_adjustments(self):
        result = evaluate(self.rows, self.sim_cfg, self.now)
        rec = result['recommendations'][0]
        proposal = {'recommendation_id':rec['id'], 'change_pct':rec['change_pct']}
        validated = validate_proposals({'analysis':'test','proposals':[proposal,proposal]}, result)
        self.assertEqual(len(validated['accepted_for_simulation']), 1)
        self.assertEqual(len(validated['rejected']), 1)

    def test_ai_keeps_report_series_separate_and_links_recommendations(self):
        rows = copy.deepcopy(self.rows)
        duplicate = copy.deepcopy(rows[-1])
        duplicate['source']['series_key'] = 'other-report'
        rows.append(duplicate)
        result = evaluate(rows, self.sim_cfg, self.now)
        package = make_package(rows, result)
        self.assertEqual(len(package['metrics']), 2)
        linked = {r['series'] for r in package['local_recommendations']}
        self.assertTrue(linked <= {m['series'] for m in package['metrics']})


class LocalServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        shutil.copyfile(ROOT/'config.json', self.root/'config.json')
        self.app = App(self.root, 8877)
        self.server = Server(('127.0.0.1', 0), Handler)
        self.app.port = self.server.server_address[1]
        self.server.app = self.app
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def request(self, path, body=None, headers=None):
        headers = headers or {}
        if body is not None:
            headers['Content-Type'] = 'application/json'
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(f'http://127.0.0.1:{self.app.port}{path}', data=data, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.load(response)
        except urllib.error.HTTPError as error:
            return error.code, json.load(error)

    def test_authenticated_ingestion_and_unknown_write_routes(self):
        # Even a valid pairing token cannot access an account execution route.
        code, _ = self.request('/api/ingest', {'snapshots':[]})
        self.assertEqual(code, 403)
        code, result = self.request('/api/runtime/start', {}, {'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code, 404)
        self.assertIn('没有账户执行接口', result['error'])

    def test_status_and_report_separate_collector_success_from_data_availability(self):
        from unittest.mock import patch
        with patch.object(self.app,'refresh_legacy',return_value={'added':0}):self.app.poll_once()
        code,s=self.request('/api/status');self.assertEqual(code,200)
        self.assertEqual(s['monitor_health']['collector']['state'],'ok')
        self.assertEqual(s['snapshot_count'],0);self.assertEqual(s['evaluation']['collection_health']['accounts'][0]['state'],'missing')
        aid=self.app.cfg()['accounts'][0]['id'];code,r=self.request('/api/report?account_id='+aid)
        self.assertEqual(code,200);self.assertEqual(len(r['evidence_and_risks']['collection_health']['accounts']),1)
        self.assertEqual(r['evidence_and_risks']['monitor_runtime']['history']['successful_cycles'],1)
        self.assertFalse(r['execution_available']);self.assertEqual(self.app.store.events(),[])

    def test_heartbeat_is_not_an_observed_business_snapshot(self):
        code,_=self.request('/api/heartbeat',{'tab_count':3,'issues':[]},{'X-Monitor-Token':self.app.token})
        self.assertEqual(code,200);code,s=self.request('/api/status');self.assertEqual(code,200)
        self.assertEqual(s['monitor_health']['browser_communication']['state'],'recent')
        self.assertEqual(s['snapshot_count'],0);self.assertEqual(s['evaluation']['collection_health']['accounts'][0]['state'],'missing')

    def test_cross_origin_and_rebinding_requests_rejected(self):
        code, _ = self.request('/api/pairing', headers={'Origin':'https://evil.invalid'})
        self.assertEqual(code, 403)
        code, _ = self.request('/api/status', headers={'Host':'evil.invalid'})
        self.assertEqual(code, 403)
        code, _ = self.request('/api/settings', {}, {'Origin':'https://evil.invalid','X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code, 403)

    def test_simulations_never_pollute_production_snapshots(self):
        code, result = self.request('/api/simulate', {'scenario':'low_roi'}, {'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code, 200)
        self.assertTrue(result['synthetic'])
        self.assertTrue(result['evaluation']['recommendations'][0]['eligible'])
        self.assertEqual(self.app.store.snapshots(), [])
        self.assertFalse(result['executed'])

    def test_outcome_replay_is_synthetic_and_never_records_a_real_applied_event(self):
        code,result=self.request('/api/simulate',{'scenario':'bid_drop_recovery'},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertTrue(result['synthetic'])
        review=result['evaluation']['outcome_review']
        self.assertEqual(review['items'][0]['review_status'],'observed')
        self.assertEqual(review['summary']['simulated'],1)
        self.assertEqual(review['summary']['platform_confirmed_applied'],0)
        self.assertFalse(review['candidates'][0]['execution_eligible'])
        self.assertEqual(self.app.store.snapshots(),[]);self.assertEqual(self.app.store.events(),[])

    def test_action_preview_accepts_only_current_local_recommendations(self):
        code, _ = self.request('/api/action-preview', {'recommendation_id':'invented'}, {'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,400)
        code, _ = self.request('/api/action-preview', {'recommendation_id':'invented'})
        self.assertEqual(code,403)
        code, _ = self.request('/api/action-preview', {'action':'budget_up','after':100000}, {'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,400)
        self.assertEqual(self.app.store.snapshots(),[])

    def test_creative_share_survives_normalized_import_and_account_report_without_actions(self):
        from unittest.mock import patch
        from test_creative_share import fixture
        from test_engine import NOW
        rows,cfg=fixture()
        self.app.settings=cfg
        for row in rows:row['source']['dataset_id']='creative-share-http-test'
        parent=normalize_snapshot(rows[0],cfg,NOW)
        rows[1]['context']['parent_source_key']=parent['source']['series_key']
        auth={'X-Monitor-CSRF':self.app.csrf}
        with patch('monitor.server.now_cn',return_value=NOW),patch('monitor.catalog.now_cn',return_value=NOW):
            code,result=self.request('/api/import',{'snapshots':rows},auth)
            self.assertEqual(code,200);self.assertEqual(result['added'],2)
            code,status=self.request('/api/status')
            self.assertEqual(code,200);self.assertFalse(status['execution_available'])
            item=next(o for o in status['evaluation']['monitoring_analysis']['entities'] if o['scope']=='creative')
            self.assertEqual(item['creative_spend_share']['share_percent'],25)
            metric=next(m for m in status['metric_coverage']['accounts'][0]['metrics'] if m['field']=='creative_spend_share')
            self.assertEqual(metric['data_status'],'fresh');self.assertFalse(metric['supported_input'])
            code,report=self.request('/api/report?account_id=123')
            self.assertEqual(code,200)
            self.assertEqual(report['evidence_and_risks']['creative_spend_shares'][0]['share_percent'],25)
            self.assertFalse(report['executed'])
        self.assertEqual(self.app.store.events(),[]);self.assertEqual(self.app.store.migrations(),[])

    def test_budget_handover_replay_does_not_persist_workflows_or_reports(self):
        code,data=self.request('/api/simulate',{'scenario':'budget_migration'},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertFalse(data['persisted']);self.assertFalse(data['executed'])
        self.assertEqual(data['evaluation']['budget_migrations'][0]['state'],'completed_reported')
        self.assertEqual(self.app.store.migrations(),[]);self.assertEqual(self.app.store.events(),[])
        self.assertEqual(self.app.store.snapshots(),[])

    def test_unmanaged_service_and_report_do_not_invent_supervisor_recovery(self):
        code,status=self.request('/api/status')
        self.assertEqual(code,200)
        evidence=status['monitor_health']['supervision']
        self.assertEqual(evidence['state'],'not_managed')
        self.assertFalse(evidence['current_process_supervised'])
        self.assertFalse(status['execution_available'])
        aid=self.app.cfg()['accounts'][0]['id']
        code,report=self.request('/api/report?account_id='+aid)
        self.assertEqual(code,200)
        self.assertFalse(report['evidence_and_risks']['monitor_runtime']['supervision']['manager_lock_held'])
        self.assertFalse(report['executed'])

    def test_collector_policy_is_token_protected_and_does_not_expose_targets_or_ai(self):
        code,_=self.request('/api/collector-config')
        self.assertEqual(code,403)
        code,p=self.request('/api/collector-config',headers={'X-Monitor-Token':self.app.token,'Origin':'chrome-extension://unit'})
        self.assertEqual(code,200);self.assertEqual(p['protocol'],2)
        self.assertNotIn('target_roi',json.dumps(p));self.assertNotIn('endpoint',json.dumps(p))
        code,_=self.request('/api/collector-config',headers={'X-Monitor-Token':self.app.token,'Origin':'https://evil.example'})
        self.assertEqual(code,403)

    def test_explicit_account_registry_preserves_targets_and_rejects_stale_changes(self):
        from monitor.accounts import collection_policy
        auth={'X-Monitor-CSRF':self.app.csrf}
        code,view=self.request('/api/accounts');self.assertEqual(code,200)
        new={'id':'303','name':'第三个直播户','scene':'live','tab_group':'另一组','observe':True}
        body={'expected_revision':view['revision'],'accounts':view['accounts']+[new]}
        code,_=self.request('/api/accounts',body,auth);self.assertEqual(code,400)
        policy=collection_policy(self.app.cfg())
        code,_=self.request('/api/heartbeat',{'collector_protocol':2,'policy_revision':policy['revision'],'tab_count':0,'issues':[]},{'X-Monitor-Token':self.app.token})
        self.assertEqual(code,200)
        code,result=self.request('/api/accounts',body,auth);self.assertEqual(code,200)
        self.assertEqual(len(self.app.cfg()['accounts']),3);self.assertIsNone(self.app.cfg()['accounts'][-1]['target_roi'])
        original=copy.deepcopy(self.app.cfg());code,_=self.request('/api/accounts',body,auth)
        self.assertEqual(code,400);self.assertEqual(self.app.cfg(),original)

    def test_two_registered_live_accounts_with_same_plan_id_keep_separate_reports(self):
        from monitor.accounts import collection_policy
        from monitor.demo import scenario
        from monitor.schema import now_cn
        cfg=self.app.cfg();policy=collection_policy(cfg)
        self.app.heartbeat({'collector_protocol':2,'policy_revision':policy['revision']})
        view=self.app.account_registry()
        self.app.save_account_registry({'expected_revision':view['revision'],'accounts':view['accounts']+[{'id':'303','name':'第三直播户','scene':'live','tab_group':'另一组','observe':True}]})
        rows,_,_=scenario(self.app.cfg(),'stable_growth');original=rows[-1];batch=[];at=now_cn()
        for aid in [cfg['accounts'][0]['id'],'303']:
            row=copy.deepcopy(original);row.update(account_id=aid,entity_id='same-plan',scope='plan')
            row['source']['url']='https://qianchuan.jinritemai.com/uni-prom?aavid='+aid
            row['source'].update(captured_at=at.isoformat(),data_at=at.isoformat(),metric_date=at.date().isoformat())
            batch.append(row)
        current=collection_policy(self.app.cfg())
        code,result=self.request('/api/ingest',{'snapshots':batch,'collector_protocol':2,'policy_revision':current['revision']},{'X-Monitor-Token':self.app.token})
        self.assertEqual(code,200,result);self.assertEqual(result['added'],2)
        for aid in [cfg['accounts'][0]['id'],'303']:
            code,report=self.request('/api/report?account_id='+aid)
            self.assertEqual(code,200);self.assertIsNone(report['account']['target_roi'])
            self.assertEqual({s['account_id'] for s in report['evidence_and_risks']['sources']},{aid})
            self.assertEqual(report['evidence_summary']['source_count'],1)
        before=len(self.app.store.snapshots());invalid=copy.deepcopy(batch);invalid[-1]['account_id']='404'
        code,_=self.request('/api/ingest',{'snapshots':invalid,'collector_protocol':2,'policy_revision':current['revision']},{'X-Monitor-Token':self.app.token})
        self.assertEqual(code,400);self.assertEqual(len(self.app.store.snapshots()),before)

    def test_removed_account_is_hidden_from_status_and_history_without_deleting_source_rows(self):
        aid=self.app.cfg()['accounts'][0]['id']
        from monitor.demo import scenario
        rows,_,_=scenario(self.app.cfg(),'stable_growth')
        self.app.store.add(rows)
        from test_migration import fixture
        _,_,_,plan=fixture();plan['account_id']=aid;self.app.store.add_migration(plan)
        stored=len(self.app.store.snapshots());self.assertGreater(stored,1)
        self.app.settings['accounts']=[a for a in self.app.cfg()['accounts'] if a['id']!=aid]
        code,status=self.request('/api/status');self.assertEqual(code,200);self.assertEqual(status['latest'],[])
        self.assertEqual(status['evaluation']['budget_migrations'],[])
        code,history=self.request('/api/history');self.assertEqual(code,200);self.assertEqual(history['snapshots'],[])
        self.assertEqual(len(self.app.store.snapshots()),stored)
        self.assertEqual(len(self.app.store.migrations()),1)

    def test_local_handover_create_reserve_cancel_without_platform_requests(self):
        from unittest.mock import patch
        from test_migration import fixture
        from test_engine import NOW
        rows,cfg,_,_=fixture();account=self.app.cfg()['accounts'][0]['id']
        for row in rows:row['account_id']=account
        self.app.settings['accounts'][0].update(target_roi=2,roi_basis='overall_payment')
        self.app.store.add(rows)
        from monitor.demo import synthetic_maintenance_history
        _,refresh=synthetic_maintenance_history([r for r in rows if r['entity_id']=='old'])
        self.app.store.add_events([refresh])
        auth={'X-Monitor-CSRF':self.app.csrf}
        with patch('monitor.server.now_cn',return_value=NOW),patch('monitor.store.now_cn',return_value=NOW+timedelta(days=7)),patch('urllib.request.OpenerDirector.open') as network:
            # Direct App call leaves the HTTP test client out of the network mock.
            data=self.app.migration_plan({'source_snapshot_id':'s18','target_snapshot_id':'t18'})
            network.assert_not_called()
            self.assertTrue(data['persisted'],data);self.assertFalse(data['executed'])
            self.assertEqual(len(self.app.store.migrations()),1)
            status=self.app.status();self.assertFalse(status['execution_available'])
            self.assertEqual(status['evaluation']['budget_migrations'][0]['state'],'ready_reduce')
        code,data=self.request('/api/migration-cancel',{'id':data['plan']['id']},auth)
        self.assertEqual(code,200);self.assertFalse(data['executed']);self.assertTrue(self.app.store.migrations()[0]['cancelled'])
        self.assertEqual(len(self.app.store.snapshots()),38);self.assertEqual(len(self.app.store.events()),1)

    def test_handover_requires_local_auth_and_submission_route_stays_absent(self):
        code,_=self.request('/api/migration-plan',{'source_snapshot_id':'s','target_snapshot_id':'t'})
        self.assertEqual(code,403)
        code,_=self.request('/api/budget-submit',{'approved':True},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,404)

    def test_account_report_is_readonly_scoped_and_does_not_export_tokens(self):
        rows,_,_=scenario(self.app.cfg());self.app.store.add(rows)
        account=rows[-1]['account_id'];code,data=self.request('/api/report?account_id='+account)
        self.assertEqual(code,200);self.assertEqual(data['account']['id'],account)
        self.assertFalse(data['execution_available']);self.assertNotIn(self.app.token,json.dumps(data))
        other=self.app.cfg()['accounts'][1]['id'];code,data=self.request('/api/report?account_id='+other)
        self.assertEqual(code,200);self.assertEqual(data['evidence_summary']['source_count'],0)
        self.assertEqual(self.app.store.events(),[]);self.assertEqual(self.app.store.migrations(),[])

    def test_markdown_report_has_ordered_sections_and_correct_media_type(self):
        account=self.app.cfg()['accounts'][0]['id']
        with urllib.request.urlopen(f'http://127.0.0.1:{self.app.port}/api/report?account_id={account}&format=markdown') as response:
            self.assertIn('text/markdown',response.headers['Content-Type']);text=response.read().decode('utf-8')
        self.assertLess(text.index('## 核心结论'),text.index('## 分级行动指令'))
        self.assertLess(text.index('## 分级行动指令'),text.index('## 数据佐证与风险提示'))
        self.assertIn('未提交账户操作',text)

    def test_report_rejects_unknown_account_format_and_cross_origin(self):
        self.assertEqual(self.request('/api/report?account_id=999')[0],400)
        account=self.app.cfg()['accounts'][0]['id']
        self.assertEqual(self.request('/api/report?account_id='+account+'&format=html')[0],400)
        self.assertEqual(self.request('/api/report?account_id='+account,headers={'Origin':'https://evil.invalid'})[0],403)

    def test_emergency_replay_cannot_persist_or_submit_account_actions(self):
        code,data=self.request('/api/simulate',{'scenario':'emergency_stop'},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertTrue(data['synthetic']);self.assertFalse(data['persisted']);self.assertFalse(data['executed'])
        self.assertTrue(any(a['rule']=='primary_spend_stop' and a['priority']=='P0' for a in data['evaluation']['alerts']))
        self.assertFalse(any(r['eligible'] and r['action']=='budget_up' for r in data['evaluation']['recommendations']))
        self.assertEqual(self.app.store.snapshots(),[]);self.assertEqual(self.app.store.events(),[]);self.assertEqual(self.app.store.migrations(),[])

    def test_quality_replay_is_independent_and_blocks_incremental_growth(self):
        original=self.app.cfg()['accounts']
        code,data=self.request('/api/simulate',{'scenario':'traffic_quality'},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertTrue(data['synthetic']);self.assertFalse(data['persisted']);self.assertFalse(data['executed'])
        self.assertTrue(any(a['rule']=='traffic_quality_decline' and a['priority']=='P1' for a in data['evaluation']['alerts']))
        self.assertFalse(any(r['eligible'] and r['action']=='budget_up' for r in data['evaluation']['recommendations']))
        self.assertEqual(self.app.cfg()['accounts'],original)
        self.assertEqual(self.app.store.snapshots(),[]);self.assertEqual(self.app.store.events(),[]);self.assertEqual(self.app.store.migrations(),[])

    def test_maintenance_replay_crosschecks_sequence_without_persisting_reports(self):
        cfg=copy.deepcopy(self.app.cfg())
        code,data=self.request('/api/simulate',{'scenario':'decline_maintenance'},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertTrue(data['synthetic']);self.assertFalse(data['persisted']);self.assertFalse(data['executed'])
        review=data['evaluation']['maintenance_review'][0]
        self.assertEqual(review['state'],'handover_ready');self.assertTrue(review['refresh_verified_locally'])
        self.assertEqual(data['evaluation']['outcome_review']['items'][0]['review_status'],'maintenance_review')
        self.assertEqual(self.app.store.snapshots(),[]);self.assertEqual(self.app.store.events(),[]);self.assertEqual(self.app.store.migrations(),[])
        self.assertEqual(self.app.cfg(),cfg)

    def test_live_exclusion_replay_blocks_growth_and_does_not_submit_pause(self):
        cfg=copy.deepcopy(self.app.cfg())
        code,data=self.request('/api/simulate',{'scenario':'live_exclusion'},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertTrue(data['synthetic']);self.assertFalse(data['persisted']);self.assertFalse(data['executed'])
        e=data['evaluation'];self.assertEqual(len(e['live_exclusion_reviews']),2)
        self.assertTrue(any(a['rule']=='live_universal_standard_conflict' for a in e['alerts']))
        self.assertTrue(any(r['action']=='pause_plan' for r in e['recommendations']))
        self.assertFalse(any(r['eligible'] for r in e['recommendations'] if r['action'] in {'pause_plan','budget_up'}))
        self.assertEqual(self.app.cfg(),cfg);self.assertEqual(self.app.store.snapshots(),[])
        self.assertEqual(self.app.store.events(),[]);self.assertEqual(self.app.store.migrations(),[])

    def test_roi_recovery_replay_crosschecks_report_chain_without_persistence(self):
        cfg=copy.deepcopy(self.app.cfg())
        code,data=self.request('/api/simulate',{'scenario':'roi_recovery'},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertTrue(data['synthetic']);self.assertFalse(data['persisted']);self.assertFalse(data['executed'])
        item=data['evaluation']['roi_recovery_reviews'][0]
        self.assertEqual(item['state'],'ready_return_step');self.assertEqual(len(item['step_history']),2)
        rec=next(r for r in data['evaluation']['recommendations'] if r['rule']=='roi_recovery_step')
        self.assertTrue(rec['eligible']);self.assertEqual((rec['before'],rec['after']),(3.31,3.5))
        self.assertEqual(rec['evidence']['strategy_target_roi'],2.5)
        self.assertEqual(self.app.cfg(),cfg);self.assertEqual(self.app.store.snapshots(),[])
        self.assertEqual(self.app.store.events(),[]);self.assertEqual(self.app.store.migrations(),[])

    def test_cross_day_roi_replay_uses_separate_ledgers_without_persistence(self):
        cfg=copy.deepcopy(self.app.cfg())
        code,result=self.request('/api/simulate',{'scenario':'roi_recovery_cross_day'},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertFalse(result['persisted']);self.assertFalse(result['executed']);self.assertTrue(result['synthetic'])
        item=result['evaluation']['roi_recovery_reviews'][0]
        self.assertEqual(item['state'],'ready_return_step');self.assertTrue(item['cross_day']);self.assertEqual(len(item['daily_boundaries']),1)
        self.assertEqual(item['reported_changes_on_current_day'],1)
        self.assertEqual(item['step_history'][0]['first_hour']['ad_spend'],180)
        self.assertEqual(len(item['step_history'][0]['first_hour']['segments']),2)
        self.assertEqual(self.app.cfg(),cfg);self.assertEqual(self.app.store.snapshots(),[]);self.assertEqual(self.app.store.events(),[])

    def test_product_carrying_replay_holds_only_mapped_plan_and_does_not_write(self):
        cfg=copy.deepcopy(self.app.cfg())
        code,data=self.request('/api/simulate',{'scenario':'product_carrying'},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertTrue(data['synthetic']);self.assertFalse(data['persisted']);self.assertFalse(data['executed'])
        carrying=data['evaluation']['product_carrying'];inventory=carrying['inventory'][0]
        self.assertEqual(inventory['units_per_hour'],24);self.assertEqual(inventory['stock_coverage_hours'],.5)
        self.assertEqual(carrying['plans'][0]['alignment_state'],'category_mismatch')
        budgets={r['entity_id']:r for r in data['evaluation']['recommendations'] if r['action']=='budget_up'}
        self.assertFalse(budgets['demo-plan']['eligible']);self.assertTrue(budgets['demo-unrelated-plan']['eligible'])
        self.assertFalse(any(r['eligible'] for r in data['evaluation']['recommendations'] if r['action']=='review_product_mix'))
        self.assertEqual(self.app.cfg(),cfg);self.assertEqual(self.app.store.snapshots(),[]);self.assertEqual(self.app.store.events(),[])

    def test_cross_day_manual_import_preserves_daily_declarations_and_report_links(self):
        from unittest.mock import patch
        from monitor.demo import roi_recovery_cross_day_scenario
        rows,cfg,at,events=roi_recovery_cross_day_scenario(self.app.cfg());self.app.settings=cfg
        for row in rows:row['source'].update(kind='import',dataset_id='cross-day-import-test')
        auth={'X-Monitor-CSRF':self.app.csrf}
        with patch('monitor.server.now_cn',return_value=at),patch('monitor.schema.now_cn',return_value=at),patch('monitor.events.now_cn',return_value=at),patch('monitor.store.now_cn',return_value=at):
            code,result=self.request('/api/import',{'snapshots':rows},auth)
            self.assertEqual(code,200);self.assertEqual(result['added'],29)
            normalized=self.app.store.snapshots();key=normalized[-1]['source']['series_key']
            self.assertEqual(sum(r['context'].get('day_boundary_verified') is True for r in normalized),2)
            for event in events:event.update(source='manual_verified_report',status='reported_applied',source_key=key)
            code,result=self.request('/api/event-import',{'events':events},auth)
            self.assertEqual(code,200);self.assertEqual(result['added'],2)
            code,status=self.request('/api/status');self.assertEqual(code,200)
            item=status['evaluation']['roi_recovery_reviews'][0]
            self.assertEqual(item['state'],'ready_return_step');self.assertEqual(len(item['daily_boundaries']),1)
            self.assertTrue(next(r for r in status['evaluation']['recommendations'] if r['rule']=='roi_recovery_step')['eligible'])
            self.assertFalse(status['execution_available']);self.assertEqual(self.app.store.migrations(),[])

    def test_roi_recovery_links_survive_local_snapshot_and_event_imports(self):
        from unittest.mock import patch
        from monitor.demo import roi_recovery_scenario
        rows,cfg,at,events=roi_recovery_scenario(self.app.cfg())
        self.app.settings=cfg
        for row in rows:row['source'].update(kind='import',dataset_id='recovery-test')
        auth={'X-Monitor-CSRF':self.app.csrf}
        with patch('monitor.server.now_cn',return_value=at),patch('monitor.schema.now_cn',return_value=at),patch('monitor.events.now_cn',return_value=at):
            code,result=self.request('/api/import',{'snapshots':rows},auth)
            self.assertEqual(code,200);self.assertEqual(result['added'],28)
            source_key=self.app.store.snapshots()[-1]['source']['series_key']
            for event in events:event.update(source='manual_verified_report',status='reported_applied',source_key=source_key)
            code,result=self.request('/api/event-import',{'events':events},auth)
            self.assertEqual(code,200);self.assertEqual(result['added'],2)
            self.assertEqual(next(e for e in self.app.store.events() if e['action']=='roi_target_up')['roi_recovery_root_id'],'demo-roi-root')
            code,status=self.request('/api/status');self.assertEqual(code,200)
            self.assertEqual(status['evaluation']['roi_recovery_reviews'][0]['state'],'ready_return_step')
            rec=next(r for r in status['evaluation']['recommendations'] if r['rule']=='roi_recovery_step')
            self.assertTrue(rec['eligible']);self.assertEqual(rec['after'],3.5)
            self.assertFalse(status['execution_available']);self.assertEqual(self.app.store.migrations(),[])

    def test_cold_start_replay_keeps_growth_caps_and_production_configuration(self):
        cfg=copy.deepcopy(self.app.cfg())
        code,data=self.request('/api/simulate',{'scenario':'cold_start'},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertTrue(data['synthetic']);self.assertFalse(data['persisted']);self.assertFalse(data['executed'])
        rec=next(r for r in data['evaluation']['recommendations'] if r['action']=='budget_up')
        self.assertTrue(rec['eligible']);self.assertEqual(rec['before'],300);self.assertEqual(rec['after'],360)
        self.assertEqual(data['evaluation']['lifecycle_reviews'][0]['state'],'cold_ready')
        self.assertEqual(self.app.cfg(),cfg);self.assertEqual(self.app.store.snapshots(),[])
        self.assertEqual(self.app.store.events(),[]);self.assertEqual(self.app.store.migrations(),[])

    def test_roi_tuning_replay_keeps_current_plan_goal_separate_without_persistence(self):
        cfg=copy.deepcopy(self.app.cfg())
        code,data=self.request('/api/simulate',{'scenario':'roi_tuning'},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertTrue(data['synthetic']);self.assertFalse(data['persisted']);self.assertFalse(data['executed'])
        recs={r['action']:r for r in data['evaluation']['recommendations'] if r['action'].startswith('roi_target_')}
        self.assertEqual(recs['roi_target_up']['before'],3.5);self.assertEqual(recs['roi_target_up']['after'],3.68)
        self.assertEqual(recs['roi_target_down']['after'],3.32)
        self.assertEqual(recs['roi_target_down']['evidence']['strategy_target_roi'],2.5)
        self.assertTrue(recs['roi_target_down']['eligible'])
        self.assertEqual(self.app.cfg(),cfg);self.assertEqual(self.app.store.snapshots(),[])
        self.assertEqual(self.app.store.events(),[]);self.assertEqual(self.app.store.migrations(),[])

    def test_creative_idle_replay_has_closed_daily_ledgers_and_exact_pause_without_persistence(self):
        cfg=copy.deepcopy(self.app.cfg())
        code,data=self.request('/api/simulate',{'scenario':'creative_idle'},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertTrue(data['synthetic']);self.assertFalse(data['persisted']);self.assertFalse(data['executed'])
        rec=next(r for r in data['evaluation']['recommendations'] if r['action']=='pause_creative')
        self.assertTrue(rec['eligible']);self.assertIs(rec['before'],True);self.assertIs(rec['after'],False)
        self.assertEqual(rec['evidence']['observed_seconds'],86400);self.assertEqual(rec['evidence']['spend_delta'],0)
        self.assertEqual(rec['parameters']['parent_plan_id'],'demo-parent-plan');self.assertFalse(rec['evidence']['platform_verified'])
        self.assertEqual(self.app.cfg(),cfg);self.assertEqual(self.app.store.snapshots(),[])
        self.assertEqual(self.app.store.events(),[]);self.assertEqual(self.app.store.migrations(),[])

    def test_creative_rotation_replay_is_synthetic_has_no_live_urls_or_persistence(self):
        cfg=copy.deepcopy(self.app.cfg())
        code,data=self.request('/api/simulate',{'scenario':'creative_rotation'},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertTrue(data['synthetic']);self.assertFalse(data['persisted'])
        d=data['creative_rotation']['drafts'][0];self.assertTrue(d['local_eligible'],d['blockers'])
        self.assertTrue(d['synthetic_only']);self.assertFalse(d['executed'])
        self.assertTrue(all(r['url'] is None for r in d['requests'].values()))
        self.assertEqual(self.app.cfg(),cfg);self.assertEqual(self.app.store.snapshots(),[])
        self.assertEqual(self.app.store.events(),[]);self.assertEqual(self.app.store.migrations(),[])

    def test_creative_rotation_file_preview_recomputes_actual_current_snapshot_evaluation(self):
        from test_creative_rotation import fixture
        from test_engine import NOW
        from unittest.mock import patch
        rows,_,document=fixture()
        account=next(a for a in self.app.cfg()['accounts'] if a['scene']=='product')['id']
        for row in rows:row['account_id']=account
        document['plans'][0]['account_id']=account;document['duplicate_inventory'][0]['account_id']=account;self.app.store.add(rows)
        before=len(self.app.store.snapshots())
        with patch('monitor.server.now_cn',return_value=NOW):
            code,out=self.request('/api/creative-rotation-preview',{'document_text':json.dumps(document)},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertTrue(out['drafts'][0]['local_eligible'],out['drafts'][0]['blockers'])
        self.assertFalse(out['persisted']);self.assertEqual(len(self.app.store.snapshots()),before)
        self.assertEqual(self.app.store.events(),[]);self.assertEqual(self.app.store.migrations(),[])

    def test_creative_rotation_preview_rejects_missing_auth_foreign_origin_and_credentials(self):
        from test_creative_rotation import fixture
        _,_,document=fixture()
        self.assertEqual(self.request('/api/creative-rotation-preview',document)[0],403)
        self.assertEqual(self.request('/api/creative-rotation-preview',document,{'X-Monitor-CSRF':self.app.csrf,'Origin':'https://evil.invalid'})[0],403)
        document['Authorization']='PRIVATE_ROTATION_TOKEN'
        code,out=self.request('/api/creative-rotation-preview',document,{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,400);self.assertNotIn('PRIVATE_ROTATION_TOKEN',json.dumps(out))

    def test_rotation_registry_and_execution_routes_are_absent(self):
        for path in ('/api/creative-rotation-import','/api/creative-rotation-submit','/api/creative-rotation-execute'):
            self.assertEqual(self.request(path,{}, {'X-Monitor-CSRF':self.app.csrf})[0],404)

    def test_reading_empty_workflow_list_does_not_create_workflow_storage(self):
        code,out=self.request('/api/creative-workflows')
        self.assertEqual(code,200);self.assertEqual(out['workflows'],[])
        self.assertFalse(out['native_material_submission_enabled'])
        self.assertFalse((self.root/'data/creative-workflows.db').exists())

    def test_workflow_replay_uses_temporary_durable_storage_without_real_workflow_rows(self):
        code,out=self.request('/api/simulate',{'scenario':'creative_workflow'},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);w=out['creative_workflow']
        self.assertEqual(w['workflow']['state'],'completed_synthetic');self.assertEqual(w['synthetic_calls'],['add','retire'])
        self.assertTrue(w['temporary_durable_storage_verified']);self.assertTrue(w['reopened_before_every_step'])
        self.assertFalse(w['executed']);self.assertEqual(self.app.store.snapshots(),[])
        self.assertFalse((self.root/'data/creative-workflows.db').exists())
        self.assertEqual(self.app.store.events(),[]);self.assertEqual(self.app.store.migrations(),[])

    def test_workflow_create_recomputes_current_intent_then_saves_and_cancels_local_only(self):
        from test_creative_rotation import fixture
        from test_engine import NOW
        from unittest.mock import patch
        rows,_,document=fixture();account=next(a for a in self.app.cfg()['accounts'] if a['scene']=='product')['id']
        for r in rows:r['account_id']=account;r['source']['kind']='import'
        document['plans'][0]['account_id']=account;document['duplicate_inventory'][0]['account_id']=account
        self.app.store.add(rows);body={'document_text':json.dumps(document)};auth={'X-Monitor-CSRF':self.app.csrf}
        with patch('monitor.server.now_cn',return_value=NOW):
            code,preview=self.request('/api/creative-rotation-preview',body,auth)
            self.assertEqual(code,200);d=preview['drafts'][0];self.assertTrue(d['local_eligible'],d['blockers'])
            create={**body,'account_id':account,'plan_id':'2001','intent_digest':d['intent_digest']}
            code,out=self.request('/api/creative-workflow-create',create,auth)
            self.assertEqual(code,200);self.assertEqual(out['workflow']['state'],'awaiting_native_integration')
            self.assertFalse(out['executed']);workflow_id=out['workflow']['id']
            self.assertEqual(self.request('/api/creative-workflow-create',create,auth)[1]['workflow']['id'],workflow_id)
            listed=self.request('/api/creative-workflows')[1]['workflows'];self.assertEqual(len(listed),1)
            self.assertEqual(listed[0]['journal'][0]['event'],'created')
            code,cancelled=self.request('/api/creative-workflow-cancel',{'id':workflow_id},auth)
            self.assertEqual(code,200);self.assertFalse(cancelled['workflow']['parent_reservation_held'])
        self.assertEqual(len(self.app.store.snapshots()),len(rows));self.assertEqual(self.app.store.events(),[])
        self.assertEqual(self.app.store.migrations(),[])

    def test_workflow_create_rejects_forged_intent_unqualified_data_and_extra_flags(self):
        from test_creative_rotation import fixture
        from test_engine import NOW
        from unittest.mock import patch
        _,_,document=fixture();account=next(a for a in self.app.cfg()['accounts'] if a['scene']=='product')['id']
        document['plans'][0]['account_id']=account;document['duplicate_inventory'][0]['account_id']=account
        body={'document_text':json.dumps(document),'account_id':account,'plan_id':'2001','intent_digest':'a'*64}
        with patch('monitor.server.now_cn',return_value=NOW):
            self.assertEqual(self.request('/api/creative-workflow-create',body,{'X-Monitor-CSRF':self.app.csrf})[0],400)
            body['enabled']=True
            self.assertEqual(self.request('/api/creative-workflow-create',body,{'X-Monitor-CSRF':self.app.csrf})[0],400)
        self.assertFalse((self.root/'data/creative-workflows.db').exists())

    def test_workflow_mutations_require_csrf_and_reject_foreign_origin(self):
        for path in ('create','cancel'):
            uri='/api/creative-workflow-'+path
            self.assertEqual(self.request(uri,{})[0],403)
            self.assertEqual(self.request(uri,{}, {'X-Monitor-CSRF':self.app.csrf,'Origin':'https://evil.invalid'})[0],403)
        self.assertEqual(self.request('/api/creative-workflows',headers={'Origin':'https://evil.invalid'})[0],403)

    def test_workflow_execution_routes_remain_absent(self):
        for path in ('drive','execute','submit','unlock','reconcile'):
            self.assertEqual(self.request('/api/creative-workflow-'+path,{}, {'X-Monitor-CSRF':self.app.csrf})[0],404)

    def test_material_review_compares_native_payload_without_persisting_or_releasing_state(self):
        from test_material_readback import fixture
        from test_engine import NOW
        from unittest.mock import patch
        payload,_=fixture();account=next(a for a in self.app.cfg()['accounts'] if a['scene']=='product')['id']
        payload['query']['account_id']=account;before=len(self.app.store.audit_rows())
        with patch('monitor.material_readback.now_cn',return_value=NOW):
            code,out=self.request('/api/material-readback-review',{'document_text':json.dumps(payload)},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertEqual(out['configuration']['state'],'present')
        self.assertEqual(out['material_observations'][0]['material_id'],'8012367229158357')
        self.assertFalse(out['can_release_hold']);self.assertFalse(out['persisted'])
        self.assertEqual(self.app.store.snapshots(),[]);self.assertEqual(self.app.store.events(),[])
        self.assertEqual(len(self.app.store.audit_rows()),before);self.assertFalse((self.root/'data/creative-workflows.db').exists())

    def test_material_review_rejects_credentials_missing_csrf_and_cross_site_requests(self):
        from test_material_readback import fixture
        payload,_=fixture()
        self.assertEqual(self.request('/api/material-readback-review',payload)[0],403)
        self.assertEqual(self.request('/api/material-readback-review',payload,{'X-Monitor-CSRF':self.app.csrf,'Origin':'https://evil.invalid'})[0],403)
        payload['Access-Token']='PRIVATE_NATIVE_VALUE'
        code,out=self.request('/api/material-readback-review',payload,{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,400);self.assertNotIn('PRIVATE_NATIVE_VALUE',json.dumps(out))

    def test_native_material_fetch_or_unlock_routes_do_not_exist(self):
        for suffix in ('fetch','read','submit','unlock'):
            self.assertEqual(self.request('/api/material-'+suffix,{}, {'X-Monitor-CSRF':self.app.csrf})[0],404)

    def test_control_creation_replay_uses_fictitious_account_without_request_url_or_persistence(self):
        cfg=copy.deepcopy(self.app.cfg())
        code,out=self.request('/api/simulate',{'scenario':'control_creation'},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);d=out['control_creation'];self.assertTrue(d['synthetic'])
        self.assertEqual(d['account_id'],'9990001');self.assertIsNone(d['request']['url'])
        self.assertIn('8012367229158357',d['request']['body_json']);self.assertFalse(d['native_client_enabled'])
        self.assertFalse(d['executed']);self.assertFalse(d['persisted']);self.assertEqual(self.app.cfg(),cfg)
        self.assertEqual(self.app.store.snapshots(),[]);self.assertEqual(self.app.store.events(),[])

    def test_current_full_action_preview_prepares_boost_but_has_no_submission_route(self):
        from test_control_creation import fixture
        from unittest.mock import patch
        rows,cfg,at,_,_=fixture();account=next(a for a in self.app.cfg()['accounts'] if a['scene']=='live')
        account.update(target_roi=2);self.app.settings['accounts']=[account]
        for row in rows:row['account_id']=account['id']
        self.app.store.add(rows)
        with patch('monitor.server.now_cn',return_value=at):
            _,data=self.request('/api/status');p=next(p for p in data['evaluation']['recommendations'] if p['action']=='one_click_boost')
            self.assertTrue(p['eligible'])
            code,out=self.request('/api/action-preview',{'recommendation_id':p['id']},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertTrue(out['supported_for_preview'],out['blockers'])
        self.assertFalse(out['live_execution_allowed']);self.assertFalse(out['request']['submitted'])
        self.assertEqual(len(self.app.store.snapshots()),len(rows));self.assertEqual(self.app.store.events(),[])
        with self.app.store.connect() as db:self.assertFalse(db.execute("SELECT name FROM sqlite_master WHERE name='submission_attempts'").fetchall())

    def test_control_creation_native_routes_are_not_available_even_with_local_csrf(self):
        for path in ('/api/control-create','/api/control-creation-submit','/api/control-creation-enable','/api/control-creation-unlock'):
            self.assertEqual(self.request(path,{}, {'X-Monitor-CSRF':self.app.csrf})[0],404)

    def test_official_report_preview_is_pure_and_import_requires_same_digest(self):
        from test_official_reports import payload
        account=self.app.cfg()['accounts'][0]['id'];document=payload(account)
        body={'document_text':json.dumps(document)};auth={'X-Monitor-CSRF':self.app.csrf}
        audits=len(self.app.store.audit_rows())
        code,preview=self.request('/api/official-report-preview',body,auth)
        self.assertEqual(code,200);self.assertFalse(preview['persisted']);self.assertEqual(self.app.store.snapshots(),[])
        self.assertEqual(len(self.app.store.audit_rows()),audits)
        body['preview_digest']=preview['preview_digest']
        code,result=self.request('/api/official-report-import',body,auth)
        self.assertEqual(code,200);self.assertEqual(result['added'],1);self.assertFalse(result['raw_response_saved'])
        rows=self.app.store.snapshots();self.assertEqual(len(rows),1)
        text=json.dumps(rows);self.assertNotIn('PRIVATE_REQUEST_ID',text);self.assertNotIn('not persisted',text)
        self.assertNotIn('gmv',rows[0]['metrics']);self.assertFalse(rows[0]['control']['history_known'])
        code,result=self.request('/api/official-report-import',body,auth)
        self.assertEqual(code,200);self.assertEqual(result['added'],0)
        self.assertEqual(self.app.store.events(),[]);self.assertEqual(self.app.store.migrations(),[])

    def test_native_custom_report_review_keeps_raw_precision_without_storage_or_ai(self):
        from test_custom_reports import fixture
        account=next(a for a in self.app.cfg()['accounts'] if a['scene']=='product')['id']
        document=fixture(account)
        raw=json.dumps(document).replace('"opaque_amount": 0','"opaque_amount": 0.1234567890123456789012345')
        before=len(self.app.store.audit_rows())
        code,out=self.request('/api/custom-report-review',{'document_text':raw},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertEqual(out['account_id'],account)
        self.assertIn('0.1234567890123456789012345',out['rows'][0]['cells']['test_amount'])
        self.assertFalse(out['persisted']);self.assertFalse(out['ai_sent']);self.assertFalse(out['executed'])
        self.assertEqual(self.app.store.snapshots(),[]);self.assertEqual(self.app.store.events(),[])
        self.assertEqual(len(self.app.store.audit_rows()),before)

    def test_native_custom_report_review_requires_local_auth_and_rejects_credentials(self):
        from test_custom_reports import fixture
        account=next(a for a in self.app.cfg()['accounts'] if a['scene']=='product')['id']
        document=fixture(account)
        self.assertEqual(self.request('/api/custom-report-review',document)[0],403)
        auth={'X-Monitor-CSRF':self.app.csrf,'Origin':'https://evil.invalid'}
        self.assertEqual(self.request('/api/custom-report-review',document,auth)[0],403)
        document['pages'][0]['response']['Access-Token']='PRIVATE_CREDENTIAL'
        code,out=self.request('/api/custom-report-review',document,{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,400);self.assertNotIn('PRIVATE_CREDENTIAL',json.dumps(out))
        self.assertEqual(self.app.store.snapshots(),[])

    def test_native_custom_report_has_no_import_or_execution_route(self):
        auth={'X-Monitor-CSRF':self.app.csrf}
        for path in ('/api/custom-report-import','/api/custom-report-execute'):
            self.assertEqual(self.request(path,{},auth)[0],404)

    def test_official_report_rejects_tampering_and_invalid_batch_without_storage(self):
        from test_official_reports import payload
        document=payload(self.app.cfg()['accounts'][0]['id']);auth={'X-Monitor-CSRF':self.app.csrf}
        code,preview=self.request('/api/official-report-preview',document,auth);self.assertEqual(code,200)
        for digest in ('汉'*64,'g'*64,True,None):
            self.assertEqual(self.request('/api/official-report-import',{**document,'preview_digest':digest},auth)[0],400)
        document['preview_digest']=preview['preview_digest'];document['reports'][0]['response']['data']['stat_cost_for_roi2']=101
        self.assertEqual(self.request('/api/official-report-import',document,auth)[0],400)
        document=payload(self.app.cfg()['accounts'][0]['id']);invalid=copy.deepcopy(document['reports'][0]);invalid['query']['advertiser_id']='999'
        document['reports'].append(invalid)
        self.assertEqual(self.request('/api/official-report-preview',document,auth)[0],400)
        document=payload(self.app.cfg()['accounts'][0]['id'])
        document['reports'][0]['response']['data']['stat_cost_for_roi2']=10**1000
        self.assertEqual(self.request('/api/official-report-preview',document,auth)[0],400)
        self.assertEqual(self.app.store.snapshots(),[])

    def test_official_report_has_no_network_or_external_permission_path(self):
        from test_official_reports import payload
        from unittest.mock import patch
        document=payload(self.app.cfg()['accounts'][0]['id']);before=copy.deepcopy(self.app.cfg())
        with patch('urllib.request.OpenerDirector.open') as network:
            preview=self.app.official_report(document)
            self.app.official_report({**document,'preview_digest':preview['preview_digest']},persist=True)
            network.assert_not_called()
        self.assertEqual(self.app.cfg(),before);self.assertEqual(self.app.store.events(),[])
        raw=payload(self.app.cfg()['accounts'][0]['id']);raw['reports'][0]['response']['data']['access_token']='PRIVATE_SECRET'
        code,error=self.request('/api/official-report-preview',raw,{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,400);self.assertNotIn('PRIVATE_SECRET',json.dumps(error))

    def test_official_report_requires_local_auth_and_rejects_duplicate_raw_json_fields(self):
        from test_official_reports import payload
        document=payload(self.app.cfg()['accounts'][0]['id'])
        self.assertEqual(self.request('/api/official-report-preview',document)[0],403)
        self.assertEqual(self.request('/api/official-report-import',document,{'X-Monitor-CSRF':self.app.csrf,'Origin':'https://evil.invalid'})[0],403)
        self.assertEqual(self.request('/api/official-report-import',document,{'X-Monitor-CSRF':self.app.csrf})[0],400)
        body=json.dumps(document).replace('"code": 0','"code": 0, "code": 1').encode()
        req=urllib.request.Request(f'http://127.0.0.1:{self.app.port}/api/official-report-preview',data=body,
            headers={'Content-Type':'application/json','X-Monitor-CSRF':self.app.csrf})
        with self.assertRaises(urllib.error.HTTPError) as caught:urllib.request.urlopen(req)
        self.assertEqual(caught.exception.code,400);self.assertEqual(self.app.store.snapshots(),[])

    def test_history_queries_preserve_series_and_account_boundary(self):
        rows,_,_=scenario(self.app.cfg())
        row=rows[-1];other=copy.deepcopy(row)
        other['id']='other';other['source']['series_key']='different-report'
        self.app.store.add([row,other])
        code,data=self.request('/api/history?series_key=different-report&account_id='+row['account_id'])
        self.assertEqual(code,200);self.assertEqual([r['id'] for r in data['snapshots']],['other'])
        self.assertEqual(data['window_hours'],28)
        code,_=self.request('/api/history?account_id=999')
        self.assertEqual(code,400)

    def test_operation_reports_are_local_records_and_never_commands(self):
        from test_events import event
        value=event();value['account_id']=self.app.cfg()['accounts'][0]['id']
        code,_=self.request('/api/event-import',{'events':[value]})
        self.assertEqual(code,403)
        code,data=self.request('/api/event-import',{'events':[value]},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,200);self.assertFalse(data['executed'])
        self.assertEqual(self.app.store.snapshots(),[])
        self.assertEqual(self.app.store.events()[0]['status'],'reported_applied')
        self.assertFalse(self.app.cfg()['accounts'][0]['target_roi'])

    def test_mixed_report_and_snapshot_import_is_rejected_before_writing(self):
        code,_=self.request('/api/event-import',{'events':[{}],'snapshots':[]},{'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code,400);self.assertEqual(self.app.store.events(),[])

    def test_web_settings_cannot_expand_account_scope(self):
        before = (self.root/'config.json').read_bytes()
        code, _ = self.request('/api/settings', {'accounts':[{'id':'999','target_roi':2}]}, {'X-Monitor-CSRF':self.app.csrf})
        self.assertEqual(code, 400)
        self.assertEqual(before, (self.root/'config.json').read_bytes())

    def test_missing_legacy_source_does_not_create_or_modify_original(self):
        cfg = self.app.cfg(); cfg['legacy_root'] = str(self.root/'not-created')
        rows, diagnostic = inspect_legacy(cfg)
        self.assertEqual(rows, [])
        self.assertEqual(diagnostic['state'], 'missing')
        self.assertFalse((self.root/'not-created').exists())


if __name__ == '__main__':
    unittest.main()
