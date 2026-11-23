"""Fair account turns; scheduling never grants or replays a platform write."""
import json


class FairAccounts:
    def __init__(self,path):
        self.path=path;self.sequence=0;self.served={};self.last_account=None
        try:
            value=json.loads(path.read_text('utf-8'));self.sequence=int(value['sequence']);self.served=value['served']
        except (OSError,ValueError,KeyError,TypeError):pass

    def order(self,rows):
        return sorted((r for r in rows if r['ready']),key=lambda r:(r['priority'],self.served.get(r['account_id'],0),r['updated_at'],r['account_id'],r['kind']))

    def mark(self,account_id):
        self.sequence+=1;self.served[account_id]=self.sequence;self.last_account=account_id
        temp=self.path.with_suffix('.tmp');temp.write_text(json.dumps({'sequence':self.sequence,'served':self.served},ensure_ascii=False),'utf-8');temp.replace(self.path)
