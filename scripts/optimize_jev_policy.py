"""Screen directional JEV close-and-pause policies on the long historical dataset."""
from dataclasses import replace
from pathlib import Path
import argparse
import json
import sys
import time

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from jevmesh.fmz_config import FMZConfig
from jevmesh.fmz_engine import backtest_fmz
from jevmesh.fmz_data import EMA_WINDOWS


def prepare_minute_data(study_dir, market_study_dir):
    study_dir = Path(study_dir)
    market_study_dir = Path(market_study_dir)
    market = pd.read_pickle(market_study_dir / "processed/market_1m.pkl")
    funding = pd.read_pickle(market_study_dir / "processed/funding.pkl")
    close = market.close
    feature_parts = [close.ewm(span=window, adjust=False, min_periods=window).mean().shift(1).to_numpy()
                     for window in EMA_WINDOWS]
    feature_parts.extend([close.shift(1).to_numpy(),
                          np.log(close).diff().rolling(60).std().shift(1).to_numpy()])
    features = np.column_stack(feature_parts).astype(np.float64, copy=False)
    bars = np.column_stack([market.open, market.high, market.low, market.close,
                            market.mark_open, market.mark_high, market.mark_low,
                            market.mark_close]).astype(np.float64, copy=False)
    times = market.index.to_numpy(dtype=np.int64)
    funding_values = np.zeros(len(market), dtype=np.float64)
    first_minute = int(times[0]) // 60_000
    for row in funding.itertuples(index=False):
        index = int(row.timestamp) // 60_000 - first_minute
        if 0 <= index < len(funding_values):
            funding_values[index] += float(row.rate)
    predictions = pd.read_json(study_dir / "processed/event_predictions_walkforward.jsonl", lines=True)
    news = np.zeros((len(times), 2), dtype=np.float32)
    for row in predictions.itertuples(index=False):
        event_time = int(row.available_ms) + 5_000
        index = int(np.searchsorted(times, event_time, side="left"))
        if index >= len(times):
            continue
        breakout = float(row.p_breakout)
        if breakout > news[index, 0]:
            news[index] = [breakout, (float(row.p_up)-float(row.p_down))/max(breakout, 1e-9)]
    return {"times": times, "bars": bars, "funding": funding_values, "news": news,
            "minute_index": np.arange(len(times), dtype=np.int32), "features": features,
            "resolution": "1m"}


def candidate_configs(base):
    breakout_thresholds = (.15, .20, .25, .30, .35, .40)
    direction_thresholds = (.03, .05, .075, .10, .15)
    hold_seconds = (30., 60., 120., 300., 600.)
    candidates = []
    for action in (2, 4):
        for breakout_threshold in breakout_thresholds:
            for direction_threshold in direction_thresholds:
                for hold in hold_seconds:
                    candidates.append(replace(
                        base,
                        jev_action=action,
                        jev_breakout_threshold=breakout_threshold,
                        jev_direction_threshold=direction_threshold,
                        jev_hold_seconds=hold,
                    ))
    return candidates


def result_row(index, config, result):
    return {
        "candidate": index,
        "jev_action": config.jev_action,
        "jev_breakout_threshold": config.jev_breakout_threshold,
        "jev_direction_threshold": config.jev_direction_threshold,
        "jev_hold_seconds": config.jev_hold_seconds,
        "return_pct": result["return_pct"],
        "final_equity": result["final_equity"],
        "max_drawdown_pct": result["max_drawdown_pct"],
        "fills": result["fills"],
        "martingale_adds": result["martingale_adds"],
        "risk_stops": result["risk_stops"],
        "liquidations": result["liquidations"],
        "jev_signals": result["jev_signals"],
        "jev_forced_leg_closes": result["jev_forced_leg_closes"],
        "jev_veto_checks": result["jev_veto_checks"],
        "local_margin_rejections": result["local_margin_rejections"],
        "turnover": result["turnover"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--parameters", type=Path, required=True)
    parser.add_argument("--study-dir", type=Path, required=True)
    parser.add_argument("--market-study-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = FMZConfig.load(args.parameters)
    data = prepare_minute_data(args.study_dir, args.market_study_dir)
    candidates = candidate_configs(config)
    rows = []
    started = time.perf_counter()
    for index, candidate in enumerate(candidates):
        result = backtest_fmz(data, candidate)
        row = result_row(index, candidate, result)
        row["rank_return"] = result["return_pct"]
        rows.append(row)
        if (index+1) % 25 == 0 or index+1 == len(candidates):
            best = max(rows, key=lambda item: item["return_pct"])
            print(json.dumps({"completed": index+1, "total": len(candidates),
                              "best_return_pct": best["return_pct"],
                              "best_candidate": best["candidate"],
                              "elapsed_seconds": round(time.perf_counter()-started, 1)}), flush=True)
    rows.sort(key=lambda item: item["return_pct"], reverse=True)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.with_suffix(".json").write_text(json.dumps({
        "base_parameters": config.to_dict(),
        "dataset": {"resolution": "1m", "bars": len(data["times"]),
                    "start": pd.to_datetime(data["times"][0], unit="ms", utc=True).isoformat(),
                    "last_bar": pd.to_datetime(data["times"][-1], unit="ms", utc=True).isoformat()},
        "candidate_count": len(rows),
        "ranking": rows,
    }, indent=2), encoding="utf-8")
    pd.DataFrame(rows).to_csv(output.with_suffix(".csv"), index=False)
    print(json.dumps({"completed": len(rows), "best": rows[:10]}, indent=2), flush=True)


if __name__ == "__main__":
    main()
