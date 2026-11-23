import copy
import unittest
from datetime import datetime,timedelta
from monitor.plan_metrics import account_metrics

NOW=datetime.fromisoformat('2026-10-26T10:00:00+08:00')
ACCOUNT={'id':'123','scene':'live'}

def row(**changes):
    r={'id':'s1','account_id':'123','entity_id':'900','scope':'plan','display_name':'计划甲',
       'metric_basis':'marketing','metrics':{'spend':100,'marketing_roi':2.8,'payment_orders':0},
       'source':{'kind':'browser_dom','captured_at':NOW.isoformat(),'metric_date':'2026-10-26','metric_period':'day','data_at':None},
       'control':{'running':True},'quality':{'issues':[]}}
    for k,v in changes.items():
        if isinstance(v,dict):r.setdefault(k,{}).update(v)
        else:r[k]=v
    return r


class MetricsTests(unittest.TestCase):
    def view(self,rows,surfaces=()):return account_metrics(ACCOUNT,rows,surfaces,NOW)

    def test_live_account_data_is_connected_without_an_execution_surface(self):
        r=row(scope='account',entity_id='123',metric_basis='overall_payment',metrics={'roi':3.1})
        v=self.view([r]);self.assertTrue(v['data_connected']);self.assertEqual(v['execution_plan_count'],0)
        self.assertEqual(v['summary']['metrics']['roi'],3.1)

    def test_zero_is_preserved_and_roi_bases_are_not_substituted(self):
        p=self.view([row()])['plans'][0]
        self.assertEqual(p['metrics']['payment_orders'],0)
        self.assertEqual(p['metrics']['marketing_roi'],2.8);self.assertNotIn('roi',p['metrics'])

    def test_new_missing_cell_does_not_resurrect_old_value(self):
        old=row();new=row(id='new',source={'captured_at':(NOW+timedelta(seconds=1)).isoformat()})
        new['metrics'].pop('marketing_roi')
        p=self.view([old,new])['plans'][0]
        self.assertNotIn('marketing_roi',p['metrics'])

    def test_latest_whole_row_wins_without_merging_old_basis_date_or_base_scope(self):
        current=row(source={'captured_at':(NOW-timedelta(seconds=1)).isoformat()});old=row(id='yesterday',source={'metric_date':'2026-10-25'},metrics={'spend':500})
        base=row(id='base',source={'segment':'plan_card_base'},metrics={'spend':77})
        net=row(id='net',metric_basis='net');net['metrics']={'spend':105,'net_roi':2.7}
        rows=self.view([current,old,base,net])['plans']
        self.assertEqual(len(rows),3)
        self.assertEqual(next(r['metrics']['spend'] for r in rows if r['metric_date']=='2026-10-25'),500)
        self.assertTrue(any(r['segment']=='plan_card_base' for r in rows))
        latest=next(r for r in rows if r['metric_date']=='2026-10-26' and r['segment']=='overall')
        self.assertNotIn('marketing_roi',latest['metrics']);self.assertEqual(latest['metrics']['net_roi'],2.7)

    def test_config_only_snapshot_does_not_erase_performance_or_invent_live_data(self):
        config=row(id='config',metric_basis='unknown',source={'captured_at':(NOW+timedelta(seconds=10)).isoformat()})
        config['metrics']={'budget':1000}
        p=self.view([row(),config])['plans']
        self.assertEqual(len(p),1);self.assertEqual(p[0]['metrics']['spend'],100)
        self.assertEqual(self.view([config])['plans'],[])

    def test_foreign_accounts_and_demo_are_excluded(self):
        v=self.view([row(account_id='456'),row(source={'kind':'demo'})])
        self.assertEqual(v['plans'],[]);self.assertFalse(v['data_connected'])

    def test_same_time_conflict_is_not_resolved_by_iteration_order(self):
        a=row();b=row(id='conflict',metrics={'spend':999})
        for rows in ([a,b],[b,a]):self.assertEqual(self.view(rows)['plans'],[])

    def test_execution_config_has_a_separate_clock_and_does_not_make_old_roi_fresh(self):
        s={'account_id':'123','plan_id':'900','name':'原生计划','budget':'2000.00','target_roi':'3.00','roi_basis':'net','running':True,'captured_at':NOW.isoformat()}
        old=row(source={'captured_at':(NOW-timedelta(hours=2)).isoformat(),'metric_date':'2026-10-25'})
        p=self.view([old],[s])['plans'][0]
        self.assertFalse(p['read_fresh']);self.assertFalse(p['is_today'])
        self.assertTrue(p['execution_ready']);self.assertEqual(p['configuration']['roi_basis'],'net')

    def test_balance_only_read_does_not_hide_today_account_performance(self):
        good=row(scope='account',entity_id='123',metric_basis='overall_payment',metrics={'roi':3.2})
        balance=row(scope='account',entity_id='123',metric_basis='unknown',source={'captured_at':(NOW+timedelta(seconds=30)).isoformat()})
        balance['metrics']={'balance':10000}
        self.assertEqual(self.view([good,balance])['summary']['metrics']['roi'],3.2)

    def test_product_view_provenance_survives_ingestion_and_keeps_series_separate(self):
        from monitor.schema import normalize_snapshot
        a=row(source={'url':'https://qianchuan.jinritemai.com/overall-prom?aavid=123'},context={'observed_plan_view':'1'})
        b=copy.deepcopy(a);b['context']['observed_plan_view']='2'
        cfg={'accounts':[{'id':'123','scene':'product'}]}
        left=normalize_snapshot(a,cfg,received_at=NOW);right=normalize_snapshot(b,cfg,received_at=NOW)
        self.assertEqual(left['context']['observed_plan_view'],'1')
        self.assertNotEqual(left['source']['series_key'],right['source']['series_key'])


if __name__=='__main__':unittest.main()
