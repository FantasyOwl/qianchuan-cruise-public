import unittest
from monitor.chase_batching import groups,batch_limits


class RangeBatchingTests(unittest.TestCase):
    def test_two_to_five_uses_all_qualified_rows_without_single_item_tail(self):
        cfg={'batch_size_min':2,'batch_size_max':5,'batch_size':5}
        for n in range(101):
            with self.subTest(n=n):
                chunks=list(groups(list(range(n)),cfg));flat=sum(chunks,[])
                self.assertTrue(all(2<=len(c)<=5 for c in chunks))
                self.assertEqual(flat,list(range(n if n>=2 else 0)))
        self.assertEqual([len(c) for c in groups(list(range(6)),cfg)],[4,2])

    def test_tight_range_rebalances_instead_of_discarding_a_usable_tail(self):
        self.assertEqual([len(c) for c in groups(list(range(12)),{'batch_size_min':4,'batch_size_max':5})],[4,4,4])

    def test_legacy_fixed_batches_and_invalid_range(self):
        self.assertEqual(list(groups(list(range(7)),{'batch_size':3})),[[0,1,2],[3,4,5]])
        for cfg in ({'batch_size_min':2},{'batch_size_min':5,'batch_size_max':2},{'batch_size_min':2,'batch_size_max':21}):
            with self.assertRaises(ValueError):batch_limits(cfg)
