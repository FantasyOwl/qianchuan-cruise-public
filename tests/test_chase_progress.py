import copy
import json
import unittest
from datetime import timedelta
from unittest.mock import patch

from tests.test_hybrid_chase import HybridTests
from monitor.official_auto import QuotaEvidenceMissing
from monitor.official_workflow import digest


class ChaseProgressTests(unittest.TestCase):
    setUp=HybridTests.setUp
    tearDown=HybridTests.tearDown
    transport=HybridTests.transport
    command=HybridTests.command
    observed=HybridTests.observed
    feed=HybridTests.feed
    tasks=HybridTests.tasks
    page_materials=HybridTests.page_materials
    ready=HybridTests.ready

    def seed(self,mids,*,state='verified',hours=0,aid=None):
        i={'account_id':aid or self.account['id'],'plan_id':self.pid,'action':'material_chase','before':None,'after':None,
           'material_ids':mids,'body':{'budget':200}}
        at=(self.at-timedelta(hours=hours)).isoformat();rid='history'+str(len(mids))+state+str(hours)+str(aid)
        with self.queue.db() as db:db.execute('INSERT INTO api_actions VALUES(?,?,?,?,?,?,?,?)',(
            rid,state,json.dumps(i),digest(i),at,(self.at+timedelta(minutes=15)).isoformat(),at,'{}'))
        return rid

    def start_materials(self):
        self.materials_data=[(str(89581211092803411+n),400-n*50,4) for n in range(4)]
        self.hybrid.request(self.account['id'],reason='test')
        self.feed(self.command(),self.tasks());return self.command()

    def test_api_scan_excludes_previously_verified_materials_before_grouping(self):
        self.materials=[(str(89581211092803411+n),400-n*50,4) for n in range(4)]
        self.seed([m[0] for m in self.materials[:2]])
        value=self.api.scan_workflow(self.account,self.profile,self.pid,materials=True)
        self.assertEqual([b['material_ids'] for b in value['batches']],[[m[0] for m in self.materials[2:]]])
        self.assertEqual(self.posts,[])

    def test_web_scan_excludes_previously_verified_materials_before_grouping(self):
        c=self.start_materials();self.seed([m[0] for m in self.materials_data[:2]])
        self.feed(c,self.page_materials());self.feed(self.command(),self.tasks());c=self.command()
        self.assertEqual(c['action']['intent']['material_ids'],[m[0] for m in self.materials_data[2:]])
        self.assertEqual(c['action']['intent']['target_kind'],'control_task');self.assertEqual(self.posts,[])

    def test_late_duplicate_batch_yields_to_the_next_nonduplicate_batch(self):
        c=self.start_materials();self.feed(c,self.page_materials())
        self.seed([m[0] for m in self.materials_data[:2]])
        self.feed(self.command(),self.tasks());c=self.command()
        self.assertEqual(c['phase'],'prepare');self.assertEqual(c['action']['intent']['material_ids'],[m[0] for m in self.materials_data[2:]])
        self.assertEqual(self.queue.auto.daily(self.account['id']),1)

    def test_all_duplicate_batches_rescan_instead_of_parking_account_forever(self):
        c=self.start_materials();self.feed(c,self.page_materials());self.seed([m[0] for m in self.materials_data])
        self.feed(self.command(),self.tasks());self.assertEqual(self.command()['phase'],'materials')
        self.assertEqual(self.queue.auto.daily(self.account['id']),0);self.assertEqual(self.posts,[])

    def test_duplicate_fence_ignores_cancelled_rejected_old_and_other_account_records(self):
        self.seed(['111111'],state='cancelled');self.seed(['222222'],state='rejected')
        self.seed(['333333'],hours=25);self.seed(['444444'],aid='8502833055693536')
        self.seed(['555555'],state='unknown',hours=25)
        self.assertEqual(self.queue.recent_chase_materials(self.account['id'],self.pid),{'555555'})
        self.assertEqual(self.queue.recent_chase_materials(self.account['id'],self.pid,include_pending=False),set())

    def test_repeat_running_option_remains_explicit_and_keeps_old_authority(self):
        self.profile['features']['f5']['allow_running_materials']=True
        self.materials=[(str(89581211092803411+n),400-n*50,4) for n in range(4)]
        self.seed([m[0] for m in self.materials[:2]])
        value=self.api.scan_workflow(self.account,self.profile,self.pid,materials=True)
        self.assertEqual(len(value['batches']),2)

    def test_browser_chase_does_not_inherit_main_plan_one_hour_interval(self):
        history=[{'account_id':self.account['id'],'plan_id':self.pid,'entity_id':self.pid,'action':'roi_target_up',
                  'before':'3','after':'3.1','state':'verified','at':(self.at-timedelta(minutes=1)).isoformat()}]
        with patch.object(self.queue,'_history',return_value=history):c=self.ready()
        self.assertTrue(c['action']['intent']['cost_guarantee']['allowed'])
        self.assertNotIn('main_plan_interval',str(c['action']['intent']['cost_guarantee']['checks']))

    def test_missing_api_quota_evidence_switches_before_any_write(self):
        workflow={'tasks':[],'batches':[{'ready':True,'material_ids':['89581211092803411','89041279016210106'],
                   'native_contract':{'body':{'budget':200}}}],'unbatched_materials':[]}
        with self.queue.db() as db:db.execute('UPDATE api_auto SET next_at=?',(self.at.isoformat(),))
        with patch.object(self.api,'scan_workflow',return_value=copy.deepcopy(workflow)),patch.object(self.queue.auto,'verify',side_effect=QuotaEvidenceMissing('历史任务未查到')):
            self.queue.auto.tick()
        self.assertTrue(self.hybrid.active(self.account['id']));self.assertEqual(self.posts,[])
        self.assertEqual(self.queue.auto.row(self.account['id'])['state'],'browser_wait')
