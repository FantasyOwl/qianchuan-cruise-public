import copy
import unittest

from monitor.schema import normalize_snapshot
from test_engine import NOW,series,settings


def card(segment):
    row=series()[-1]
    row['source'].update(kind='browser_dom',url='https://qianchuan.jinritemai.com/uni-prom/detail?aavid=123',
        coverage='visible_only',segment=segment)
    row['metrics']={'base_spend':100,'base_gmv':250,'base_roi':2.5,'base_orders':2,'budget':10000}
    return row


class PlanCardSegmentTests(unittest.TestCase):
    def test_overall_and_base_views_have_distinct_sources_and_both_lack_independent_clocks(self):
        base=normalize_snapshot(card('plan_card_base'),settings(),NOW)
        raw=card('plan_card_overall');raw['metrics']={'spend':120,'gmv':300,'roi':2.5,'orders':3,'budget':10000}
        overall=normalize_snapshot(raw,settings(),NOW)
        self.assertNotEqual(base['source']['series_key'],overall['source']['series_key'])
        self.assertNotEqual(base['id'],overall['id'])
        self.assertIsNone(base['source']['data_at']);self.assertIsNone(overall['source']['data_at'])
        self.assertEqual(base['metrics']['base_spend'],100);self.assertNotIn('spend',base['metrics'])
        self.assertEqual(overall['metrics']['spend'],120)

    def test_base_segment_rejects_overall_spend_roi_orders_or_account_balance(self):
        for key in ('spend','roi','orders','balance','target_roi'):
            row=card('plan_card_base');row['metrics'][key]=1
            with self.subTest(key=key),self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)

    def test_card_segments_cannot_be_borrowed_by_other_paths_scopes_periods_or_imports(self):
        original=card('plan_card_base')
        for field,value in [('url','https://qianchuan.jinritemai.com/uni-prom?aavid=123'),('kind','import'),
            ('metric_period','live_session'),('segment','plan_card_unknown')]:
            row=copy.deepcopy(original);row['source'][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)
        row=copy.deepcopy(original);row['scope']='account'
        with self.assertRaises(ValueError):normalize_snapshot(row,settings(),NOW)


if __name__=='__main__':unittest.main()
