"""Freeze the best actually-active event candidate before holdout evaluation."""
from pathlib import Path
import datetime as dt
import hashlib
import json
ROOT = Path(__file__).resolve().parents[1]
out = ROOT / 'reports'
if (out / 'evaluation.json').exists():
    raise RuntimeError('Holdout results already exist; refusing to select another candidate after viewing them')
freeze = json.loads((out / 'selection_freeze.json').read_text(encoding='utf-8'))
eligible = [t for t in freeze['finalists'] if t['validation_1s']['event_stops'] > 0 and t['train_1m']['event_stops'] > 0]
chosen = max(eligible, key=lambda t: t['final_score'])
path = out / 'active_event_parameters.json'
path.write_text(json.dumps(chosen['parameters'], indent=2), encoding='utf-8')
record = {'frozen_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'selected_trial': chosen['trial'],
          'selection_rule': 'Best existing frozen finalist with actual event flattening in both training and Jul-Aug validation; no September results inspected',
          'candidate': chosen, 'parameter_sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
(out / 'active_event_freeze.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
print(json.dumps({'active_event_trial': chosen['trial'], 'parameters': chosen['parameters']}))
