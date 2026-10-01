from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import time

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .data import END, EXPERIMENT, REPORTS, START, digest, load_context, millis, write_json
from .engine import DynamicConfig, backtest
from .event_model import prepare_events

WINDOWS = {
    "1m": ("2026-08-22T00:00:00Z", END),
    "2m": ("2026-07-22T00:00:00Z", END),
    "3m": ("2026-06-22T00:00:00Z", END),
    "6m": ("2026-03-22T00:00:00Z", END),
    "12m": ("2025-09-22T00:00:00Z", END),
    "24m": ("2024-09-22T00:00:00Z", END),
    "36m": (START, END),
}


def common_parameters() -> dict:
    return dict(
        vol_multiplier=16.0,
        minimum_spacing=0.01,
        maximum_spacing=0.08,
        fixed_spacing=0.03,
        profit_fraction=1.0,
        base_amount_rate=0.15,
        multiplier=1.15,
        max_adds=2,
        trend_entry=2.5,
        trend_confirmation_minutes=180.0,
        trend_exit_fraction=0.5,
        event_strength=1.0,
        event_hold_seconds=600.0,
        event_delay_seconds=5.0,
        event_direction_gate=False,
        event_block_logvol=0.30,
        parameter_hysteresis=0.25,
        requote_seconds=900.0,
    )


def reference_candidates() -> list[DynamicConfig]:
    common = common_parameters()
    return [
        DynamicConfig(name="fixed_grid_reference", mode=0, **common),
        DynamicConfig(name="dynamic_volatility_no_jev", mode=1, **common),
        DynamicConfig(name="dynamic_volatility_plus_jev", mode=3, **common),
    ]


def prepare_context() -> tuple[dict, np.ndarray, dict, dict, dict]:
    data, market, features, data_audit = load_context()
    events, event_audit = prepare_events(data, data_audit)
    signature_inputs = {
        "dataset_signature": data_audit["dataset_signature"],
        "prediction_sha256": event_audit["prediction_sha256"],
        "engine_sha256": digest(EXPERIMENT / "engine.py"),
        "data_sha256": digest(EXPERIMENT / "data.py"),
        "event_model_sha256": digest(EXPERIMENT / "event_model.py"),
        "feature_method": data_audit["feature_method"],
    }
    signature = hashlib.sha256(json.dumps(signature_inputs, sort_keys=True).encode()).hexdigest()
    audit = {"data": data_audit, "events": event_audit, "signature": signature, "signature_inputs": signature_inputs}
    return data, features, events, market, audit


def event_risk_seconds(events: dict, config: DynamicConfig, start: str, end: str) -> int:
    if config.mode < 2:
        return 0
    start_ms = millis(start)
    end_ms = millis(end)
    event_times = events["times"] + int((config.event_delay_seconds - 5.0) * 1000)
    values = events["jev"][:, 0]
    hold_ms = int(config.event_hold_seconds * 1000)
    threshold = config.event_block_logvol
    total = 0
    for index, value in enumerate(values):
        if value <= threshold:
            continue
        left = max(start_ms, int(event_times[index]))
        right = int(event_times[index] + hold_ms * (1.0 - threshold / value))
        if index + 1 < len(event_times):
            right = min(right, int(event_times[index + 1]))
        right = min(right, end_ms)
        if right > left:
            total += right - left
    return int(total // 1000)


def evaluate(context: tuple, config: DynamicConfig, label: str, start: str, end: str,
             record: bool = False, output_dir: Path | None = None) -> dict:
    data, features, events, market, _ = context
    config.validate()
    output = backtest(data, features, events, config, start, end, record=record, path_mode=0)
    if record:
        result, curve, trace = output
        output_dir = output_dir or REPORTS / "curves"
        output_dir.mkdir(parents=True, exist_ok=True)
        key = hashlib.sha256(json.dumps({"config": config.to_dict(), "label": label}, sort_keys=True).encode()).hexdigest()[:20]
        np.savez_compressed(output_dir / f"{key}.npz", curve=curve.to_numpy(),
                            curve_columns=np.asarray(curve.columns, dtype=str),
                            trace=trace.to_numpy(), trace_columns=np.asarray(trace.columns, dtype=str))
        result["curve_key"] = key
        result["curve"] = curve
        result["trace"] = trace
    else:
        result = output
    result.update({"label": label, "config": config.to_dict(),
                   "event_risk_seconds": event_risk_seconds(events, config, start, end)})
    return result


def enrich_curve(curve: pd.DataFrame, data: dict) -> pd.DataFrame:
    result = curve.copy()
    times = result["timestamp"].to_numpy(dtype=np.int64)
    positions = np.searchsorted(data["times"], times, side="left")
    positions = np.clip(positions, 0, len(data["times"]) - 1)
    mark = np.asarray(data["bars"][positions, 7], dtype=np.float64)
    result["datetime"] = pd.to_datetime(times, unit="ms", utc=True)
    result["mark_price"] = mark
    result["gross_notional"] = (result["long_qty"] + result["short_qty"]) * mark
    result["net_notional"] = (result["long_qty"] - result["short_qty"]) * mark
    result["equity_return_pct"] = (result["equity"] / result["equity"].iloc[0] - 1.0) * 100.0
    result["drawdown_pct"] = (result["equity"].cummax() - result["equity"]) / result["equity"].cummax() * 100.0
    result["both_sides_open"] = ((result["long_qty"] > 0) & (result["short_qty"] > 0)).astype(int)
    return result


def plot_final(curve: pd.DataFrame, output: Path, title: str) -> None:
    dates = curve["datetime"]
    fig, axes = plt.subplots(4, 1, figsize=(15, 14), dpi=150, sharex=True,
                             gridspec_kw={"height_ratios": (2.1, 1.0, 1.2, 1.4)})
    axes[0].plot(dates, curve["equity"], color="#0b7285", lw=1.0, label="Equity")
    axes[0].plot(dates, curve["wallet"], color="#868e96", lw=.75, label="Wallet")
    axes[0].set_ylabel("USDT")
    axes[0].set_title(title)
    axes[0].grid(alpha=.2)
    axes[0].legend(loc="upper left")
    axes[1].fill_between(dates, -curve["drawdown_pct"], 0, color="#e03131", alpha=.22)
    axes[1].plot(dates, -curve["drawdown_pct"], color="#c92a2a", lw=.8)
    axes[1].set_ylabel("Drawdown %")
    axes[1].grid(alpha=.2)
    axes[2].plot(dates, curve["gross_notional"], color="#7048e8", lw=.75, label="Gross notional")
    axes[2].plot(dates, curve["net_notional"], color="#f08c00", lw=.7, label="Net notional")
    axes[2].set_ylabel("Notional USDT")
    axes[2].grid(alpha=.2)
    axes[2].legend(loc="upper left")
    axes[3].plot(dates, curve["spacing"] * 100, color="#1971c2", lw=.85, label="Dynamic spacing %")
    axes[3].plot(dates, curve["event_logvol"], color="#e03131", lw=.65, alpha=.8, label="JEV pressure")
    axes[3].step(dates, curve["martingale_adds"], where="post", color="#2f9e44", lw=.85, label="Cumulative adds")
    axes[3].set_ylabel("Grid / signal")
    axes[3].grid(alpha=.2)
    axes[3].legend(loc="upper left", ncol=3)
    fig.tight_layout()
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def write_table(rows: list[dict], path: Path, title: str) -> None:
    rows = sorted(rows, key=lambda row: (list(WINDOWS).index(row["label"]), row["name"]))
    write_json(path.with_suffix(".json"), {"title": title, "windows": WINDOWS, "rows": rows})
    lines = [f"# {title}", "", "|期限|方案|末期权益|收益率|最大回撤|成交|加仓|参数更新|JEV事件|JEV网格更新|事件门控秒|",
             "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        lines.append("|{label}|{name}|{final_equity:.2f}|{return_pct:.3f}%|{max_drawdown_pct:.3f}%|{fills}|{martingale_adds}|{parameter_updates}|{event_used}|{event_grid_updates}|{event_risk_seconds}|".format(**row))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def run_baseline(workers: int = 2) -> dict:
    context = prepare_context()
    jobs = [(config, label, *WINDOWS[label]) for config in reference_candidates() for label in WINDOWS]
    rows = []
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(evaluate, context, *job) for job in jobs]
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            print(json.dumps({"stage": "baseline", "name": row["name"], "label": row["label"],
                              "return_pct": round(row["return_pct"], 3),
                              "drawdown_pct": round(row["max_drawdown_pct"], 3)}, ensure_ascii=False), flush=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    write_table(rows, REPORTS / "baseline_comparison.md", "动态网格基线比较")
    write_json(REPORTS / "run_audit.json", context[4])
    return {"rows": rows, "audit": context[4]}


def run_final(config_path: Path, workers: int = 1) -> dict:
    del workers
    context = prepare_context()
    config = DynamicConfig(**json.loads(Path(config_path).read_text(encoding="utf-8"))).validate()
    rows = []
    for label, (start, end) in WINDOWS.items():
        started = time.perf_counter()
        row = evaluate(context, config, label, start, end, record=(label == "36m"))
        row["elapsed_seconds"] = time.perf_counter() - started
        if label == "36m":
            curve = enrich_curve(row.pop("curve"), context[0])
            trace = row.pop("trace")
            curve.to_csv(REPORTS / "final_36m_curve_60s.csv", index=False)
            trace.to_csv(REPORTS / "final_36m_decision_trace.csv", index=False)
            plot_final(curve, REPORTS / "final_36m_dynamic_grid.png",
                       f"ETHUSDT JEV spectral dynamic grid | {config.name} | 1-second replay, 60-second display sample")
            event_trace = trace.loc[trace["events"] > 0].copy()
            event_trace.to_csv(REPORTS / "final_36m_jev_events.csv", index=False)
        rows.append(row)
        print(json.dumps({"stage": "final", "name": config.name, "label": label,
                          "return_pct": round(row["return_pct"], 3),
                          "drawdown_pct": round(row["max_drawdown_pct"], 3),
                          "fills": row["fills"], "adds": row["martingale_adds"]}, ensure_ascii=False), flush=True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    write_table(rows, REPORTS / "final_horizons.md", f"{config.name} 逐秒回测")
    write_json(REPORTS / "final_strategy.json", {"config": config.to_dict(), "rows": rows, "audit": context[4]})
    return {"rows": rows, "audit": context[4], "config": config.to_dict()}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("baseline", "final"), default="baseline")
    parser.add_argument("--config", type=Path)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.stage == "baseline":
        run_baseline(args.workers)
    else:
        if not args.config:
            raise SystemExit("--config is required for --stage final")
        run_final(args.config, args.workers)


if __name__ == "__main__":
    main()
