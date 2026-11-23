import unittest
from datetime import timedelta

from tests import test_cruise_actions as fixtures


class CruiseReconciliationTests(unittest.TestCase):
    setUp=fixtures.CruiseActionTests.setUp
    tearDown=fixtures.CruiseActionTests.tearDown
    observe=fixtures.CruiseActionTests.observe
    create=fixtures.CruiseActionTests.create
    approve=fixtures.CruiseActionTests.approve
    claim=fixtures.CruiseActionTests.claim
    begin=fixtures.CruiseActionTests.begin
    receipt=fixtures.CruiseActionTests.receipt
    ready=fixtures.CruiseActionTests.ready

    def unknown(self,receipt=True):
        c=self.ready()
        if receipt:self.queue.receipt(self.receipt(c,observation=None))
        else:self.now+=timedelta(seconds=91);self.queue.status(self.policy)
        self.now+=timedelta(seconds=1)
        return c

    def refresh(self,**updates):
        self.observe(rows=[fixtures.task(self.now,document_id='fresh-task-doc',budget='6000.00',**updates)])

    def preview(self,c):
        return self.queue.reconcile_preview({'id':c['id'],'worker_id':'worker-a','epoch':'epoch-a'},self.policy)

    def confirm(self,p):
        return self.queue.reconcile_confirm({'id':p['id'],'reconciliation_digest':p['reconciliation_digest'],'confirmed':True},self.policy)

    def test_task_result_recovery_requires_fresh_task_document_and_explicit_confirmation(self):
        c=self.unknown()
        with self.assertRaises(ValueError):self.preview(c)
        self.refresh();p=self.preview(c)
        self.assertTrue(self.queue.has_active_or_unknown())
        self.assertEqual(p['evidence']['surface']['task_id'],'900')
        result=self.confirm(p);self.assertEqual(result['state'],'reconciled_target');self.assertFalse(result['executed'])
        self.assertFalse(self.queue.has_active_or_unknown())
        self.assertEqual(self.queue.status(self.policy)['actions'][0]['resolution']['evidence']['value'],'6000.00')
        with self.assertRaisesRegex(ValueError,'15分钟'):self.create(after='6500.00')

    def test_other_parent_or_task_cannot_resolve_unknown(self):
        c=self.unknown();self.refresh(plan_id='790')
        with self.assertRaises(ValueError):self.preview(c)
        self.refresh(task_id='999')
        with self.assertRaises(ValueError):self.preview(c)

    def test_setting_change_between_preview_and_confirmation_is_rejected(self):
        c=self.unknown();self.refresh();p=self.preview(c)
        self.observe(rows=[fixtures.task(self.now,document_id='fresh-task-doc',budget='6500.00')])
        with self.assertRaises(ValueError):self.confirm(p)
        self.assertTrue(self.queue.has_active_or_unknown())

    def test_first_late_receipt_is_acknowledged_without_overwriting_independent_resolution(self):
        c=self.unknown(receipt=False);self.refresh();p=self.preview(c);self.confirm(p)
        late=self.receipt(c,observation=None)
        result=self.queue.receipt(late)
        self.assertTrue(result['late']);self.assertEqual(result['state'],'reconciled_target')
        self.assertEqual(self.queue.receipt(late)['state'],'reconciled_target')

    def test_statistics_date_change_invalidates_task_approval_but_live_metrics_do_not(self):
        self.observe(rows=[fixtures.task(self.now,context={'metric_date':'2026-10-25'})])
        d=self.create()
        self.observe(rows=[fixtures.task(self.now,context={'metric_date':'2026-10-26'})])
        with self.assertRaises(ValueError):self.approve(d)


if __name__=='__main__':unittest.main()
