import io
import json
import unittest
import urllib.error
from unittest.mock import Mock, patch

from monitor.ai import request_analysis, make_package, NoRedirect
from monitor.evaluation import evaluate_all
from test_engine import series,settings,NOW


class AITransportTests(unittest.TestCase):
    def setUp(self):
        self.evaluation=evaluate_all(series(roi=3),settings(),NOW)
        self.package=make_package(series(roi=3),self.evaluation)
        self.cfg={'enabled':True,'endpoint':'https://ai-test.invalid/v1/chat/completions','model':'offline-test'}
        self.environment=patch.dict('os.environ',{'QIANCHUAN_AI_API_KEY':'FAKE_TEST_SECRET'})
        self.environment.start();self.addCleanup(self.environment.stop)
        self.opener=Mock();self.factory=patch('monitor.ai.urllib.request.build_opener',return_value=self.opener)
        self.builder=self.factory.start();self.addCleanup(self.factory.stop)

    def reply(self,raw):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=raw;self.opener.open.return_value=response
        return response

    def test_mocked_protocol_sends_anonymous_payload_and_validates_reduced_suggestion(self):
        rec=next(r for r in self.evaluation['recommendations'] if r['action']=='budget_up')
        answer={'analysis':'先复核来源与保障风险','proposals':[{'recommendation_id':rec['id'],'change_pct':.1}]}
        self.reply(json.dumps({'choices':[{'message':{'content':json.dumps(answer)}}]}).encode())
        result=request_analysis(self.cfg,self.package,self.evaluation)
        self.assertEqual(len(result['accepted_for_simulation']),1);self.assertFalse(result['executed'])
        request=self.opener.open.call_args.args[0];body=json.loads(request.data)
        self.assertNotIn('FAKE_TEST_SECRET',request.data.decode());self.assertNotIn('plan1',request.data.decode())
        self.assertEqual(body['model'],'offline-test');self.assertEqual(self.opener.open.call_args.kwargs['timeout'],45)
        self.assertEqual(self.opener.open.call_count,1)
        self.assertIn(NoRedirect,self.builder.call_args.args)

    def test_disabled_or_bad_endpoint_never_opens_transport(self):
        for update in ({'enabled':False},{'endpoint':'http://ai-test.invalid'},{'endpoint':'https://x.invalid?key=secret'}):
            with self.assertRaises(ValueError):request_analysis({**self.cfg,**update},self.package,self.evaluation)
        self.opener.open.assert_not_called()

    def test_missing_key_stops_before_transport(self):
        with patch.dict('os.environ',{'QIANCHUAN_AI_API_KEY':''}):
            with self.assertRaises(ValueError):request_analysis(self.cfg,self.package,self.evaluation)
        self.opener.open.assert_not_called()

    def test_http_error_does_not_read_or_repeat_secret_echo_body(self):
        body=io.BytesIO(b'FAKE_TEST_SECRET')
        self.opener.open.side_effect=urllib.error.HTTPError(self.cfg['endpoint'],401,'FAKE_TEST_SECRET',{},body)
        with self.assertRaises(ValueError) as caught:request_analysis(self.cfg,self.package,self.evaluation)
        self.assertNotIn('FAKE_TEST_SECRET',str(caught.exception));self.assertIn('401',str(caught.exception))
        self.assertEqual(body.tell(),0);self.assertEqual(self.opener.open.call_count,1)

    def test_timeout_or_connection_failure_has_no_retry_or_sensitive_error_text(self):
        for error in (TimeoutError('FAKE_TEST_SECRET'),urllib.error.URLError('FAKE_TEST_SECRET')):
            self.opener.open.reset_mock();self.opener.open.side_effect=error
            with self.assertRaises(ValueError) as caught:request_analysis(self.cfg,self.package,self.evaluation)
            self.assertNotIn('FAKE_TEST_SECRET',str(caught.exception));self.assertEqual(self.opener.open.call_count,1)

    def test_redirect_is_rejected_without_contacting_new_destination(self):
        with self.assertRaises(ValueError):
            NoRedirect().redirect_request(None,None,302,'redirect',{},'https://other.invalid/')
        self.opener.open.assert_not_called()

    def test_oversized_response_stops_before_parsing(self):
        response=self.reply(b'x'*(1024*1024+1))
        with self.assertRaises(ValueError):request_analysis(self.cfg,self.package,self.evaluation)
        response.read.assert_called_once_with(1024*1024+1)

    def test_invalid_utf8_json_or_completion_structure_returns_format_error(self):
        for raw in (b'\xff',b'not json',b'{"choices":[]}',b'{"choices":[{"message":{"content":{}}}]}',
                    b'{"choices":[{"message":{"content":"not json"}}]}'):
            self.reply(raw)
            with self.assertRaises(ValueError):request_analysis(self.cfg,self.package,self.evaluation)

    def test_response_cannot_create_actions_or_override_local_blockers(self):
        rec=self.evaluation['recommendations'][0];rec['eligible']=False
        answer={'analysis':'untrusted','proposals':[{'recommendation_id':rec['id'],'change_pct':.2},
                                                  {'recommendation_id':'new-action','change_pct':.2}]}
        self.reply(json.dumps({'choices':[{'message':{'content':json.dumps(answer)}}]}).encode())
        result=request_analysis(self.cfg,self.package,self.evaluation)
        self.assertEqual(result['accepted_for_simulation'],[]);self.assertEqual(len(result['rejected']),2)


if __name__=='__main__':unittest.main()
