import copy
import unittest
from datetime import timedelta

from monitor.accounts import collection_policy, registry_view, replace_registry, validate_collection_batch
from test_engine import NOW


def settings():
    return {'accounts':[{'id':'101','name':'甲直播','scene':'live','tab_group':'甲组','observe':True,'target_roi':2.5,'roi_basis':'overall_payment'},
                        {'id':'202','name':'乙图文','scene':'product','tab_group':'乙组','observe':True,'target_roi':None,'roi_basis':'net'}],
            'rules':{'budget_step_up':.2},'ai':{'enabled':False},'legacy_root':'unchanged'}


def diagnostic(cfg):
    return {'collector':'extension','collector_protocol':2,'policy_revision':collection_policy(cfg)['revision'],'last_seen':NOW.isoformat()}


def payload(cfg):
    view=registry_view(cfg)
    return {'expected_revision':view['revision'],'accounts':copy.deepcopy(view['accounts'])}


class AccountRegistryTests(unittest.TestCase):
    def test_authorized_api_registry_can_add_an_account_without_chrome(self):
        cfg=settings();p=payload(cfg)
        p['accounts'].append({'id':'303','name':'API新增户','scene':'live','delivery_entry':'both','tab_group':'','observe':True})
        changed=replace_registry(cfg,p,None,NOW,api_connected=True)
        self.assertEqual(changed['accounts'][-1]['tab_group'],'API账户')
        self.assertIsNone(changed['accounts'][-1]['target_roi'])
        view=registry_view(changed,None,NOW,api_connected=True)
        self.assertTrue(view['scope_changes_available']);self.assertFalse(view['collector_ready_for_scope_changes'])
        self.assertEqual(cfg,settings())
    def test_multiple_same_scene_accounts_and_distinct_groups_remain_separate(self):
        cfg=settings();p=payload(cfg);p['accounts'].append({'id':'303','name':'丙直播','scene':'live','tab_group':'丙组','observe':True})
        changed=replace_registry(cfg,p,diagnostic(cfg),NOW)
        self.assertEqual(len(collection_policy(changed)['accounts']),3)
        self.assertEqual(changed['accounts'][0]['target_roi'],2.5)
        self.assertIsNone(changed['accounts'][1]['target_roi'])
        self.assertIsNone(changed['accounts'][2]['target_roi'])
        self.assertEqual(changed['accounts'][2]['roi_basis'],'overall_payment')
        self.assertEqual(changed['rules'],cfg['rules']);self.assertEqual(changed['ai'],cfg['ai'])
        self.assertEqual(cfg,settings())

    def test_old_collector_unknown_stale_or_wrong_scope_cannot_expand_registry(self):
        cfg=settings();p=payload(cfg);p['accounts'][0]['tab_group']='改组'
        for d in [None,{'collector_protocol':1},dict(diagnostic(cfg),last_seen=(NOW-timedelta(minutes=4)).isoformat()),
                  dict(diagnostic(cfg),policy_revision='0'*64),dict(diagnostic(cfg),collector='codex_plugin')]:
            with self.subTest(d=d),self.assertRaises(ValueError):replace_registry(cfg,p,d,NOW)

    def test_display_name_only_can_change_without_affecting_read_scope_or_target(self):
        cfg=settings();p=payload(cfg);p['accounts'][0]['name']='新的显示名称'
        changed=replace_registry(cfg,p,None,NOW)
        self.assertEqual(collection_policy(changed),collection_policy(cfg))
        self.assertEqual(changed['accounts'][0]['target_roi'],2.5)

    def test_api_entry_is_account_local_and_does_not_require_browser_connection(self):
        cfg=settings();p=payload(cfg);p['accounts'][0]['delivery_entry']='both'
        changed=replace_registry(cfg,p,None,NOW)
        self.assertEqual(changed['accounts'][0]['delivery_entry'],'both')
        self.assertEqual(changed['accounts'][1]['delivery_entry'],'uni_promotion')
        self.assertEqual(collection_policy(changed),collection_policy(cfg))
        self.assertEqual(changed['accounts'][0]['target_roi'],2.5)
        self.assertEqual(changed['accounts'][1]['roi_basis'],'net')
        # A pre-upgrade client cannot silently reset a saved API entry.
        old_client=payload(changed)
        for row in old_client['accounts']:row.pop('delivery_entry')
        again=replace_registry(changed,old_client,None,NOW)
        self.assertEqual(again['accounts'][0]['delivery_entry'],'both')

    def test_api_entry_rejects_unknown_and_nonstrings_before_saving(self):
        for entry in ('standard','',None,{},True):
            cfg=settings();p=payload(cfg);p['accounts'][0]['delivery_entry']=entry
            with self.subTest(entry=entry),self.assertRaises(ValueError):
                replace_registry(cfg,p,None,NOW)

    def test_registry_revision_includes_target_updates_to_prevent_stale_overwrite(self):
        cfg=settings();p=payload(cfg);cfg['accounts'][0]['target_roi']=3
        with self.assertRaisesRegex(ValueError,'已变化'):replace_registry(cfg,p,diagnostic(cfg),NOW)

    def test_disabled_and_removed_accounts_leave_read_policy_but_do_not_create_targets(self):
        cfg=settings();p=payload(cfg);p['accounts'][0]['observe']=False;p['accounts'].pop()
        changed=replace_registry(cfg,p,diagnostic(cfg),NOW)
        self.assertEqual(collection_policy(changed)['accounts'],[])
        self.assertEqual(changed['accounts'][0]['target_roi'],2.5)

    def test_invalid_ids_groups_duplicates_switches_and_payload_fields_are_rejected(self):
        for update in [{'id':'101&x=1'},{'scene':'unknown'},{'scene':{}},{'tab_group':''},{'tab_group':'x\nwrong'},
                       {'observe':'true'},{'name':''},{'target_roi':99}]:
            cfg=settings();p=payload(cfg);p['accounts'][0].update(update)
            with self.subTest(update=update),self.assertRaises(ValueError):replace_registry(cfg,p,diagnostic(cfg),NOW)
        cfg=settings();p=payload(cfg);p['accounts'][1]['id']='101'
        with self.assertRaises(ValueError):replace_registry(cfg,p,diagnostic(cfg),NOW)

    def test_local_account_cap_and_nonempty_registry_are_explicit(self):
        cfg=settings();p=payload(cfg);p['accounts']=[]
        with self.assertRaises(ValueError):replace_registry(cfg,p,diagnostic(cfg),NOW)
        p['accounts']=[{'id':str(i),'name':'账户','scene':'live','tab_group':'指定组','observe':False} for i in range(101)]
        with self.assertRaises(ValueError):replace_registry(cfg,p,diagnostic(cfg),NOW)

    def test_scope_revision_is_stable_for_display_names_order_and_targets_only(self):
        cfg=settings();policy=collection_policy(cfg)
        cfg['accounts'].reverse();cfg['accounts'][0].update(name='修改',target_roi=9)
        self.assertEqual(policy,collection_policy(cfg))
        cfg['accounts'][0]['tab_group']='新组'
        self.assertNotEqual(policy['revision'],collection_policy(cfg)['revision'])

    def test_new_protocol_rejects_stale_revision_and_disabled_account_atomically(self):
        cfg=settings();policy=collection_policy(cfg)
        good={'collector_protocol':2,'policy_revision':policy['revision'],'snapshots':[{'account_id':'101'}]}
        validate_collection_batch(cfg,good)
        for invalid in [dict(good,collector_protocol=True),dict(good,policy_revision='0'*64),
                        dict(good,snapshots=[{'account_id':'101'},{'account_id':'999'}])]:
            with self.assertRaises(ValueError):validate_collection_batch(cfg,invalid)
        cfg['accounts'][0]['observe']=False
        with self.assertRaises(ValueError):validate_collection_batch(cfg,good)

    def test_legacy_payload_is_only_compatible_with_original_two_account_scope(self):
        cfg={'accounts':[{'id':'1999999999999002','name':'直播','scene':'live'},
                         {'id':'1999999999999001','name':'图文','scene':'product'}]}
        validate_collection_batch(cfg,{'snapshots':[]})
        cfg['accounts'][0]['tab_group']='不同组'
        with self.assertRaises(ValueError):validate_collection_batch(cfg,{'snapshots':[]})


if __name__=='__main__':unittest.main()
