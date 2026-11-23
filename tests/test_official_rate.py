import copy
import json
import tempfile
import threading
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from monitor.official_rate import RateGovernor, RateDeferred, WRITE_PRIORITY, READBACK_PRIORITY, READ_PRIORITY
from monitor.official_api import OfficialAPI, OfficialTransport, OfficialWriteTransport, OfficialBusinessError
from monitor.official_monitoring import config, read_plans
from monitor.schema import now_cn


class RateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.at=1000.;self.path=Path(self.tmp.name)/'rate.db';self.open=[]
    def tearDown(self):
        for g in self.open:g.close()
        self.tmp.cleanup()
    def governor(self,**kw):
        kw.setdefault('jitter',lambda:0)
        g=RateGovernor(self.path,clock=lambda:self.at,sleeper=self.sleep,**kw);self.open.append(g);return g
    def sleep(self,n):self.at+=n

    def test_persistent_shared_window_across_two_clients_and_restart(self):
        first=self.governor();second=self.governor()
        first.acquire('report_data');second.acquire('plans')
        self.assertGreaterEqual(self.at,1002.05)
        third=self.governor();third.acquire('plans')
        self.assertGreaterEqual(self.at,1005.10-1e-6)
        self.assertEqual(third.status()['requests_last_minute'],3)
        self.assertTrue(third.status()['persistent'])

    def test_sliding_minute_has_no_natural_boundary_burst(self):
        g=self.governor();times=[]
        for i in range(60):
            g.acquire('endpoint'+str(i%4));times.append(self.at)
        for at in times:
            self.assertLessEqual(sum(at-60<t<=at for t in times),25)
            self.assertLessEqual(sum(at-1<t<=at for t in times),1)

    def test_penalty_persists_without_inventing_half_hour_server_penalties(self):
        g=self.governor();self.assertEqual(g.trip('plans',40100,'rate_limited'),10)
        resumed=self.governor()
        for name in ('plans','plans'):
            with self.assertRaises(RateDeferred):resumed.acquire(name)
        resumed.acquire('report_data');resumed.acquire('pause_control_task')
        self.at+=11;self.assertEqual(resumed.trip('plans',40100,'rate_limited'),20)
        self.at+=1
        with self.assertRaises(RateDeferred) as caught:resumed.acquire('plans')
        self.assertGreaterEqual(caught.exception.retry_after,19)
        self.assertEqual(caught.exception.origin,'local_cooldown')
        self.assertEqual(resumed.status()['counters']['locally_deferred'],3)
        self.assertEqual([r['endpoint'] for r in resumed.status()['cooldowns']],['plans'])

    def test_migration_caps_only_legacy_local_40100_not_server_retry_after(self):
        g=self.governor()
        with g._connection() as db,db:
            db.execute("INSERT OR REPLACE INTO rate_breakers(endpoint,until_at,last_at,strikes,code,reason,server_until) VALUES ('plans',5000,900,6,40100,'rate_limited',0)")
            db.execute("INSERT OR REPLACE INTO rate_breakers(endpoint,until_at,last_at,strikes,code,reason,server_until) VALUES ('report_data',5000,900,6,429,'rate_limited',5000)")
        restarted=self.governor()
        self.assertFalse(any(x['endpoint']=='plans' for x in restarted.status()['cooldowns']))
        self.assertEqual(next(x for x in restarted.status()['cooldowns'] if x['endpoint']=='report_data')['until'],5000)

    def test_native_error_diagnostics_redact_credentials_and_distinguish_local_wait(self):
        g=self.governor();t=OfficialTransport(governor=g);calls=[]
        class Response:
            headers={'Retry-After':'180'}
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,n):return json.dumps({'code':40100,'message':'每分钟调用过多 paper-token secret-token','request_id':'native-request-123'}).encode()
        class Opener:
            def open(self,*a,**kw):calls.append(1);return Response()
        with patch('monitor.official_api.urllib.request.build_opener',return_value=Opener()):
            with self.assertRaises(OfficialBusinessError) as remote:t('plans',{'advertiser_id':11},'paper-token')
            with self.assertRaises(OfficialBusinessError) as local:t('plans',{'advertiser_id':11},'paper-token')
        self.assertEqual(len(calls),1);self.assertEqual(remote.exception.origin,'platform')
        self.assertEqual(local.exception.origin,'local_cooldown');self.assertIn('本次未请求平台',str(local.exception))
        self.assertEqual(remote.exception.retry_after,300);self.assertIn('每分钟调用过多',str(remote.exception))
        self.assertEqual(remote.exception.limit_kind,'minute_penalty')
        errors=g.status()['last_platform_errors'];self.assertEqual(len(errors),1)
        self.assertEqual(errors[0]['request_id'],'native-request-123')
        self.assertNotIn('paper-token',json.dumps(errors));self.assertNotIn('secret-token',json.dumps(errors))

    def test_read_write_transports_share_budget_and_post_is_never_replayed(self):
        g=self.governor();read=OfficialTransport(governor=g);write=OfficialWriteTransport(governor=g);calls=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,*args):return b'{"code":0,"data":{}}'
        class Opener:
            def open(self,req,**kwargs):calls.append((req.method,g.clock()));return Response()
        with patch('monitor.official_api.urllib.request.build_opener',return_value=Opener()):
            read('report_data',{},'paper-token');write('pause_control_task',{},'paper-token')
        self.assertEqual([c[0] for c in calls],['GET','POST']);self.assertGreaterEqual(calls[1][1]-calls[0][1],2.05-1e-6)
        err=urllib.error.HTTPError('https://api.oceanengine.com',429,'too many',{'Retry-After':'123'},None)
        with patch('monitor.official_api.urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=err
            with self.assertRaises(OfficialBusinessError) as caught:write('pause_control_task',{},'paper-token')
            self.assertEqual(opener.return_value.open.call_count,1)
        self.assertEqual(caught.exception.retry_after,123)
        with self.assertRaises(OfficialBusinessError):read('plans',{},'paper-token')

    def test_queue_wait_releases_database_for_status_and_other_clients(self):
        g=self.governor();other=self.governor();g.acquire('plans');observed=[]
        def sleep(n):
            thread=threading.Thread(target=lambda:observed.append(other.status()))
            thread.start();thread.join(1);self.assertFalse(thread.is_alive());self.at+=n
        g.sleep=sleep;g.acquire('plans');self.assertTrue(observed)

    def test_long_wait_defers_without_reserving_a_stale_write(self):
        g=self.governor(max_wait=1);g.acquire('plans')
        with self.assertRaises(RateDeferred):g.acquire('plans')
        self.assertEqual(g.status()['requests_last_minute'],1)
        self.assertEqual(self.at,1000)

    def test_developer_limit_reduces_future_pacing_not_only_the_cooldown(self):
        g=self.governor();g.trip('plans',40110,'rate_limited');self.at+=61
        g.acquire('plans');first=self.at;g.acquire('plans')
        self.assertGreaterEqual(self.at-first,6.1-1e-6)
        # An hour without a new penalty recovers the conservative base speed.
        self.at+=3601;g.acquire('plans');first=self.at;g.acquire('plans')
        self.assertLess(self.at-first,4)

    def test_cross_client_queue_prioritizes_ready_write_and_receipt_without_waiting_for_a_cooled_endpoint(self):
        producer=self.governor();consumer=self.governor();now=self.at
        producer.trip('plans',40100,'rate_limited')
        with producer._connection() as db,db:
            def add(endpoint,priority):
                return db.execute('INSERT INTO rate_queue(endpoint,priority,queued_at,deadline,live_until) VALUES (?,?,?,?,?)',
                                  (endpoint,priority,now,now+60,now+15)).lastrowid
            add('plans',WRITE_PRIORITY)
            ordinary=add('report_data',READ_PRIORITY)
            original=add('control_tasks',READBACK_PRIORITY)
            writing=add('pause_control_task',WRITE_PRIORITY)
        self.assertIsNone(consumer._attempt(ordinary,'report_data')[0])
        self.assertIsNone(consumer._attempt(original,'control_tasks')[0])
        self.assertIsNotNone(consumer._attempt(writing,'pause_control_task')[0])
        self.at+=2.1
        self.assertIsNotNone(consumer._attempt(original,'control_tasks')[0])
        self.at+=2.1
        self.assertIsNotNone(consumer._attempt(ordinary,'report_data')[0])

    def test_old_reads_gain_priority_and_dead_tickets_do_not_hold_slots(self):
        g=self.governor()
        with g._connection() as db,db:
            def add(endpoint,priority,enqueued,expires):
                return db.execute('INSERT INTO rate_queue(endpoint,priority,queued_at,deadline,live_until) VALUES (?,?,?,?,?)',
                                  (endpoint,priority,enqueued,self.at+60,expires)).lastrowid
            add('plans',WRITE_PRIORITY,self.at-30,self.at-1)
            ordinary=add('report_data',READ_PRIORITY,self.at-20,self.at+1)
            writing=add('pause_control_task',WRITE_PRIORITY,self.at,self.at+1)
        self.assertIsNone(g._attempt(writing,'pause_control_task')[0])
        self.assertIsNotNone(g._attempt(ordinary,'report_data')[0])
        self.assertEqual(g.status()['queue_pending'],1)

    def test_telemetry_contains_no_request_or_credential_contents(self):
        g=self.governor();r=g.acquire('report_data');g.outcome(r,'success')
        text=json.dumps(g.status());self.assertNotIn('token',text);self.assertNotIn('advertiser',text)
        self.assertEqual(g.status()['counters']['success'],1)


class ReadReductionTests(unittest.TestCase):
    def setUp(self):
        import test_official_workflow as fixtures
        self.f=fixtures.WorkflowTests('runTest');self.f.setUp()
    def tearDown(self):self.f.tearDown()

    def test_config_cache_is_scoped_copied_and_expires(self):
        f=self.f;first=config(f.api,f.account['id'],'OVERALL_ROI_LIVE_MATERIAL_VIDEO','chengfang','paper-token')
        first['metrics']=[]
        second=config(f.api,f.account['id'],'OVERALL_ROI_LIVE_MATERIAL_VIDEO','chengfang','paper-token')
        self.assertTrue(second['metrics']);self.assertEqual(sum(n=='report_config' for n,q in f.calls),1)
        config(f.api,'22','OVERALL_ROI_LIVE_MATERIAL_VIDEO','chengfang','paper-token')
        config(f.api,f.account['id'],'OVERALL_ROI_LIVE_MATERIAL_VIDEO','chengfang','another-paper-token')
        self.assertEqual(sum(n=='report_config' for n,q in f.calls),3)
        f.api.clock=lambda:10**12
        config(f.api,f.account['id'],'OVERALL_ROI_LIVE_MATERIAL_VIDEO','chengfang','paper-token')
        self.assertEqual(sum(n=='report_config' for n,q in f.calls),4)

    def test_repeated_scan_reuses_schema_and_identity_but_preflight_rechecks(self):
        f=self.f;first=f.scanned();counts={n:sum(a==n for a,q in f.calls) for n in ('report_config','plan_materials')}
        second=f.scanned()
        self.assertEqual(counts,{n:sum(a==n for a,q in f.calls) for n in counts})
        review=f.queue.prepare({'account_id':f.account['id'],'plan_id':f.pid,'action':'material_chase','batch_key':second['batches'][0]['key']})
        real=f.api.transport
        def changed(name,q,token=None):
            r=real(name,q,token)
            if name=='plan_materials':r['ad_material_infos'][0]['audit_status']='REJECT'
            return r
        f.api.transport=changed
        with self.assertRaisesRegex(ValueError,'审核'):f.confirm(review)
        self.assertEqual(f.posts,[])

    def test_configuration_preflight_uses_one_native_family_and_no_reports(self):
        f=self.f;before=copy.deepcopy(f.api.plan_readings);calls=[]
        def native(name,q,token=None):
            calls.append((name,q));self.assertEqual(name,'plans')
            return {'ad_list':[{'ad_info':{'id':int(f.pid),'name':'fresh','adlab_scene':q['adlab_scene'],
                'marketing_goal':q['marketing_goal'],'smart_bid_type':q['filtering']['smart_bid_type'],
                'budget':9999,'opt_status':'ENABLE','status':'DELIVERY_OK','deep_external_action':'AD_CONVERT_TYPE_LIVE_PAY_ROI'},
                'room_info':[{'anchor_id':f.uid}]}],'page_info':{'total_num':1}}
        f.api.transport=native
        r=read_plans(f.api,f.account,'g',configuration={'plan_id':f.pid,'delivery_entry':'chengfang','smart_bid_type':'SMART_BID_CUSTOM'})
        self.assertEqual(len(calls),1);self.assertEqual(r['configuration']['budget'],9999)
        self.assertTrue(all(v is None for v in r['configuration']['metrics'].values()))
        self.assertEqual(f.api.plan_readings,before)
        with self.assertRaises(ValueError):read_plans(f.api,f.account,'g',configuration={'plan_id':'999','delivery_entry':'chengfang','smart_bid_type':'SMART_BID_CUSTOM'})
        from datetime import timedelta
        from monitor.official_workflow import fresh_parent
        f.plan['captured_at']=(now_cn()-timedelta(hours=1)).isoformat()
        f.api.configuration_readings={}
        old=copy.deepcopy(f.api.plan_readings);count=len(calls)
        f.api.read_plans=OfficialAPI.read_plans.__get__(f.api)
        self.assertEqual(fresh_parent(f.api,f.account,f.pid)['budget'],9999)
        self.assertEqual(len(calls),count+1);self.assertEqual(f.api.plan_readings,old)
        self.assertEqual(fresh_parent(f.api,f.account,f.pid)['budget'],9999)
        self.assertEqual(len(calls),count+1);self.assertEqual(f.api.configuration_cache_hits,1)
        # Submission explicitly asks for a new configuration and does not use
        # the short read-only cache. A changed budget must be visible there.
        read_plans(f.api,f.account,'g',configuration={'plan_id':f.pid,'delivery_entry':'chengfang','smart_bid_type':'SMART_BID_CUSTOM'})
        self.assertEqual(len(calls),count+2)

    def test_same_day_lifetime_uses_verified_native_coverage_but_cross_day_needs_its_own_read(self):
        from datetime import timedelta
        from monitor.official_workflow import low_spend_tasks
        f=self.f;at=now_cn().replace(hour=12,minute=0,second=0,microsecond=0)
        f.profile['features']['f4'].update(enabled=True,pause_rule='low_spend',low_spend_hours=2,low_spend_max=20)
        t={'id':'10001','scene':'MATERIAL_ADD_BUDGET','create_time':(at-timedelta(hours=3)).isoformat(),
            'metrics':{'spend':19},'metrics_window':{'start_at':at.replace(hour=0).isoformat(),'end_at':at.isoformat()}}
        f.api.transport=lambda *a:(_ for _ in ()).throw(AssertionError('same-day coverage must not reread'))
        result=low_spend_tasks(f.api,f.account,f.plan,'paper-token',at,[t],f.profile)
        self.assertEqual(result[0]['metrics']['lifetime_spend'],19)
        t=copy.deepcopy(t);t.pop('lifetime_spend_window');t['metrics']={'spend':0};t['create_time']=(at-timedelta(hours=27)).isoformat();reads=[]
        def prior(name,q,token=None):
            reads.append(q);return {'task_list':[{'id':10001,'create_time':t['create_time'],'task_status':'PROCESSING','metrics':{'stat_cost_for_roi2_assist':55}}],'page_info':{'total_number':1}}
        f.api.transport=prior
        result=low_spend_tasks(f.api,f.account,f.plan,'paper-token',at,[t],f.profile)
        self.assertEqual(len(reads),1);self.assertEqual(result[0]['metrics']['lifetime_spend'],55)
        self.assertEqual(reads[0]['start_time'],t['create_time'][:10]+' 00:00:00')

    def test_cumulative_rate_error_propagates_instead_of_looking_like_no_candidate(self):
        from datetime import timedelta
        from monitor.official_workflow import low_spend_tasks
        f=self.f;at=now_cn();f.profile['features']['f4'].update(enabled=True,pause_rule='low_spend',low_spend_hours=2,low_spend_max=20)
        task={'id':'10001','scene':'MATERIAL_ADD_BUDGET','create_time':(at-timedelta(days=1)).isoformat(),'metrics':{'spend':0}}
        f.api.transport=lambda *a:(_ for _ in ()).throw(OfficialBusinessError(40100,operation='control_tasks',reason='rate_limited'))
        with self.assertRaises(OfficialBusinessError):low_spend_tasks(f.api,f.account,f.plan,'paper-token',at,[task],f.profile)
        self.assertNotIn('lifetime_spend',task['metrics'])

    def test_pause_preflight_reads_only_its_exact_task_and_keeps_independent_readback(self):
        f=self.f;f.scanned();r=f.queue.prepare({'account_id':f.account['id'],'plan_id':f.pid,'action':'pause_task','task_id':'10001'})
        f.calls.clear();self.assertEqual(f.confirm(r)['state'],'verified')
        reads=[q for n,q in f.calls if n=='control_tasks']
        self.assertEqual(len(reads),2)
        self.assertTrue(all(q['filtering'].get('search_keyword')=='10001' and q['scene']=='MATERIAL_ADD_BUDGET' for q in reads))
        self.assertEqual(f.posts[0]['task_ids'],[10001])

    def test_full_plan_read_saves_native_configuration_even_if_later_report_rate_limits(self):
        from datetime import timedelta
        from monitor.official_workflow import fresh_parent
        f=self.f;f.api.configuration_readings={}
        f.plan['captured_at']=(now_cn()-timedelta(hours=1)).isoformat()
        before_plans=copy.deepcopy(f.api.plan_readings)
        calls=[]
        def partial(name,q,token=None):
            calls.append(name)
            if name=='plans':
                if q.get('adlab_scene')=='OVERALL_PROJECT' and q.get('filtering',{}).get('smart_bid_type')=='SMART_BID_CUSTOM':
                    return {'ad_list':[{'ad_info':{'id':int(f.pid),'name':'fresh-before-report','adlab_scene':'OVERALL_PROJECT',
                        'marketing_goal':q['marketing_goal'],'smart_bid_type':'SMART_BID_CUSTOM',
                        'budget':8888,'opt_status':'ENABLE','status':'DELIVERY_OK','deep_external_action':'AD_CONVERT_TYPE_LIVE_PAY_ROI'},
                        'room_info':[{'anchor_id':f.uid}]}],'page_info':{'total_num':1}}
                return {'ad_list':[],'page_info':{'total_num':0}}
            raise OfficialBusinessError(40100,operation=name,reason='rate_limited',retry_after=40)
        f.api.transport=partial
        with self.assertRaises(OfficialBusinessError):
            read_plans(f.api,f.account,'g',background=True)
        # plan_readings must not publish partial metrics, yet configuration_readings
        # preserved the freshly read native plan config for fresh_parent reuse.
        self.assertEqual(f.api.plan_readings,before_plans)
        plan_calls_before=calls.count('plans')
        reused=fresh_parent(f.api,f.account,f.pid)
        self.assertEqual(reused['budget'],8888)
        self.assertEqual(calls.count('plans'),plan_calls_before)
        self.assertEqual(f.api.configuration_cache_hits,1)

    def test_repeated_platform_40100_steps_pacing_up_to_24s_and_recovers_after_5m(self):
        at=[1000.0]
        g=RateGovernor(clock=lambda:at[0],sleeper=lambda n:at.__setitem__(0,at[0]+n),jitter=lambda:0)
        for _ in range(3):
            g.trip('plans',40100,'rate_limited')
            at[0]+=45
        g.acquire('plans');first=at[0];g.acquire('plans')
        self.assertGreaterEqual(at[0]-first,24.4-1e-6)
        at[0]+=301;g.acquire('plans');first=at[0];g.acquire('plans')
        self.assertLess(at[0]-first,4)


if __name__=='__main__':unittest.main()
