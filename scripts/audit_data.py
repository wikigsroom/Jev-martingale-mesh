from pathlib import Path
import hashlib
import json
import sys
import numpy as np
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from jevmesh.data import write_json

market = pd.read_pickle(ROOT / 'data/processed/market_1m.pkl')
receipts = []
seconds = 0
close_mismatches = 0
for path in sorted((ROOT / 'data/processed/seconds').glob('*.npz')):
    receipt = json.loads((ROOT / f'data/audit/seconds/{path.stem}.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(path.read_bytes()).hexdigest() == receipt['processed_sha256'], path
    with np.load(path) as z:
        ts, bars = z['timestamp'], z['ohlcv']
        assert len(ts) == 86400 and np.all(np.diff(ts) == 1000)
        assert np.isfinite(bars).all() and (bars[:, :4] > 0).all()
        assert (bars[:, 1] >= bars[:, [0, 2, 3]].max(1)).all()
        assert (bars[:, 2] <= bars[:, [0, 1, 3]].min(1)).all()
        minute_ts = ts[::60]
        close_mismatches += int((np.abs(bars[59::60, 3]-market.loc[minute_ts, 'close'].to_numpy()) > .11).sum())
        seconds += len(ts)
    receipts.append(receipt)
events = json.loads((ROOT / 'data/processed/event_questions.json').read_text(encoding='utf-8'))
assert all(e['input_last_market_ms'] < e['received_ms'] for e in events)
assert all(e['received_ms'] >= max(e['published_ms'], e['modified_ms']) for e in events)
result = {'days': len(receipts), 'seconds': seconds, 'aggregate_trades': sum(r['trades'] for r in receipts),
          'compressed_trade_bytes': sum(r['size'] for r in receipts),
          'no_trade_seconds_forward_carried': sum(r['no_trade_seconds'] for r in receipts),
          'minute_close_mismatches_over_one_tick': close_mismatches, 'event_questions': len(events),
          'point_in_time_input_violations': 0, 'all_processed_second_files_sha256_verified': True}
write_json(ROOT / 'data/audit/final_data_audit.json', result)
print(json.dumps(result, indent=2))
