"""Run one frozen 1-second policy over standard ETHUSDT horizons."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jevmesh.fmz_config import FMZConfig
from jevmesh.fmz_engine import backtest_fmz
from run_long_historical_fmz import prepare_arrays, write_report

WINDOWS = [("3m", "2026-06-22T00:00:00Z"), ("6m", "2026-03-22T00:00:00Z"),
           ("9m", "2025-12-22T00:00:00Z"), ("12m", "2025-09-22T00:00:00Z"),
           ("24m", "2024-09-22T00:00:00Z"), ("36m", "2023-09-24T00:00:00Z")]


def iso(value: str) -> str:
    return pd.Timestamp(value, tz="UTC").isoformat().replace("+00:00", "Z")


def manifest_for_window(full: dict, start: str, end: str) -> dict:
    start_ts, end_ts = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    days = [row for row in full["seconds"]
            if start_ts.strftime("%Y-%m-%d") <= row["date"] < end_ts.strftime("%Y-%m-%d")]
    signature_data = {"start": iso(start), "end_exclusive": iso(end),
                      "market_sha256": full["market_sha256"],
                      "funding_sha256": full["funding_sha256"],
                      "predictions_sha256": full["predictions_sha256"],
                      "news_source": full.get("news_source"), "seconds": days}
    result = dict(full)
    result.update(signature_data, symbol="ETHUSDT", dataset_signature=hashlib.sha256(
        json.dumps(signature_data, separators=(",", ":")).encode()).hexdigest(),
        bars=int((end_ts - start_ts).total_seconds()), calendar_days=len(days),
        seconds_start=start_ts.isoformat(), seconds_end_exclusive=end_ts.isoformat())
    return result


def render_chart(root: Path, rows: list[dict]) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    figure, axes = plt.subplots(2, 1, figsize=(15, 10), gridspec_kw={"height_ratios": [2, 1]})
    colors = plt.cm.viridis(np.linspace(.1, .9, len(rows)))
    for color, row in zip(colors, rows):
        curve = pd.read_csv(root / row["horizon"] / "minute_equity.csv")
        curve["datetime"] = pd.to_datetime(curve.timestamp, unit="ms", utc=True)
        step = max(1, len(curve) // 5000)
        sample = curve.iloc[::step]
        axes[0].plot(sample.datetime, sample.equity, color=color, linewidth=1.1, label=row["horizon"])
        peak = sample.equity.cummax().clip(lower=float(row["initial_equity"]))
        axes[1].plot(sample.datetime, (1 - sample.equity / peak) * 100, color=color,
                     linewidth=1.0, label=row["horizon"])
    axes[0].axhline(1000, color="#555", linewidth=.8, linestyle="--")
    axes[0].set(title="ETHUSDT frozen 1-second policy: equity", ylabel="Equity (U)")
    axes[1].set(title="Displayed drawdown from running peak", ylabel="Drawdown (%)", xlabel="UTC")
    axes[0].legend(ncol=3); axes[1].legend(ncol=3)
    figure.tight_layout(); figure.savefig(root / "equity_drawdown_horizons.png", dpi=160); plt.close(figure)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection", type=Path, required=True)
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--market-study-dir", type=Path, required=True)
    parser.add_argument("--full-start", default="2023-09-24T00:00:00Z")
    parser.add_argument("--end", default="2026-09-22T00:00:00Z")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    selection = json.loads(args.selection.read_text(encoding="utf-8"))
    cfg = FMZConfig(**selection["parameters"]).validate()
    arrays, full_manifest = prepare_arrays(args.study_dir, args.full_start, args.end,
                                           args.market_study_dir, symbol="ETHUSDT")
    args.output.mkdir(parents=True, exist_ok=True)
    rows = []
    for horizon, start in WINDOWS:
        report_dir = args.output / horizon
        result, curve = backtest_fmz({**arrays}, cfg, start=start, end=args.end, record=True)
        write_report(result, curve, curve.attrs["decisions"], cfg,
                     manifest_for_window(full_manifest, start, args.end),
                     report_dir=report_dir, start=start, end=args.end,
                     study_dir=args.study_dir, parameters_source=args.selection)
        metrics = json.loads((report_dir / "metrics.json").read_text(encoding="utf-8"))
        row = {key: metrics[key] for key in
               ("initial_equity", "final_equity", "net_profit", "return_pct", "max_drawdown_pct",
                "cagr_pct", "fills", "martingale_adds", "risk_stops", "liquidations", "fees",
                "funding_net", "turnover", "jev_signals", "jev_forced_leg_closes", "jev_veto_checks",
                "active_months", "longest_no_fill_month_streak", "bars", "dataset_signature")}
        row.update(horizon=horizon, start=start, end_exclusive=args.end, curve_display_sampling="60s")
        rows.append(row)
        print(json.dumps({"horizon": horizon, **row}, ensure_ascii=False), flush=True)
    pd.DataFrame(rows).to_csv(args.output / "summary.csv", index=False)
    (args.output / "selected_parameters.json").write_text(
        json.dumps(cfg.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    render_chart(args.output, rows)
    payload = {"symbol": "ETHUSDT", "resolution": "1s", "selection": selection["selected_name"],
               "candidate": selection["selected_candidate"], "parameters": cfg.to_dict(),
               "windows": rows, "dataset_signature_full": full_manifest["dataset_signature"],
               "execution_note": "Returns, fills, fees, funding and risks use 1-second bars; minute_equity.csv is a 60-second display trace."}
    (args.output / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = ["# ETHUSDT 1 秒重调优方案多区间回测", "",
             f"- 选中候选：`{selection['selected_name']}`（候选 {selection['selected_candidate']}）。",
             "- 选择流程：24 个月开发集 → 6 个月验证集 → 6 个月最终样本外；最终样本外未用于调参。",
             "- 约束：初始权益 1000 U、单笔名义金额最低 130 U、1 秒撮合、JEV 延迟 5 秒、账户回撤停机关闭。",
             "- 收益、成交、手续费、资金费和风险统计均按 1 秒 bar 计算；`minute_equity.csv` 是每 60 秒展示采样。", "",
             "| 区间 | 期末权益(U) | 回报 | 最大回撤 | 成交 | JEV信号 | JEV强制平腿 | 最长无成交月 |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    lines += [f"| {r['horizon']} | {r['final_equity']:.4f} | {r['return_pct']:.4f}% | {r['max_drawdown_pct']:.4f}% | "
              f"{r['fills']} | {r['jev_signals']} | {r['jev_forced_leg_closes']} | {r['longest_no_fill_month_streak']} |"
              for r in rows]
    lines += ["", "## 文件", "- `summary.csv` / `summary.json`：统一指标。",
              "- `equity_drawdown_horizons.png`：权益和回撤图。",
              "- 各区间目录：`metrics.json`、`monthly.csv`、`daily.csv`、`minute_equity.csv`、`trade_decisions.csv`。"]
    (args.output / "SUMMARY.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": rows}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
