import copy
import unittest
from types import SimpleNamespace
from tests import test_official_monitoring as fixtures
from monitor.cruise_api import CruiseApp
from monitor.official_read_models import monitoring_model
from monitor.cruise_rules import default_profile
from monitor.schema import now_cn


class AnchorIdentityTests(unittest.TestCase):
    setUp=fixtures.OfficialMonitoringTests.setUp
    tearDown=fixtures.OfficialMonitoringTests.tearDown
    transport=fixtures.OfficialMonitoringTests.transport
    plan=fixtures.OfficialMonitoringTests.plan

    def handle_list(self,detail_changes=None):
        self.account.update(scene='live',delivery_entry='uni_promotion')
        real=self.api.transport
        def observed(name,q,token=None):
            if name=='plan_detail':
                self.calls.append((name,copy.deepcopy(q)))
                return {'ad_id':int(self.ids['UNI_PROJECT']),'marketing_goal':'LIVE_PROM_GOODS','adlab_scene':0,
                    'room_info':[{'anchor_id':71110310948,'anchor_name':'native room'}],**(detail_changes or {})}
            value=real(name,q,token)
            if name=='plans':
                for row in value['ad_list']:row['room_info'][0]['anchor_id']='custom_douyin_handle'
            return value
        self.api.transport=observed

    def test_public_handle_resolves_only_through_exact_plan_detail_and_reports_use_numeric_uid(self):
        self.handle_list();result=self.api.read_plans(self.account,'g')['result'];p=result['plans'][0]
        self.assertEqual(p['anchors'][0]['id'],'71110310948');self.assertEqual(p['metrics']['spend'],100)
        self.assertEqual(p['anchor_identity']['source'],'exact_plan_detail')
        detail=[q for n,q in self.calls if n=='plan_detail'];self.assertEqual(detail,[{'advertiser_id':int(self.account['id']),'ad_id':int(self.ids['UNI_PROJECT'])}])
        reports=[q for n,q in self.calls if n=='author_report'];self.assertTrue(reports)
        self.assertTrue(all(q['aweme_id']==71110310948 for q in reports))

    def test_foreign_detail_identity_never_replaces_good_cached_data(self):
        self.account.update(scene='live',delivery_entry='uni_promotion');baseline=self.api.read_plans(self.account,'g')['result']
        original=self.transport
        for changed in [{'ad_id':999},{'advertiser_id':999},{'marketing_goal':'VIDEO_PROM_GOODS'},{'adlab_scene':1},{'adlab_scene':None}]:
            with self.subTest(changed=changed):
                self.api.transport=original;self.handle_list(changed)
                with self.assertRaises(ValueError):self.api.read_plans(self.account,'g')
                self.assertEqual(self.api.plan_readings[self.account['id']],baseline)

    def test_missing_invalid_or_duplicate_detail_uids_are_never_guessed(self):
        for rooms in [[],None,[{'anchor_id':'custom_douyin_handle'}],[{'anchor_id':0}],[{'anchor_id':71110310948},{'anchor_id':71110310948}]]:
            with self.subTest(rooms=rooms):
                self.api.transport=self.transport;self.handle_list({'room_info':rooms})
                with self.assertRaisesRegex(ValueError,'详情也未能核验'):self.api.read_plans(self.account,'g')
                self.assertNotIn(self.account['id'],self.api.plan_readings)

    def test_multiple_verified_rooms_do_not_get_assigned_a_single_room_report(self):
        self.handle_list({'room_info':[{'anchor_id':71110310948},{'anchor_id':89929253244}]})
        p=self.api.read_plans(self.account,'g')['result']['plans'][0]
        self.assertEqual(len(p['anchors']),2);self.assertIsNone(p['metrics']['spend'])
        self.assertFalse(any(n=='author_report' for n,q in self.calls))

    def test_existing_numeric_uid_path_does_not_add_detail_calls(self):
        self.account.update(scene='live',delivery_entry='uni_promotion');self.api.read_plans(self.account,'g')
        self.assertFalse(any(n=='plan_detail' for n,q in self.calls))

    def test_account_report_remains_visible_when_no_plan_reading_has_been_published(self):
        at=now_cn();aid=self.account['id']
        reading={'account_id':aid,'delivery_entry':'uni_promotion','entry_label':'全域','date':at.date().isoformat(),
            'captured_at':at.isoformat(),'data_status':'observed','metrics':{'spend':123.4,'roi':2.8}}
        result=monitoring_model(self.account,{}, {aid+':uni_promotion':reading},at)
        self.assertTrue(result['data_connected']);self.assertEqual(result['plans'],[])
        self.assertEqual(result['summaries'][0]['metrics']['spend'],123.4);self.assertEqual(result['dates'],[at.date().isoformat()])

    def test_plan_parse_error_is_reported_instead_of_falling_back_to_browser_scan(self):
        self.api.polling[self.account['id']]={'state':'partial','error':'账户已更新',
            'components':{'plans':{'state':'error','error':'抖音号UID未能核验'}}}
        result=CruiseApp.official_scan(SimpleNamespace(official_api=self.api),self.account,default_profile(),'main')
        self.assertIsNotNone(result);self.assertEqual(result['scanned'],0);self.assertEqual(result['proposals'],[])
        self.assertIn('抖音号UID未能核验',result['summary']);self.assertEqual(result['diagnostics'][0]['status'],'api_plans_pending')
