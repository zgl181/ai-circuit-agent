@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 正在启动电路板设计 AI 界面（浏览器会自动打开 http://localhost:8765）...
.venv\Scripts\python.exe scripts\app.py
pause
