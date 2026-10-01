from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
EXPERIMENT = Path(__file__).resolve().parent
REPORTS = EXPERIMENT / "reports"
ARRAY_DIR = ROOT / "data/long_backtest_eth_1s_202309_202609/engine_arrays"
MARKET_PATH = ROOT / "data/long_backtest_eth_202309_202609/processed/market_1m.pkl"
MANIFEST_PATH = ARRAY_DIR / "dataset_manifest.json"
PREDICTION_PATH = ROOT / "data/long_backtest_eth_202309_202609_blockbeats/processed/event_predictions_eth_calibrated_walkforward.jsonl"

START = "2023-09-24T00:00:00Z"
END = "2026-09-22T00:00:00Z"
EXPECTED_SIGNATURE = "42a2ead2d2c80a1fce404526e6bdcd30b86ed5bd891a172602ce78f24ee3d3ad"
DATA_VERSION = "ethusdt-corrected-1s-blockbeats-20261002-v1"


def write_json(path: Path, value: object) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def digest(path: Path) -> str:
    checksum = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            checksum.update(block)
    return checksum.hexdigest()


def millis(value: str | int) -> int:
    return int(pd.to_datetime(value, utc=True).timestamp() * 1000)


def market_features(market: pd.DataFrame) -> pd.DataFrame:
    """Causal completed-minute features used to adapt the next grid."""
    close = market["close"]
    returns = np.log(close).diff()
    fast = returns.ewm(span=30, min_periods=30, adjust=False).std()
    slow = returns.ewm(span=240, min_periods=240, adjust=False).std()
    daily = returns.ewm(span=1440, min_periods=1440, adjust=False).std()
    sigma = (.65 * fast + .35 * slow).ewm(span=10, adjust=False).mean() * np.sqrt(30)
    trend = close.ewm(span=60, adjust=False).mean() / close.ewm(span=240, adjust=False).mean() - 1
    efficiency = returns.rolling(240).sum().abs() / returns.abs().rolling(240).sum().clip(lower=1e-10)
    macro_fast = close.ewm(span=360, min_periods=360, adjust=False).mean()
    macro_slow = close.ewm(span=1440, min_periods=1440, adjust=False).mean()
    macro_scale = (daily * np.sqrt(48)).clip(lower=1e-8)
    macro_z = (macro_fast / macro_slow - 1) / macro_scale
    super_fast = close.ewm(span=1440, min_periods=1440, adjust=False).mean()
    super_slow = close.ewm(span=4320, min_periods=4320, adjust=False).mean()
    super_z = (super_fast / super_slow - 1) / macro_scale
    ema_fast = close.ewm(span=60, adjust=False).mean()
    ema_slow = close.ewm(span=120, adjust=False).mean()
    frame = pd.DataFrame({
        "sigma30": sigma,
        "vol_ratio": fast / slow.clip(lower=1e-8),
        "trend_z": trend / sigma.clip(lower=1e-8),
        "efficiency": efficiency,
        "ret30_z": np.log(close / close.shift(30)) / sigma.clip(lower=1e-8),
        "ret240_z": np.log(close / close.shift(240)) / (sigma * np.sqrt(8)).clip(lower=1e-8),
        "daily_sigma30": daily * np.sqrt(30),
        "last_close": close,
        "macro_z": macro_z,
        "super_z": super_z,
        "ema_spread_60_120": ema_fast / ema_slow - 1,
        "last_vs_ema120": close / ema_slow - 1,
    })
    # The shift is the information barrier: a completed minute only affects the next minute.
    return frame.shift(1).replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _check_arrays(data: dict[str, np.ndarray], manifest: dict) -> None:
    count = int(manifest["bars"])
    if count != 94_521_600:
        raise ValueError(f"Unexpected full-range second count: {count}")
    expected_start = millis(START)
    expected_end = millis(END)
    if data["times"].shape != (count,) or int(data["times"][0]) != expected_start:
        raise ValueError("The second-bar start does not match the frozen dataset")
    if int(data["times"][-1]) + 1000 != expected_end:
        raise ValueError("The second-bar end does not match the frozen dataset")
    if data["bars"].shape != (count, 8) or data["funding"].shape != (count,):
        raise ValueError("Invalid second-bar array shape")
    if data["minute_index"].shape != (count,) or data["news"].shape != (count, 2):
        raise ValueError("Invalid aligned feature/event array shape")
    if not np.isfinite(data["bars"][:1000]).all() or np.any(data["bars"][:1000] <= 0):
        raise ValueError("Invalid second-bar values")
    if np.any(np.diff(data["times"][:10000]) != 1000):
        raise ValueError("Second timestamps are not contiguous")
    if manifest.get("news_source") != "BlockBeats public newsflash feed":
        raise ValueError("Unexpected news source")


def load_context() -> tuple[dict, pd.DataFrame, np.ndarray, dict]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if manifest.get("dataset_signature") != EXPECTED_SIGNATURE:
        raise ValueError("Frozen dataset signature changed")
    if manifest.get("start") != "2023-09-24T00:00:00Z" or manifest.get("end_exclusive") != "2026-09-22T00:00:00Z":
        raise ValueError("Frozen dataset coverage changed")
    names = ("times", "bars", "funding", "minute_index", "news")
    data = {name: np.load(ARRAY_DIR / f"{name}.npy", mmap_mode="r") for name in names}
    data["resolution"] = "1s"
    _check_arrays(data, manifest)
    market = pd.read_pickle(MARKET_PATH)
    if len(market) != int(manifest["market_bars"]):
        raise ValueError("Corrected minute market length mismatch")
    if int(market.index[0]) != millis("2023-09-23T00:00:00Z"):
        raise ValueError("Corrected minute market start mismatch")
    features = market_features(market).to_numpy(dtype=np.float64)
    if features.shape != (len(market), 12) or not np.isfinite(features).all():
        raise ValueError("Causal minute feature matrix is invalid")
    audit = {
        "version": DATA_VERSION,
        "dataset_manifest": str(MANIFEST_PATH),
        "dataset_signature": manifest["dataset_signature"],
        "start": START,
        "end_exclusive": END,
        "resolution": "1s",
        "seconds": int(len(data["times"])),
        "market_bars": int(len(market)),
        "market_sha256": manifest["market_sha256"],
        "funding_sha256": manifest["funding_sha256"],
        "predictions_sha256": manifest["predictions_sha256"],
        "news_source": manifest["news_source"],
        "news_availability_basis": manifest.get("news_availability_basis"),
        "news_content_basis": manifest.get("news_content_basis"),
        "model_method": manifest.get("model_method"),
        "feature_method": "shifted completed-minute volatility/trend features; no current-minute or future price input",
    }
    return data, market, features, audit
