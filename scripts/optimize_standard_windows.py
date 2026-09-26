"""Screen FMZ and JEV policies across the standard multi-window backtests."""
from __future__ import annotations

import argparse
import json
import random
import sys
from dataclasses import replace
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from jevmesh.fmz_config import FMZConfig
from jevmesh.fmz_engine import backtest_fmz
from optimize_jev_policy import prepare_minute_data


WINDOWS = {
    "3m": ("2026-06-22", "2026-09-22"),
    "6m": ("2026-03-22", "2026-09-22"),
    "9m": ("2025-12-22", "2026-09-22"),
    "12m": ("2025-09-22", "2026-09-22"),
    "24m": ("2024-09-22", "2026-09-22"),
    "36m": ("2023-09-25", "2026-09-22"),
}


def base_config(path: Path) -> FMZConfig:
    config = FMZConfig.load(path)
    return replace(config, initial_equity=1000.0, base_amount_min=130.0,
                   min_trade_notional=130.0, account_drawdown_stop=0.0,
                   stop_cooldown_minutes=0.0, reentry_delay_seconds=30.0,
                   poll_seconds=1.0, risk_guard=0, leverage=10.0,
                   news_delay_seconds=5.0)


def anchors(base: FMZConfig) -> list[FMZConfig]:
    return [
        base,
        replace(base, jev_action=3, jev_breakout_threshold=.30, jev_hold_seconds=180.),
        replace(base, jev_action=3, jev_breakout_threshold=.25, jev_hold_seconds=180.),
        replace(base, jev_action=3, jev_breakout_threshold=.22, jev_hold_seconds=180.),
        replace(base, jev_action=3, jev_breakout_threshold=.20, jev_hold_seconds=300.),
        replace(base, jev_action=3, jev_breakout_threshold=.35, jev_hold_seconds=300.),
        replace(base, jev_action=1, jev_breakout_threshold=.40, jev_hold_seconds=900.),
        replace(base, jev_action=2, jev_breakout_threshold=.25,
                jev_direction_threshold=.10, jev_hold_seconds=300.),
        replace(base, controller=0, gross_utilization=.7, max_adds=5,
                base_spacing=.01, ratio=1.2, profit_target=.05,
                base_amount_rate=.25, basket_stop=.2, jev_action=3,
                jev_breakout_threshold=.3, jev_hold_seconds=180.),
        replace(base, controller=1, gross_utilization=.7, max_adds=3,
                base_spacing=.02, ratio=1.2, profit_target=.1,
                base_amount_rate=.25, basket_stop=.2, jev_action=3,
                jev_breakout_threshold=.3, jev_hold_seconds=180.),
    ]


def random_configs(base: FMZConfig, count: int, seed: int) -> list[FMZConfig]:
    rng = random.Random(seed)
    pairs = [(15, 60), (30, 120), (60, 120), (60, 240), (120, 480), (240, 720)]
    configs = []
    for _ in range(count):
        fast, slow = rng.choice(pairs)
        configs.append(replace(
            base, controller=rng.choice([0, 1, 2, 4]),
            base_spacing=rng.choice([.003, .005, .01, .02, .035, .05, .08]),
            base_amount_rate=rng.choice([.03, .1, .25, .5, 1.]),
            ratio=rng.choice([1.05, 1.1, 1.2, 1.35, 1.5, 1.75]),
            profit_target=rng.choice([.01, .02, .05, .1, .15, .2]),
            max_loss_notional_multiple=rng.choice([3., 5., 9., 20.]),
            max_adds=rng.choice([0, 1, 2, 3, 5, 8]),
            gross_utilization=rng.choice([.3, .5, .7, 1.]),
            basket_stop=rng.choice([.05, .1, .2, .4, .6, .9]),
            ema_fast_minutes=fast, ema_slow_minutes=slow,
            trend_enter=rng.choice([.001, .003, .005, .01, .02]),
            trend_exit_fraction=rng.choice([.3, .5, .7, 1.]),
            trend_confirm_minutes=rng.choice([0., 15., 30., 60., 120.]),
            directional_stop=rng.choice([.005, .01, .02, .05, .1]),
            directional_trail=rng.choice([.02, .05, .1, .2]),
            jev_action=rng.choice([0, 1, 2, 3, 4]),
            jev_breakout_threshold=rng.choice([.2, .22, .25, .28, .3, .35, .4]),
            jev_direction_threshold=rng.choice([.03, .05, .1, .15, .2]),
            jev_hold_seconds=rng.choice([60., 120., 180., 300., 600., 900.]),
        ))
    return configs


def config_signature(config: FMZConfig) -> str:
    return json.dumps(config.to_dict(), sort_keys=True, separators=(",", ":"))


def evaluate(config: FMZConfig, data: dict, window_names: list[str]) -> dict:
    window_results = {}
    for name in window_names:
        start, end = WINDOWS[name]
        metrics = backtest_fmz(data, config, start=start, end=end)
        window_results[name] = {key: metrics[key] for key in (
            "initial_equity", "final_equity", "net_profit", "return_pct",
            "max_drawdown_pct", "min_equity", "fees", "funding_net",
            "fills", "martingale_adds", "risk_stops", "liquidations",
            "halted", "jev_signals", "jev_forced_leg_closes", "jev_veto_checks")}
    returns = [window_results[name]["return_pct"] for name in window_names]
    drawdowns = [window_results[name]["max_drawdown_pct"] for name in window_names]
    fills = [window_results[name]["fills"] for name in window_names]
    liquidations = sum(window_results[name]["liquidations"] for name in window_names)
    median_return = float(pd.Series(returns).median())
    mean_return = float(pd.Series(returns).mean())
    worst_return = float(min(returns))
    max_drawdown = float(max(drawdowns))
    zero_fill_penalty = sum(1 for value in fills if value == 0) * 50.
    liquidation_penalty = liquidations * 1000.
    robust_score = median_return + .35 * mean_return + .15 * worst_return - .25 * max_drawdown - zero_fill_penalty - liquidation_penalty
    return {"parameters": config.to_dict(), "windows": window_results,
            "mean_return_pct": mean_return, "median_return_pct": median_return,
            "worst_window_return_pct": worst_return,
            "max_window_drawdown_pct": max_drawdown,
            "robust_score": robust_score, "liquidations_total": liquidations}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--market-study-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--random-count", type=int, default=100)
    parser.add_argument("--seed", type=int, default=20260926)
    args = parser.parse_args()

    base = base_config(args.parameters)
    data = prepare_minute_data(args.study_dir, args.market_study_dir)
    configs = anchors(base) + random_configs(base, args.random_count, args.seed)
    unique, seen = [], set()
    for config in configs:
        signature = config_signature(config)
        if signature not in seen:
            seen.add(signature)
            unique.append(config)

    screen_windows = ["3m", "6m", "9m", "12m", "24m", "36m"]
    rows = []
    for index, config in enumerate(unique, 1):
        row = evaluate(config, data, screen_windows)
        row["candidate"] = index
        rows.append(row)
        if index % 20 == 0 or index == len(unique):
            best = max(rows, key=lambda item: item["robust_score"])
            print(json.dumps({"completed": index, "total": len(unique),
                              "best_robust_score": round(best["robust_score"], 4),
                              "best_mean_return_pct": round(best["mean_return_pct"], 4)}), flush=True)

    rows.sort(key=lambda item: (item["robust_score"], item["mean_return_pct"]), reverse=True)
    for rank, row in enumerate(rows, 1):
        row["robust_rank"] = rank
    output = args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "screen_resolution": "1m",
        "screen_windows": {name: WINDOWS[name] for name in screen_windows},
        "candidate_count": len(rows),
        "constraints": {"initial_equity": 1000.0, "base_amount_min": 130.0,
                         "min_trade_notional": 130.0, "account_drawdown_stop": 0.0,
                         "no_virtualization": True},
        "ranking": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(output), "candidate_count": len(rows),
        "top": [{"rank": row["robust_rank"], "candidate": row["candidate"],
                  "robust_score": row["robust_score"],
                  "mean_return_pct": row["mean_return_pct"],
                  "median_return_pct": row["median_return_pct"],
                  "worst_window_return_pct": row["worst_window_return_pct"],
                  "max_window_drawdown_pct": row["max_window_drawdown_pct"],
                  "jev_action": row["parameters"]["jev_action"],
                  "jev_breakout_threshold": row["parameters"]["jev_breakout_threshold"],
                  "jev_hold_seconds": row["parameters"]["jev_hold_seconds"]}
                 for row in rows[:10]]}, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    main()
