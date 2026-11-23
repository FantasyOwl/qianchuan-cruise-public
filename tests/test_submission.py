import copy
import json
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path
from unittest.mock import Mock, patch

from monitor.actuation import prepare_action
from monitor.evaluation import evaluate_all
from monitor.submission import AttemptLedger, BudgetClient, BudgetPermit, _post_budget
from test_engine import NOW, series, settings


def fixture():
    cfg=settings();account='1999999999999002';cfg['accounts'][0]['id']=account
    rows=series(roi=3)
    for r in rows:
        r.update(account_id=account,entity_id='8012367229158357')
        r['context']={'budget_mode':'specified'}
    p=next(p for p in evaluate_all(rows,cfg,NOW)['recommendations'] if p['action']=='budget_up')
    preview=prepare_action(p,rows[-1],cfg,NOW)
    permit=BudgetPermit(account,preview['digest'],NOW,NOW+timedelta(minutes=2),'future-test-only-authorization')
    return rows,cfg,p,permit


class SubmissionTests(unittest.TestCase):
    def setUp(self):
        clock=patch('monitor.submission.now_cn',return_value=NOW);clock.start();self.addCleanup(clock.stop)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'attempts.db';self.ledger=AttemptLedger(self.path)

    def test_default_denies_before_token_or_transport_read(self):
        rows,cfg,p,permit=fixture();token=Mock();transport=Mock()
        with self.assertRaises(PermissionError):
            BudgetClient(self.ledger,transport=transport).dispatch('one',p['id'],rows,cfg,permit,token,NOW)
        token.assert_not_called();transport.assert_not_called()

    def test_revalidated_budget_exact_id_and_mock_response_are_audited_without_secret(self):
        rows,cfg,p,permit=fixture();calls=[]
        def transport(body,token,timeout):
            calls.append((json.loads(body),timeout));self.assertEqual(token,'test-secret')
            return {'code':0,'data':{'results':[{'ad_id':8012367229158357,'status':'SUCCESS'}]}}
        client=BudgetClient(self.ledger,enable_network=True,transport=transport)
        result=client.dispatch('one',p['id'],rows,cfg,permit,lambda:'test-secret',NOW)
        self.assertEqual(calls[0][0]['update_budget_infos'][0]['ad_id'],8012367229158357)
        self.assertEqual(result['state'],'confirmed');self.assertFalse(result['platform_state_observed'])
        with self.ledger.connect() as db:stored=str(db.execute('SELECT * FROM submission_attempts').fetchall())
        self.assertNotIn('test-secret',stored)
        self.assertIn('8012367229158357',stored);self.assertIn('before',stored);self.assertIn('after',stored)

    def test_expired_mismatched_or_config_dict_permits_do_not_read_token(self):
        rows,cfg,p,permit=fixture();token=Mock();transport=Mock()
        permits=[{'approved':True},
                 BudgetPermit(permit.account_id,permit.request_digest,NOW-timedelta(minutes=10),NOW,'reference'),
                 BudgetPermit('other',permit.request_digest,NOW,NOW+timedelta(minutes=2),'reference'),
                 BudgetPermit(permit.account_id,'tampered',NOW,NOW+timedelta(minutes=2),'reference'),
                 BudgetPermit(permit.account_id,permit.request_digest,NOW,NOW+timedelta(minutes=6),'reference')]
        for value in permits:
            with self.assertRaises(PermissionError):
                BudgetClient(self.ledger,enable_network=True,transport=transport).dispatch('one',p['id'],rows,cfg,value,token,NOW)
        token.assert_not_called();transport.assert_not_called()

    def test_current_target_or_snapshot_change_invalidates_prior_review(self):
        rows,cfg,p,permit=fixture();token=Mock();transport=Mock()
        for mutate in (lambda r,c:c['accounts'][0].update(target_roi=None),
                       lambda r,c:r[-1]['metrics'].update(budget=11000),
                       lambda r,c:r[-1]['control'].update(guarantee_verified=False)):
            r,c=copy.deepcopy((rows,cfg));mutate(r,c)
            with self.assertRaises((ValueError,PermissionError)):
                BudgetClient(self.ledger,enable_network=True,transport=transport).dispatch('one',p['id'],r,c,permit,token,NOW)
        token.assert_not_called();transport.assert_not_called()

    def test_timeout_is_unknown_redacted_and_same_body_cannot_replay_under_new_id(self):
        rows,cfg,p,permit=fixture();transport=Mock(side_effect=TimeoutError('test-secret echoed'))
        client=BudgetClient(self.ledger,enable_network=True,transport=transport)
        r=client.dispatch('one',p['id'],rows,cfg,permit,lambda:'test-secret',NOW)
        self.assertEqual(r['state'],'unknown');self.assertNotIn('test-secret',json.dumps(r))
        for attempt in ['one','different-id']:
            with self.assertRaises(ValueError):client.dispatch(attempt,p['id'],rows,cfg,permit,lambda:'test-secret',NOW)
        self.assertEqual(transport.call_count,1)

    def test_claim_survives_restart_without_resending(self):
        rows,cfg,p,permit=fixture();self.ledger.claim('crash',permit.request_digest,NOW)
        transport=Mock()
        with self.assertRaises(ValueError):
            BudgetClient(AttemptLedger(self.path),enable_network=True,transport=transport).dispatch('later',p['id'],rows,cfg,permit,lambda:'test',NOW)
        transport.assert_not_called()

    def test_empty_or_failed_business_result_is_never_success(self):
        rows,cfg,p,permit=fixture()
        client=BudgetClient(self.ledger,enable_network=True,transport=lambda *args:{'code':0,'data':{}})
        self.assertEqual(client.dispatch('one',p['id'],rows,cfg,permit,lambda:'test',NOW)['state'],'unknown')

    def test_transport_posts_only_fixed_endpoint_and_disables_redirects(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=b'{"code":0}'
        opener=Mock();opener.open.return_value=response
        with patch('monitor.submission.urllib.request.build_opener',return_value=opener) as build:
            self.assertEqual(_post_budget('{"advertiser_id":1}','test',10),{'code':0})
        request=opener.open.call_args.args[0]
        self.assertEqual(request.full_url,'https://api.oceanengine.com/open_api/v1.0/qianchuan/uni_promotion/ad/budget/update/')
        self.assertEqual(request.method,'POST');self.assertEqual(request.data,b'{"advertiser_id":1}')
        self.assertIsNone(build.call_args.args[1].redirect_request(None,None,None,None,None,None))


if __name__=='__main__':unittest.main()
