"""Routing policy, separate from monetary rules and grants."""
import json
from pathlib import Path

MODE='api_control_browser_chase'

def load(path):
    file=Path(path).with_name('execution-strategy.json')
    if not file.exists():return {'mode':'compatible','version':1}
    value=json.loads(file.read_text('utf-8'))
    if value!={'mode':MODE,'version':1}:raise ValueError('执行分工配置无效，未启动')
    return value

def api_control(actions):
    return getattr(actions,'execution_strategy',{}).get('mode')==MODE
