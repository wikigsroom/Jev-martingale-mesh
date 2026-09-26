"""Run the frozen optimized FMZ configuration over standard 1-second windows."""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from jevmesh.fmz_config import FMZConfig
from jevmesh.fmz_engine import backtest_fmz
from run_long_historical_fmz import prepare_arrays, write_report


WINDOWS = {
    "3m": ("2026-06-22T00:00:00Z", "2026-09-22T00:00:00Z"),
    "6m": ("2026-03-22T00:00:00Z", "2026-09-22T00:00:00Z"),
    "9m": ("2025-12-22T00:00:00Z", "2026-09-22T00:00:00Z"),
    "12m": ("2025-09-22T00:00:00Z", "2026-09-22T00:00:00Z"),
    "24m": ("2024-09-22T00:00:00Z", "2026-09-22T00:00:00Z"),
    "36m": ("2023-09-25T00:00:00Z", "2026-09-22T00:00:00Z"),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--market-study-dir", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()

    cfg = FMZConfig.load(args.parameters)
    cfg = replace(cfg, initial_equity=1000.0, base_amount_min=130.0,
                  min_trade_notional=130.0, account_drawdown_stop=0.0,
                  stop_cooldown_minutes=0.0, reentry_delay_seconds=30.0,
                  poll_seconds=1.0, risk_guard=0, leverage=10.0,
                  news_delay_seconds=5.0).validate()
    full_start, full_end = WINDOWS["36m"]
    arrays, manifest = prepare_arrays(args.study_dir, full_start, full_end,
                                      args.market_study_dir)
    data = {**arrays}
    summary = {
        "resolution": "1s",
        "parameters": cfg.to_dict(),
        "parameters_source": str(args.parameters),
        "dataset_signature": manifest["dataset_signature"],
        "windows": {},
    }
    args.output_root.mkdir(parents=True, exist_ok=True)
    for name, (start, end) in WINDOWS.items():
        report_dir = args.output_root / name
        result, curve = backtest_fmz(data, cfg, start=start, end=end, record=True)
        decisions = curve.attrs["decisions"]
        write_report(result, curve, decisions, cfg, manifest, report_dir,
                     start=start, end=end, study_dir=args.study_dir,
                     parameters_source=args.parameters)
        metrics = json.loads((report_dir / "metrics.json").read_text(encoding="utf-8"))
        summary["windows"][name] = {
            "start": start,
            "end_exclusive": end,
            "initial_equity": metrics["initial_equity"],
            "final_equity": metrics["final_equity"],
            "net_profit": metrics["net_profit"],
            "return_pct": metrics["return_pct"],
            "cagr_pct": metrics["cagr_pct"],
            "max_drawdown_pct": metrics["max_drawdown_pct"],
            "min_equity": metrics["min_equity"],
            "fees": metrics["fees"],
            "funding_net": metrics["funding_net"],
            "fills": metrics["fills"],
            "martingale_adds": metrics["martingale_adds"],
            "risk_stops": metrics["risk_stops"],
            "liquidations": metrics["liquidations"],
            "halted": metrics["halted"],
            "jev_signals": metrics["jev_signals"],
            "jev_forced_leg_closes": metrics["jev_forced_leg_closes"],
            "jev_veto_checks": metrics["jev_veto_checks"],
            "daily_sharpe_365": metrics["daily_sharpe_365"],
            "winning_months": metrics["winning_months"],
            "losing_months": metrics["losing_months"],
            "active_months": metrics["active_months"],
            "longest_no_fill_month_streak": metrics["longest_no_fill_month_streak"],
            "report_dir": str(report_dir),
        }
        print(json.dumps({"window": name, **summary["windows"][name]},
                         ensure_ascii=False), flush=True)
    output = args.output_root / "summary.json"
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    pd.DataFrame.from_dict(summary["windows"], orient="index").reset_index(names="window").to_csv(
        args.output_root / "summary.csv", index=False)
    print(json.dumps({"summary": str(output), "windows": list(WINDOWS)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
