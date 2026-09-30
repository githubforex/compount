$ErrorActionPreference = 'SilentlyContinue'
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir

# 可选：覆盖 MySQL 连接信息（如果远程 MySQL 与默认 root/123456 不同，取消注释并修改）
# $env:MYSQL_HOST = "127.0.0.1"
# $env:MYSQL_USER = "root"
# $env:MYSQL_PASSWORD = "你的远程MySQL密码"

# 杀掉旧的 compound_server 进程
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*compound_server.py*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }

Start-Sleep -Seconds 1

# 启动新进程（启动时会自动 init_db 建库建表）
Start-Process -FilePath "python" -ArgumentList "compound_server.py" -WorkingDirectory $dir -WindowStyle Hidden
Write-Host "compound_server restarted on port 7777"
