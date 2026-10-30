@echo off
chcp 65001 >nul
REM 启动本地 MongoDB（便携版，免安装、普通用户即可运行）
set BASE=C:\Users\24013\Desktop\工具\mongodb
set DATA_DIR=C:\Users\24013\Desktop\工具\mongodb-data
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"

set "MONGO_DIR="
for /d %%d in ("%BASE%\mongodb-*") do (
  if exist "%%d\bin\mongod.exe" set "MONGO_DIR=%%d\bin"
)
if not defined MONGO_DIR (
  echo [错误] 未找到 mongod.exe，请确认 MongoDB 已解压到 %BASE%
  pause
  exit /b 1
)

echo 正在启动 MongoDB（数据目录 %DATA_DIR%）...
start "MongoDB" "%MONGO_DIR%\mongod.exe" --dbpath "%DATA_DIR%" --bind_ip 127.0.0.1 --logpath "%DATA_DIR%\mongod.log"
echo MongoDB 已在新窗口启动（默认端口 27017）
