"""Confirm frozen coarse candidates at one second, before reporting September."""
from pathlib import Path
from dataclasses import replace
import argparse
import datetime as dt
import hashlib
import json
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from jevmesh.fmz_config import FMZConfig
from jevmesh.fmz_data import load_fmz_data
from jevmesh.fmz_engine import backtest_fmz
from tune_fmz import objective


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')


def eligible(row):
    return all(row[key]['return_pct'] > 0 and not row[key]['halted']
               and row[key]['liquidations'] == 0 and row[key]['martingale_adds'] > 0
               for key in ['fit_1m', 'validation_1m'])


def main(search, limit):
    folder, out = ROOT / search, ROOT / 'reports/fmz_v2'
    protocol = json.loads((folder / 'search_protocol.json').read_text(encoding='utf-8'))
    assert protocol['engine_sha256'] == digest(ROOT / 'src/jevmesh/fmz_engine.py'), 'Engine changed after coarse search'
    assert protocol['model_sha256'] == digest(ROOT / 'models/event_head.pt')
    ranked = json.loads((folder / 'ranked_validation.json').read_text(encoding='utf-8'))
    selected = [r for r in ranked if eligible(r)][:limit]
    # Give the user's explicit all-flat event policy its own confirmed alternative.
    for action in [1, 2, 3]:
        extras = [r for r in ranked if eligible(r) and r['parameters']['controller'] == 1
                  and r['parameters']['jev_action'] == action][:2]
        selected.extend(r for r in extras if r['trial'] not in {x['trial'] for x in selected})
    write(out / 'confirmation_protocol.json', {
        'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'search': search, 'trial_ids': [r['trial'] for r in selected],
        'selection': 'Positive, no halt/liquidation, actual martingale adds in both fit and validation. Primary must use the post-TP controller and an active Jev policy. Maximize 0.4*fit_score + 0.6*validation_score at 1s, using March-August only.',
        'september_status': 'Previously observed in the earlier research; not a pristine holdout.'})
    print(json.dumps({'loading': 'March-August 1s', 'candidates': len(selected)}), flush=True)
    data = load_fmz_data('1s', end='2026-09-01')
    rows, started = [], time.perf_counter()
    for item in selected:
        row = {'trial': item['trial'], 'parameters': item['parameters']}
        cfg = FMZConfig(**row['parameters'])
        for key, kwargs in [('validation_1s', {'start': '2026-07-01'}),
                            ('fit_1s', {'end': '2026-07-01'})]:
            row[key] = backtest_fmz(data, cfg, **kwargs)
            print(json.dumps({'trial': row['trial'], 'stage': key,
                'return_pct': row[key]['return_pct'], 'dd': row[key]['max_drawdown_pct'],
                'liquidations': row[key]['liquidations'], 'elapsed_s': round(time.perf_counter()-started, 1)}), flush=True)
        row['score_1s'] = .4*objective(row['fit_1s'])+.6*objective(row['validation_1s'])
        row['eligible_1s'] = all(row[k]['return_pct'] > 0 and not row[k]['halted']
            and row[k]['liquidations'] == 0 and row[k]['martingale_adds'] > 0
            for k in ['fit_1s', 'validation_1s'])
        rows.append(row)
        write(out / 'confirmed_candidates.json', rows)
    primary = sorted([r for r in rows if r['eligible_1s'] and r['parameters']['controller'] == 1
        and r['parameters']['jev_action'] > 0 and r['validation_1s']['jev_signals'] > 0],
        key=lambda r: r['score_1s'], reverse=True)
    if not primary:
        raise RuntimeError('No eligible second-resolution post-TP candidate; no profitable parameter file fabricated')
    best = primary[0]
    alternatives = [r for r in primary if r['parameters']['jev_action'] == 1
                    and r['validation_1s']['event_flatten_count'] > 0]
    chosen = {'selected': best}
    if alternatives:
        chosen['event_flatten'] = alternatives[0]
    freeze = {'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'search': search,
        'selection_data_end_exclusive': '2026-09-01',
        'september_status': 'Previously observed in earlier research; not a pristine unseen sample',
        'search_unique_candidates': protocol['count'], 'confirmed_at_1s': len(rows),
        'artifacts': {}, 'sources': {str(p.relative_to(ROOT)): digest(p) for p in [
            ROOT / 'src/jevmesh/fmz_engine.py', ROOT / 'src/jevmesh/engine.py',
            ROOT / 'src/jevmesh/fmz_config.py', ROOT / 'src/jevmesh/fmz_data.py',
            ROOT / 'src/jevmesh/dataset.py', ROOT / 'models/event_head.pt',
            ROOT / 'data/processed/event_predictions.jsonl', ROOT / 'data/processed/market_1m.pkl',
            ROOT / 'data/processed/funding.pkl']}}
    for name, row in chosen.items():
        cfg = FMZConfig(**row['parameters'])
        filename = 'final_parameters.json' if name == 'selected' else 'event_flatten_parameters.json'
        cfg.save(out / filename)
        row['pre_september_continuous_1s'] = backtest_fmz(data, cfg)
        row['pre_september_no_jev_1s'] = backtest_fmz(data, replace(cfg, jev_action=0))
        freeze['artifacts'][name] = {'trial': row['trial'], 'file': filename,
            'parameter_sha256': digest(out / filename), 'score_1s': row['score_1s'],
            'fit_1s': row['fit_1s'], 'validation_1s': row['validation_1s'],
            'pre_september_continuous_1s': row['pre_september_continuous_1s'],
            'pre_september_no_jev_1s': row['pre_september_no_jev_1s']}
        print(json.dumps({'frozen': name, 'trial': row['trial'], 'score_1s': row['score_1s'],
            'pre_september_return': row['pre_september_continuous_1s']['return_pct']}), flush=True)
    write(out / 'selection_freeze.json', freeze)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--search', default='reports/fmz_v2/search_margin_verified')
    parser.add_argument('--limit', type=int, default=10)
    args = parser.parse_args()
    main(args.search, args.limit)
