from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
import json
from pathlib import Path
import time

import numpy as np

from .data import REPORTS, write_json
from .engine import DynamicConfig
from .run import WINDOWS, evaluate, prepare_context

SCREEN_WINDOWS = ("3m", "6m", "12m")
VALIDATION_WINDOWS = ("24m", "36m")


def candidates() -> list[DynamicConfig]:
    base = {
        "vol_multiplier": 16.0,
        "minimum_spacing": 0.01,
        "maximum_spacing": 0.08,
        "fixed_spacing": 0.03,
        "profit_fraction": 1.0,
        "base_amount_rate": 0.15,
        "multiplier": 1.15,
        "max_adds": 2,
        "trend_entry": 2.5,
        "trend_confirmation_minutes": 180.0,
        "trend_exit_fraction": 0.5,
        "event_strength": 1.0,
        "event_hold_seconds": 600.0,
        "event_delay_seconds": 5.0,
        "event_direction_gate": False,
        "event_block_logvol": 0.30,
        "parameter_hysteresis": 0.25,
        "requote_seconds": 900.0,
    }
    specs = [
        ("grid_tight", {"vol_multiplier": 10.0, "minimum_spacing": .005, "maximum_spacing": .05,
                         "fixed_spacing": .02, "profit_fraction": .75, "multiplier": 1.10}),
        ("grid_balanced", {}),
        ("grid_wide", {"vol_multiplier": 20.0, "minimum_spacing": .015, "maximum_spacing": .10,
                        "fixed_spacing": .035, "profit_fraction": 1.0}),
        ("grid_profit_tight", {"vol_multiplier": 14.0, "minimum_spacing": .008, "maximum_spacing": .07,
                                 "fixed_spacing": .025, "profit_fraction": .60}),
        ("adds_conservative", {"base_amount_rate": .10, "multiplier": 1.08, "max_adds": 1}),
        ("adds_deep", {"base_amount_rate": .10, "multiplier": 1.25, "max_adds": 3}),
        ("size_low", {"base_amount_rate": .08, "multiplier": 1.15}),
        ("size_high", {"base_amount_rate": .22, "multiplier": 1.15}),
        ("trend_fast", {"trend_entry": 2.0, "trend_confirmation_minutes": 60.0}),
        ("trend_slow", {"trend_entry": 3.0, "trend_confirmation_minutes": 360.0}),
        ("requote_fast", {"parameter_hysteresis": .15, "requote_seconds": 300.0}),
        ("requote_slow", {"parameter_hysteresis": .40, "requote_seconds": 1800.0}),
        ("jev_light", {"event_strength": .50, "event_hold_seconds": 300.0,
                        "event_block_logvol": .45, "parameter_hysteresis": .35}),
        ("jev_guard", {"event_strength": 1.50, "event_hold_seconds": 900.0,
                        "event_block_logvol": .20, "parameter_hysteresis": .20}),
        ("market_only_control", {"mode": 1}),
        ("jev_tight_grid", {"vol_multiplier": 10.0, "minimum_spacing": .005, "maximum_spacing": .05,
                             "fixed_spacing": .02, "profit_fraction": .75, "multiplier": 1.10,
                             "event_strength": .75, "event_hold_seconds": 300.0,
                             "event_block_logvol": .35}),
        ("jev_wide_grid", {"vol_multiplier": 20.0, "minimum_spacing": .015, "maximum_spacing": .10,
                            "fixed_spacing": .035, "profit_fraction": 1.0, "multiplier": 1.10,
                            "event_strength": .75, "event_hold_seconds": 600.0,
                            "event_block_logvol": .35}),
        ("jev_high_size", {"base_amount_rate": .22, "multiplier": 1.20, "max_adds": 2,
                            "event_strength": .75, "event_hold_seconds": 600.0,
                            "event_block_logvol": .30}),
        ("jev_deep_cautious", {"base_amount_rate": .10, "multiplier": 1.25, "max_adds": 3,
                                "event_strength": 1.25, "event_hold_seconds": 900.0,
                                "event_block_logvol": .25}),
    ]
    result = []
    for name, overrides in specs:
        values = {**base, "mode": 3, **overrides}
        result.append(DynamicConfig(name=name, **values).validate())
    return result


def score(rows: list[dict]) -> float:
    returns = np.asarray([row["return_pct"] for row in rows], dtype=float)
    drawdowns = np.asarray([row["max_drawdown_pct"] for row in rows], dtype=float)
    if any(row["liquidations"] for row in rows):
        return -1e9
    if any(row["fills"] == 0 or row["parameter_updates"] == 0 for row in rows):
        return -1e8
    return float(np.median(returns) + .25 * np.mean(returns) + .10 * np.min(returns)
                 - .35 * np.max(drawdowns))


def screen(workers: int) -> dict:
    context = prepare_context()
    jobs = [(config, label, *WINDOWS[label]) for config in candidates() for label in SCREEN_WINDOWS]
    grouped: dict[str, list[dict]] = {}
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(evaluate, context, *job) for job in jobs]
        for future in as_completed(futures):
            row = future.result()
            grouped.setdefault(row["name"], []).append(row)
            print(json.dumps({"stage": "screen", "name": row["name"], "label": row["label"],
                              "return_pct": round(row["return_pct"], 3),
                              "drawdown_pct": round(row["max_drawdown_pct"], 3)}, ensure_ascii=False), flush=True)
    rows = []
    for config in candidates():
        group = sorted(grouped[config.name], key=lambda row: SCREEN_WINDOWS.index(row["label"]))
        rows.append({"name": config.name, "config": config.to_dict(), "windows": group,
                     "score": score(group)})
    rows.sort(key=lambda row: row["score"], reverse=True)
    payload = {"stage": "screen", "resolution": "1s", "windows": SCREEN_WINDOWS,
               "rows": rows, "dataset_audit": context[4],
               "elapsed_seconds": time.perf_counter() - started}
    REPORTS.mkdir(parents=True, exist_ok=True)
    write_json(REPORTS / "tuning_screen.json", payload)
    lines = ["# 动态网格滚动窗口筛选", "", "筛选窗口只用于候选筛选；24m 与 36m 留作验证。", "",
             "|排名|方案|评分|3m收益|6m收益|12m收益|最大回撤|参数更新|加仓|", "|---:|---|---:|---:|---:|---:|---:|---:|---:|"]
    for rank, row in enumerate(rows, 1):
        by_label = {item["label"]: item for item in row["windows"]}
        lines.append(f"|{rank}|{row['name']}|{row['score']:.3f}|{by_label['3m']['return_pct']:.3f}%|{by_label['6m']['return_pct']:.3f}%|{by_label['12m']['return_pct']:.3f}%|{max(item['max_drawdown_pct'] for item in row['windows']):.3f}%|{sum(item['parameter_updates'] for item in row['windows'])}|{sum(item['martingale_adds'] for item in row['windows'])}|")
    (REPORTS / "tuning_screen.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def validation(workers: int, top_n: int) -> dict:
    screen_path = REPORTS / "tuning_screen.json"
    if not screen_path.exists():
        screen(workers)
    screened = json.loads(screen_path.read_text(encoding="utf-8"))
    selected = screened["rows"][:top_n]
    context = prepare_context()
    jobs = []
    for row in selected:
        config = DynamicConfig(**row["config"]).validate()
        for label in VALIDATION_WINDOWS:
            jobs.append((config, label, *WINDOWS[label]))
    grouped: dict[str, list[dict]] = {}
    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(evaluate, context, *job) for job in jobs]
        for future in as_completed(futures):
            row = future.result()
            grouped.setdefault(row["name"], []).append(row)
            print(json.dumps({"stage": "validation", "name": row["name"], "label": row["label"],
                              "return_pct": round(row["return_pct"], 3),
                              "drawdown_pct": round(row["max_drawdown_pct"], 3)}, ensure_ascii=False), flush=True)
    rows = []
    for row in selected:
        full_rows = grouped[row["name"]]
        combined = row["windows"] + full_rows
        validation_score = score(combined)
        rows.append({"name": row["name"], "config": row["config"], "screen_score": row["score"],
                     "validation_score": validation_score, "windows": sorted(combined,
                         key=lambda item: list((*SCREEN_WINDOWS, *VALIDATION_WINDOWS)).index(item["label"]))})
    rows.sort(key=lambda row: row["validation_score"], reverse=True)
    best = rows[0]
    best_config = DynamicConfig(**best["config"]).validate()
    noevent = replace(best_config, name=f"{best_config.name}_no_jev_counterfactual", mode=1).validate()
    counterfactual = []
    for label in ("12m", "24m", "36m"):
        counterfactual.append(evaluate(context, noevent, label, *WINDOWS[label]))
    payload = {"stage": "validation", "top_n": top_n, "rows": rows,
               "best_name": best["name"], "best_config": best["config"],
               "noevent_counterfactual": {"config": noevent.to_dict(), "windows": counterfactual},
               "dataset_audit": context[4], "elapsed_seconds": time.perf_counter() - started}
    write_json(REPORTS / "tuning_validation.json", payload)
    write_json(REPORTS / "selected_config.json", best["config"])
    lines = ["# 动态网格验证", "", f"最终候选（待逐期限最终回放）：`{best['name']}`", "",
             "|候选|验证评分|12m收益|24m收益|36m收益|36m回撤|36m成交|36m加仓|JEV网格更新|",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for row in rows:
        by_label = {item["label"]: item for item in row["windows"]}
        full = by_label["36m"]
        lines.append(f"|{row['name']}|{row['validation_score']:.3f}|{by_label['12m']['return_pct']:.3f}%|{by_label['24m']['return_pct']:.3f}%|{full['return_pct']:.3f}%|{full['max_drawdown_pct']:.3f}%|{full['fills']}|{full['martingale_adds']}|{full['event_grid_updates']}|")
    lines.extend(["", "同参数的 `no_jev_counterfactual` 仅用于估计 JEV 的策略增量，不把全部动态网格收益归因给 JEV。"])
    (REPORTS / "tuning_validation.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("screen", "validate"), default="screen")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--top-n", type=int, default=6)
    args = parser.parse_args()
    if args.stage == "screen":
        screen(args.workers)
    else:
        validation(args.workers, args.top_n)


if __name__ == "__main__":
    main()
