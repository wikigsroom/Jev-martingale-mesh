"""Fixed-parameter checks of whether Jev adds more than a generic news pause."""
from pathlib import Path
import hashlib
import json
import sys
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from jevmesh.fmz_config import FMZConfig
from jevmesh.fmz_data import load_fmz_data
from jevmesh.fmz_engine import backtest_fmz


def main():
    out = ROOT / 'reports/fmz_v2'
    freeze = json.loads((out / 'selection_freeze.json').read_text(encoding='utf-8'))
    param = out / 'final_parameters.json'
    assert hashlib.sha256(param.read_bytes()).hexdigest() == freeze['artifacts']['selected']['parameter_sha256']
    cfg = FMZConfig.load(param)
    print('Loading fixed-parameter event diagnostics', flush=True)
    data = load_fmz_data('1s')
    actual = data['news'].copy()
    events = np.flatnonzero(actual[:, 0] > 0)
    result = {'parameter_sha256': hashlib.sha256(param.read_bytes()).hexdigest(),
              'scope': 'Post-freeze ablations; no reselection. All variants retain the same prices, timing, grid/trend/risk parameters.',
              'all_news_count': int(len(events)), 'variants': {}}
    for name in ['same_jev_scores_direction_disabled', 'every_news_fixed_pause', 'every_news_same_direction_scores']:
        data['news'] = actual.copy()
        if name != 'same_jev_scores_direction_disabled':
            data['news'][events, 0] = 1.
        if name != 'every_news_same_direction_scores':
            data['news'][:, 1] = 0.
        metrics = backtest_fmz(data, cfg)
        result['variants'][name] = metrics
        (out / 'event_ablation.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
        print(json.dumps({'variant': name, 'return_pct': metrics['return_pct'],
            'dd': metrics['max_drawdown_pct'], 'jev_leg_closes': metrics['jev_forced_leg_closes']}), flush=True)


if __name__ == '__main__':
    main()
