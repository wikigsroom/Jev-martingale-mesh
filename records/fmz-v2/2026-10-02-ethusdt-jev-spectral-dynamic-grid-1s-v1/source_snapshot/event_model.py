from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .data import END, PREDICTION_PATH, START, digest, millis

MODEL_VERSION = "eth-nanojev-choice-head-calibrated-walkforward-v1"


def prepare_events(data: dict, manifest: dict) -> tuple[dict, dict]:
    """Load ETH-specific JEV scores with the publisher-time-plus-five-second barrier."""
    rows = []
    with Path(PREDICTION_PATH).open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        raise ValueError("No calibrated ETH event predictions found")
    times = np.asarray([int(row["decision_ms"]) for row in rows], dtype=np.int64)
    available = np.asarray([int(row["available_ms"]) for row in rows], dtype=np.int64)
    if np.any(times < available + 5000) or np.any(np.diff(times) < 0):
        raise ValueError("JEV timing is not causal or sorted")
    first_ms = int(data["times"][0])
    end_ms = int(data["times"][-1]) + 1000
    inside = (times >= first_ms) & (times < end_ms)
    if not inside.all():
        raise ValueError("JEV event coverage does not match the frozen second range")
    p_up = np.asarray([float(row["p_up"]) for row in rows], dtype=np.float64)
    p_down = np.asarray([float(row["p_down"]) for row in rows], dtype=np.float64)
    breakout = np.asarray([float(row["p_breakout"]) for row in rows], dtype=np.float64)
    bias = (p_up - p_down) / np.maximum(breakout, 1e-12)
    prediction = np.column_stack((breakout, bias))
    events = {
        "times": times,
        "available_ms": available,
        "market": prediction,
        "jev": prediction,
    }
    unique_times, counts = np.unique(times, return_counts=True)
    audit = {
        "version": MODEL_VERSION,
        "source": "BlockBeats public newsflash feed + ETHUSDT calibrated NanoJev choice head",
        "prediction_path": str(PREDICTION_PATH),
        "prediction_sha256": digest(PREDICTION_PATH),
        "events": int(len(rows)),
        "unique_delivery_seconds": int(len(unique_times)),
        "same_second_collisions": int(np.sum(counts > 1)),
        "start": START,
        "end_exclusive": END,
        "minimum_delay_seconds": float(np.min((times - available) / 1000)),
        "maximum_breakout_score": float(np.max(breakout)),
        "breakout_quantiles": {str(q): float(np.quantile(breakout, q)) for q in (.5, .9, .95, .99)},
        "direction_bias_quantiles": {str(q): float(np.quantile(bias, q)) for q in (.01, .5, .99)},
        "score_semantics": "p_breakout=p_up+p_down is a short-horizon direction-breakout proxy; it is not a profit or liquidation probability",
        "strategy_delay_seconds": 5.0,
        "dataset_prediction_sha256": manifest.get("predictions_sha256"),
    }
    return events, audit
