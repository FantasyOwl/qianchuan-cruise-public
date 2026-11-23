@echo off
chcp 65001 >nul
cd /d "%~dp0"
python background.py Install
if errorlevel 1 goto done
python background.py Start
:done
pause
