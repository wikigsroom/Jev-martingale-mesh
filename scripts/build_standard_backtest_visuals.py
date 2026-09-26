"""Build static charts and an inline dashboard for standard backtests."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib import font_manager


CHINESE_FONT = Path("C:/Windows/Fonts/msyh.ttc")
if CHINESE_FONT.exists():
    font_manager.fontManager.addfont(str(CHINESE_FONT))
    plt.rcParams["font.family"] = font_manager.FontProperties(fname=str(CHINESE_FONT)).get_name()
plt.rcParams["axes.unicode_minus"] = False


WINDOW_ORDER = ["3m", "6m", "9m", "12m", "24m", "36m"]
WINDOW_LABELS = {
    "3m": "3个月",
    "6m": "6个月",
    "9m": "9个月",
    "12m": "12个月",
    "24m": "24个月",
    "36m": "36个月",
}


def load_summary(root: Path) -> tuple[dict, pd.DataFrame]:
    summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))
    frame = pd.DataFrame.from_dict(summary["windows"], orient="index")
    frame = frame.loc[WINDOW_ORDER].copy()
    return summary, frame


def style_axis(axis):
    axis.set_facecolor("#111827")
    axis.grid(axis="y", color="#334155", alpha=.5, linewidth=.7)
    axis.tick_params(colors="#cbd5e1", labelsize=9)
    for spine in axis.spines.values():
        spine.set_color("#334155")
    axis.yaxis.label.set_color("#cbd5e1")
    axis.xaxis.label.set_color("#cbd5e1")
    axis.title.set_color("#f8fafc")


def build_summary_chart(frame: pd.DataFrame, output: Path) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(14, 5.8), dpi=160,
                             facecolor="#0b1120")
    x = np.arange(len(frame))
    colors = ["#f87171" if value < 0 else "#34d399" for value in frame.return_pct]
    axes[0].bar(x, frame.return_pct, color=colors, width=.65)
    axes[0].axhline(0, color="#94a3b8", linewidth=.8)
    axes[0].set_title("独立窗口收益率", loc="left", fontweight="bold")
    axes[0].set_ylabel("收益率 (%)")
    axes[0].set_xticks(x, [WINDOW_LABELS[name] for name in frame.index])
    for index, value in enumerate(frame.return_pct):
        axes[0].text(index, value + (2 if value >= 0 else -4), f"{value:.1f}%",
                     ha="center", va="bottom" if value >= 0 else "top",
                     color="#e2e8f0", fontsize=9)
    style_axis(axes[0])
    axes[1].bar(x, frame.max_drawdown_pct, color="#f59e0b", width=.65)
    axes[1].set_title("最大回撤", loc="left", fontweight="bold")
    axes[1].set_ylabel("回撤 (%)")
    axes[1].set_xticks(x, [WINDOW_LABELS[name] for name in frame.index])
    for index, value in enumerate(frame.max_drawdown_pct):
        axes[1].text(index, value + 1, f"{value:.1f}%", ha="center",
                     color="#e2e8f0", fontsize=9)
    style_axis(axes[1])
    fig.suptitle("JEV action=3 优化策略：标准窗口 1 秒回测", color="#f8fafc",
                 fontsize=15, fontweight="bold", x=.06, ha="left")
    fig.text(.06, .02, "初始权益 1,000 U；base_amount_min / min_trade_notional = 130 U；账户回撤停机关闭",
             color="#94a3b8", fontsize=9)
    fig.tight_layout(rect=(0, .06, 1, .93))
    fig.savefig(output, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def build_equity_chart(root: Path, output: Path) -> None:
    fig, axis = plt.subplots(figsize=(14, 6), dpi=160, facecolor="#0b1120")
    style_axis(axis)
    for name in WINDOW_ORDER:
        curve = pd.read_csv(root / name / "minute_equity.csv")
        equity = curve.equity.to_numpy(dtype=float)
        normalized = equity / equity[0] * 100
        progress = np.linspace(0, 100, len(normalized))
        axis.plot(progress, normalized, linewidth=1.15,
                  label=WINDOW_LABELS[name])
    axis.axhline(100, color="#94a3b8", linewidth=.8, linestyle="--")
    axis.set_title("各独立窗口归一化权益曲线", loc="left", fontweight="bold")
    axis.set_xlabel("窗口进度 (%)；每个窗口从 100 开始")
    axis.set_ylabel("归一化权益")
    legend = axis.legend(ncol=3, frameon=False, loc="upper left")
    for text in legend.get_texts():
        text.set_color("#cbd5e1")
    fig.suptitle("权益路径与回撤路径", color="#f8fafc", fontsize=15,
                 fontweight="bold", x=.06, ha="left")
    fig.tight_layout(rect=(0, 0, 1, .93))
    fig.savefig(output, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def build_monthly_heatmap(root: Path, output: Path) -> None:
    all_months = []
    for name in WINDOW_ORDER:
        monthly = pd.read_csv(root / name / "monthly.csv")
        monthly["window"] = name
        all_months.append(monthly[["month", "return_pct", "window"]])
    data = pd.concat(all_months, ignore_index=True)
    pivot = data.pivot(index="window", columns="month", values="return_pct").reindex(WINDOW_ORDER)
    fig, axis = plt.subplots(figsize=(16, 4.4), dpi=160, facecolor="#0b1120")
    matrix = np.ma.masked_invalid(pivot.to_numpy(dtype=float))
    image = axis.imshow(matrix, aspect="auto", cmap="RdYlGn", vmin=-20, vmax=20)
    axis.set_yticks(np.arange(len(pivot.index)), [WINDOW_LABELS[name] for name in pivot.index])
    axis.set_xticks(np.arange(len(pivot.columns)), pivot.columns, rotation=60, ha="right")
    axis.tick_params(colors="#cbd5e1", labelsize=8)
    for spine in axis.spines.values():
        spine.set_color("#334155")
    axis.set_title("月度回报热力图（独立窗口）", loc="left", color="#f8fafc",
                   fontweight="bold")
    colorbar = fig.colorbar(image, ax=axis, pad=.01)
    colorbar.ax.tick_params(colors="#cbd5e1", labelsize=8)
    colorbar.set_label("月度收益率 (%)", color="#cbd5e1")
    fig.tight_layout()
    fig.savefig(output, facecolor=fig.get_facecolor(), bbox_inches="tight")
    plt.close(fig)


def dashboard_html(summary: dict, frame: pd.DataFrame) -> str:
    rows = []
    for name in WINDOW_ORDER:
        item = summary["windows"][name]
        rows.append({
            "name": WINDOW_LABELS[name],
            "key": name,
            "return": round(item["return_pct"], 4),
            "cagr": round(item["cagr_pct"], 4),
            "drawdown": round(item["max_drawdown_pct"], 4),
            "final": round(item["final_equity"], 4),
            "fills": int(item["fills"]),
            "adds": int(item["martingale_adds"]),
            "jev": int(item["jev_signals"]),
            "stops": int(item["risk_stops"]),
        })
    payload = json.dumps(rows, ensure_ascii=False)
    return f'''<style>
:root {{ color-scheme: dark; --bg:#080d19; --panel:#111827; --line:#243244; --text:#e5edf7; --muted:#94a3b8; --green:#34d399; --red:#fb7185; --amber:#f59e0b; --blue:#60a5fa; }}
.jev-dashboard {{ font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color:var(--text); background:linear-gradient(135deg,#080d19 0%,#0f172a 55%,#111827 100%); border:1px solid var(--line); border-radius:18px; padding:24px; max-width:1160px; box-sizing:border-box; }}
.jev-dashboard h2 {{ margin:0 0 6px; font-size:24px; letter-spacing:-.02em; }}
.jev-dashboard .lede {{ margin:0 0 20px; color:var(--muted); font-size:13px; }}
.jev-dashboard .grid {{ display:grid; grid-template-columns:1.45fr 1fr; gap:16px; align-items:start; }}
.jev-dashboard .panel {{ background:rgba(17,24,39,.78); border:1px solid var(--line); border-radius:14px; padding:14px; }}
.jev-dashboard .panel h3 {{ margin:0 0 10px; font-size:14px; color:#f8fafc; }}
.jev-dashboard svg {{ width:100%; height:auto; display:block; overflow:visible; }}
.jev-dashboard .table-wrap {{ overflow:auto; }}
.jev-dashboard table {{ width:100%; border-collapse:collapse; font-size:12px; white-space:nowrap; }}
.jev-dashboard th, .jev-dashboard td {{ padding:8px 7px; border-bottom:1px solid rgba(36,50,68,.75); text-align:right; }}
.jev-dashboard th:first-child, .jev-dashboard td:first-child {{ text-align:left; }}
.jev-dashboard th {{ color:var(--muted); font-weight:600; }}
.jev-dashboard .positive {{ color:var(--green); }} .jev-dashboard .negative {{ color:var(--red); }}
.jev-dashboard .note {{ margin-top:14px; color:var(--muted); font-size:11px; line-height:1.5; }}
@media (max-width:850px) {{ .jev-dashboard .grid {{ grid-template-columns:1fr; }} }}
</style>
<section class="jev-dashboard" id="jev-standard-backtest-dashboard">
  <h2>JEV 优化策略 · 标准窗口 1 秒回测</h2>
  <p class="lede">独立起始权益 1,000 U · 每笔名义金额下限 130 U · 账户回撤停机关闭 · 数据截至 2026-09-22（右端不含）</p>
  <div class="grid">
    <div class="panel"><h3>收益率 / 最大回撤</h3><svg id="jev-bars" viewBox="0 0 720 330" role="img" aria-label="收益率和最大回撤"></svg></div>
    <div class="panel"><h3>窗口指标</h3><div class="table-wrap"><table><thead><tr><th>窗口</th><th>期末 U</th><th>收益</th><th>CAGR</th><th>回撤</th><th>成交</th><th>JEV</th></tr></thead><tbody id="jev-table"></tbody></table></div></div>
  </div>
  <p class="note">解释：每个窗口均从 1,000 U 独立开始，不把短窗口的期末权益滚入长窗口；3m/6m 是当前样本内的亏损区间，不能用 36m 累计收益掩盖。JEV action=3 只在事件期间暂停新增腿/再入场，保留已有篮子的止盈与硬风险规则。</p>
</section>
<script>
(() => {{
  const data = {payload};
  const svg = document.getElementById('jev-bars');
  const table = document.getElementById('jev-table');
  const W = 720, H = 330, left = 56, right = 16, top = 22, bottom = 52;
  const plotW = W-left-right, plotH = H-top-bottom;
  const maxAbs = Math.max(25, ...data.map(d => Math.max(Math.abs(d.return), d.drawdown))) * 1.12;
  const xStep = plotW / data.length;
  const y = value => top + plotH * (1 - (value + maxAbs) / (2*maxAbs));
  const zero = y(0);
  const yAxis = [maxAbs, maxAbs/2, 0, -maxAbs/2, -maxAbs];
  svg.innerHTML = yAxis.map(value => `<line x1="${{left}}" x2="${{W-right}}" y1="${{y(value)}}" y2="${{y(value)}}" stroke="#334155" stroke-width="1"/><text x="${{left-8}}" y="${{y(value)+4}}" fill="#94a3b8" font-size="10" text-anchor="end">${{value.toFixed(0)}}%</text>`).join('');
  svg.innerHTML += `<line x1="${{left}}" x2="${{W-right}}" y1="${{zero}}" y2="${{zero}}" stroke="#94a3b8" stroke-width="1"/>`;
  data.forEach((item, index) => {{
    const center = left + xStep*(index+.5), barW = Math.min(28, xStep*.24);
    const returnY = item.return >= 0 ? y(item.return) : zero;
    const returnH = Math.abs(y(item.return)-zero);
    const ddY = zero, ddH = y(-item.drawdown)-zero;
    svg.innerHTML += `<rect x="${{center-barW-3}}" y="${{returnY}}" width="${{barW}}" height="${{Math.max(1,returnH)}}" rx="3" fill="${{item.return>=0?'#34d399':'#fb7185'}}"/><rect x="${{center+3}}" y="${{ddY}}" width="${{barW}}" height="${{Math.max(1,ddH)}}" rx="3" fill="#f59e0b"/><text x="${{center}}" y="${{H-25}}" fill="#cbd5e1" font-size="11" text-anchor="middle">${{item.name}}</text><text x="${{center-barW/2-3}}" y="${{returnY-6}}" fill="#e5edf7" font-size="9" text-anchor="middle">${{item.return.toFixed(1)}}%</text><text x="${{center+barW/2+3}}" y="${{ddY+ddH+14}}" fill="#fcd34d" font-size="9" text-anchor="middle">-${{item.drawdown.toFixed(1)}}%</text>`;
  }});
  table.innerHTML = data.map(item => `<tr><td>${{item.name}}</td><td>${{item.final.toFixed(1)}}</td><td class="${{item.return>=0?'positive':'negative'}}">${{item.return.toFixed(1)}}%</td><td class="${{item.cagr>=0?'positive':'negative'}}">${{item.cagr.toFixed(1)}}%</td><td>${{item.drawdown.toFixed(1)}}%</td><td>${{item.fills}}</td><td>${{item.jev}}</td></tr>`).join('');
}})();
</script>'''


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--visualization-dir", type=Path, required=True)
    args = parser.parse_args()
    summary, frame = load_summary(args.root)
    args.root.mkdir(parents=True, exist_ok=True)
    args.visualization_dir.mkdir(parents=True, exist_ok=True)
    build_summary_chart(frame, args.root / "standard_returns_drawdown.png")
    build_equity_chart(args.root, args.root / "standard_equity_curves.png")
    build_monthly_heatmap(args.root, args.root / "standard_monthly_heatmap.png")
    html = dashboard_html(summary, frame)
    html_path = args.visualization_dir / "standard-backtest-dashboard.html"
    html_path.write_text(html, encoding="utf-8")
    print(json.dumps({"html": str(html_path), "pngs": [
        str(args.root / "standard_returns_drawdown.png"),
        str(args.root / "standard_equity_curves.png"),
        str(args.root / "standard_monthly_heatmap.png"),
    ]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
