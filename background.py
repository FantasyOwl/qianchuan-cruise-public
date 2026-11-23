"""Manage this workspace's optional Windows background task, locally only."""
import argparse
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser(description='Local Windows background task; no advertising operations')
    parser.add_argument('mode',nargs='?',default='Plan',choices=['Plan','Install','Start','Pause','Stop','Status','Remove'])
    args=parser.parse_args()
    if os.name!='nt':
        parser.error('Windows Task Scheduler is required')
    script=Path(__file__).resolve().parent/'tools/background_task.ps1'
    powershell=Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    # A list of arguments, no shell, encoded command, policy change or elevation.
    result=subprocess.run([str(powershell),'-NoProfile','-NonInteractive','-File',str(script),
        '-Mode',args.mode,'-PythonPath',sys.executable],stdin=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW)
    return result.returncode


if __name__=='__main__':
    raise SystemExit(main())
