"""Start the existing owned background service and open its local console."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time
from urllib.request import urlopen
import webbrowser

ROOT=Path(__file__).resolve().parents[1]
BASE='http://127.0.0.1:8877/'

def ready():
    try:
        with urlopen(BASE+'api/cruise',timeout=8) as response:
            value=json.load(response)
        return isinstance(value,dict) and isinstance(value.get('ego_executor'),dict) and bool(value.get('now'))
    except (OSError,ValueError):
        return False

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--no-open',action='store_true',help='启动并核对服务，不打开浏览器')
    args=parser.parse_args()
    if not ready():
        for mode in ('Install','Start'):
            result=subprocess.run([sys.executable,str(ROOT/'background.py'),mode],cwd=ROOT)
            if result.returncode:
                print('后台启动未完成，请查看上方错误。')
                return result.returncode
        deadline=time.monotonic()+60
        print('后台已请求启动，正在等待控制台就绪...')
        while not ready():
            if time.monotonic()>=deadline:
                print('控制台尚未就绪，请运行“查看后台状态.cmd”检查。')
                return 1
            time.sleep(1)
    print('千川巡航控制台已就绪：'+BASE)
    print('三个户的智能体状态可在“账户独立执行”中查看；此入口不会修改规则或授权。')
    if not args.no_open and not webbrowser.open(BASE,new=0):
        print('浏览器未能自动打开，请手动打开上方地址。')
        return 1
    return 0

if __name__=='__main__':
    raise SystemExit(main())
