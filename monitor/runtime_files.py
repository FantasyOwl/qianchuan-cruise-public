"""Asset access and Ego entry points for source and packaged runs."""
import json

_assets = None


def use_packaged_assets(reader):
    global _assets
    _assets = reader


def read_asset(root, name):
    return _assets(name) if _assets else (root / name).read_bytes()


def ego_entry(app, script_name, config=None):
    release = getattr(app, 'release', None)
    if release is not None:
        return release.ego_entry(app.port, script_name, config)
    script = app.root / 'monitor/ego' / script_name
    prefix = '' if config is None else 'globalThis.__qcxWorkerConfigPath=' + json.dumps(str(config)) + ';'
    return prefix + 'await import(' + json.dumps(script.as_uri()) + ')'
