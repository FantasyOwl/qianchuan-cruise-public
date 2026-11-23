import unittest
from types import SimpleNamespace
from monitor.official_monitoring import pages


class NativeEmptyPagingTests(unittest.TestCase):
    def test_only_observed_explicit_first_empty_task_list_is_accepted(self):
        api=SimpleNamespace(transport=lambda *args:{'task_list':[],'page_info':{'page':1,'page_size':100}})
        self.assertEqual(pages(api,'control_tasks',{},'task_list','total_number','paper-token'),[])

    def test_nonempty_tasks_without_total_still_fail(self):
        api=SimpleNamespace(transport=lambda *args:{'task_list':[{'id':1}],'page_info':{'page':1,'page_size':100}})
        with self.assertRaisesRegex(ValueError,'准确总数'):pages(api,'control_tasks',{},'task_list','total_number','paper-token')

    def test_empty_missing_total_on_a_later_page_never_completes_a_partial_read(self):
        def transport(name,q,token):
            return {'task_list':[{'id':1}],'page_info':{'total_number':2}} if q['page']==1 else {'task_list':[],'page_info':{'page':2,'page_size':100}}
        with self.assertRaisesRegex(ValueError,'准确总数'):pages(SimpleNamespace(transport=transport),'control_tasks',{},'task_list','total_number','paper-token')

    def test_other_report_endpoints_and_missing_lists_remain_strict(self):
        api=SimpleNamespace(transport=lambda *args:{'rows':[],'page_info':{'page':1,'page_size':100}})
        with self.assertRaisesRegex(ValueError,'准确总数'):pages(api,'report_data',{},'rows','total_number','paper-token')
        with self.assertRaisesRegex(ValueError,'结构无效'):pages(api,'control_tasks',{},'task_list','total_number','paper-token')
