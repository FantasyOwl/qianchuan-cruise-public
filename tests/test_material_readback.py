import copy
import json
import unittest
from datetime import timedelta
from unittest.mock import patch
import urllib.parse

from monitor.material_readback import FORMAT,PATH,MaterialReadClient,MaterialReadPermit,_get,prepare_material_request,review_supplied_materials
from test_engine import NOW,settings


def fixture(kind='VIDEO',scene='product'):
    cfg=settings();cfg['accounts'][0].update(scene=scene,observe=True)
    native={'VIDEO':{'video_id':'video-8012367229158357'},
        'IMAGE':{'image_ids':['image-1']},'TITLE':{'title':'exact title',**({'title_type':'CUSTOM'} if scene=='product' else {})},
        'CAROUSEL':{'carousel_id':'8753264005940210'},'LIVE_ROOM':{}}[kind]
    family={'VIDEO':'video_material','IMAGE':'image_material','TITLE':'title_material','CAROUSEL':'carousel_material','LIVE_ROOM':'room_material'}[kind]
    detail={'ad_id':2001,'marketing_goal':'VIDEO_PROM_GOODS' if scene=='product' else 'LIVE_PROM_GOODS','is_multi_aweme_uid':False,
        'modify_time':'2026-10-23 16:00:00','multi_product_creative_list':[{'product_id':3001,'creative_type':'PROGRAMMATIC_CREATIVE',family:[copy.deepcopy(native)]}],
        'programmatic_creative_media_list':{family:[copy.deepcopy(native)]}}
    item={'material_id':8012367229158357,**copy.deepcopy(native)}
    if kind=='IMAGE':item={'material_id':8012367229158357,'image_id':'image-1'}
    if kind=='CAROUSEL':item={'material_id':8012367229158357,'title':'carousel title'}
    row={'material_info':{'material_type':kind,family:item},'material_status':'DELIVERY_OK','audit_status':'PASS','is_delete':False,
        'material_select_type':'CUSTOM','product_info':[{'product_id':3001}]}
    query={'account_id':'123','plan_id':'2001','material_type':kind,'start_date':NOW.date().isoformat(),'end_date':NOW.date().isoformat(),'page_size':10,'max_pages':5}
    target={'native':native,'material_id':'8012367229158357','desired_presence':'present',**({'product_id':'3001'} if scene=='product' else {})}
    payload={'format':FORMAT,'query':query,'target':target,'request_started_at':(NOW-timedelta(seconds=2)).isoformat(),
        'received_at':NOW.isoformat(),'detail_response':{'code':0,'data':detail},'material_responses':[page([row])]}
    return payload,cfg


def page(rows,*,number=1,size=10,total=None):
    total=len(rows) if total is None else total
    return {'code':0,'data':{'ad_material_infos':rows,'page_info':{'page':number,'page_size':size,'total_number':total,'total_page':(total+size-1)//size}}}


def review(payload=None,cfg=None):
    if payload is None:payload,cfg=fixture()
    return review_supplied_materials(payload,cfg,NOW)


class MaterialReviewTests(unittest.TestCase):
    def test_explicit_configuration_and_list_status_are_separate_without_execution_or_unlock(self):
        out=review();self.assertEqual(out['configuration']['state'],'present')
        self.assertTrue(out['desired_state_matches_returned_configuration']);self.assertEqual(out['matched_observations'],1)
        self.assertTrue(out['material_observations'][0]['aggregate_usable_observed'])
        self.assertFalse(out['use_specific_usable_verified']);self.assertFalse(out['can_release_hold'])
        self.assertFalse(out['platform_verified']);self.assertFalse(out['complete_inventory_verified'])
        self.assertFalse(out['material_observations'][0]['deletion_eligibility_verified'])

    def test_empty_spend_list_does_not_prove_absence_or_complete_inventory(self):
        p,c=fixture();p['material_responses']=[page([])];out=review(p,c)
        self.assertEqual(out['configuration']['state'],'present');self.assertTrue(out['pagination_exhausted'])
        self.assertEqual(out['matched_observations'],0);self.assertFalse(out['complete_inventory_verified'])
        self.assertFalse(out['can_release_hold'])

    def test_explicit_empty_configuration_only_matches_returned_configuration_not_a_real_delete(self):
        p,c=fixture();p['detail_response']['data']['multi_product_creative_list'][0]['video_material']=[]
        p['target']['desired_presence']='absent';p['material_responses']=[page([])];out=review(p,c)
        self.assertEqual(out['configuration']['state'],'not_observed');self.assertTrue(out['desired_state_matches_returned_configuration'])
        self.assertFalse(out['submission_state_changed']);self.assertFalse(out['platform_verified'])

    def test_missing_family_is_unknown_instead_of_an_empty_array(self):
        p,c=fixture();del p['detail_response']['data']['multi_product_creative_list'][0]['video_material'];p['target']['desired_presence']='absent'
        out=review(p,c);self.assertEqual(out['configuration']['state'],'unavailable');self.assertFalse(out['desired_state_matches_returned_configuration'])

    def test_all_current_native_types_keep_distinct_identifiers_and_large_integer_precision(self):
        for kind,scene in [('VIDEO','product'),('IMAGE','product'),('TITLE','product'),('CAROUSEL','product'),('LIVE_ROOM','live')]:
            p,c=fixture(kind,scene);out=review_supplied_materials({'document_text':json.dumps(p)},c,NOW)
            self.assertEqual(out['material_observations'][0]['material_id'],'8012367229158357')
            self.assertEqual(out['configuration']['state'],'unavailable' if kind=='LIVE_ROOM' else 'present')
        p,c=fixture('VIDEO');p['target']['native']={'aweme_item_id':'8731940833522106597'}
        p['detail_response']['data']['multi_product_creative_list'][0]['video_material']=[{'aweme_item_id':8731940833522106597}]
        p['material_responses'][0]['data']['ad_material_infos'][0]['material_info']['video_material']={'aweme_item_id':8731940833522106597,'material_id':8012367229158357}
        self.assertEqual(review(p,c)['configuration']['state'],'present')

    def test_wrong_plan_goal_or_account_is_rejected(self):
        for field,value in [('ad_id',2002),('marketing_goal','LIVE_PROM_GOODS')]:
            p,c=fixture();p['detail_response']['data'][field]=value
            with self.assertRaises(ValueError):review(p,c)
        p,c=fixture();p['query']['account_id']='999'
        with self.assertRaises(ValueError):review(p,c)

    def test_live_membership_does_not_infer_programmatic_mode_from_container_name(self):
        p,c=fixture(scene='live');out=review(p,c)
        self.assertEqual(out['configuration']['state'],'present');self.assertIsNone(out['configuration']['creative_mode'])

    def test_product_programmatic_type_and_multi_aweme_scope_must_be_explicit(self):
        for field,value in [('creative_type','CUSTOM'),('creative_type',None)]:
            p,c=fixture();p['detail_response']['data']['multi_product_creative_list'][0][field]=value
            self.assertEqual(review(p,c)['configuration']['state'],'unavailable')
        p,c=fixture();p['detail_response']['data']['is_multi_aweme_uid']=True
        self.assertEqual(review(p,c)['configuration']['state'],'unavailable')
        p['target']['aweme_uid']='8229182011915337030';p['detail_response']['data']['multi_product_creative_list'][0]['aweme_uid']=8229182011915337030
        out=review(p,c);self.assertEqual(out['configuration']['state'],'present');self.assertFalse(out['use_specific_usable_verified'])

    def test_wrong_product_or_uid_never_matches_some_other_use(self):
        for field,value in [('product_id',3002),('aweme_uid',9002)]:
            p,c=fixture();p['detail_response']['data']['multi_product_creative_list'][0][field]=value
            if field=='aweme_uid':p['target']['aweme_uid']='9001'
            self.assertEqual(review(p,c)['configuration']['state'],'not_observed')

    def test_duplicate_use_and_contradictory_single_uid_scope_are_rejected(self):
        p,c=fixture();entry=p['detail_response']['data']['multi_product_creative_list'][0]
        p['detail_response']['data']['multi_product_creative_list'].append(copy.deepcopy(entry))
        with self.assertRaises(ValueError):review(p,c)
        entry['aweme_uid']=9001;p['detail_response']['data']['multi_product_creative_list'][1]['aweme_uid']=9002
        with self.assertRaises(ValueError):review(p,c)

    def test_duplicate_native_config_and_dynamic_title_are_ambiguous(self):
        p,c=fixture();items=p['detail_response']['data']['multi_product_creative_list'][0]['video_material'];items.append(copy.deepcopy(items[0]))
        self.assertEqual(review(p,c)['configuration']['state'],'ambiguous')
        p,c=fixture('TITLE');p['detail_response']['data']['multi_product_creative_list'][0]['title_material'][0]['dynamic_words']=[{'word_id':99}]
        self.assertEqual(review(p,c)['configuration']['state'],'ambiguous')

    def test_single_image_target_cannot_claim_an_entire_image_group(self):
        p,c=fixture('IMAGE');p['detail_response']['data']['multi_product_creative_list'][0]['image_material'][0]['image_ids'].append('image-2')
        self.assertEqual(review(p,c)['configuration']['state'],'ambiguous')

    def test_unknown_audit_deletion_or_aggregate_product_scope_never_proves_use_usable(self):
        for field in ('audit_status','is_delete','material_status'):
            p,c=fixture();del p['material_responses'][0]['data']['ad_material_infos'][0][field]
            out=review(p,c);self.assertIsNone(out['material_observations'][0]['aggregate_usable_observed'])
            self.assertFalse(out['use_specific_usable_verified'])
        p,c=fixture();p['material_responses'][0]['data']['ad_material_infos'][0]['product_info']=[{'product_id':3002}]
        self.assertEqual(review(p,c)['matched_observations'],0)

    def test_declared_material_id_is_not_interchanged_with_carousel_library_id(self):
        p,c=fixture('CAROUSEL');del p['target']['material_id'];out=review(p,c)
        self.assertEqual(out['configuration']['state'],'present');self.assertEqual(out['matched_observations'],0)

    def test_explicit_deleted_status_is_only_a_reported_observation(self):
        p,c=fixture();row=p['material_responses'][0]['data']['ad_material_infos'][0];row.update(material_status='DELETED',is_delete=True)
        out=review(p,c);self.assertTrue(out['material_observations'][0]['reported_deleted']);self.assertFalse(out['can_release_hold'])
        self.assertEqual(out['configuration']['state'],'present')

    def test_mutable_pagination_and_repeated_material_ids_are_rejected(self):
        p,c=fixture();row=p['material_responses'][0]['data']['ad_material_infos'][0]
        p['material_responses']=[page([copy.deepcopy(row) for _ in range(10)],total=11),page([copy.deepcopy(row)],number=2,total=11)]
        with self.assertRaises(ValueError):review(p,c)
        for n,r in enumerate(p['material_responses'][0]['data']['ad_material_infos']):r['material_info']['video_material']['material_id']=9000+n
        p['material_responses'][1]['data']['page_info'].update(total_number=12)
        with self.assertRaises(ValueError):review(p,c)

    def test_capped_partial_pages_stay_partial_without_inventory_claim(self):
        p,c=fixture();row=p['material_responses'][0]['data']['ad_material_infos'][0]
        rows=[copy.deepcopy(row) for _ in range(10)]
        for n,r in enumerate(rows):r['material_info']['video_material']['material_id']=9000+n
        p['query']['max_pages']=1;p['material_responses']=[page(rows,total=20)]
        out=review(p,c);self.assertFalse(out['pagination_exhausted']);self.assertFalse(out['complete_inventory_verified'])

    def test_id_float_boolean_unknown_enums_and_invalid_time_are_rejected(self):
        for value in (True,1.1,'0900'):
            p,c=fixture();p['material_responses'][0]['data']['ad_material_infos'][0]['material_info']['video_material']['material_id']=value
            with self.assertRaises(ValueError):review(p,c)
        for key,value in [('material_status',{}),('audit_status','NEW'),('is_delete','false')]:
            p,c=fixture();p['material_responses'][0]['data']['ad_material_infos'][0][key]=value
            with self.assertRaises(ValueError):review(p,c)
        p,c=fixture();p['received_at']=(NOW+timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError):review(p,c)
        p,c=fixture();p['material_responses'][0]['data']['ad_material_infos'][0]['material_info']['video_material']['video_id']='unrelated'
        with self.assertRaisesRegex(ValueError,'映射矛盾'):review(p,c)
        p,c=fixture();p['target']['desired_presence']={}
        with self.assertRaises(ValueError):review(p,c)

    def test_credentials_duplicate_keys_nonfinite_and_extra_flags_are_rejected(self):
        p,c=fixture();p['detail_response']['data']['access_token']='PRIVATE'
        with self.assertRaises(ValueError):review(p,c)
        p,c=fixture();p['material_responses'][0]='{"code":0,"code":0,"data":{}}'
        with self.assertRaises(ValueError):review(p,c)
        p,c=fixture();p['enabled']=True
        with self.assertRaises(ValueError):review(p,c)
        p,c=fixture();p['detail_response']['data']['unknown']=float('nan')
        with self.assertRaises(ValueError):review(p,c)

    def test_review_does_not_mutate_package_settings_or_authenticate_modify_time(self):
        p,c=fixture();before=copy.deepcopy((p,c));out=review(p,c)
        self.assertEqual((p,c),before);self.assertIsNone(out['configuration']['platform_state_at'])
        self.assertEqual(out['configuration']['modify_time_raw'],'2026-10-23 16:00:00')


class MaterialClientTests(unittest.TestCase):
    def setUp(self):
        self.p,self.cfg=fixture();self.calls=[];self.provider_calls=[]
        self.request=prepare_material_request(self.p['query'],self.cfg)
        self.permit=MaterialReadPermit('123',self.request['digest'],NOW-timedelta(seconds=1),NOW+timedelta(minutes=1),'test-read-reference')

    def token(self):self.provider_calls.append(1);return 'FAKE_PRIVATE_TOKEN'

    def transport(self,kind,params,token,timeout):
        self.calls.append((kind,params,timeout))
        self.assertEqual(token,'FAKE_PRIVATE_TOKEN')
        return json.dumps(self.p['detail_response'] if kind=='detail' else self.p['material_responses'][params['page']-1])

    def read(self,client=None,permit=None):
        client=client or MaterialReadClient(enable_network=True,transport=self.transport,clock=lambda:NOW)
        return client.read(self.p['query'],self.p['target'],self.cfg,permit or self.permit,self.token)

    def test_default_off_does_not_call_clock_transport_or_token_provider(self):
        client=MaterialReadClient(transport=self.transport,clock=lambda:(_ for _ in ()).throw(AssertionError()))
        with self.assertRaises(PermissionError):self.read(client)
        self.assertEqual(self.calls,[]);self.assertEqual(self.provider_calls,[])

    def test_read_uses_get_contracts_bounded_pages_and_never_releases_state(self):
        out=self.read();self.assertTrue(out['ok']);self.assertEqual([c[0] for c in self.calls],['detail','materials'])
        params=self.calls[1][1];filtering=json.loads(params['filtering'])
        self.assertEqual(filtering['material_status'],'ALL');self.assertEqual(filtering['material_select_type'],'ALL')
        self.assertNotIn('video_type',filtering);self.assertNotIn('marketing_goal',params)
        self.assertFalse(out['can_release_hold']);self.assertFalse(out['submission_state_changed']);self.assertFalse(out['persisted'])

    def test_image_live_room_and_live_title_do_not_send_unsupported_filters(self):
        for kind,scene in [('IMAGE','product'),('LIVE_ROOM','live'),('TITLE','live')]:
            p,c=fixture(kind,scene);f=json.loads(prepare_material_request(p['query'],c)['params']['filtering'])
            if kind in {'IMAGE','LIVE_ROOM'}:self.assertNotIn('material_status',f)
            if scene=='live':self.assertNotIn('material_select_type',f)

    def test_expired_wrong_digest_account_or_long_permit_never_reads_credentials(self):
        for permit in (MaterialReadPermit('999',self.request['digest'],NOW-timedelta(seconds=1),NOW+timedelta(seconds=1),'r'),
            MaterialReadPermit('123','bad',NOW-timedelta(seconds=1),NOW+timedelta(seconds=1),'r'),
            MaterialReadPermit('123',self.request['digest'],NOW-timedelta(minutes=10),NOW+timedelta(seconds=1),'r'),
            MaterialReadPermit('123',self.request['digest'],NOW-timedelta(seconds=2),NOW,'r')):
            with self.assertRaises(PermissionError):self.read(permit=permit)
        self.assertEqual(self.provider_calls,[]);self.assertEqual(self.calls,[])

    def test_scope_mutation_while_providing_credentials_is_rejected_before_transport(self):
        def token():self.cfg['accounts'][0]['observe']=False;return 'FAKE'
        client=MaterialReadClient(enable_network=True,transport=self.transport,clock=lambda:NOW)
        with self.assertRaises(ValueError):client.read(self.p['query'],self.p['target'],self.cfg,self.permit,token)
        self.assertEqual(self.calls,[])

    def test_token_provider_failure_and_timeout_do_not_echo_private_text_or_retry(self):
        client=MaterialReadClient(enable_network=True,transport=self.transport,clock=lambda:NOW)
        with self.assertRaises(ValueError) as e:client.read(self.p['query'],self.p['target'],self.cfg,self.permit,lambda:(_ for _ in ()).throw(RuntimeError('PRIVATE')))
        self.assertNotIn('PRIVATE',str(e.exception))
        def fail(*args):self.calls.append('once');raise TimeoutError('FAKE_PRIVATE_TOKEN')
        out=self.read(MaterialReadClient(enable_network=True,transport=fail,clock=lambda:NOW))
        self.assertFalse(out['ok']);self.assertEqual(self.calls,['once']);self.assertNotIn('FAKE_PRIVATE_TOKEN',json.dumps(out))

    def test_expiry_or_clock_rollback_between_detail_and_pages_does_not_continue(self):
        for later in (NOW-timedelta(seconds=1),NOW+timedelta(minutes=2)):
            self.calls=[];clocks=iter([NOW,NOW,later]);client=MaterialReadClient(enable_network=True,transport=self.transport,clock=lambda:next(clocks))
            out=self.read(client);self.assertFalse(out['ok']);self.assertEqual([c[0] for c in self.calls],['detail'])

    def test_pagination_cap_is_enforced_even_if_server_reports_more_pages(self):
        self.p['query']['max_pages']=1;row=self.p['material_responses'][0]['data']['ad_material_infos'][0]
        rows=[copy.deepcopy(row) for _ in range(10)]
        for n,r in enumerate(rows):r['material_info']['video_material']['material_id']=9000+n
        self.p['material_responses']=[page(rows,total=20)];self.request=prepare_material_request(self.p['query'],self.cfg)
        self.permit=MaterialReadPermit('123',self.request['digest'],NOW-timedelta(seconds=1),NOW+timedelta(seconds=30),'r')
        out=self.read();self.assertTrue(out['ok']);self.assertFalse(out['comparison']['pagination_exhausted']);self.assertEqual(len(self.calls),2)

    def test_bounded_reader_collects_contiguous_pages_without_claiming_inventory(self):
        row=self.p['material_responses'][0]['data']['ad_material_infos'][0];rows=[copy.deepcopy(row) for _ in range(11)]
        for n,r in enumerate(rows):r['material_info']['video_material']['material_id']=8012367229158357+n
        self.p['material_responses']=[page(rows[:10],total=11),page(rows[10:],number=2,total=11)]
        out=self.read();self.assertTrue(out['ok']);self.assertEqual(len(self.calls),3)
        self.assertTrue(out['comparison']['pagination_exhausted']);self.assertFalse(out['comparison']['complete_inventory_verified'])
        self.assertEqual(out['comparison']['matched_observations'],1)

    def test_inflight_mutation_cannot_expand_approved_pagination_or_rewrite_detail(self):
        self.p['query']['max_pages']=1;row=self.p['material_responses'][0]['data']['ad_material_infos'][0]
        rows=[copy.deepcopy(row) for _ in range(10)]
        for n,r in enumerate(rows):r['material_info']['video_material']['material_id']=8012367229158357+n
        self.p['material_responses']=[page(rows,total=20)];self.request=prepare_material_request(self.p['query'],self.cfg)
        self.permit=MaterialReadPermit('123',self.request['digest'],NOW-timedelta(seconds=1),NOW+timedelta(seconds=30),'r')
        def mutable(kind,params,token,timeout):
            self.calls.append(kind)
            if kind=='detail':self.p['query']['max_pages']=3;return self.p['detail_response']
            self.p['detail_response']['data']['ad_id']=999;return self.p['material_responses'][0]
        out=self.read(MaterialReadClient(enable_network=True,transport=mutable,clock=lambda:NOW))
        self.assertTrue(out['ok']);self.assertEqual(self.calls,['detail','materials'])
        self.assertEqual(out['comparison']['query']['max_pages'],1)

    def test_custom_transport_malformed_pages_cannot_be_promoted_to_platform_evidence(self):
        self.p['material_responses'][0]['data']['page_info']['page']=2
        out=self.read();self.assertFalse(out['ok']);self.assertFalse(out['can_release_hold'])

    def test_fixed_official_get_transport_disables_redirect_and_bounds_response(self):
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def geturl(self):return saved['request'].full_url
            def read(self,n):saved['limit']=n;return b'{"code":0,"data":{}}'
        class Opener:
            def open(self,request,timeout):saved.update(request=request,timeout=timeout);return Response()
        saved={}
        with patch('monitor.material_readback.urllib.request.build_opener',return_value=Opener()):
            _get('materials',{'advertiser_id':'123','ad_id':'2001','page':1},'FAKE',10)
        self.assertEqual(saved['request'].method,'GET');self.assertEqual(saved['timeout'],10)
        self.assertEqual(urllib.parse.urlsplit(saved['request'].full_url).path,PATH)
        self.assertEqual(saved['limit'],1024*1024+1)


if __name__=='__main__':unittest.main()
