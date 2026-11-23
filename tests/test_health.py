import copy
import json
import shutil
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from monitor.ai import make_package
from monitor.evaluation import evaluate_all
from monitor.health import runtime_health, source_health
from monitor.reporting import build_report, report_markdown
from monitor.server import App, ROOT
from monitor.store import Store
from test_engine import NOW, series, settings


class SourceHealthTests(unittest.TestCase):
    def test_current_partial_evidence_is_never_a_continuity_or_coverage_certificate(self):
        h=source_health(series(),settings(),NOW)
        self.assertEqual(h['accounts'][0]['state'],'partial_observation')
        self.assertEqual(h['sources'][0]['progress'],'advancing')
        self.assertFalse(h['continuous_coverage_verified']);self.assertTrue(h['accounts'][0]['unverified_coverage'])

    def test_null_target_does_not_hide_collection_status(self):
        cfg=settings();cfg['accounts'][0]['target_roi']=None
        h=source_health(series(),cfg,NOW)
        self.assertTrue(h['sources'][0]['clock_current']);self.assertEqual(h['accounts'][0]['state'],'partial_observation')

    def test_missing_and_synthetic_data_do_not_count_as_real_collection(self):
        self.assertEqual(source_health([],settings(),NOW)['accounts'][0]['state'],'missing')
        rows=series()
        for row in rows:row['source']['kind']='demo'
        h=source_health(rows,settings(),NOW)
        self.assertEqual(h['accounts'][0]['state'],'synthetic_only');self.assertEqual(h['accounts'][0]['clock_current_sources'],0)

    def test_fresh_capture_does_not_fill_missing_data_time(self):
        row=series()[-1];row['source']['data_at']=None
        h=source_health([row],settings(),NOW)
        self.assertEqual(h['sources'][0]['capture_state'],'recent');self.assertEqual(h['sources'][0]['data_state'],'missing')
        self.assertIsNone(h['sources'][0]['data_at']);self.assertEqual(h['accounts'][0]['state'],'data_time_missing')

    def test_repeated_reads_do_not_refresh_old_metrics(self):
        rows=series()[-2:]
        for row in rows:row['source']['data_at']=(NOW-timedelta(minutes=15)).isoformat()
        h=source_health(rows,settings(),NOW)
        self.assertEqual(h['sources'][0]['progress'],'unchanged');self.assertEqual(h['sources'][0]['data_state'],'expired')
        self.assertFalse(h['sources'][0]['clock_current'])

    def test_equal_recent_data_time_is_not_reported_as_stopped_spend(self):
        rows=series()[-2:]
        for row in rows:row['source']['data_at']=(NOW-timedelta(minutes=10)).isoformat()
        h=source_health(rows,settings(),NOW)
        self.assertEqual(h['sources'][0]['progress'],'unchanged');self.assertTrue(h['sources'][0]['clock_current'])

    def test_newer_capture_with_regressed_data_blocks_older_profitable_budget_proposal(self):
        rows=series(roi=3);baseline=evaluate_all(rows,settings(),NOW)
        self.assertTrue(any(r['eligible'] and r['action']=='budget_up' for r in baseline['recommendations']))
        row=copy.deepcopy(rows[-1]);row['id']='new-regressed'
        row['source'].update(captured_at=(NOW+timedelta(seconds=1)).isoformat(),data_at=(NOW-timedelta(minutes=1)).isoformat())
        rows.append(row);e=evaluate_all(rows,settings(),NOW)
        self.assertEqual(e['collection_health']['sources'][0]['snapshot_id'],'new-regressed')
        self.assertEqual(e['collection_health']['sources'][0]['progress'],'regressed')
        rec=next(r for r in e['recommendations'] if r['action']=='budget_up')
        self.assertFalse(rec['eligible']);self.assertTrue(any('最新采集' in x for x in rec['blockers']))
        self.assertNotEqual(e['entities'][0]['quality'],'fresh')

    def test_recovered_clock_can_advance_after_a_regression(self):
        rows=series();row=copy.deepcopy(rows[-1]);row['source'].update(captured_at=(NOW+timedelta(seconds=1)).isoformat(),data_at=(NOW-timedelta(minutes=1)).isoformat());rows.append(row)
        row=copy.deepcopy(rows[-1]);row['source'].update(captured_at=(NOW+timedelta(seconds=2)).isoformat(),data_at=(NOW+timedelta(seconds=2)).isoformat());rows.append(row)
        h=source_health(rows,settings(),NOW+timedelta(seconds=2))
        self.assertEqual(h['sources'][0]['progress'],'advancing');self.assertTrue(h['sources'][0]['clock_current'])

    def test_coincident_different_data_clocks_are_ambiguous_in_either_order(self):
        rows=series();row=copy.deepcopy(rows[-1]);row['id']='conflict';row['source']['data_at']=(NOW-timedelta(seconds=60)).isoformat()
        for values in (rows+[row],[row]+rows):
            h=source_health(values,settings(),NOW)
            self.assertEqual(h['sources'][0]['data_state'],'conflicting_time');self.assertFalse(h['sources'][0]['clock_current'])

    def test_historical_day_is_not_current_but_live_session_keeps_independent_clock(self):
        row=series()[-1];row['source'].update(metric_date=(NOW-timedelta(days=1)).date().isoformat())
        h=source_health([row],settings(),NOW);self.assertEqual(h['sources'][0]['data_state'],'historical')
        row['source']['metric_period']='live_session'
        h=source_health([row],settings(),NOW);self.assertEqual(h['sources'][0]['data_state'],'recent')

    def test_source_and_account_boundaries_are_not_merged(self):
        row=series()[-1];other=copy.deepcopy(row);other['source'].update(series_key='independent',data_at=None)
        outsider=copy.deepcopy(row);outsider['account_id']='not-allowed'
        h=source_health([row,other,outsider],settings(),NOW)
        self.assertEqual(len(h['sources']),2);self.assertEqual(h['accounts'][0]['clock_current_sources'],1)

    def test_identical_source_and_entity_ids_in_other_scope_do_not_block_plan_growth(self):
        rows=series(roi=3);other=copy.deepcopy(rows[-1]);other['scope']='creative';other['source']['data_at']=None
        e=evaluate_all(rows+[other],settings(),NOW)
        self.assertTrue(any(r['eligible'] and r['action']=='budget_up' and r['scope']=='plan' for r in e['recommendations']))

    def test_read_time_consistency_and_data_date_are_checked(self):
        row=series()[-1];row['source']['captured_at']=(NOW-timedelta(minutes=3)).isoformat()
        self.assertEqual(source_health([row],settings(),NOW)['sources'][0]['data_state'],'invalid_time')
        row=series()[-1];row['source']['data_at']=(NOW-timedelta(days=1)).isoformat()
        self.assertEqual(source_health([row],settings(),NOW)['sources'][0]['data_state'],'date_mismatch')

    def test_input_is_immutable_and_ai_identifiers_are_anonymized(self):
        rows=series();before=copy.deepcopy(rows);e=evaluate_all(rows,settings(),NOW)
        p=make_package(rows,e);self.assertEqual(rows,before)
        self.assertNotIn('plan1',json.dumps(p['collection_health']));self.assertNotIn('report1',json.dumps(p['collection_health']))
        self.assertTrue(p['collection_health'][0]['clock_current'])

    def test_export_scopes_collection_evidence_to_the_selected_account(self):
        cfg=settings();cfg['accounts'].append({'id':'other','scene':'live','target_roi':2,'roi_basis':'overall_payment'})
        rows=series();other=copy.deepcopy(rows[-1]);other.update(account_id='other',entity_id='PRIVATE_OTHER');rows.append(other)
        report=build_report(rows,cfg,evaluate_all(rows,cfg,NOW),'123',NOW)
        self.assertNotIn('PRIVATE_OTHER',json.dumps(report));self.assertEqual(len(report['evidence_and_risks']['collection_health']['accounts']),1)
        self.assertIn('持续采集与指标时效',report_markdown(report))


class RuntimeHealthTests(unittest.TestCase):
    def health(self,cycles=None,diagnostics=None,run_id='new',now=NOW):
        return runtime_health(diagnostics or {},cycles or [],run_id,NOW.isoformat(),60,now)

    def cycle(self,n=1,run='new',start=NOW,finish=NOW,state='ok'):
        return {'id':n,'run_id':run,'started_at':start.isoformat(),'finished_at':finish.isoformat() if finish else None,'state':state,'added':0,'error_type':None}

    def test_missing_first_cycle_is_starting_then_stalled_without_fabricated_attempts(self):
        self.assertEqual(self.health()['collector']['state'],'starting')
        h=self.health(now=NOW+timedelta(seconds=181));self.assertEqual(h['collector']['state'],'stalled');self.assertEqual(h['history']['retained_cycles'],0)

    def test_only_old_run_success_does_not_certify_current_collector(self):
        h=self.health([self.cycle(run='old')]);self.assertEqual(h['collector']['state'],'starting')
        self.assertFalse(h['continuous_coverage_verified'])

    def test_running_poll_can_become_stalled(self):
        row=self.cycle(finish=None,state='running')
        self.assertEqual(self.health([row])['collector']['state'],'running')
        self.assertEqual(self.health([row],now=NOW+timedelta(minutes=4))['collector']['state'],'stalled')

    def test_fresh_heartbeat_does_not_clear_poll_failure_or_old_data(self):
        row=self.cycle(state='error');row['error_type']='OSError'
        h=self.health([row],{'extension':{'last_seen':NOW.isoformat()}})
        self.assertEqual(h['browser_communication']['state'],'recent');self.assertEqual(h['collector']['state'],'error')

    def test_most_recent_server_received_communication_wins_and_future_clock_is_invalid(self):
        h=self.health(diagnostics={'extension':{'last_seen':(NOW-timedelta(hours=2)).isoformat(),'last_ingest_at':NOW.isoformat()}})
        self.assertEqual(h['browser_communication']['state'],'recent')
        h=self.health(diagnostics={'extension':{'last_seen':(NOW+timedelta(hours=2)).isoformat()}})
        self.assertEqual(h['browser_communication']['state'],'invalid_time')

    def test_restart_gap_and_unfinished_old_poll_are_preserved_without_inventing_sampling(self):
        rows=[self.cycle(1,'old',NOW-timedelta(hours=1),NOW-timedelta(hours=1)),self.cycle(2,'old',NOW-timedelta(minutes=50),None,'running'),self.cycle(3)]
        h=self.health(rows);self.assertEqual(h['history']['restart_transitions'],1)
        self.assertEqual(h['history']['previous_unfinished_cycles'],1);self.assertEqual(len(h['history']['observed_gaps']),1)
        self.assertEqual(h['history']['retained_cycles'],3)

    def test_closed_previous_run_records_a_restart_gap(self):
        rows=[self.cycle(1,'old',NOW-timedelta(hours=1),NOW-timedelta(hours=1)),self.cycle(2)]
        gap=self.health(rows)['history']['observed_gaps'][0]
        self.assertTrue(gap['across_restart']);self.assertEqual(gap['seconds'],3600)


class PersistedHealthTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        shutil.copyfile(ROOT/'config.json',self.root/'config.json');self.app=App(self.root)
    def tearDown(self):self.temp.cleanup()

    def test_poll_error_and_recovery_survive_store_reopen_without_source_writes(self):
        with patch.object(self.app,'refresh_legacy',side_effect=RuntimeError('secret-not-for-diagnostics')):
            self.assertEqual(self.app.poll_once()['state'],'error')
        with patch.object(self.app,'refresh_legacy',return_value={'added':0}):self.assertEqual(self.app.poll_once()['state'],'ok')
        reopened=Store(self.root/'data/monitor.db');rows=reopened.cycles()
        self.assertEqual([r['state'] for r in rows],['error','ok']);self.assertEqual(rows[0]['error_type'],'RuntimeError')
        self.assertNotIn('secret',json.dumps(rows));self.assertIsNone(reopened.diagnostics()['collector']['error'])
        self.assertEqual(reopened.snapshots(),[]);self.assertEqual(reopened.events(),[]);self.assertEqual(reopened.migrations(),[])

    def test_heartbeat_and_ingest_diagnostics_preserve_both_server_clocks(self):
        self.app.store.patch_diagnostic('extension',{'last_ingest_at':NOW.isoformat(),'received':1})
        self.app.store.patch_diagnostic('extension',{'last_seen':(NOW+timedelta(seconds=1)).isoformat(),'issues':[]})
        value=self.app.store.diagnostics()['extension'];self.assertEqual(value['last_ingest_at'],NOW.isoformat());self.assertIn('last_seen',value)

    def test_new_app_keeps_old_incomplete_cycle_but_starts_without_success_claim(self):
        self.app.store.begin_cycle(self.app.run_id,NOW.isoformat());other=App(self.root)
        h=runtime_health(other.store.diagnostics(),other.store.cycles(),other.run_id,other.started_at,60)
        self.assertEqual(h['history']['previous_unfinished_cycles'],1);self.assertEqual(h['collector']['state'],'starting')

    def test_retention_is_bounded_without_truncating_business_snapshots(self):
        with self.app.store.connect() as c:
            c.executemany('INSERT INTO monitor_cycles(run_id,started_at,state) VALUES (?,?,?)',
                [('synthetic-old',NOW.isoformat(),'ok')]*10001)
        cycle_id=self.app.store.begin_cycle('new',NOW.isoformat());rows=self.app.store.cycles()
        self.assertEqual(len(rows),10000);self.assertEqual(rows[-1]['id'],cycle_id)
        self.assertEqual(rows[-1]['state'],'running');self.assertEqual(self.app.store.snapshots(),[])
