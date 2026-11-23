import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from monitor.ego_scheduler import FairAccounts


class FairAccountTests(unittest.TestCase):
    def test_fifty_accounts_each_get_a_turn_before_the_first_repeats(self):
        with TemporaryDirectory() as directory:
            scheduler=FairAccounts(Path(directory)/'schedule.json')
            rows=[dict(account_id=str(n),priority=2,ready=True,updated_at='same',kind='chase') for n in range(50)]
            order=[]
            for _ in range(100):
                r=scheduler.order(rows)[0];order.append(r['account_id']);scheduler.mark(r['account_id'])
            self.assertEqual(len(set(order[:50])),50);self.assertEqual(order[:50],order[50:])
            loaded=FairAccounts(scheduler.path);self.assertEqual(loaded.served,scheduler.served)

    def test_readback_and_stop_loss_precede_new_chase_but_paused_accounts_do_not_occupy_turns(self):
        with TemporaryDirectory() as directory:
            scheduler=FairAccounts(Path(directory)/'schedule.json')
            rows=[dict(account_id=a,priority=p,ready=r,updated_at='same',kind='rules') for a,p,r in [('cotton',2,True),('verify',0,True),('loss',1,True),('blocked',0,False)]]
            self.assertEqual([r['account_id'] for r in scheduler.order(rows)],['verify','loss','cotton'])
