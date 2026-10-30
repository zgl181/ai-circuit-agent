@echo off
chcp 65001 >nul
cd /d "%~dp0"
REM 先拉起本地 MongoDB（真实持久化后端），再启动网页界面
call start-mongo.bat
echo 等待 MongoDB 启动...
timeout /t 6 >nul
echo 正在启动电路板设计 AI 界面（浏览器会自动打开 http://localhost:8765）...
.venv\Scripts\python.exe scripts\app.py
pause
