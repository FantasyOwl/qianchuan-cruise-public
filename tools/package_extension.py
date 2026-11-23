"""Prepare a reviewable, source-only extension archive. Never install or grant access."""
import hashlib
import json
from pathlib import Path
import os
import zipfile


def main():
    root=Path(__file__).resolve().parent.parent
    directory=root/'extension'
    files=('access.js','policy.js','background.js','collector.js','control-dom.js','control-worker.js','cruise-dom.js','account-entry-dom.js','account-entry-worker.js','cruise-worker.js','batch-dom.js','batch-worker.js','hybrid-dom.js','hybrid-home-dom.js','hybrid-probe-dom.js','hybrid-worker.js','hybrid-rule-worker.js','live-close-dom.js','live-close-worker.js','manifest.json','popup.css','popup.html','popup.js','README.md')
    manifest=json.loads((directory/'manifest.json').read_text('utf-8'))
    assert manifest['manifest_version']==3
    assert manifest['permissions']==['tabGroups','scripting','alarms','storage']
    assert not manifest.get('host_permissions')
    assert manifest['optional_host_permissions']==['https://qianchuan.jinritemai.com/*','http://127.0.0.1:8877/*']
    assert not manifest.get('externally_connectable') and not manifest.get('web_accessible_resources')
    review={'version':manifest['version'],'installed_by_this_script':False,'browser_load_verified':False,
        'default_enabled':False,'required_permissions':manifest['permissions'],
        'default_execution_armed':False,'execution_enable':'explicit popup switch plus saved account authority, local per-action confirmation or a preconfirmed concrete one-time slowdown schedule',
        'read_only_diagnostics':'scoped visible DOM only; bounded selectors; no input values, cookies, application internals, form preparation or submission permission',
        'execution_layouts':['product main-plan list','live main-plan detail header','live original-cruise control task table','live native material table and batch chase drawer','live homepage one-click control form'],
        'execution_scope':'single approved main-plan budget/ROI/pause, control-task budget/duration/pause/stop, or explicitly approved same-page material batch creation or preconfirmed timed native slowdown; no deletion or automatic boost termination',
        'optional_site_access':manifest['optional_host_permissions'],
        'accounts':'explicitly enabled accounts from authenticated local collector policy',
        'tab_group':'exact per-account Chrome group from local configuration','page_paths':['/uni-prom','/uni-prom/detail','/board-next','/overall-prom','/overall-prom/detail'],
        'local_policy_get':'http://127.0.0.1:8877/api/collector-config','collector_protocol':2,
        'local_job_get':'http://127.0.0.1:8877/api/cruise/worker-jobs',
        'local_post_paths':['http://127.0.0.1:8877/api/ingest','http://127.0.0.1:8877/api/heartbeat',
            *['http://127.0.0.1:8877/api/cruise/hybrid/'+suffix for suffix in ('worker-next','worker-result','worker-begin','worker-final')],
            'http://127.0.0.1:8877/api/cruise/close/worker-parents',
            'http://127.0.0.1:8877/api/cruise/worker-job-result',
            *['http://127.0.0.1:8877/api/'+prefix+'/'+suffix for prefix in ('browser-actions','cruise/actions','cruise/close')
                for suffix in ('worker-observe','worker-claim','worker-begin','worker-receipt')],
            *['http://127.0.0.1:8877/api/cruise/batch/'+suffix for suffix in
                ('worker-observe','worker-claim','worker-begin','worker-receipt','worker-readback-jobs','worker-readback-result')]],
        'files_sha256':{name:hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in files},
        'notice':'Source manifest and integrity inventory, not a browser-load test or security certification.'}
    (root/'docs/collector-permissions.json').write_text(json.dumps(review,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    target=root.parent/'千川多账户监测与确认调控-待加载.zip';temporary=target.with_suffix('.tmp')
    with zipfile.ZipFile(temporary,'w',zipfile.ZIP_DEFLATED) as archive:
        for name in files:archive.write(directory/name,Path('千川多账户监测与确认调控')/name)
    os.replace(temporary,target)
    print(json.dumps({'archive':str(target),'version':manifest['version'],'files':len(files),
        'bytes':target.stat().st_size,'sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'installed':False},ensure_ascii=True))


if __name__=='__main__':main()
