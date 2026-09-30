# 远程部署说明（本地 Windows → 远程 Windows）

项目：交易计划（复利计算器）
技术栈：Python + MySQL + pymysql，端口 7777

---

## 一、远程服务器一次性准备（只需做一次）

### 1. 开启 OpenSSH Server
在远程 Windows 服务器上：
- 设置 → 应用 → 可选功能 → 添加功能 → 搜索「OpenSSH 服务器」→ 安装
- 安装后在「服务」里启动 `OpenSSH SSH Server`，设为自动启动

### 2. 安装 Python
- 官网下载 Windows 安装包，**勾选「Add Python to PATH」**
- 验证：远程执行 `python --version`

### 3. 安装 MySQL
- 安装 MySQL Server 8.0（记住 root 密码）
- 确保 MySQL 服务已启动
- 数据库不用手动建：程序启动时会自动建 `compound` 库和表

### 4. 配置 SSH 免密（推荐，否则每次部署要输密码）
在**本地** Windows 执行：
```
ssh-keygen -t ed25519        # 一路回车生成密钥
ssh 用户名@远程IP "mkdir %USERPROFILE%\.ssh 2>nul & type %USERPROFILE%\.ssh\authorized_keys 2>nul > %USERPROFILE%\.ssh\authorized_keys"
type %USERPROFILE%\.ssh\id_ed25519.pub | ssh 用户名@远程IP "cat >> %USERPROFILE%\.ssh\authorized_keys"
```

### 5. 创建远程目录
```
ssh 用户名@远程IP "mkdir C:\apps\compound"
```

---

## 二、本地一键部署

1. 用记事本打开 `deploy.bat`，改这 3 个配置：
   - `REMOTE_HOST`：远程服务器 IP
   - `REMOTE_USER`：远程登录用户名
   - `REMOTE_DIR`：远程部署目录（默认 `C:\apps\compound`）
2. 双击 `deploy.bat`，自动完成：传文件 → 装依赖 → 重启服务
3. 浏览器访问 `http://远程IP:7777`

---

## 三、MySQL 连接配置

程序默认连 `127.0.0.1:3306`，用户 `root`，密码 `123456`，库 `compound`。

如果远程 MySQL 密码不同，编辑远程的 `restart.ps1`，取消注释并改这几行：
```powershell
$env:MYSQL_HOST = "127.0.0.1"
$env:MYSQL_USER = "root"
$env:MYSQL_PASSWORD = "你的远程MySQL密码"
```

支持的环境变量：`MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DB`

---

## 四、文件清单

| 文件 | 作用 |
| --- | --- |
| `compound_server.py` | 主服务（含建库建表逻辑） |
| `echarts.min.js` / `echarts-gl.min.js` | 图表库（静态） |
| `deploy.bat` | 本地一键部署脚本 |
| `restart.ps1` | 远程重启脚本（随部署一起上传） |
