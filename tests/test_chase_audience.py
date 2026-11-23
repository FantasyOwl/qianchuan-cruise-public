import copy,unittest
from monitor.chase_audience import validate,configured,DEFAULT,check_api_fields
from monitor.cruise_rules import default_profile,validate_profile
from monitor.official_workflow import digest
from monitor.official_contracts import chase_contract

class AudienceTests(unittest.TestCase):
    def profile(self):
        p=default_profile();p['features']['f5']['rules']=[{'chase_type':'控成本追投','material_spend_min':100,'material_roi_min':2,'daily_budget':300,'bid_value':25,'optimization_target':'直播间成交'}]
        return validate_profile(p)
    def test_unlimited_defaults_preserve_existing_rules_and_grant_digest(self):
        p=self.profile();original=digest(p);p['features']['f5']['rules'][0]['audience']=dict(DEFAULT)
        self.assertEqual(digest(validate_profile(p)),original)
    def test_three_fields_roundtrip_without_region_or_other_targeting(self):
        p=self.profile();p['features']['f5']['rules'][0]['audience']={'gender':'female','ages':['31-40','18-23'],'exclude_fans':True}
        row=validate_profile(p)['features']['f5']['rules'][0]
        self.assertEqual(row['audience'],{'gender':'female','ages':['18-23','31-40'],'exclude_fans':True});self.assertTrue(configured(row))
        with self.assertRaises(ValueError):chase_contract(row)
        self.assertEqual(chase_contract(row,for_browser_readback=True)['body']['budget'],300)
    def test_invalid_or_unsupported_settings_are_rejected(self):
        for value in [{'region':'杭州'},{'gender':'x'},{'ages':['under18']},{'ages':['18-23','18-23']},{'exclude_fans':'true'}]:
            with self.subTest(value=value),self.assertRaises(ValueError):validate(value)
    def test_targeting_stays_in_its_rule_and_is_inactive_for_volume(self):
        rule={'chase_type':'放量追投','audience':{'gender':'female'},'total_budget':200,'control_duration':2}
        self.assertFalse(configured(rule));self.assertEqual(chase_contract(rule)['body']['budget'],200)
    def test_api_proof_does_not_claim_absent_fields_and_rejects_contradictory_fields(self):
        expected={'gender':'female','ages':['18-23','31-40'],'exclude_fans':True}
        self.assertEqual(check_api_fields(None,expected),{})
        self.assertEqual(check_api_fields({'gender':'GENDER_FEMALE','age':['AGE_BETWEEN_31_40','AGE_BETWEEN_18_23']},expected),{'gender':True,'ages':True})
        for raw in ({'gender':'GENDER_MALE'},{'age':['AGE_BETWEEN_18_23']},{'age':['AGE_BETWEEN_0_17']}):
            with self.subTest(raw=raw),self.assertRaises(ValueError):check_api_fields(raw,expected)

if __name__=='__main__':unittest.main()
