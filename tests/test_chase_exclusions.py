import copy
import json
import unittest
from datetime import timedelta
from tests import test_official_workflow as wf
from tests import test_hybrid_chase as web


class ExclusionTests(unittest.TestCase):
    setUp=wf.WorkflowTests.setUp
    tearDown=wf.WorkflowTests.tearDown
    transport=wf.WorkflowTests.transport
    scanned=wf.WorkflowTests.scanned
    prepared=wf.WorkflowTests.prepared
    confirm=wf.WorkflowTests.confirm

    def save(self,ids):return self.queue.save_material_exclusions({'account_id':self.account['id'],'material_ids':ids})

    def test_exact_ids_persist_dedupe_clear_and_do_not_enable_ads(self):
        ids=['8206994744541607698','8350231211045693350']
        self.save([ids[0],ids[0],ids[1]])
        self.assertEqual(self.queue.material_exclusions(self.account['id']),ids)
        self.assertEqual(self.queue.material_exclusions('999'),[])
        self.assertEqual(self.queue.exclusion_status()[0]['material_ids'],ids)
        self.assertEqual(self.queue.auto.status(),[])
        self.save([]);self.assertEqual(self.queue.material_exclusions(self.account['id']),[])
        for invalid in ([8206994744541607698],[True],['1e19'],['12.5'],['0']):
            with self.assertRaises(ValueError):self.save(invalid)
        self.assertEqual(self.posts,[])

    def test_api_scan_omits_exclusions_and_clear_reenables_candidates(self):
        self.assertEqual(len(self.scanned()['batches']),1)
        self.save([self.materials[1][0]])
        result=self.scanned();self.assertEqual(result['batches'],[])
        self.assertEqual(result['scanned_rows'],3)
        self.assertEqual(result['excluded_material_count'],1)
        self.save([]);self.assertEqual(len(self.scanned()['batches']),1)
        self.assertEqual(self.posts,[])

    def test_already_prepared_api_card_cannot_confirm_excluded_material(self):
        card=self.prepared();self.save([card['intent']['material_ids'][0]])
        with self.assertRaisesRegex(ValueError,'已变化或已处理'):self.confirm(card)
        self.assertEqual(self.queue.status()[0]['state'],'cancelled')
        self.assertEqual(self.posts,[])

    def test_exclusion_saved_after_network_preflight_is_checked_at_final_api_claim(self):
        card=self.prepared();original=self.queue._fresh
        def fresh(*args):
            result=original(*args);self.save([card['intent']['material_ids'][0]]);return result
        self.queue._fresh=fresh
        with self.assertRaisesRegex(ValueError,'已排除'):self.confirm(card)
        self.assertEqual(self.posts,[])


class SingleAndWebExclusionTests(unittest.TestCase):
    setUp=web.HybridTests.setUp
    tearDown=web.HybridTests.tearDown
    transport=web.HybridTests.transport
    confirm=web.HybridTests.confirm
    command=web.HybridTests.command
    observed=web.HybridTests.observed
    feed=web.HybridTests.feed
    tasks=web.HybridTests.tasks
    page_materials=web.HybridTests.page_materials
    ready=web.HybridTests.ready
    begin_web=web.HybridTests.begin_web

    def start_one(self):
        aid=self.account['id'];self.queue.auto.disable({'account_id':aid})
        self.materials_data=[('89581211092803411',500,4),('89041279016210106',400,4.2),('84300024958326955',300,4.5),('88712204312382425',200,4.8)]
        started=self.hybrid.start_test({'account_id':aid})
        self.feed(self.command(),self.tasks());self.feed(self.command(),self.page_materials())
        self.assertEqual(self.hybrid.status()[0]['batches_ready'],1)
        self.feed(self.command(),self.tasks())
        return started,self.command()

    def verify(self,c,permit):
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        self.at+=timedelta(seconds=2)
        cmd=self.command();batch=cmd['action']['intent']['browser_batch']
        self.feed(cmd,[{'id':'10000009','name':batch['task_name'],'material_ids':batch['material_ids'],'after':batch['after'],'status':'调控中'}],document='independent-document')

    def test_single_grant_cannot_run_api_or_second_browser_task_and_stops_after_readback(self):
        aid=self.account['id'];started,c=self.start_one()
        grant=self.queue.auto.row(aid);self.assertEqual(grant['execution_mode'],'single_test')
        self.assertEqual(self.hybrid.start_test({'account_id':aid})['run_id'],started['run_id'])
        with self.assertRaisesRegex(ValueError,'仅授权测试一条'):self.hybrid.start({'account_id':aid})
        with self.assertRaisesRegex(ValueError,'仅授权测试一条'):
            self.queue.auto.verify_scope({'account_id':aid,'plan_id':self.pid,'auto_authority':{'grant_id':grant['grant_id']}})
        calls=len(self.calls);self.queue.auto.tick();self.assertEqual(len(self.calls),calls)
        self.verify(c,self.begin_web(c))
        self.assertEqual(self.hybrid.status()[0]['state'],'completed')
        self.assertEqual(len(self.queue.status()),1)
        self.assertFalse(self.queue.auto.row(aid)['enabled'])
        self.assertIsNone(self.hybrid.next(self.worker)['command'])
        self.queue.auto.tick();self.assertEqual(self.posts,[])

    def test_stop_after_permit_denies_click_and_does_not_restart_after_expiry(self):
        _,c=self.start_one();permit=self.begin_web(c)
        self.hybrid.stop({'account_id':self.account['id']})
        with self.assertRaisesRegex(ValueError,'已停止'):
            self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        self.at+=timedelta(seconds=121);self.hybrid.tick()
        self.assertEqual(self.queue.status()[0]['state'],'cancelled')
        self.assertIsNone(self.hybrid.next(self.worker)['command'])
        self.assertEqual(self.hybrid.status()[0]['state'],'cancelled')
        self.assertFalse(self.queue.auto.row(self.account['id'])['enabled'])

    def test_web_scan_excludes_and_rescans_unsubmitted_old_batch(self):
        c=self.ready();mid=c['action']['intent']['material_ids'][0]
        self.queue.save_material_exclusions({'account_id':self.account['id'],'material_ids':[mid]})
        self.assertEqual(self.queue.status()[0]['state'],'cancelled')
        with self.assertRaises(ValueError):self.begin_web(c)
        self.feed(self.command(),self.page_materials())
        self.feed(self.command(),self.tasks())
        c=self.command();self.assertNotIn(mid,c['action']['intent']['material_ids'])
        self.assertEqual(self.posts,[])

    def test_exclusion_changed_between_begin_and_click_blocks_final_but_keeps_original_evidence(self):
        c=self.ready();permit=self.begin_web(c);mid=c['action']['intent']['material_ids'][0]
        self.queue.save_material_exclusions({'account_id':self.account['id'],'material_ids':[mid]})
        with self.assertRaisesRegex(ValueError,'已排除'):
            self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        self.assertEqual(self.queue.status()[0]['state'],'submitting')
        self.assertEqual(self.command()['phase'],'verify')
        self.assertEqual(self.posts,[])

    def test_stopped_posted_original_is_verified_once_without_creating_remaining_batches(self):
        c=self.ready();permit=self.begin_web(c)
        self.hybrid.final({'run_id':c['run_id'],'action_id':c['action']['id'],'submit_token':permit['submit_token']})
        with self.queue.db() as db:
            row=db.execute('SELECT * FROM hybrid_runs WHERE id=?',(c['run_id'],)).fetchone();data=json.loads(row['data'])
            data['batches'].append(copy.deepcopy(data['batches'][0]));self.hybrid.save(db,row,data)
        self.hybrid.stop({'account_id':self.account['id']});self.at+=timedelta(seconds=2)
        cmd=self.command();batch=cmd['action']['intent']['browser_batch']
        self.feed(cmd,[{'id':'10000009','name':batch['task_name'],'material_ids':batch['material_ids'],'after':batch['after'],'status':'调控中'}],document='new-readback')
        self.assertEqual(self.hybrid.status()[0]['state'],'completed')
        self.assertEqual(len(self.queue.status()),1)
