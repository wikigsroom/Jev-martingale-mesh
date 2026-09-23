"""Build actual one-second trade OHLC from Binance aggregate trades, with hashes."""
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
import argparse
import datetime as dt
import hashlib
import json
import sys
import time
import zipfile
import numpy as np
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from jevmesh.data import archive_job, download_archive, write_json, ROOT


def build_day(day):
    output = ROOT / f"data/processed/seconds/{day}.npz"
    receipt_path = ROOT / f"data/audit/seconds/{day}.json"
    if output.exists() and receipt_path.exists():
        return json.loads(receipt_path.read_text(encoding="utf-8"))
    started = time.perf_counter()
    receipt = download_archive(archive_job("aggTrades", day))
    if receipt.get("status") != 200 or receipt.get("error"):
        write_json(receipt_path, receipt)
        return receipt
    with zipfile.ZipFile(receipt["path"]) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".csv"))
        with archive.open(name) as handle:
            frame = pd.read_csv(handle, usecols=[1, 2, 5], dtype="float64")
    frame.columns = ["price", "quantity", "timestamp"]
    frame["second"] = frame.timestamp.astype("int64") // 1000
    group = frame.groupby("second", sort=True)
    bars = group.price.agg(["first", "max", "min", "last"])
    bars["volume"] = group.quantity.sum()
    start = int(pd.Timestamp(day, tz="UTC").timestamp())
    bars = bars.reindex(np.arange(start, start + 86400))
    missing = bars["last"].isna()
    if missing.iloc[0]:
        minute = pd.read_pickle(ROOT / "data/processed/market_1m.pkl")
        previous_ms = start*1000-60000
        if previous_ms not in minute.index:
            raise ValueError(f"No earlier known trade for {day}")
        bars.iloc[0, bars.columns.get_loc("last")] = minute.loc[previous_ms, "close"]
    bars["last"] = bars["last"].ffill()
    for col in ["first", "max", "min"]:
        bars[col] = bars[col].fillna(bars["last"])
    bars["volume"] = bars["volume"].fillna(0)
    if bars.isna().any().any():
        raise ValueError(f"Missing opening trade for {day}; no future backfill is allowed")
    output.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output, timestamp=bars.index.to_numpy(dtype="int64") * 1000,
                        ohlcv=bars.to_numpy(dtype="float64"))
    receipt.update(trades=len(frame), seconds=len(bars), no_trade_seconds=int(missing.sum()),
                   elapsed_seconds=round(time.perf_counter()-started, 2),
                   processed_sha256=hashlib.sha256(output.read_bytes()).hexdigest())
    write_json(receipt_path, receipt)
    return receipt


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-03-01")
    parser.add_argument("--end", default="2026-09-21")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    days = pd.date_range(args.start, args.end).strftime("%Y-%m-%d").tolist()
    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(build_day, day): day for day in days}
        for future in as_completed(futures):
            try:
                result = future.result()
                print(json.dumps({k: result.get(k) for k in ["period", "status", "size", "trades", "elapsed_seconds", "error"]}), flush=True)
            except Exception as exc:
                print(json.dumps({"day": futures[future], "error": str(exc)}), flush=True)
