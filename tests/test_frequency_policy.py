import copy
import json
import unittest
from datetime import datetime,timedelta,timezone
from unittest.mock import patch
from tests import test_official_rate as rate_fixtures
from tests import test_official_monitoring as monitor_fixtures
from tests import test_schedule_responsiveness as schedule_fixtures
from monitor.official_api import OfficialTransport,OfficialWriteTransport,OfficialBusinessError
from monitor.official_monitoring import plan_scope
from monitor.schema import parse_time


class OfficialLimitPolicyTests(unittest.TestCase):
    setUp=rate_fixtures.RateTests.setUp
    tearDown=rate_fixtures.RateTests.tearDown
    governor=rate_fixtures.RateTests.governor
    sleep=rate_fixtures.RateTests.sleep

    def test_official_code_types_and_only_explicit_penalties(self):
        self.assertEqual(OfficialTransport._limit_kind(40100,'系统请求频率超限'),'platform_total')
        self.assertEqual(OfficialTransport._limit_kind(40110,'请求超频'),'developer_qps')
        self.assertEqual(OfficialTransport._limit_kind(40110,'调用分钟频次超限'),'minute_penalty')
        self.assertEqual(OfficialTransport._limit_kind(40110,'调用天频次超限'),'day_penalty')
        self.assertEqual(OfficialTransport._limit_kind(40000,'每分钟请求参数'),'unknown')

    def test_minute_and_day_penalties_respect_five_minutes_and_beijing_midnight(self):
        g=self.governor();self.assertEqual(g.trip('plans',40110,'rate_limited',limit_kind='minute_penalty'),300)
        self.at+=299
        from monitor.official_rate import RateDeferred
        with self.assertRaises(RateDeferred):g.acquire('plans')
        g.acquire('report_data')
        self.at=datetime(2026,10,28,15,59,30,tzinfo=timezone.utc).timestamp()
        self.assertEqual(g.trip('plans',40110,'rate_limited',limit_kind='day_penalty'),30)
        self.at+=31;g.acquire('plans')
        self.assertFalse(g.status()['cooldowns'])

    def test_global_quota_never_blocks_unrelated_endpoint_or_replays_post(self):
        g=self.governor();t=OfficialWriteTransport(governor=g);calls=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,n):return b'{"code":40100,"message":"platform busy","request_id":"test-global"}'
        class Opener:
            def open(self,*a,**kw):calls.append(1);return Response()
        with patch('monitor.official_api.urllib.request.build_opener',return_value=Opener()):
            with self.assertRaises(OfficialBusinessError) as caught:t('pause_control_task',{},'paper-token')
        self.assertEqual(len(calls),1);self.assertEqual(caught.exception.limit_kind,'platform_total')
        g.acquire('account_report');self.assertEqual(g.status()['counters']['platform_total_limited'],1)

    def test_developer_40110_is_not_retried_as_dynamic_global_quota(self):
        g=self.governor();calls=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*a):pass
            def read(self,n):return b'{"code":40110,"message":"developer quota"}'
        class Opener:
            def open(self,*a,**kw):calls.append(1);return Response()
        with patch('monitor.official_api.urllib.request.build_opener',return_value=Opener()):
            with self.assertRaises(OfficialBusinessError) as caught:OfficialTransport(governor=g)('plans',{})
        self.assertEqual(caught.exception.limit_kind,'developer_qps');self.assertEqual(len(calls),1)


class EmptyDiscoveryTests(unittest.TestCase):
    setUp=monitor_fixtures.OfficialMonitoringTests.setUp
    tearDown=monitor_fixtures.OfficialMonitoringTests.tearDown
    plan=monitor_fixtures.OfficialMonitoringTests.plan
    transport=monitor_fixtures.OfficialMonitoringTests.transport

    def query(self):return {'advertiser_id':11,'marketing_goal':'LIVE_PROM_GOODS','adlab_scene':'UNI_PROJECT','filtering':{'smart_bid_type':'SMART_BID_CUSTOM'}}
    def test_only_complete_empty_scopes_are_reused_and_manual_or_preflight_bypasses(self):
        calls=[]
        def empty(*a):calls.append(1);return {'ad_list':[],'page_info':{'total_num':0}}
        self.api.transport=empty;q=self.query()
        plan_scope(self.api,q,'g','not-live','2026-10-29',background=True)
        _,source=plan_scope(self.api,q,'g','not-live','2026-10-29',background=True)
        self.assertTrue(source['reused']);self.assertEqual(len(calls),1)
        plan_scope(self.api,q,'g','not-live','2026-10-29',background=False)
        self.assertEqual(len(calls),2)
        plan_scope(self.api,q,'other-grant','not-live','2026-10-29',background=True)
        plan_scope(self.api,{**q,'advertiser_id':22},'g','not-live','2026-10-29',background=True)
        plan_scope(self.api,q,'g','not-live','2026-10-30',background=True)
        self.assertEqual(len(calls),5)
        self.api.clock=lambda:11000
        plan_scope(self.api,q,'g','not-live','2026-10-30',background=True)
        self.assertEqual(len(calls),6)

    def test_invalid_or_failed_empty_response_is_never_cached(self):
        self.api.transport=lambda *a:{'ad_list':[],'page_info':{}}
        with self.assertRaises(ValueError):plan_scope(self.api,self.query(),'g','not-live','2026-10-29',background=True)
        self.assertEqual(self.api.empty_plan_scopes,{})

    def test_background_reports_refresh_while_plan_list_is_limited(self):
        from monitor.schema import now_cn
        calls=[];self.api.clock=lambda:10000
        self.api.read_account=lambda *a:calls.append('account')
        def blocked(*a,**kw):calls.append('plans');raise OfficialBusinessError(40100,operation='plans',reason='rate_limited',retry_after=20)
        self.api.read_plans=blocked
        out=self.api.poll_next([self.account])
        self.assertEqual(out['state'],'partial');self.assertEqual(calls,['account','plans'])
        self.assertEqual(out['components']['account']['state'],'ok');self.assertIn('账户指标已更新',out['error'])
        self.api.clock=lambda:10021
        self.api.poll_next([self.account]);self.assertEqual(calls,['account','plans','plans'])


class ScheduleQuotaTests(unittest.TestCase):
    setUp=schedule_fixtures.ScheduleResponsivenessTests.setUp
    tearDown=schedule_fixtures.ScheduleResponsivenessTests.tearDown
    transport=schedule_fixtures.ScheduleResponsivenessTests.transport
    payload=schedule_fixtures.ScheduleResponsivenessTests.payload

    def confirmed(self):
        r=self.queue.prepare(self.payload());self.queue.confirm({'id':r['id'],'intent_digest':r['intent_digest'],'confirmed':True})
        self.at=parse_time(r['intent']['after']['trigger_at']);self.queue.clock=lambda:self.at
        return r

    def test_due_job_only_reads_exact_configuration_and_boost_not_full_account(self):
        r=self.confirmed();reads=[]
        self.plan['captured_at']=(self.at-timedelta(days=1)).isoformat()
        def fresh(account,gid,*,configuration=None,**kw):
            self.assertIsNotNone(configuration);reads.append(configuration)
            p=copy.deepcopy(self.plan);p['captured_at']=self.at.isoformat()
            return {'configuration':p}
        self.api.read_plans=fresh
        self.queue.schedules.tick()
        self.assertEqual(len(reads),1);self.assertEqual(len(self.posts),1)
        self.assertEqual(self.queue.schedules.status()[0]['state'],'active_acknowledged')
        self.assertTrue(all(q['scene']=='SMART_BOOST' for n,q in self.calls if n=='control_tasks'))

    def test_preflight_rate_limit_retries_within_confirmed_window_without_post(self):
        r=self.confirmed();original=self.api.read_plans;reads=[]
        def transient(*a,**kw):
            reads.append(1)
            if len(reads)==1:raise OfficialBusinessError(40100,operation='plans',reason='rate_limited',retry_after=15)
            return original(*a,**kw)
        self.api.read_plans=transient;self.queue.schedules.tick()
        self.assertEqual(self.posts,[]);self.assertEqual(self.queue.schedules.status()[0]['state'],'scheduled')
        self.queue.schedules.tick();self.assertEqual(len(reads),1)
        self.at+=timedelta(seconds=16);self.queue.schedules.tick()
        self.assertEqual(len(self.posts),1);self.assertEqual(self.queue.schedules.status()[0]['state'],'active_acknowledged')

    def test_long_penalty_does_not_create_outside_confirmed_window(self):
        self.confirmed()
        self.api.read_plans=lambda *a,**kw:(_ for _ in ()).throw(OfficialBusinessError(40110,reason='rate_limited',retry_after=300))
        self.queue.schedules.tick();self.assertEqual(self.posts,[])
        self.assertEqual(self.queue.schedules.status()[0]['state'],'blocked')

    def test_writer_local_admission_failure_is_unsent_and_uncertain_post_is_not_replayed(self):
        self.confirmed();writer=self.queue.writer;attempts=[]
        def local(name,q,token=None):
            attempts.append(1)
            if len(attempts)==1:raise OfficialBusinessError(40100,reason='rate_limited',retry_after=10,origin='local_cooldown')
            return writer(name,q,token)
        self.queue.writer=local;self.queue.schedules.tick();self.assertEqual(self.posts,[])
        self.at+=timedelta(seconds=11);self.queue.schedules.tick();self.assertEqual(len(self.posts),1)
        self.queue.schedules.tick();self.assertEqual(len(self.posts),1)


if __name__=='__main__':unittest.main()
