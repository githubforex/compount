@echo off
setlocal

REM ==================== CONFIG (改成你的远程服务器信息) ====================
set REMOTE_HOST=192.168.1.100
set REMOTE_USER=Administrator
set REMOTE_DIR=C:\apps\compound
REM ======================================================================

cd /d "%~dp0"

echo [1/3] Uploading files to remote...
scp compound_server.py echarts.min.js echarts-gl.min.js restart.ps1 "%REMOTE_USER%@%REMOTE_HOST%:%REMOTE_DIR%/"

echo [2/3] Installing python deps on remote...
ssh "%REMOTE_USER%@%REMOTE_HOST%" "python -m pip install pymysql --quiet"

echo [3/3] Restarting service on remote...
ssh "%REMOTE_USER%@%REMOTE_HOST%" "powershell -ExecutionPolicy Bypass -File %REMOTE_DIR%\restart.ps1"

echo.
echo Deploy done! Open http://%REMOTE_HOST%:7777
pause
