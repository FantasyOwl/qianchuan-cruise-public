"""Temporary local queue for Node/DOM integration tests; no monitor poller."""
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from monitor.server import App, Handler, Server
from monitor.accounts import collection_policy


def main():
    (ROOT / 'data').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='browser-control-http-', dir=ROOT / 'data') as directory:
        test_root = Path(directory).resolve()
        assert test_root.is_relative_to((ROOT / 'data').resolve())
        cfg = json.loads((ROOT / 'config.json').read_text('utf-8'))
        cfg['accounts'] = [{'id': '1000001', 'name': '隔离测试账户', 'scene': 'live' if '--live' in sys.argv else 'product', 'tab_group': '明确分组',
                            'observe': True, 'roi_basis': 'overall_payment', 'target_roi': None}]
        (test_root / 'config.json').write_text(json.dumps(cfg), 'utf-8')
        app = App(test_root)
        server = Server(('127.0.0.1', 0), Handler)
        app.port = server.server_address[1]; server.app = app
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        print(json.dumps({'port': app.port, 'csrf': app.csrf, 'token': app.token, 'policy': collection_policy(app.cfg())}), flush=True)
        try: sys.stdin.readline()
        finally:
            server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__': main()
