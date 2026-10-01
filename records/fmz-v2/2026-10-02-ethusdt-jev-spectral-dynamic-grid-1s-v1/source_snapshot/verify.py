from __future__ import annotations

import json

import numpy as np

from .data import ARRAY_DIR, END, MANIFEST_PATH, START, millis, load_context
from .event_model import prepare_events
from .engine import DynamicConfig
from .run import WINDOWS


def main() -> None:
    data, market, features, data_audit = load_context()
    events, event_audit = prepare_events(data, data_audit)
    if data["resolution"] != "1s":
        raise ValueError("The experiment must remain one-second based")
    if int(data["times"][0]) != millis(START) or int(data["times"][-1]) + 1000 != millis(END):
        raise ValueError("Second coverage mismatch")
    if np.any(np.diff(data["times"][:100_000]) != 1000):
        raise ValueError("Non-contiguous second timestamps")
    if np.any(events["times"] < events["available_ms"] + 5000):
        raise ValueError("A JEV prediction is used before its five-second availability barrier")
    if np.any(np.diff(events["times"]) < 0):
        raise ValueError("JEV predictions are not sorted")
    if features.shape[1] != 12 or not np.isfinite(features).all():
        raise ValueError("Invalid causal feature matrix")
    selected_path = MANIFEST_PATH
    output = {
        "passed": True,
        "dataset_manifest": str(selected_path),
        "dataset_signature": data_audit["dataset_signature"],
        "resolution": "1s",
        "start": START,
        "end_exclusive": END,
        "seconds_checked": int(len(data["times"])),
        "minute_bars_checked": int(len(market)),
        "event_predictions_checked": int(len(events["times"])),
        "event_audit": event_audit,
        "windows": WINDOWS,
        "constraints": {"initial_equity": 1000.0, "base_amount_min": 130.0,
                         "min_trade_notional": 130.0, "max_adds_minimum": 1,
                         "event_delay_seconds": 5.0, "account_drawdown_stop": 0.0,
                         "docker_wsl_virtualization": False},
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
