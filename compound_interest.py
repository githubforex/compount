# -*- coding: utf-8 -*-
"""
复利计算 + 网页(网格)展示生成器
默认: 本金 27000, 年/期利率 10%, 复利 60 期
运行: python compound_interest.py [本金] [利率%] [期数]
生成: compound_interest.html
"""
import math
import sys
import html as _html


def compute(principal: float, rate: float, periods: int):
    rows = []
    cur = principal
    for i in range(1, periods + 1):
        nxt = cur * (1 + rate)
        diff = nxt - cur
        rows.append({
            "n": i,
            "amount": nxt,
            "diff": diff,
            "multiple": nxt / principal,
        })
        cur = nxt
    return rows


def build_chart_svg(rows, width=1000, height=340, pad_l=80, pad_r=20, pad_t=20, pad_b=46):
    """对数 Y 轴的 SVG 折线图，展示指数增长曲线。"""
    amounts = [r["amount"] for r in rows]
    y_min = math.log10(amounts[0])
    y_max = math.log10(amounts[-1])

    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    n = len(rows)

    def px(i):
        return pad_l + plot_w * (i / (n - 1)) if n > 1 else pad_l + plot_w / 2

    def py(v):
        frac = (math.log10(v) - y_min) / (y_max - y_min)
        return pad_t + plot_h * (1 - frac)

    pts = " ".join(f"{px(i):.2f},{py(rows[i]['amount']):.2f}" for i in range(n))
    area = f"M{pad_l},{pad_t + plot_h} L" + " L".join(
        f"{px(i):.2f},{py(rows[i]['amount']):.2f}" for i in range(n)
    ) + f" L{px(n - 1):.2f},{pad_t + plot_h} Z"

    # 对数刻度线
    ticks = []
    y0 = math.floor(y_min)
    y1 = math.ceil(y_max)
    k = y0
    while k <= y1:
        ticks.append(k)
        k += 1

    tick_marks = ""
    for k in ticks:
        y = py(10 ** k)
        tick_marks += (
            f'<line x1="{pad_l}" y1="{y:.2f}" x2="{width - pad_r}" y2="{y:.2f}" '
            f'stroke="#e2e8f0" stroke-width="1"/>'
            f'<text x="{pad_l - 8}" y="{y + 4:.2f}" text-anchor="end" '
            f'font-size="11" fill="#64748b">10<sup>{k}</sup></text>'
        )

    # X 轴刻度
    x_ticks = ""
    for i in range(0, n, 5):
        x_ticks += (
            f'<text x="{px(i):.2f}" y="{height - pad_b + 18}" text-anchor="middle" '
            f'font-size="11" fill="#64748b">{rows[i]["n"]}</text>'
        )

    return f'''
<svg viewBox="0 0 {width} {height}" preserveAspectRatio="xMidYMid meet" style="width:100%;height:auto">
  <defs>
    <linearGradient id="areaGrad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#2563eb" stop-opacity="0.35"/>
      <stop offset="100%" stop-color="#2563eb" stop-opacity="0.02"/>
    </linearGradient>
  </defs>
  {tick_marks}
  <path d="{area}" fill="url(#areaGrad)"/>
  <polyline points="{pts}" fill="none" stroke="#2563eb" stroke-width="2.5" stroke-linejoin="round"/>
  <circle cx="{px(n-1):.2f}" cy="{py(rows[-1]['amount']):.2f}" r="5" fill="#dc2626"/>
  {x_ticks}
  <text x="{pad_l + plot_w/2:.2f}" y="{height - 8}" text-anchor="middle" font-size="12" fill="#64748b">期数</text>
</svg>'''


def build_html(principal, rate, periods, rows):
    fmt = lambda v: f"{v:,.2f}"
    final = rows[-1]["amount"]
    gain = final - principal

    stat_cards = "".join(f'''
      <div class="stat">
        <div class="stat-label">{label}</div>
        <div class="stat-value">{value}</div>
        <div class="stat-sub">{sub}</div>
      </div>''' for label, value, sub in [
        ("本金", fmt(principal), "初始投入"),
        ("每期利率", f"{rate*100:g}%", "复利计息"),
        ("期数", str(periods), "次"),
        ("期末总额", fmt(final), f"约 {rows[-1]['multiple']:,.2f} 倍本金"),
        ("累计收益", fmt(gain), f"收益率 {gain/principal*100:,.2f}%"),
    ])

    body_rows = ""
    for r in rows:
        body_rows += f'''<tr>
          <td>{r["n"]}</td>
          <td class="num">{fmt(r["amount"])}</td>
          <td class="num diff">+{fmt(r["diff"])}</td>
          <td class="num">{r["multiple"]:,.4f}x</td>
        </tr>'''

    return f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>复利明细 - 本金 {fmt(principal)} / 利率 {rate*100:g}% / {periods} 期</title>
<style>
  :root {{
    --blue: #2563eb; --red: #dc2626; --ink: #0f172a; --muted: #64748b;
    --line: #e2e8f0; --bg: #f1f5f9; --card: #ffffff;
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0; font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif;
    background: var(--bg); color: var(--ink); padding: 24px 16px 64px;
  }}
  .wrap {{ max-width: 1080px; margin: 0 auto; }}
  h1 {{ font-size: 24px; margin: 0 0 4px; }}
  .subtitle {{ color: var(--muted); margin-bottom: 24px; }}
  .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 24px; }}
  .stat {{
    background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    padding: 16px 18px; box-shadow: 0 1px 2px rgba(15,23,42,.04);
  }}
  .stat-label {{ font-size: 12px; color: var(--muted); margin-bottom: 6px; }}
  .stat-value {{ font-size: 22px; font-weight: 700; }}
  .stat-sub {{ font-size: 12px; color: var(--muted); margin-top: 4px; }}
  .panel {{
    background: var(--card); border: 1px solid var(--line); border-radius: 12px;
    padding: 20px; margin-bottom: 24px; box-shadow: 0 1px 2px rgba(15,23,42,.04);
  }}
  .panel h2 {{ font-size: 16px; margin: 0 0 12px; }}
  .table-scroll {{ overflow-x: auto; max-height: 560px; overflow-y: auto; border-radius: 8px; }}
  table {{ border-collapse: collapse; width: 100%; font-size: 14px; }}
  th, td {{ padding: 8px 12px; text-align: right; border-bottom: 1px solid var(--line); }}
  th {{ background: #f8fafc; color: var(--muted); font-weight: 600; position: sticky; top: 0; }}
  th:first-child, td:first-child {{ text-align: center; color: var(--muted); }}
  .num {{ font-variant-numeric: tabular-nums; }}
  .diff {{ color: var(--red); }}
  tr:hover td {{ background: #f0f7ff; }}
  .foot {{ color: var(--muted); font-size: 13px; margin-top: 12px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>复利明细（网格展示）</h1>
  <div class="subtitle">每期总额 = 上期 × (1 + 利率)，差额 = 当期利息</div>

  <div class="grid">{stat_cards}</div>

  <div class="panel">
    <h2>增长曲线（Y 轴为对数刻度）</h2>
    {build_chart_svg(rows)}
  </div>

  <div class="panel">
    <h2>逐期明细</h2>
    <div class="table-scroll">
      <table>
        <thead>
          <tr><th>期数</th><th>总额（元）</th><th>差额（元）</th><th>倍数</th></tr>
        </thead>
        <tbody>{body_rows}</tbody>
      </table>
    </div>
    <div class="foot">共 {periods} 期 · 期末总额 {fmt(final)} 元 · 累计收益 {fmt(gain)} 元</div>
  </div>
</div>
</body>
</html>'''


def main():
    principal = float(sys.argv[1]) if len(sys.argv) > 1 else 27000.0
    rate_pct = float(sys.argv[2]) if len(sys.argv) > 2 else 10.0
    periods = int(sys.argv[3]) if len(sys.argv) > 3 else 60
    rate = rate_pct / 100.0

    rows = compute(principal, rate, periods)
    html_doc = build_html(principal, rate, periods, rows)

    out = "compound_interest.html"
    with open(out, "w", encoding="utf-8") as f:
        f.write(html_doc)

    print(f"[OK] principal={principal}, rate={rate_pct}%, periods={periods}")
    print(f"[OK] final amount = {rows[-1]['amount']:,.2f}")
    print(f"[OK] wrote -> {out}")


if __name__ == "__main__":
    main()
