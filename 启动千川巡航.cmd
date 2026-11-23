@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动千川巡航并打开控制台，请稍候...
python tools\launch_console.py
if errorlevel 1 goto failed
exit /b 0
:failed
echo.
echo 启动未完成，请保留上方错误信息。不要点击“暂停后台运行”。
pause
exit /b 1
