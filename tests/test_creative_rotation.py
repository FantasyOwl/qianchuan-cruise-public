import copy
import json
import unittest
from datetime import timedelta

from monitor.creative_rotation import FORMAT,preview_creative_rotations
from monitor.evaluation import evaluate_all
from test_engine import NOW,settings
from test_specialized import declining_rows


def fixture(kind='VIDEO',scene='product'):
    cfg=settings();cfg['accounts'][0]['scene']=scene
    rows=declining_rows()
    for n,r in enumerate(rows):
        r.update(id='weak-'+str(n),entity_id='1001',scene=scene)
        r['context'].update(parent_plan_id='2001',plan_id='2001',creative_type='programmatic')
    parent=copy.deepcopy(rows[-1]);parent.update(id='parent-latest',scope='plan',entity_id='2001')
    parent['source']['series_key']='parent-config';rows.append(parent)
    old,new={
        'VIDEO':({'video_id':'old-video','image_mode':'VIDEO_VERTICAL'},{'video_id':'new-video','image_mode':'VIDEO_VERTICAL'}),
        'IMAGE':({'image_ids':['old-image'],'image_mode':'SQUARE'},{'image_ids':['new-image'],'image_mode':'SQUARE'}),
        'TITLE':({'title':'old title',**({'title_type':'CUSTOM'} if scene=='product' else {})},
                 {'title':'new title',**({'title_type':'CUSTOM'} if scene=='product' else {})}),
        'CAROUSEL':({'carousel_id':'7001'},{'aweme_carousel_id':'7002'}),
    }[kind]
    binding={'product_id':'3001'} if scene=='product' else {}
    slot={'snapshot_id':'weak-18','material_type':kind,'native':old,'duplicate_group_id':'old-group',
          'mapping_verified':True,'delete_class':'SELF_PROVIDED','deletion_eligibility_verified':True,**binding}
    if kind!='TITLE':slot['material_id']='8012367229158357'
    candidate={'id':'candidate-new','material_type':kind,'native':new,'duplicate_group_id':'new-group',
               'enabled':True,'reviewed':True,'reviewed_at':(NOW-timedelta(minutes=1)).isoformat(),
               'queued_at':(NOW-timedelta(days=1)).isoformat(),'review_source':'manual-reviewed-pool',**binding}
    plan={'account_id':'123','plan_id':'2001','parent_snapshot_id':'parent-latest','observed_at':NOW.isoformat(),
          'inventory_verified':True,'inventory_complete':True,'inventory_source':'declared-complete-config',
          'duplicate_groups_verified':True,'multi_aweme':False,
          'capacity':{'unit':'configured_entries','active_count':1,'maximum':5,'verified':True,'source':'declared-local-cap'},
          'slots':[slot],'pool':[candidate]}
    return rows,cfg,{'format':FORMAT,'duplicate_inventory':[{'account_id':'123','observed_at':NOW.isoformat(),'source':'declared-account-counts',
        'verified':True,'complete':True,'groups':[{'group_id':'old-group','active_count':1},{'group_id':'new-group','active_count':0}]}],'plans':[plan]}


def preview(args=None):
    rows,cfg,payload=args or fixture()
    evaluation=evaluate_all(rows,cfg,NOW)
    return preview_creative_rotations(payload,rows,cfg,evaluation,NOW)


class CreativeRotationTests(unittest.TestCase):
    def test_real_evaluation_selects_one_weak_asset_and_builds_explicit_add_first_graph(self):
        out=preview();d=out['drafts'][0]
        self.assertTrue(d['local_eligible'],d['blockers']);self.assertEqual(d['selection']['candidate_id'],'candidate-new')
        self.assertEqual([s['action'] for s in d['stages']],['add','verify_add','cooldown','retire','verify_retire','observe'])
        self.assertGreaterEqual(d['stages'][2]['minimum_seconds'],3600)
        self.assertFalse(d['execution_eligible']);self.assertFalse(d['executed']);self.assertFalse(out['persisted'])
        self.assertFalse(out['workflow_runner_implemented']);self.assertFalse(out['native_material_submission_enabled'])

    def test_video_image_title_and_carousel_have_distinct_native_product_fields(self):
        for kind,key in [('VIDEO','video_material'),('IMAGE','image_material'),('TITLE','title_material'),('CAROUSEL','carousel_material')]:
            with self.subTest(kind=kind):
                d=preview(fixture(kind))['drafts'][0];self.assertTrue(d['local_eligible'],d['blockers'])
                add=json.loads(d['requests']['add']['body_json']);retire=json.loads(d['requests']['retire']['body_json'])
                self.assertEqual(add['ad_id'],2001);self.assertEqual(add['multi_product_creative_list'][0]['product_id'],3001)
                self.assertIn(key,add['multi_product_creative_list'][0]);self.assertNotIn('programmatic_creative_media_list',add)
                if kind=='TITLE':self.assertEqual(retire['titles'],['old title']);self.assertNotIn('material_ids',retire)
                else:self.assertEqual(retire['material_ids'],[8012367229158357])

    def test_native_numbers_are_preserved_in_wire_text_without_browser_integer_roundoff(self):
        rows,cfg,p=fixture();p['plans'][0]['pool'][0]['native']={'aweme_item_id':'8731940833522106597'}
        d=preview((rows,cfg,p))['drafts'][0]
        self.assertIn('8731940833522106597',d['requests']['add']['body_json'])
        self.assertIn('8012367229158357',d['requests']['retire']['body_json'])
        self.assertEqual(d['selection']['new_native']['aweme_item_id'],'8731940833522106597')

    def test_product_multi_aweme_binding_is_nested_under_exact_product(self):
        rows,cfg,p=fixture();plan=p['plans'][0];plan['multi_aweme']=True
        plan['slots'][0]['aweme_uid']='8229182011915337030';plan['pool'][0]['aweme_uid']='8229182011915337030'
        d=preview((rows,cfg,p))['drafts'][0];self.assertTrue(d['local_eligible'],d['blockers'])
        body=json.loads(d['requests']['add']['body_json']);self.assertEqual(body['multi_product_creative_list'][0]['aweme_uid'],8229182011915337030)

    def test_live_video_uses_programmatic_media_without_product_binding(self):
        d=preview(fixture(scene='live'))['drafts'][0];self.assertTrue(d['local_eligible'],d['blockers'])
        body=json.loads(d['requests']['add']['body_json']);self.assertIn('video_material',body['programmatic_creative_media_list'])
        self.assertNotIn('multi_product_creative_list',body)

    def test_queue_selection_is_deterministic_and_not_an_invented_roi_ranking(self):
        rows,cfg,p=fixture();a=p['plans'][0]['pool'][0];b=copy.deepcopy(a);b['id']='a-earlier'
        b['queued_at']=(NOW-timedelta(days=2)).isoformat();b['native']['video_id']='earlier-reviewed-video'
        p['plans'][0]['pool'].append(b)
        first=preview((rows,cfg,p))['drafts'][0]['selection']['candidate_id'];p['plans'][0]['pool'].reverse()
        self.assertEqual(first,'a-earlier');self.assertEqual(preview((rows,cfg,p))['drafts'][0]['selection']['candidate_id'],first)

    def test_capacity_never_deletes_first_to_make_room(self):
        rows,cfg,p=fixture();p['plans'][0]['capacity']['maximum']=1
        d=preview((rows,cfg,p))['drafts'][0];self.assertFalse(d['local_eligible']);self.assertIsNone(d['requests'])
        self.assertIn('不能先删旧素材','；'.join(d['blockers']))

    def test_shared_material_or_title_cannot_retire_other_product_uses(self):
        for kind in ('VIDEO','TITLE'):
            rows,cfg,p=fixture(kind);plan=p['plans'][0];peer=copy.deepcopy(plan['slots'][0]);peer['snapshot_id']=None;peer['product_id']='3002'
            plan['slots'].append(peer);plan['capacity']['active_count']=2
            d=preview((rows,cfg,p))['drafts'][0];self.assertIsNone(d['requests']);self.assertIn('多个用途','；'.join(d['blockers']))

    def test_smart_unknown_or_unverified_delete_class_has_no_delete_preview(self):
        for field,value in [('delete_class','SMART'),('delete_class','UNKNOWN'),('deletion_eligibility_verified',False)]:
            rows,cfg,p=fixture();p['plans'][0]['slots'][0][field]=value
            d=preview((rows,cfg,p))['drafts'][0];self.assertFalse(d['local_eligible']);self.assertIsNone(d['requests'])

    def test_duplicate_limit_checks_transient_add_before_retire(self):
        rows,cfg,p=fixture();plan=p['plans'][0];group=plan['pool'][0]['duplicate_group_id']
        for n in range(3):
            peer=copy.deepcopy(plan['slots'][0]);peer.update(snapshot_id=None,material_id=str(5000+n),duplicate_group_id=group)
            peer['native']['video_id']='peer-'+str(n);plan['slots'].append(peer)
        plan['capacity'].update(active_count=4,maximum=5)
        p['duplicate_inventory'][0]['groups'][1]['active_count']=3
        d=preview((rows,cfg,p))['drafts'][0];self.assertFalse(d['local_eligible']);self.assertIsNone(d['selection'])

    def test_already_configured_asset_and_wrong_product_pool_entries_are_not_selected(self):
        for mode in ('existing','product','stale','unreviewed'):
            rows,cfg,p=fixture();plan=p['plans'][0];c=plan['pool'][0]
            if mode=='existing':c['native']=copy.deepcopy(plan['slots'][0]['native'])
            if mode=='product':c['product_id']='9999'
            if mode=='stale':c['reviewed_at']=(NOW-timedelta(days=1)).isoformat()
            if mode=='unreviewed':c['reviewed']=False
            with self.subTest(mode=mode):self.assertFalse(preview((rows,cfg,p))['drafts'][0]['local_eligible'])

    def test_missing_parent_or_old_snapshot_or_unknown_programmatic_mode_blocks(self):
        for mode in ('missing','stale','mode','parent_relation'):
            rows,cfg,p=fixture()
            if mode=='missing':p['plans'][0]['parent_snapshot_id']='missing'
            if mode=='stale':p['plans'][0]['slots'][0]['snapshot_id']='weak-0'
            if mode=='mode':rows[-1]['context']['creative_type']='unknown'
            if mode=='parent_relation':rows[-2]['context']['parent_plan_id']='other'
            with self.subTest(mode=mode):self.assertFalse(preview((rows,cfg,p))['drafts'][0]['local_eligible'])

    def test_same_capture_state_conflict_is_not_resolved_by_registry_snapshot_choice(self):
        rows,cfg,p=fixture();bad=copy.deepcopy(rows[-1]);bad['id']='conflicting-parent';bad['control']['running']=False
        rows.insert(0,bad)
        d=preview((rows,cfg,p))['drafts'][0];self.assertFalse(d['local_eligible']);self.assertIsNone(d['requests'])

    def test_inventory_attestations_and_time_are_mandatory_not_page_label_inferences(self):
        for mode in ('coverage','time','mapping','group'):
            rows,cfg,p=fixture();plan=p['plans'][0]
            if mode=='coverage':plan['inventory_complete']=False
            if mode=='time':plan['observed_at']=(NOW-timedelta(minutes=1)).isoformat()
            if mode=='mapping':plan['slots'][0]['mapping_verified']=False
            if mode=='group':plan['duplicate_groups_verified']=False
            with self.subTest(mode=mode):self.assertFalse(preview((rows,cfg,p))['drafts'][0]['local_eligible'])

    def test_unverified_product_families_live_multi_account_and_p0_risk_are_blocked(self):
        rows,cfg,p=fixture();rows[-1]['promotion_type']='standard'
        self.assertFalse(preview((rows,cfg,p))['drafts'][0]['local_eligible'])
        rows,cfg,p=fixture(scene='live');p['plans'][0]['multi_aweme']=True
        self.assertFalse(preview((rows,cfg,p))['drafts'][0]['local_eligible'])
        rows,cfg,p=fixture();ev=evaluate_all(rows,cfg,NOW);ev['alerts'].append({'account_id':'123','priority':'P0'})
        self.assertFalse(preview_creative_rotations(p,rows,cfg,ev,NOW)['drafts'][0]['local_eligible'])

    def test_live_title_respects_all_aweme_video_restriction(self):
        rows,cfg,p=fixture('TITLE','live');plan=p['plans'][0]
        video={'snapshot_id':None,'material_type':'VIDEO','material_id':'8001','native':{'aweme_item_id':'8002'},
               'duplicate_group_id':'peer-video','mapping_verified':True,'delete_class':'SELF_PROVIDED','deletion_eligibility_verified':True}
        plan['slots'].append(video);plan['capacity']['active_count']=2
        p['duplicate_inventory'][0]['groups'].append({'group_id':'peer-video','active_count':1})
        self.assertFalse(preview((rows,cfg,p))['drafts'][0]['local_eligible'])
        video['native']={'video_id':'library-video','image_mode':'VIDEO_VERTICAL'}
        self.assertTrue(preview((rows,cfg,p))['drafts'][0]['local_eligible'])

    def test_lossy_ids_unknown_fields_credentials_and_duplicate_json_keys_rejected(self):
        for value in ('0123',123.0,True,{},str(2**64)):
            rows,cfg,p=fixture();p['plans'][0]['account_id']=value
            with self.assertRaises(ValueError):preview((rows,cfg,p))
        rows,cfg,p=fixture();p['plans'][0]['pool'][0]['native']['Access-Token']='SECRET_NOT_TO_ECHO'
        with self.assertRaises(ValueError) as error:preview((rows,cfg,p))
        self.assertNotIn('SECRET_NOT_TO_ECHO',str(error.exception))
        rows,cfg,p=fixture();text=json.dumps(p).replace('"format":','"format":"duplicate", "format":',1)
        with self.assertRaises(ValueError):preview_creative_rotations({'document_text':text},rows,cfg,evaluate_all(rows,cfg,NOW),NOW)

    def test_ambiguous_video_or_carousel_precedence_is_not_used(self):
        for kind,extra in [('VIDEO',{'aweme_item_id':'777'}),('CAROUSEL',{'carousel_id':'778'})]:
            rows,cfg,p=fixture(kind);p['plans'][0]['pool'][0]['native'].update(extra)
            with self.assertRaises(ValueError):preview((rows,cfg,p))

    def test_no_input_mutation_or_credential_execution_permission(self):
        args=fixture();before=copy.deepcopy(args);out=preview(args)
        self.assertEqual(args,before);self.assertFalse(out['ai_sent']);self.assertFalse(out['platform_verified'])
        d=out['drafts'][0]
        self.assertFalse(d['can_release_hold']);self.assertFalse(d['automatic_retry_allowed'])
        self.assertTrue(all(not r['executable'] for r in d['requests'].values()))

    def test_synthetic_registry_never_gets_a_live_submission_address(self):
        from monitor.demo import creative_rotation_scenario
        rows,cfg,when,registry=creative_rotation_scenario(settings())
        result=preview_creative_rotations(registry,rows,cfg,evaluate_all(rows,cfg,when),when)
        d=result['drafts'][0];self.assertTrue(d['local_eligible'],d['blockers']);self.assertTrue(d['synthetic_only'])
        for request in d['requests'].values():
            self.assertIsNone(request['url']);self.assertTrue(request['synthetic_only']);self.assertFalse(request['executable'])

    def test_synthetic_and_imported_parent_weak_sources_cannot_be_joined(self):
        rows,cfg,p=fixture();rows[-1]['source']['kind']='demo'
        self.assertFalse(preview((rows,cfg,p))['drafts'][0]['local_eligible'])

    def test_historical_mode_parent_creation_or_missing_clock_blocks_rotation(self):
        for mode in ('type','parent','created','clock'):
            rows,cfg,p=fixture()
            if mode=='type':rows[1]['context']['creative_type']='custom'
            if mode=='parent':rows[1]['context']['parent_plan_id']='9001'
            if mode=='created':rows[1]['context']['created_at']=(NOW-timedelta(days=3)).isoformat()
            if mode=='clock':rows[1]['source']['data_at']=None
            with self.subTest(mode=mode):self.assertFalse(preview((rows,cfg,p))['drafts'][0]['local_eligible'])

    def test_local_budget_neutral_draft_does_not_substitute_a_missing_user_roi_target(self):
        rows,cfg,p=fixture();cfg['accounts'][0]['target_roi']=None
        d=preview((rows,cfg,p))['drafts'][0];self.assertTrue(d['local_eligible'],d['blockers'])
        self.assertIsNone(cfg['accounts'][0]['target_roi']);self.assertFalse(d['execution_eligible'])

    def test_parent_status_is_never_a_rotation_request_even_for_a_primary_plan(self):
        rows,cfg,p=fixture()
        for row in rows:row['context']['is_primary_plan']=True
        d=preview((rows,cfg,p))['drafts'][0];self.assertTrue(d['local_eligible'],d['blockers'])
        self.assertEqual(set(d['requests']),{'add','retire'})
        self.assertTrue(d['parent_plan_reservation_required']);self.assertTrue(d['rebuild_before_submission_required'])
        self.assertNotIn('opt_status',d['requests']['retire']['body_json'])

    def test_static_title_and_explicit_zero_idle_are_not_confused_with_delete_titles(self):
        from test_strategies import idle_rows
        rows=idle_rows();cfg=settings();_,_,p=fixture(scene='live')
        result=preview_creative_rotations(p,rows,cfg,evaluate_all(rows,cfg,NOW),NOW)
        self.assertFalse(result['drafts'][0]['local_eligible']);self.assertIsNone(result['drafts'][0]['requests'])

    def test_account_group_cap_includes_other_plans_outside_current_plan_inventory(self):
        rows,cfg,p=fixture();p['duplicate_inventory'][0]['groups'][1]['active_count']=3
        self.assertFalse(preview((rows,cfg,p))['drafts'][0]['local_eligible'])
        p['duplicate_inventory'][0]['groups'][1]['active_count']=2
        d=preview((rows,cfg,p))['drafts'][0];self.assertTrue(d['local_eligible'],d['blockers'])
        self.assertEqual(d['selection']['declared_group_count_before'],2);self.assertEqual(d['selection']['duplicate_scope'],'account')

    def test_missing_stale_contradictory_and_duplicate_account_group_evidence_is_not_zero(self):
        for mode in ('missing','stale','conflict','missing_group','duplicate'):
            rows,cfg,p=fixture();profile=p['duplicate_inventory'][0]
            if mode=='missing':p['duplicate_inventory']=[]
            if mode=='stale':profile['observed_at']=(NOW-timedelta(days=1)).isoformat()
            if mode=='conflict':profile['groups'][0]['active_count']=0
            if mode=='missing_group':profile['groups']=profile['groups'][:1]
            if mode=='duplicate':
                profile['groups'].append(copy.deepcopy(profile['groups'][0]))
                with self.assertRaises(ValueError):preview((rows,cfg,p))
                continue
            with self.subTest(mode=mode):self.assertFalse(preview((rows,cfg,p))['drafts'][0]['local_eligible'])

    def test_multiple_plan_drafts_share_account_group_headroom_independent_of_input_order(self):
        rows,cfg,p=fixture();second=copy.deepcopy(rows)
        for r in second:
            r['id']='second-'+r['id'];r['entity_id']='2002' if r['scope']=='plan' else '1002'
            r['context'].update(parent_plan_id='2002',plan_id='2002');r['source']['series_key']='second-'+r['source']['series_key']
        plan=copy.deepcopy(p['plans'][0]);plan.update(plan_id='2002',parent_snapshot_id='second-parent-latest')
        plan['slots'][0].update(snapshot_id='second-weak-18',material_id='8250068783313802')
        plan['slots'][0]['native']['video_id']='second-old-video';p['plans'].append(plan)
        p['duplicate_inventory'][0]['groups'][0]['active_count']=2;p['duplicate_inventory'][0]['groups'][1]['active_count']=2
        for _ in range(2):
            out=preview((rows+second,cfg,p));ready=[d for d in out['drafts'] if d['local_eligible']]
            self.assertEqual([d['plan_id'] for d in ready],['2001'])
            self.assertIsNone(next(d for d in out['drafts'] if d['plan_id']=='2002')['requests'])
            p['plans'].reverse()

    def test_multi_image_cardinality_is_not_assumed_to_be_one_configured_entry(self):
        for target in ('slots','pool'):
            rows,cfg,p=fixture('IMAGE');p['plans'][0][target][0]['native']['image_ids'].append('another-image')
            with self.subTest(target=target):
                d=preview((rows,cfg,p))['drafts'][0];self.assertFalse(d['local_eligible']);self.assertIsNone(d['requests'])

    def test_single_aweme_declaration_cannot_hide_multiple_declared_uids(self):
        rows,cfg,p=fixture();plan=p['plans'][0]
        plan['slots'][0]['aweme_uid']='5001';plan['pool'][0]['aweme_uid']='5001'
        peer=copy.deepcopy(plan['slots'][0]);peer.update(snapshot_id=None,aweme_uid='5002',material_id='8250068783313802')
        peer['native']['video_id']='peer';plan['slots'].append(peer);plan['capacity']['active_count']=2
        p['duplicate_inventory'][0]['groups'][0]['active_count']=2
        d=preview((rows,cfg,p))['drafts'][0];self.assertFalse(d['local_eligible']);self.assertIn('多个抖音UID','；'.join(d['blockers']))


if __name__=='__main__':unittest.main()
