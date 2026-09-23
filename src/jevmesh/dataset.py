from pathlib import Path
import json
import re
from functools import lru_cache
import numpy as np
import pandas as pd
from .data import ROOT, write_json


def load_news():
    return pd.read_json(ROOT / "data/processed/news.jsonl", lines=True).sort_values("available_ms")


def prepare_event_questions():
    market = pd.read_pickle(ROOT / "data/processed/market_1m.pkl")
    times = market.index.to_numpy()
    close = market.close.to_numpy()
    logret = np.log(market.close).diff()
    volatility = logret.rolling(30, min_periods=20).std().shift(1).to_numpy()
    rows = []
    @lru_cache(maxsize=3)
    def seconds_day(day):
        path = ROOT / f'data/processed/seconds/{day}.npz'
        with np.load(path) as data:
            return data['ohlcv']

    def second_price(ts, column):
        stamp = pd.to_datetime(ts, unit='ms', utc=True)
        day = stamp.strftime('%Y-%m-%d')
        offset = int((ts % 86400000)//1000)
        return seconds_day(day)[offset, column]
    for event in load_news().to_dict("records"):
        # Training is explicitly a 30-second price-regime proxy, not a claim of
        # counterfactual grid P&L or direct confidence in a profitable trade.
        received = int(event["available_ms"])+5000
        i = int(np.searchsorted(times, received, side="left"))
        if i < 31 or i+5 >= len(times):
            continue
        current_minute = int(np.searchsorted(times, received, side="right"))-1
        past = current_minute-1
        if past < 30:
            continue
        sigma = max(.00005, volatility[current_minute])
        threshold = max(.0003, sigma*np.sqrt(.5))
        entry_ms = ((received+999)//1000)*1000
        future_return = float(second_price(entry_ms+29000, 3)/second_price(entry_ms, 0)-1)
        label = 0 if future_return > threshold else 2 if future_return < -threshold else 1
        ret5 = close[past]/close[past-5]-1
        ret30 = close[past]/close[past-30]-1
        state = ("Asset: BTCUSDT perpetual. Only information available now is supplied.\n"
                 f"News publisher: {event['source']}. Headline: {event['title']}\n"
                 f"Summary: {event['excerpt'][:650]}\n"
                 f"Completed-market returns: past 5 minutes {ret5*100:.3f}%; past 30 minutes {ret30*100:.3f}%. "
                 f"Past one-minute return standard deviation {sigma*100:.4f}%. "
                 f"Directional threshold: {threshold*100:.3f}% over the next 30 seconds. "
                 "The future price is unknown.")
        rows.append({**event, "received_ms": received, "decision_ms": received, "label_start_ms": entry_ms, "label_horizon_seconds": 30, "state": state,
                     "label": label, "future_return": future_return, "threshold": threshold,
                     "input_last_market_ms": int(times[past])+59999,
                     "instructions": "Classify the BTCUSDT price return over the next 30 seconds relative to the stated threshold. Choose the most likely outcome based only on the supplied news and past market data.",
                     "candidates": {"up": "The future 30-second return is greater than the positive threshold.",
                                    "range": "The future 30-second return lies between the negative and positive thresholds, inclusive.",
                                    "down": "The future 30-second return is below the negative threshold."}})
    path = ROOT / "data/processed/event_questions.json"
    write_json(path, rows)
    write_json(ROOT / "data/audit/event_labels.json", {
        "events": len(rows), "label_definition": "next 30 one-second aggregate-trade bars close return from first post-receipt open; three classes with threshold=max(0.03%, past 30-min 1m std*sqrt(0.5))",
        "train_end": "2026-06-01", "head_validation_end": "2026-07-01",
        "strategy_validation_end": "2026-09-01", "holdout_start": "2026-09-01",
        "warning": "This is a price-regime proxy. It is neither an observed causal news effect nor a trained conditional grid-liquidation probability.",
        "label_counts": pd.Series([r["label"] for r in rows]).value_counts().sort_index().to_dict()})
    print(f"Prepared {len(rows)} point-in-time input questions", flush=True)
    return rows


def news_scores(kind="nanojev"):
    if kind == "none":
        return [], []
    if kind == "nanojev":
        path = ROOT / "data/processed/event_predictions.jsonl"
        if not path.exists():
            raise FileNotFoundError("Run genuine NanoJev inference first; no rule fallback is substituted")
        events = pd.read_json(path, lines=True)
        return events.available_ms.to_numpy(dtype=np.int64), events.p_breakout.to_numpy(dtype=float)
    if kind == "original_nanojev":
        events = pd.read_json(ROOT / "data/processed/event_predictions.jsonl", lines=True)
        return events.available_ms.to_numpy(dtype=np.int64), 1-events.original_p_range.to_numpy(dtype=float)
    events = load_news()
    pattern = re.compile(r"\b(hack\w*|exploit\w*|attack|emergency|bankrupt\w*|ban\w*|seiz\w*|liquidat\w*|halt\w*|approv\w*|tariff\w*|interest rate|federal reserve|fed\b|inflation|cpi\b)", re.I)
    scores = np.array([.9 if pattern.search(t+" "+e) else .1 for t, e in zip(events.title, events.excerpt)])
    return events.available_ms.to_numpy(dtype=np.int64), scores


def add_signals(times, kind, delay_seconds):
    signals = np.zeros(len(times), dtype=np.float64)
    event_times, scores = news_scores(kind)
    for ts, score in zip(event_times, scores):
        if int(ts)+int(delay_seconds*1000) < times[0]:
            continue
        index = np.searchsorted(times, int(ts)+int(delay_seconds*1000), side="left")
        if index < len(times):
            signals[index] = max(signals[index], float(score))
    return signals


def make_dataset(resolution="1m", signal_kind="nanojev", delay_seconds=5.0, start=None, end=None):
    market = pd.read_pickle(ROOT / "data/processed/market_1m.pkl")
    if start:
        start_ms = int(pd.Timestamp(start, tz="UTC").timestamp()*1000)
    else:
        start_ms = int(market.index[0])
    if end:
        end_ms = int(pd.Timestamp(end, tz="UTC").timestamp()*1000)
    else:
        end_ms = int(market.index[-1])+60000
    minute_times = market.index.to_numpy(dtype=np.int64)
    prior5 = market.close.shift(6).to_numpy()
    prior_range = (market.high.rolling(15).max()/market.low.rolling(15).min()-1).shift(1).to_numpy()
    if resolution == "1m":
        mask = (minute_times >= start_ms) & (minute_times < end_ms)
        times = minute_times[mask]
        bars = market[["open", "high", "low", "close", "mark_open", "mark_high", "mark_low", "mark_close"]].to_numpy()[mask]
        trends = market.open.to_numpy()[mask]/prior5[mask]-1
        ranges = prior_range[mask]
    else:
        files = sorted((ROOT / "data/processed/seconds").glob("*.npz"))
        chunks, tchunks = [], []
        for path in files:
            day = int(pd.Timestamp(path.stem, tz="UTC").timestamp()*1000)
            if day+86400000 <= start_ms or day >= end_ms:
                continue
            with np.load(path) as data:
                times_day = data["timestamp"]
                mask = (times_day >= start_ms) & (times_day < end_ms)
                tchunks.append(times_day[mask])
                chunks.append(data["ohlcv"][mask, :4])
        if not chunks:
            raise ValueError("No actual one-second archive data available")
        times = np.concatenate(tchunks)
        trade = np.concatenate(chunks)
        if times[0] != start_ms or times[-1]+1000 != end_ms or np.any(np.diff(times) != 1000):
            raise ValueError("One-second coverage is incomplete; refusing to bridge gaps")
        mi = np.searchsorted(minute_times, times, side="right")-1
        # Historical mark ticks are unavailable. Carry ONLY the previous completed
        # minute's mark/trade basis; do not interpolate future mark closes.
        prev = np.maximum(0, mi-1)
        basis = (market.mark_close.to_numpy()[prev]/market.close.to_numpy()[prev])
        basis[mi == 0] = market.mark_open.iloc[0]/market.open.iloc[0]
        bars = np.concatenate([trade, trade*basis[:, None]], axis=1)
        trends = trade[:, 0]/prior5[mi]-1
        ranges = prior_range[mi]
    trends = np.nan_to_num(trends, nan=0.0)
    ranges = np.nan_to_num(ranges, nan=1.0)  # Warm-up disables entry without peeking.
    funding = np.zeros(len(times))
    rates = pd.read_pickle(ROOT / "data/processed/funding.pkl")
    for row in rates.itertuples(index=False):
        settle = int(row.timestamp)//60000*60000
        idx = np.searchsorted(times, settle)
        if idx < len(times) and times[idx] == settle:
            funding[idx] = row.rate
    return {"times": times, "bars": np.ascontiguousarray(bars), "funding": funding,
            "signals": add_signals(times, signal_kind, delay_seconds),
            "trends": trends, "ranges": ranges, "resolution": resolution,
            "mark_mode": "archived_1m_ohlc" if resolution == "1m" else "trade_price_times_previous_completed_minute_basis"}
