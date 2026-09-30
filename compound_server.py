# -*- coding: utf-8 -*-
"""
复利计算服务端
监听 7777 端口, 提供:
  GET /             -> 交互页面(3 个输入框: 基数、复利次数、每期利率)
  GET /api/compute  -> JSON 复利明细 (query: principal, periods, rate)
运行: python compound_server.py
访问: http://localhost:7777
"""
import json
import math
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs
import datetime
import threading
import pymysql

HOST = "0.0.0.0"
PORT = 7777
DEFAULT_RATE = 0.10          # 每期利率 10%
MAX_PERIODS = 500            # 防止页面被超大期数拖死
MAX_PRINCIPAL = 1e12

# ---- 交易日历：法定节假日（联网优先，内置兜底） ----
# 内置法定节假日（周一~周五的休市日，"月-日"格式）。周末自动休市。
HOLIDAYS_BUILTIN = {
    2024: ["01-01", "02-09", "02-12", "02-13", "02-14", "02-15", "02-16",
           "04-04", "04-05", "05-01", "05-02", "05-03", "06-10",
           "09-16", "09-17", "10-01", "10-02", "10-03", "10-04", "10-07"],
    2025: ["01-01", "01-28", "01-29", "01-30", "01-31", "02-03", "02-04",
           "04-04", "05-01", "05-02", "05-05", "06-02",
           "10-01", "10-02", "10-03", "10-06", "10-07", "10-08"],
    2026: ["01-01", "01-02", "02-17", "02-18", "02-19", "02-20", "02-23",
           "04-06", "05-01", "05-04", "05-05", "06-19",
           "09-25", "10-01", "10-02", "10-05", "10-06", "10-07"],
}

_HOLIDAY_CACHE = {}


def fetch_holidays(year):
    """拉取法定节假日集合 {(month, day), ...}，联网优先、内置兜底。"""
    if year in _HOLIDAY_CACHE:
        return _HOLIDAY_CACHE[year]
    result = set()
    try:
        import urllib.request
        url = "https://timor.tech/api/holiday/year/%d" % year
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=6) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("code") == 0:
            for k, v in data.get("holiday", {}).items():
                if v.get("holiday"):  # 法定节假日（holiday=False 是调休补班日，股市仍休市）
                    m, d = k.split("-")
                    result.add((int(m), int(d)))
    except Exception:
        result = set()
    if not result:
        result = {tuple(int(x) for x in k.split("-")) for k in HOLIDAYS_BUILTIN.get(year, [])}
    _HOLIDAY_CACHE[year] = result
    return result


def is_trading_day(dt, holidays):
    """交易日 = 周一~周五 且 非法定节假日。"""
    if dt.weekday() >= 5:  # 5=周六 6=周日
        return False
    if (dt.month, dt.day) in holidays:
        return False
    return True


def project_end(start_date, periods, holidays):
    """从起始日（含）起数第 periods 个交易日，返回该日期。"""
    d = start_date
    n = 0
    while True:
        if is_trading_day(d, holidays):
            n += 1
            if n == periods:
                return d
        d += datetime.timedelta(days=1)


# ---- MySQL 持久化 ----
DB_CONFIG = {
    "host": "127.0.0.1",
    "port": 3306,
    "user": "root",
    "password": "123456",
    "database": "compound",
    "charset": "utf8mb4",
}


def _conn():
    return pymysql.connect(host=DB_CONFIG["host"], port=DB_CONFIG["port"],
                           user=DB_CONFIG["user"], password=DB_CONFIG["password"],
                           database=DB_CONFIG["database"], charset="utf8mb4",
                           autocommit=True)


def init_db():
    c = pymysql.connect(host=DB_CONFIG["host"], port=DB_CONFIG["port"],
                        user=DB_CONFIG["user"], password=DB_CONFIG["password"],
                        charset="utf8mb4", autocommit=True)
    with c.cursor() as cur:
        cur.execute("CREATE DATABASE IF NOT EXISTS compound DEFAULT CHARACTER SET utf8mb4")
    c.close()
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS plans (
                name VARCHAR(255) PRIMARY KEY,
                principal VARCHAR(32),
                rate VARCHAR(32),
                periods VARCHAR(32),
                start_date VARCHAR(32)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
            cur.execute("""CREATE TABLE IF NOT EXISTS reflections (
                plan_name VARCHAR(255),
                ref_date VARCHAR(32),
                content LONGTEXT,
                PRIMARY KEY (plan_name, ref_date)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    finally:
        conn.close()


def load_plans():
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT name, principal, rate, periods, start_date FROM plans")
            rows = cur.fetchall()
        return {name: {"principal": principal, "rate": rate, "periods": periods, "start_date": start_date}
                for name, principal, rate, periods, start_date in rows}
    finally:
        conn.close()


def save_plan(name, params):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO plans (name, principal, rate, periods, start_date)
                           VALUES (%s, %s, %s, %s, %s)
                           ON DUPLICATE KEY UPDATE principal=VALUES(principal), rate=VALUES(rate),
                           periods=VALUES(periods), start_date=VALUES(start_date)""",
                        (name, params.get("principal", ""), params.get("rate", ""),
                         params.get("periods", ""), params.get("start_date", "")))
    finally:
        conn.close()


def load_reflections():
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT plan_name, ref_date, content FROM reflections")
            rows = cur.fetchall()
        return {plan_name + "|" + ref_date: content for plan_name, ref_date, content in rows}
    finally:
        conn.close()


def save_reflection(plan_name, ref_date, content):
    conn = _conn()
    try:
        with conn.cursor() as cur:
            cur.execute("""INSERT INTO reflections (plan_name, ref_date, content)
                           VALUES (%s, %s, %s)
                           ON DUPLICATE KEY UPDATE content=VALUES(content)""",
                        (plan_name, ref_date, content))
    finally:
        conn.close()


def compute(principal, rate, periods):
    rows = []
    cur = principal
    for i in range(1, periods + 1):
        nxt = round(cur * (1 + rate), 2)
        diff = round(nxt - cur, 2)
        rows.append({
            "n": i,
            "amount": nxt,
            "diff": diff,
            "multiple": round(nxt / principal, 6),
        })
        cur = nxt
    return rows


def fmt(v):
    return f"{v:,.2f}"


HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>复利计算器 - 服务端</title>
<script>(function(){try{var t=localStorage.getItem('theme');if(t)document.documentElement.setAttribute('data-theme',t);}catch(e){}})();</script>
<style>
  /* 设计语言参考 Beautiful UI (beautifului.dev) — oklch 近似双主题 */
  :root {
    --accent:#4c6ef5;
    --accent-strong:#3b5bdb;
    --accent-tint:rgba(76,110,245,.12);
    --violet:#7c3aed;
    --green:#2f9e44;
    --orange:#f76707;
    --red:#e03131;
    --ink:#1b1b1f;
    --muted:#6b7280;
    --line:#e6e6ea;
    --bg:#f4f4f7;
    --card:#ffffff;
    --th-bg:#f7f7fa;
    --hover-bg:#f2f6ff;
    --glow1:rgba(76,110,245,.16);
    --glow2:rgba(124,58,237,.10);
    --shadow-sm:0 1px 2px rgba(15,23,42,.05);
    --shadow:0 1px 2px rgba(15,23,42,.04), 0 10px 30px rgba(15,23,42,.06);
    --shadow-lg:0 16px 48px rgba(15,23,42,.12);
    --shadow-3d:inset 0 1px 0 rgba(255,255,255,.95), inset 0 -1px 0 rgba(15,23,42,.04), 0 1px 2px rgba(15,23,42,.05), 0 4px 10px rgba(15,23,42,.07), 0 14px 30px rgba(15,23,42,.09), 0 32px 64px -16px rgba(15,23,42,.16);
    --btn-3d:#2a3fc4;
    --radius:16px;
    --grad:linear-gradient(120deg,#4c6ef5 0%,#7c3aed 100%);
  }
  [data-theme="dark"] {
    --accent:#4d7cfe;
    --accent-strong:#6b97ff;
    --accent-tint:rgba(77,124,254,.16);
    --violet:#a78bfa;
    --green:#3fb950;
    --orange:#ff922b;
    --red:#ff6b6b;
    --ink:#e8e8ec;
    --muted:#9aa0aa;
    --line:#2a2a30;
    --bg:#131316;
    --card:#1d1d22;
    --th-bg:#242429;
    --hover-bg:rgba(77,124,254,.08);
    --glow1:rgba(77,124,254,.22);
    --glow2:rgba(167,139,250,.14);
    --shadow-sm:0 1px 2px rgba(0,0,0,.4);
    --shadow:0 1px 3px rgba(0,0,0,.35), 0 10px 30px rgba(0,0,0,.35);
    --shadow-lg:0 20px 56px rgba(0,0,0,.55);
    --shadow-3d:inset 0 1px 0 rgba(255,255,255,.08), inset 0 -1px 0 rgba(0,0,0,.35), 0 1px 2px rgba(0,0,0,.5), 0 4px 10px rgba(0,0,0,.5), 0 14px 30px rgba(0,0,0,.55), 0 32px 64px -16px rgba(0,0,0,.7);
    --btn-3d:#1e37b0;
    --grad:linear-gradient(120deg,#4d7cfe 0%,#a78bfa 100%);
  }
  * { box-sizing:border-box; }
  body {
    margin:0; font-family:"Inter",-apple-system,"Segoe UI","Microsoft YaHei",sans-serif;
    color:var(--ink); padding:28px 16px 80px;
    background:
      radial-gradient(1100px 480px at 15% -8%, var(--glow1), transparent 60%),
      radial-gradient(900px 420px at 88% -4%, var(--glow2), transparent 55%),
      var(--bg);
    background-attachment:fixed;
    transition:color .2s ease;
  }
  .wrap { max-width:1080px; margin:0 auto; }
  .head { display:flex; align-items:baseline; gap:10px; }
  h1 {
    font-size:32px; font-weight:800; margin:0; letter-spacing:-.02em;
    background:var(--grad);
    -webkit-background-clip:text; background-clip:text;
    -webkit-text-fill-color:transparent;
    filter:drop-shadow(0 2px 3px rgba(76,110,245,.35));
  }
  .badge {
    font-size:11px; font-weight:600; color:var(--accent);
    background:var(--accent-tint); padding:3px 10px; border-radius:999px;
    vertical-align:middle;
  }
  .subtitle { color:var(--muted); margin:8px 0 22px; font-size:14px; }
  .panel {
    background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
    padding:24px; margin-bottom:18px; box-shadow:var(--shadow-3d);
    transition:background .2s ease, border-color .2s ease;
  }
  .panel h2 { font-size:15px; font-weight:600; margin:0 0 14px; }
  form { display:grid; grid-template-columns:repeat(4, 1fr) auto; gap:14px; align-items:end; }
  label { display:block; font-size:13px; color:var(--muted); margin-bottom:6px; }
  input {
    width:100%; padding:12px 14px; font-size:16px; border:1px solid var(--line);
    border-radius:12px; outline:none; font-variant-numeric:tabular-nums;
    background:var(--card); color:var(--ink);
    box-shadow:inset 0 2px 4px rgba(15,23,42,.05), inset 0 -1px 0 rgba(255,255,255,.7);
    transition:border-color .15s ease, box-shadow .15s ease;
  }
  input:hover { border-color:var(--muted); }
  input:focus { border-color:var(--accent); box-shadow:inset 0 2px 4px rgba(15,23,42,.05), 0 0 0 4px var(--accent-tint); }
  [data-theme="dark"] input { box-shadow:inset 0 2px 4px rgba(0,0,0,.45), inset 0 -1px 0 rgba(255,255,255,.04); }
  [data-theme="dark"] input:focus { box-shadow:inset 0 2px 4px rgba(0,0,0,.45), 0 0 0 4px var(--accent-tint); }
  button {
    padding:12px 26px; font-size:15px; font-weight:700; color:#fff;
    background:var(--grad); border:none; border-radius:12px; cursor:pointer;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.35), 0 5px 0 var(--btn-3d), 0 10px 20px rgba(76,110,245,.35);
    transform:translateY(0);
    transition:filter .15s ease, transform .12s ease, box-shadow .12s ease;
  }
  button:hover { filter:brightness(1.08); }
  button:active {
    transform:translateY(4px);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.35), 0 1px 0 var(--btn-3d), 0 4px 8px rgba(76,110,245,.3);
  }
  .rate-note { font-size:12px; color:var(--muted); margin-top:12px; }
  .plans-tabs { display:flex; flex-wrap:wrap; gap:8px; margin-bottom:16px; }
  .plan-tab { border:1px solid var(--line); background:var(--card); border-radius:8px; padding:6px 14px; cursor:pointer; font-size:13px; color:var(--ink); }
  .plan-tab.active { background:var(--accent); color:#fff; border-color:var(--accent); }
  .plan-new { border-style:dashed; color:var(--muted); }
  .project-result { font-size:14px; margin-bottom:14px; }
  .project-result b { color:var(--accent); }
  .calendar { margin-top:6px; }
  .cal-week, .cal-grid { display:grid; grid-template-columns:repeat(7, 1fr); gap:4px; }
  .cal-head { text-align:center; font-size:12px; color:var(--muted); padding:4px 0; }
  .cal-cell { position:relative; text-align:center; padding:9px 0 8px; border-radius:8px; font-size:13px; font-variant-numeric:tabular-nums; min-height:20px; }
  .cal-empty { background:transparent; }
  .cal-trading { background:var(--accent-tint); }
  .cal-weekend { background:var(--th-bg); color:var(--muted); }
  .cal-holiday { background:rgba(224,49,49,.12); color:var(--red); }
  .cal-below { background:rgba(47,158,68,.18); }
  .cal-above { background:rgba(224,49,49,.18); }
  .cal-start { box-shadow:inset 0 0 0 2px var(--green); }
  .cal-end { box-shadow:inset 0 0 0 2px var(--accent); }
  .cal-badge { position:absolute; top:1px; right:4px; font-size:9px; color:var(--red); }
  .cal-profit { display:block; font-size:10px; color:var(--red); margin-top:2px; line-height:1.1; white-space:nowrap; }
  .cal-month { grid-column:1 / -1; text-align:left; font-size:12px; font-weight:700; color:var(--muted); padding:8px 2px 2px; border-top:1px solid var(--line); margin-top:6px; }
  .refl-btn { border:none; background:transparent; cursor:pointer; font-size:15px; padding:2px 6px; }
  .refl-btn.has { color:var(--green); }
  .modal-overlay { position:fixed; inset:0; background:rgba(0,0,0,.45); display:flex; align-items:center; justify-content:center; z-index:100; }
  .modal { background:var(--card); border-radius:16px; width:720px; max-width:92vw; max-height:86vh; display:flex; flex-direction:column; box-shadow:var(--shadow-lg); overflow:hidden; }
  .modal-head { display:flex; align-items:center; justify-content:space-between; padding:14px 18px; border-bottom:1px solid var(--line); }
  .modal-head span { font-weight:700; font-size:16px; }
  .modal-close { border:none; background:transparent; font-size:22px; cursor:pointer; color:var(--muted); line-height:1; }
  .modal-toolbar { display:flex; align-items:center; gap:10px; padding:10px 18px; border-bottom:1px solid var(--line); }
  .tb-btn { border:1px solid var(--line); background:var(--card); border-radius:8px; padding:6px 12px; cursor:pointer; font-size:13px; color:var(--ink); }
  .tb-hint { font-size:12px; color:var(--muted); }
  .refl-editor { flex:1; overflow-y:auto; padding:16px 18px; min-height:240px; font-size:14px; line-height:1.6; color:var(--ink); outline:none; }
  .refl-editor:empty::before { content:attr(data-placeholder); color:var(--muted); }
  .refl-editor img { max-width:100%; border-radius:8px; border:1px solid var(--line); }
  .img-wrap { position:relative; display:inline-block; margin:4px 4px 4px 0; }
  .img-wrap img { max-width:100%; border-radius:8px; border:1px solid var(--line); display:block; }
  .img-del { position:absolute; top:4px; right:4px; width:22px; height:22px; border:none; border-radius:50%; background:rgba(0,0,0,.55); color:#fff; font-size:14px; line-height:1; cursor:pointer; display:none; align-items:center; justify-content:center; }
  .img-wrap:hover .img-del { display:flex; }
  .lightbox { position:fixed; inset:0; background:rgba(0,0,0,.8); display:flex; align-items:center; justify-content:center; z-index:200; cursor:zoom-out; }
  .lightbox img { max-width:94vw; max-height:94vh; border-radius:8px; box-shadow:0 20px 60px rgba(0,0,0,.5); }
  .modal-foot { display:flex; justify-content:flex-end; gap:10px; padding:12px 18px; border-top:1px solid var(--line); }
  .save-btn { background:var(--accent); color:#fff; border:none; border-radius:8px; padding:8px 20px; cursor:pointer; font-size:14px; }
  .cancel-btn { border:1px solid var(--line); background:var(--card); border-radius:8px; padding:8px 18px; cursor:pointer; font-size:14px; color:var(--ink); }
  .actual-input { width:110px; padding:4px 6px; border:1px solid var(--line); border-radius:6px; font-size:13px; text-align:right; font-variant-numeric:tabular-nums; background:var(--card); color:var(--ink); }
  .actual-input:focus { outline:2px solid var(--accent); outline-offset:1px; }
  .dev-pos { color:var(--red); }
  .dev-neg { color:var(--green); }
  .cal-legend { display:flex; gap:16px; margin-top:10px; font-size:12px; color:var(--muted); }
  .cal-legend .lg::before { content:""; display:inline-block; width:10px; height:10px; border-radius:3px; margin-right:5px; vertical-align:-1px; }
  .lg-t::before { background:var(--accent-tint); }
  .lg-w::before { background:var(--th-bg); }
  .lg-h::before { background:rgba(224,49,49,.35); }
  .field { position:relative; }
  .datepicker { position:absolute; left:0; top:calc(100% + 6px); width:292px; background:var(--card); border:1px solid var(--line); border-radius:14px; box-shadow:var(--shadow-lg); padding:12px; z-index:50; }
  .dp-head { display:flex; align-items:center; justify-content:space-between; margin-bottom:8px; }
  .dp-nav { width:30px; height:30px; border:1px solid var(--line); background:var(--card); border-radius:8px; cursor:pointer; font-size:16px; line-height:1; color:var(--ink); }
  .dp-title { font-size:14px; font-weight:700; }
  .dp-week, .dp-grid { display:grid; grid-template-columns:repeat(7, 1fr); gap:2px; text-align:center; }
  .dp-week span { font-size:11px; color:var(--muted); padding:4px 0; }
  .dp-day { padding:8px 0; font-size:13px; border-radius:7px; cursor:pointer; font-variant-numeric:tabular-nums; }
  .dp-day:hover { background:var(--accent-tint); }
  .dp-day.dp-weekend { color:var(--red); }
  .dp-day.dp-today { box-shadow:inset 0 0 0 1px var(--accent); }
  .insight {
    background:var(--grad); color:#fff;
    border-radius:var(--radius); padding:16px 20px; margin-bottom:18px;
    font-size:15px; line-height:1.7;
    box-shadow:inset 0 1px 0 rgba(255,255,255,.25), 0 2px 6px rgba(15,23,42,.15), 0 12px 28px rgba(76,110,245,.35);
  }
  .insight b { font-weight:800; }
  .insight .dim { opacity:.85; }
  .grid { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:14px; margin-bottom:18px; }
  .stat {
    background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
    padding:18px 20px; box-shadow:var(--shadow-3d);
    transform-style:preserve-3d; will-change:transform;
    transition:transform .15s ease-out, box-shadow .2s ease-out, border-color .2s ease-out;
  }
  .stat:hover { box-shadow:var(--shadow-lg); }
  .stat-top { display:flex; align-items:center; gap:8px; margin-bottom:10px; }
  .stat-icon { font-size:20px; line-height:1; }
  .stat-label { font-size:13px; color:var(--muted); }
  .stat-value { font-size:26px; font-weight:800; font-variant-numeric:tabular-nums; letter-spacing:-.02em; }
  .stat-sub { font-size:12px; color:var(--muted); margin-top:5px; }
  .stat-hl {
    border:1px solid transparent;
    background:linear-gradient(var(--card),var(--card)) padding-box, var(--grad) border-box;
  }
  .stat-hl .stat-value {
    background:var(--grad);
    -webkit-background-clip:text; background-clip:text;
    -webkit-text-fill-color:transparent;
  }
  .table-scroll { overflow:auto; max-height:560px; border-radius:12px; border:1px solid var(--line); }
  table { border-collapse:collapse; width:100%; font-size:14px; }
  th, td { padding:10px 14px; text-align:right; border-bottom:1px solid var(--line); }
  th { background:var(--th-bg); color:var(--muted); font-weight:600; position:sticky; top:0; }
  th:first-child, td:first-child { text-align:center; color:var(--muted); }
  .num { font-variant-numeric:tabular-nums; font-feature-settings:"tnum"; }
  .diff { color:var(--red); }
  tbody tr:nth-child(even) td { background:var(--th-bg); }
  tbody tr:hover td { background:var(--hover-bg); }
  .foot { color:var(--muted); font-size:13px; margin-top:12px; }
  .error { color:var(--red); font-size:13px; margin-top:8px; min-height:18px; }
  #result { display:none; animation:fadeUp .35s ease; }
  @keyframes fadeUp { from { opacity:0; transform:translateY(10px); } to { opacity:1; transform:none; } }
  .chart-block { margin-top:24px; }
  .chart-block h3 { font-size:14px; font-weight:600; margin:0 0 8px; }
  .theme-toggle {
    position:fixed; top:16px; right:16px; width:44px; height:44px;
    display:flex; align-items:center; justify-content:center;
    background:var(--card); border:1px solid var(--line); border-radius:50%;
    cursor:pointer; font-size:20px; line-height:1; box-shadow:var(--shadow);
    transition:background .2s ease, transform .15s ease; z-index:10;
  }
  .theme-toggle:hover { background:var(--hover-bg); transform:scale(1.08); }
</style>
<script src="/echarts.min.js"></script>
<script src="/echarts-gl.min.js"></script>
</head>
<body>
<button class="theme-toggle" id="themeToggle" title="切换深浅主题" onclick="toggleTheme()">🌙</button>
<div class="wrap">
  <div class="head">
    <h1>复利计算器</h1>
    <span class="badge">服务端版</span>
  </div>
  <div class="subtitle">输入基数、复利次数和每期利率，服务端实时计算并返回明细</div>

  <div class="panel">
    <h2>参数</h2>
    <form id="form" onsubmit="return false;">
      <div>
        <label for="plan_name">计划名称</label>
        <input id="plan_name" type="text" placeholder="如：稳赢计划A" autocomplete="off">
      </div>
      <div>
        <label for="principal">基数（本金，元）</label>
        <input id="principal" type="number" min="0" step="0.01" value="30000">
      </div>
      <div>
        <label for="periods">复利次数</label>
        <input id="periods" type="number" min="1" max="500" step="1" value="220">
      </div>
      <div>
        <label for="rate">每期利率（%）</label>
        <input id="rate" type="number" min="-99" max="1000" step="0.01" value="3">
      </div>
      <div class="field">
        <label for="start_date">计划起始时间</label>
        <input id="start_date" type="text" readonly placeholder="点击选择日期" autocomplete="off" onclick="toggleCalendar(event)" value="2026-10-08">
        <div class="datepicker" id="datepicker" style="display:none">
          <div class="dp-head">
            <button type="button" class="dp-nav" onclick="changeMonth(-1)">‹</button>
            <div class="dp-title" id="dp_title"></div>
            <button type="button" class="dp-nav" onclick="changeMonth(1)">›</button>
          </div>
          <div class="dp-week"><span>一</span><span>二</span><span>三</span><span>四</span><span>五</span><span>六</span><span>日</span></div>
          <div class="dp-grid" id="dp_grid"></div>
        </div>
      </div>
      <button type="button" onclick="compute()">计算</button>
    </form>
    <div class="rate-note">每期利率 = 输入值 ÷ 100（10% = ×1.10）。可输入负数表示负增长。</div>
    <div class="error" id="error"></div>
  </div>

  <div class="plans-tabs" id="plans_tabs"></div>

  <div id="result">
    <div class="panel">
      <h2>交易日推演</h2>
      <div class="project-result" id="project_result">选择「计划起始时间」后，自动按复利次数推演结束日期。</div>
      <div class="calendar" id="calendar"></div>
    </div>
    <div class="insight" id="insight"></div>
    <div class="grid" id="stats"></div>
    <div class="panel">
      <h2>图表</h2>
      <div class="chart-block">
        <h3>预期增长曲线（2D 对数）</h3>
        <div id="chart" style="width:100%;height:340px;"></div>
      </div>
    </div>
    <div class="panel">
      <h2>逐期明细</h2>
      <div class="table-scroll">
        <table>
          <thead>
            <tr><th>期数</th><th>日期</th><th>总额（元）</th><th>差额（元）</th><th>实际表现（元）</th><th>偏差（元）</th><th>反思</th></tr>
          </thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
      <div class="foot" id="foot"></div>
    </div>
  </div>
</div>

<script>
const fmt = v => v.toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2});
const fmtInt = v => v.toLocaleString('en-US', {maximumFractionDigits:2});
function fmtWan(v) {
  if (v >= 100000000) return (v / 100000000) + '亿';
  if (v >= 10000) return (v / 10000) + '万';
  return v;
}
function fmtCompact(v) {
  if (v >= 100000000) return (v / 100000000).toFixed(1) + '亿';
  if (v >= 10000) return (v / 10000).toFixed(1) + '万';
  return Math.round(v);
}

let lastRows = null, lastPrincipal = null, lastTradeDates = null, adjustedDiffs = null, lastCalendarData = null, actualValues = null, diffStatus = null;

function themeColors() {
  const dark = document.documentElement.getAttribute('data-theme') === 'dark';
  return dark ? {
    accent:'#4d7cfe', orange:'#ff922b', green:'#3fb950', red:'#ff6b6b',
    accentTop:'rgba(77,124,254,0.42)', accentBottom:'rgba(77,124,254,0.02)',
    orangeTop:'rgba(255,146,43,0.95)', orangeBottom:'rgba(255,146,43,0.28)',
    greenTop:'rgba(63,185,80,0.42)', greenBottom:'rgba(63,185,80,0.02)',
    axisLabel:'#9aa0aa', axisLine:'#2a2a30', splitLine:'rgba(255,255,255,0.06)',
    tooltipBg:'rgba(32,32,37,0.96)', ink:'#e8e8ec',
    gridBg:'#1d1d22',
  } : {
    accent:'#4c6ef5', orange:'#f76707', green:'#2f9e44', red:'#e03131',
    accentTop:'rgba(76,110,245,0.32)', accentBottom:'rgba(76,110,245,0.01)',
    orangeTop:'rgba(247,103,7,0.95)', orangeBottom:'rgba(247,103,7,0.28)',
    greenTop:'rgba(47,158,68,0.30)', greenBottom:'rgba(47,158,68,0.01)',
    axisLabel:'#6b7280', axisLine:'#e6e6ea', splitLine:'rgba(0,0,0,0.06)',
    tooltipBg:'rgba(255,255,255,0.96)', ink:'#1b1b1f',
    gridBg:'#ffffff',
  };
}

function toggleTheme() {
  const root = document.documentElement;
  const next = root.getAttribute('data-theme') === 'dark' ? 'light' : 'dark';
  root.setAttribute('data-theme', next);
  try { localStorage.setItem('theme', next); } catch (e) {}
  document.getElementById('themeToggle').textContent = next === 'dark' ? '☀️' : '🌙';
  if (lastRows) { renderCharts(lastRows, lastPrincipal); }
}

let dpYear = new Date().getFullYear();
let dpMonth = new Date().getMonth();

function toggleCalendar(e) {
  if (e) e.stopPropagation();
  const el = document.getElementById('datepicker');
  const show = el.style.display === 'none';
  el.style.display = show ? 'block' : 'none';
  if (show) renderDatePicker();
}

function changeMonth(delta) {
  dpMonth += delta;
  if (dpMonth < 0) { dpMonth = 11; dpYear--; }
  else if (dpMonth > 11) { dpMonth = 0; dpYear++; }
  renderDatePicker();
}

function renderDatePicker() {
  document.getElementById('dp_title').textContent = dpYear + '年' + (dpMonth + 1) + '月';
  const first = new Date(dpYear, dpMonth, 1);
  const offset = (first.getDay() + 6) % 7; // 周一起始
  const daysInMonth = new Date(dpYear, dpMonth + 1, 0).getDate();
  const today = new Date();
  let html = '';
  for (let i = 0; i < offset; i++) html += '<span></span>';
  for (let d = 1; d <= daysInMonth; d++) {
    const wd = new Date(dpYear, dpMonth, d).getDay();
    const weekend = wd === 0 || wd === 6;
    const isToday = dpYear === today.getFullYear() && dpMonth === today.getMonth() && d === today.getDate();
    html += '<span class="dp-day' + (weekend ? ' dp-weekend' : '') + (isToday ? ' dp-today' : '') + '" onclick="pickDate(' + d + ')">' + d + '</span>';
  }
  document.getElementById('dp_grid').innerHTML = html;
}

function pickDate(d) {
  const dt = new Date(dpYear, dpMonth, d);
  const iso = dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0') + '-' + String(dt.getDate()).padStart(2, '0');
  document.getElementById('start_date').value = iso;
  document.getElementById('datepicker').style.display = 'none';
}

document.addEventListener('click', function(e) {
  const dp = document.getElementById('datepicker');
  const inp = document.getElementById('start_date');
  if (dp && inp && !dp.contains(e.target) && e.target !== inp) {
    dp.style.display = 'none';
  }
});

function refreshTableDates() {
  const rows = document.querySelectorAll('#tbody tr');
  rows.forEach(function(tr, i) {
    const td = tr.children[1];
    if (td) td.textContent = lastTradeDates && lastTradeDates[i] ? lastTradeDates[i] : '';
    const btn = tr.querySelector('.refl-btn');
    if (btn) btn.dataset.date = lastTradeDates && lastTradeDates[i] ? lastTradeDates[i] : '';
  });
  updateReflectionButtons();
}

function updateDeviation(input) {
  recalculate();
}

function recalculate() {
  const principal = parseFloat(document.getElementById('principal').value) || 0;
  const rate = parseFloat(document.getElementById('rate').value) / 100;
  const inputs = document.querySelectorAll('#tbody .actual-input');
  let cur = principal;
  adjustedDiffs = [];
  actualValues = [];
  diffStatus = [];
  inputs.forEach(function(inp) {
    const tr = inp.closest('tr');
    const theoryDiff = cur * rate;               // 理论利息（基于当前总额）
    const raw = inp.value.trim();
    const hasActual = raw !== '' && !isNaN(parseFloat(raw));
    const actualVal = hasActual ? parseFloat(raw) : null;
    const usedDiff = actualVal !== null ? actualVal : theoryDiff;  // 有实际用实际，否则用理论
    const amount = cur + usedDiff;

    const diffTd = tr.querySelector('[data-role="diff"]');
    if (diffTd) diffTd.textContent = '+' + fmt(theoryDiff);
    const amountTd = tr.querySelector('[data-role="amount"]');
    if (amountTd) amountTd.textContent = fmt(amount);
    const devTd = tr.querySelector('[data-role="dev"]');
    if (devTd) {
      if (actualVal !== null) {
        const dev = actualVal - theoryDiff;
        devTd.textContent = (dev >= 0 ? '+' : '') + fmt(dev);
        devTd.className = 'num dev ' + (dev > 0 ? 'dev-pos' : (dev < 0 ? 'dev-neg' : ''));
      } else {
        devTd.textContent = '';
        devTd.className = 'num dev';
      }
    }

    let status = null;
    if (actualVal !== null) {
      if (actualVal < theoryDiff) status = 'below';
      else if (actualVal > theoryDiff) status = 'above';
    }
    diffStatus.push(status);
    actualValues.push(actualVal);
    adjustedDiffs.push(usedDiff);
    cur = amount;
  });
  if (lastCalendarData) renderCalendar(lastCalendarData);
  if (lastRows) renderChart(lastRows);
}

function project() {
  const start = document.getElementById('start_date').value;
  const periods = document.getElementById('periods').value;
  const principal = parseFloat(document.getElementById('principal').value) || 0;
  const rate = parseFloat(document.getElementById('rate').value) / 100;
  const box = document.getElementById('project_result');
  const cal = document.getElementById('calendar');
  if (!start) { box.textContent = '选择「计划起始时间」后，自动按复利次数推演结束日期。'; cal.innerHTML = ''; lastTradeDates = null; refreshTableDates(); return; }
  fetch('/api/trading?start=' + encodeURIComponent(start) + '&periods=' + encodeURIComponent(periods))
    .then(function(r) { return r.json(); })
    .then(function(d) {
      if (d.error) { box.textContent = d.error; cal.innerHTML = ''; return; }
      lastTradeDates = d.days.filter(function(x){return x.type === 'trading';}).map(function(x){return x.date;});
      refreshTableDates();
      const amount = principal * Math.pow(1 + rate, d.periods);
      const profit = amount - principal;
      box.innerHTML = '起始 <b>' + d.start + '</b> → 复利 <b>' + d.periods + '</b> 次 → 结束 <b>' + d.end + '</b>（第 ' + d.periods + ' 个交易日）<br>计划盈利金额：<b style="color:var(--red)">' + fmt(profit) + '</b> 元（本金 ' + fmt(principal) + ' → 期末 ' + fmt(amount) + '）';
      renderCalendar(d);
    });
}

function renderCalendar(d) {
  lastCalendarData = d;
  const el = document.getElementById('calendar');
  const wk = ['一','二','三','四','五','六','日'];
  const offset = (d.days[0].weekday + 6) % 7; // 周一起始
  const principal = parseFloat(document.getElementById('principal').value) || 0;
  const rate = parseFloat(document.getElementById('rate').value) / 100;
  let html = '<div class="cal-week">' + wk.map(function(w){return '<div class="cal-head">'+w+'</div>';}).join('') + '</div>';
  let tradeIndex = 0;
  html += '<div class="cal-grid">';
  let col = 0;
  for (let i = 0; i < offset; i++) { html += '<div class="cal-cell cal-empty"></div>'; col++; }
  let prevYM = null;
  for (const day of d.days) {
    const ym = day.date.slice(0, 7);
    if (ym !== prevYM) {
      while (col % 7 !== 0) { html += '<div class="cal-cell cal-empty"></div>'; col++; }
      const parts = day.date.split('-');
      html += '<div class="cal-month">' + parts[0] + '年' + parseInt(parts[1], 10) + '月</div>';
      col = 0;
      prevYM = ym;
    }
    let cls = 'cal-cell ' + (day.type === 'trading' ? 'cal-trading' : (day.type === 'holiday' ? 'cal-holiday' : 'cal-weekend'));
    if (day.date === d.start) cls += ' cal-start';
    if (day.date === d.end) cls += ' cal-end';
    let sub = '';
    if (day.type === 'trading') {
      tradeIndex++;
      const profit = adjustedDiffs && adjustedDiffs[tradeIndex - 1] != null ? adjustedDiffs[tradeIndex - 1] : (principal * Math.pow(1 + rate, tradeIndex - 1) * rate);
      sub = '<span class="cal-profit">' + (profit >= 0 ? '+' : '') + fmtCompact(profit) + '</span>';
      if (diffStatus && diffStatus[tradeIndex - 1] === 'below') cls += ' cal-below';
      else if (diffStatus && diffStatus[tradeIndex - 1] === 'above') cls += ' cal-above';
    }
    const badge = day.type !== 'trading' ? '<span class="cal-badge">休</span>' : '';
    html += '<div class="' + cls + '" title="' + day.date + '"><span class="cal-day">' + day.day + '</span>' + badge + sub + '</div>';
    col++;
  }
  html += '</div>';
  html += '<div class="cal-legend"><span class="lg lg-t">交易日</span><span class="lg lg-w">周末</span><span class="lg lg-h">法定节假日</span></div>';
  el.innerHTML = html;
}

async function compute() {
  const err = document.getElementById('error');
  err.textContent = '';
  const name = document.getElementById('plan_name').value.trim();
  if (name) currentPlan = name;
  const principal = document.getElementById('principal').value;
  const periods = document.getElementById('periods').value;
  const rate = parseFloat(document.getElementById('rate').value) / 100;
  try {
    const resp = await fetch('/api/compute?principal=' + encodeURIComponent(principal)
      + '&periods=' + encodeURIComponent(periods)
      + '&rate=' + encodeURIComponent(rate));
    const data = await resp.json();
    if (data.error) { err.textContent = data.error; document.getElementById('result').style.display='none'; return; }
    render(data);
    project();
    savePlan();
  } catch (e) {
    err.textContent = '请求失败: ' + e.message;
  }
}

function render(d) {
  document.getElementById('result').style.display = 'block';
  const gain = d.final - d.principal;

  document.getElementById('insight').innerHTML =
    '<span class="dim">' + fmt(d.principal) + ' 元 × 每期 +' + fmtInt(d.rate*100) + '% × 复利 ' + d.periods + ' 次</span> → ' +
    '期末 <b>' + fmt(d.final) + '</b> 元 · 累计收益 <b>' + fmt(gain) + '</b> 元 ' +
    '<span class="dim">（约 ' + fmtInt(d.multiple) + ' 倍本金）</span>';

  document.getElementById('stats').innerHTML =
    stat('💰', '本金', fmt(d.principal), '初始投入') +
    stat('📈', '每期利率', fmtInt(d.rate*100) + '%', '复利计息') +
    stat('🔁', '期数', d.periods, '次') +
    stat('🎯', '期末总额', fmt(d.final), '约 ' + fmtInt(d.multiple) + ' 倍本金', true) +
    stat('💹', '累计收益', fmt(gain), '收益率 ' + fmtInt(gain/d.principal*100) + '%', true);

  lastRows = d.rows;
  lastPrincipal = d.principal;
  renderCharts(d.rows, d.principal);

  adjustedDiffs = null;
  lastCalendarData = null;
  actualValues = null;
  diffStatus = null;
  document.getElementById('tbody').innerHTML = d.rows.map(r =>
    '<tr><td>' + r.n + '</td>' +
    '<td class="num dim">' + (lastTradeDates && lastTradeDates[r.n - 1] ? lastTradeDates[r.n - 1] : '') + '</td>' +
    '<td class="num" data-role="amount">' + fmt(r.amount) + '</td>' +
    '<td class="num diff" data-role="diff">+' + fmt(r.diff) + '</td>' +
    '<td><input class="actual-input" type="text" inputmode="decimal" oninput="updateDeviation(this)"></td>' +
    '<td class="num dev" data-role="dev"></td>' +
    '<td><button class="refl-btn" data-date="' + (lastTradeDates && lastTradeDates[r.n - 1] ? lastTradeDates[r.n - 1] : '') + '" onclick="openReflection(this.dataset.date)">✏️</button></td></tr>'
  ).join('');
  updateReflectionButtons();

  document.getElementById('foot').textContent =
    '共 ' + d.periods + ' 期 · 期末总额 ' + fmt(d.final) + ' 元 · 累计收益 ' + fmt(gain) + ' 元';
}

function stat(icon, label, value, sub, hl) {
  return '<div class="stat' + (hl ? ' stat-hl' : '') + '">' +
    '<div class="stat-top"><span class="stat-icon">' + icon + '</span><span class="stat-label">' + label + '</span></div>' +
    '<div class="stat-value">' + value + '</div>' +
    '<div class="stat-sub">' + sub + '</div></div>';
}

function renderCharts(rows, principal) {
  renderChart(rows);
}

const charts = {};
function initChart(id) {
  if (!charts[id]) charts[id] = echarts.init(document.getElementById(id));
  return charts[id];
}
window.addEventListener('resize', function () {
  Object.values(charts).forEach(c => c.resize());
});

function axisX(t) {
  return {
    type: 'category',
    axisLabel: { interval: 'auto', color: t.axisLabel },
    axisLine: { lineStyle: { color: t.axisLine } },
    axisTick: { show: false }
  };
}
function axisY(t, name, gap, isLog) {
  return {
    type: isLog ? 'log' : 'value',
    name: name, nameLocation: 'middle', nameGap: gap,
    nameTextStyle: { color: t.axisLabel },
    axisLabel: {
      color: t.axisLabel,
      formatter: function (v) {
        if (v >= 100000000) return (v / 100000000) + '亿';
        if (v >= 10000) return (v / 10000) + '万';
        return v;
      }
    },
    splitLine: { lineStyle: { color: t.splitLine } }
  };
}
function tooltipBase(t) {
  return {
    trigger: 'axis',
    backgroundColor: t.tooltipBg,
    borderColor: t.axisLine,
    borderWidth: 1,
    padding: [10, 14],
    textStyle: { color: t.ink, fontSize: 13 },
    extraCssText: 'box-shadow:0 8px 24px rgba(0,0,0,.15); border-radius:12px;',
    formatter: function (params) {
      let html = '第 ' + params[0].axisValue + ' 期';
      params.forEach(function (p) {
        if (p.value != null) html += '<br/>' + p.marker + p.seriesName + '：' + fmt(p.value) + ' 元';
      });
      return html;
    }
  };
}

function renderChart(rows) {
  const chart = initChart('chart');
  const t = themeColors();
  const option = {
    tooltip: {
      trigger: 'axis',
      backgroundColor: t.tooltipBg,
      borderColor: t.axisLine,
      borderWidth: 1,
      padding: [10, 14],
      textStyle: { color: t.ink, fontSize: 13 },
      extraCssText: 'box-shadow:0 8px 24px rgba(0,0,0,.15); border-radius:12px;',
      formatter: function (params) {
        const p = params[0];
        const row = rows[p.dataIndex];
        const actual = actualValues && actualValues[p.dataIndex] != null ? actualValues[p.dataIndex] : 0;
        let html = '第 ' + p.axisValue + ' 期';
        html += '<br/>总额：' + fmt(p.value) + ' 元';
        if (row) html += '<br/>差额：' + fmt(row.diff) + ' 元';
        html += '<br/>实际表现：<span style="color:' + t.red + '">' + fmt(actual) + '</span> 元';
        return html;
      }
    },
    grid: { left: 90, right: 40, top: 30, bottom: 50 },
    xAxis: Object.assign({ data: rows.map(r => r.n), name: '期数', nameLocation: 'middle', nameGap: 35, nameTextStyle: { color: t.axisLabel } }, axisX(t)),
    yAxis: axisY(t, '总额（元）', 62, true),
    series: [{
      name: '总额',
      type: 'line',
      data: rows.map(r => r.amount),
      smooth: true,
      showSymbol: false,
      lineStyle: { width: 3, color: t.accent },
      itemStyle: { color: t.accent },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: t.accentTop },
          { offset: 1, color: t.accentBottom }
        ])
      },
      endLabel: { show: true, color: t.accent, fontWeight: 700, fontSize: 12, formatter: function (p) { return fmt(p.value); } }
    }]
  };
  chart.setOption(option, true);
  chart.resize();
}

(function initThemeIcon() {
  const dark = document.documentElement.getAttribute('data-theme') === 'dark';
  document.getElementById('themeToggle').textContent = dark ? '☀️' : '🌙';
})();

document.addEventListener('mousemove', function (e) {
  const el = e.target.closest && e.target.closest('.stat');
  if (!el) return;
  const r = el.getBoundingClientRect();
  const x = (e.clientX - r.left) / r.width - 0.5;
  const y = (e.clientY - r.top) / r.height - 0.5;
  el.style.transform = 'perspective(800px) rotateX(' + (-y * 10).toFixed(2) + 'deg) rotateY(' + (x * 12).toFixed(2) + 'deg) translateY(-5px) scale(1.03)';
});
document.addEventListener('mouseout', function (e) {
  const el = e.target.closest && e.target.closest('.stat');
  if (el && !el.contains(e.relatedTarget)) el.style.transform = '';
});

let reflections = {};
let currentReflectionDate = null;

function loadReflections() {
  fetch('/api/reflections')
    .then(function(r){ return r.json(); })
    .then(function(d){ reflections = d || {}; updateReflectionButtons(); });
}

function openReflection(date) {
  if (!date) return;
  currentReflectionDate = date;
  const key = (currentPlan || '') + '|' + date;
  document.getElementById('refl_title').textContent = '反思 · ' + date;
  document.getElementById('refl_editor').innerHTML = reflections[key] || '';
  document.getElementById('refl_overlay').style.display = 'flex';
}

function closeReflection() {
  document.getElementById('refl_overlay').style.display = 'none';
  currentReflectionDate = null;
}

function saveReflection() {
  if (!currentReflectionDate) return;
  const content = document.getElementById('refl_editor').innerHTML;
  fetch('/api/reflection', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({plan: currentPlan || '', date: currentReflectionDate, content: content})
  }).then(function(r){ return r.json(); }).then(function() {
    reflections[(currentPlan || '') + '|' + currentReflectionDate] = content;
    closeReflection();
    updateReflectionButtons();
  });
}

function updateReflectionButtons() {
  document.querySelectorAll('#tbody .refl-btn').forEach(function(btn) {
    const d = btn.dataset.date;
    const key = (currentPlan || '') + '|' + d;
    if (d && reflections[key] && reflections[key].trim()) {
      btn.textContent = '✅';
      btn.classList.add('has');
      btn.title = '已写反思';
    } else {
      btn.textContent = '✏️';
      btn.classList.remove('has');
      btn.title = '写反思';
    }
  });
}

function insertImageAtCursor(dataUrl) {
  const editor = document.getElementById('refl_editor');
  if (!editor) return;
  editor.focus();
  const wrap = document.createElement('div');
  wrap.className = 'img-wrap';
  wrap.contentEditable = 'false';
  const img = document.createElement('img');
  img.src = dataUrl;
  img.alt = '行情截图';
  const del = document.createElement('button');
  del.className = 'img-del';
  del.type = 'button';
  del.textContent = '×';
  wrap.appendChild(img);
  wrap.appendChild(del);
  const sel = window.getSelection();
  if (sel && sel.rangeCount > 0 && editor.contains(sel.anchorNode)) {
    const range = sel.getRangeAt(0);
    range.deleteContents();
    range.insertNode(wrap);
    const br = document.createElement('br');
    range.setStartAfter(wrap);
    range.insertNode(br);
    range.setStartAfter(br);
    range.collapse(true);
    sel.removeAllRanges();
    sel.addRange(range);
  } else {
    editor.appendChild(wrap);
    editor.appendChild(document.createElement('br'));
  }
}

function removeImage(btn) {
  const wrap = btn.closest('.img-wrap');
  if (wrap) wrap.remove();
}

function openLightbox(src) {
  document.getElementById('lightbox_img').src = src;
  document.getElementById('lightbox').style.display = 'flex';
}

function closeLightbox() {
  document.getElementById('lightbox').style.display = 'none';
}

function insertReflectionImage() {
  const input = document.createElement('input');
  input.type = 'file';
  input.accept = 'image/*';
  input.onchange = function() {
    const file = input.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function(e) {
      insertImageAtCursor(e.target.result);
    };
    reader.readAsDataURL(file);
  };
  input.click();
}

document.addEventListener('DOMContentLoaded', function() {
  const editor = document.getElementById('refl_editor');
  if (!editor) return;
  editor.addEventListener('dblclick', function(e) {
    const img = e.target && e.target.closest ? e.target.closest('img') : null;
    if (img && img.src) openLightbox(img.src);
  });
  editor.addEventListener('click', function(e) {
    const del = e.target && e.target.closest ? e.target.closest('.img-del') : null;
    if (del) removeImage(del);
  });
  editor.addEventListener('paste', function(e) {
    const items = e.clipboardData && e.clipboardData.items;
    if (!items) return;
    for (const item of items) {
      if (item.type && item.type.indexOf('image') === 0) {
        e.preventDefault();
        const blob = item.getAsFile();
        const reader = new FileReader();
        reader.onload = function(ev) {
          insertImageAtCursor(ev.target.result);
        };
        reader.readAsDataURL(blob);
        break;
      }
    }
  });
});

let plans = {};
let currentPlan = null;

function escapeHtml(v) {
  return String(v).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function loadPlans() {
  fetch('/api/plans')
    .then(function(r){ return r.json(); })
    .then(function(d){ plans = d || {}; renderPlansTabs(); });
}

function renderPlansTabs() {
  const el = document.getElementById('plans_tabs');
  let html = '';
  for (const name in plans) {
    html += '<button class="plan-tab' + (name === currentPlan ? ' active' : '') + '" data-name="' + escapeHtml(name) + '">' + escapeHtml(name) + '</button>';
  }
  html += '<button class="plan-tab plan-new" data-new="1">+ 新计划</button>';
  el.innerHTML = html;
}

function switchPlan(name) {
  currentPlan = name;
  const p = plans[name];
  if (!p) return;
  document.getElementById('plan_name').value = name;
  document.getElementById('principal').value = p.principal || 30000;
  document.getElementById('rate').value = p.rate || 3;
  document.getElementById('periods').value = p.periods || 220;
  document.getElementById('start_date').value = p.start_date || '';
  renderPlansTabs();
  compute();
}

function newPlan() {
  currentPlan = null;
  document.getElementById('plan_name').value = '';
  document.getElementById('plan_name').focus();
  renderPlansTabs();
  document.getElementById('result').style.display = 'none';
  document.getElementById('tbody').innerHTML = '';
}

function savePlan() {
  const name = document.getElementById('plan_name').value.trim();
  if (!name) return;
  const params = {
    principal: document.getElementById('principal').value,
    rate: document.getElementById('rate').value,
    periods: document.getElementById('periods').value,
    start_date: document.getElementById('start_date').value
  };
  plans[name] = params;
  currentPlan = name;
  renderPlansTabs();
  fetch('/api/plan', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name, params: params})
  });
}

document.addEventListener('click', function(e) {
  const tab = e.target && e.target.closest ? e.target.closest('.plan-tab') : null;
  const tabsEl = document.getElementById('plans_tabs');
  if (!tab || !tabsEl || !tabsEl.contains(tab)) return;
  if (tab.dataset.new) { newPlan(); }
  else if (tab.dataset.name) { switchPlan(tab.dataset.name); }
});

loadReflections();
loadPlans();

</script>
<div class="modal-overlay" id="refl_overlay" style="display:none">
  <div class="modal">
    <div class="modal-head">
      <span id="refl_title">反思</span>
      <button class="modal-close" onclick="closeReflection()">×</button>
    </div>
    <div class="modal-toolbar">
      <button class="tb-btn" onclick="insertReflectionImage()">🖼 插入图片</button>
      <span class="tb-hint">可直接 Ctrl+V 粘贴行情截图</span>
    </div>
    <div class="refl-editor" id="refl_editor" contenteditable="true" data-placeholder="写反思、粘贴行情走势图截图..."></div>
    <div class="modal-foot">
      <button class="save-btn" onclick="saveReflection()">保存</button>
      <button class="cancel-btn" onclick="closeReflection()">取消</button>
    </div>
  </div>
</div>
<div class="lightbox" id="lightbox" style="display:none" onclick="closeLightbox()">
  <img id="lightbox_img" src="" alt="原图">
</div>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "CompoundServer/1.0"

    def _send(self, code, body, ctype):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_static(self, path):
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError:
            self._send(404, "not found", "text/plain; charset=utf-8")
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/javascript; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send(200, HTML, "text/html; charset=utf-8")
            return
        if parsed.path == "/api/compute":
            self._handle_api(parse_qs(parsed.query))
            return
        if parsed.path == "/api/trading":
            self._handle_trading(parse_qs(parsed.query))
            return
        if parsed.path == "/api/reflections":
            self._send(200, json.dumps(load_reflections(), ensure_ascii=False), "application/json; charset=utf-8")
            return
        if parsed.path == "/api/plans":
            self._send(200, json.dumps(load_plans(), ensure_ascii=False), "application/json; charset=utf-8")
            return
        if parsed.path == "/favicon.ico":
            self._send(204, "", "text/plain")
            return
        if parsed.path == "/echarts.min.js":
            self._send_static(os.path.join(os.path.dirname(os.path.abspath(__file__)), "echarts.min.js"))
            return
        if parsed.path == "/echarts-gl.min.js":
            self._send_static(os.path.join(os.path.dirname(os.path.abspath(__file__)), "echarts-gl.min.js"))
            return
        self._send(404, "not found", "text/plain; charset=utf-8")

    def do_POST(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/reflection":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                data = json.loads(body)
            except (ValueError, json.JSONDecodeError):
                self._send(400, json.dumps({"error": "invalid json"}), "application/json; charset=utf-8")
                return
            plan = data.get("plan") or ""
            date = data.get("date")
            content = data.get("content", "")
            if not date:
                self._send(400, json.dumps({"error": "date required"}), "application/json; charset=utf-8")
                return
            save_reflection(plan, date, content)
            self._send(200, json.dumps({"ok": True}), "application/json; charset=utf-8")
            return
        if parsed.path == "/api/plan":
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = self.rfile.read(length).decode("utf-8")
                data = json.loads(body)
            except (ValueError, json.JSONDecodeError):
                self._send(400, json.dumps({"error": "invalid json"}), "application/json; charset=utf-8")
                return
            name = (data.get("name") or "").strip()
            params = data.get("params") or {}
            if not name:
                self._send(400, json.dumps({"error": "name required"}), "application/json; charset=utf-8")
                return
            save_plan(name, params)
            self._send(200, json.dumps({"ok": True}), "application/json; charset=utf-8")
            return
        self._send(404, json.dumps({"error": "not found"}), "application/json; charset=utf-8")

    def _handle_api(self, q):
        def first(key, default=None):
            v = q.get(key)
            return v[0] if v else default

        try:
            principal = float(first("principal", 27000))
            periods = int(float(first("periods", 60)))
            rate = float(first("rate", DEFAULT_RATE))
        except (TypeError, ValueError):
            self._send(400, json.dumps({"error": "参数格式错误"}), "application/json; charset=utf-8")
            return

        if principal <= 0 or principal > MAX_PRINCIPAL:
            self._send(400, json.dumps({"error": f"基数需在 0 ~ {MAX_PRINCIPAL:g} 之间"}), "application/json; charset=utf-8")
            return
        if periods < 1 or periods > MAX_PERIODS:
            self._send(400, json.dumps({"error": f"复利次数需在 1 ~ {MAX_PERIODS} 之间"}), "application/json; charset=utf-8")
            return
        if rate <= -1 or rate > 10:
            self._send(400, json.dumps({"error": "利率需在 -100% ~ 1000% 之间"}), "application/json; charset=utf-8")
            return

        rows = compute(principal, rate, periods)
        result = {
            "principal": principal,
            "rate": rate,
            "periods": periods,
            "final": rows[-1]["amount"],
            "gain": round(rows[-1]["amount"] - principal, 2),
            "multiple": rows[-1]["multiple"],
            "rows": rows,
        }
        self._send(200, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")

    def _handle_trading(self, q):
        def first(key, default=None):
            v = q.get(key)
            return v[0] if v else default

        try:
            start = datetime.date.fromisoformat(first("start", ""))
            periods = int(float(first("periods", 60)))
        except (TypeError, ValueError):
            self._send(400, json.dumps({"error": "参数格式错误：start=YYYY-MM-DD, periods=整数"}), "application/json; charset=utf-8")
            return
        if periods < 1 or periods > MAX_PERIODS:
            self._send(400, json.dumps({"error": f"复利次数需在 1 ~ {MAX_PERIODS} 之间"}), "application/json; charset=utf-8")
            return

        # 逐日推演，收集区间内每天的交易状态
        holidays = set()
        years = set()
        d = start
        days = []
        n = 0
        while n < periods:
            if d.year not in years:
                years.add(d.year)
                holidays |= fetch_holidays(d.year)
            trading = is_trading_day(d, holidays)
            if trading:
                n += 1
            wd = d.weekday()
            if wd >= 5:
                typ = "weekend"
            elif not trading:
                typ = "holiday"
            else:
                typ = "trading"
            days.append({
                "date": d.isoformat(),
                "day": d.day,
                "month": d.month,
                "weekday": wd,
                "type": typ,
            })
            d += datetime.timedelta(days=1)

        result = {
            "start": start.isoformat(),
            "end": days[-1]["date"],
            "periods": periods,
            "days": days,
        }
        self._send(200, json.dumps(result, ensure_ascii=False), "application/json; charset=utf-8")

    def log_message(self, fmt_str, *args):
        print("[%s] %s" % (self.address_string(), fmt_str % args))


if __name__ == "__main__":
    init_db()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    print("Compound server running at http://localhost:%d  (Ctrl+C to stop)" % PORT)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
