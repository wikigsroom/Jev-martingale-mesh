from pathlib import Path
import numpy as np
import pandas as pd
from .data import ROOT
from .dataset import make_dataset

EMA_WINDOWS = [15, 30, 60, 120, 240, 480, 720, 1440, 2880]


def prepare_features():
    frame = pd.read_pickle(ROOT / 'data/processed/market_1m.pkl')
    close = frame.close
    parts = [close.ewm(span=w, adjust=False, min_periods=w).mean().shift(1).to_numpy() for w in EMA_WINDOWS]
    parts.append(close.shift(1).to_numpy())
    parts.append(np.log(close).diff().rolling(60).std().shift(1).to_numpy())
    return frame.index.to_numpy(dtype=np.int64), np.column_stack(parts)


def add_news(times, delay=5.):
    rows = pd.read_json(ROOT / 'data/processed/event_predictions.jsonl', lines=True)
    news = np.zeros((len(times), 2), dtype=np.float32)
    for event in rows.itertuples(index=False):
        ts = int(event.available_ms)+int(delay*1000)
        if ts < times[0] or ts > times[-1]:
            continue
        i = np.searchsorted(times, ts)
        score = event.p_up+event.p_down
        if score > news[i, 0]:
            news[i] = [score, (event.p_up-event.p_down)/max(score, 1e-9)]
    return news


def load_fmz_data(resolution='1m', start='2026-03-01', end='2026-09-22', delay=5.):
    data = make_dataset(resolution, 'none', start=start, end=end)
    mt, features = prepare_features()
    data['minute_index'] = (np.searchsorted(mt, data['times'], side='right')-1).astype(np.int32)
    data['features'] = features
    data['news'] = add_news(data['times'], delay)
    return data
