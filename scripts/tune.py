"""Chronological tuning; no September prices enter parameter selection."""
from pathlib import Path
from dataclasses import replace
import argparse
import datetime as dt
import hashlib
import json
import sys
import time
import numpy as np
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from jevmesh.config import StrategyConfig
from jevmesh.dataset import make_dataset, add_signals
from jevmesh.engine import run_backtest
from jevmesh.data import write_json


def candidates(count):
    rng = np.random.default_rng(20260923)
    out = [StrategyConfig()]
    while len(out) < count:
        choice = lambda a: float(rng.choice(a))
        step = choice([.003, .005, .008, .012, .018, .025])
        cfg = replace(StrategyConfig(), leverage=choice([3, 5, 7, 10]),
            gross_utilization=choice([.6, .75, .9]), base_notional=choice([50, 75, 100, 125]),
            multiplier=choice([1.15, 1.35, 1.5, 1.75]), max_adds=int(choice([1, 2, 3, 4])),
            grid_step=step, take_profit=step*choice([.5, .75, 1., 1.5]),
            basket_stop=choice([.08, .12, .18, .25]), basket_take_profit=choice([.01, .025, .05]),
            max_cycle_minutes=choice([120, 360, 720, 1440, 2880]),
            cooldown_minutes=choice([15, 60, 180, 360]),
            event_threshold=choice([.2, .3, .4, .5, .65, .8]),
            event_hold_minutes=choice([5, 15, 30, 60]),
            trend_stop=choice([.01, .02, .04]), vol_stop=choice([.015, .03, .06]))
        feasible = True
        for reference in [60000., 80000.]:
            unit = np.ceil(max(cfg.base_notional, 100.)/reference/.001)*.001
            add_qty = np.ceil(unit*cfg.multiplier/.001)*.001
            if (2*unit+add_qty)*reference > 99.*cfg.leverage*cfg.gross_utilization:
                feasible = False
        if feasible:
            out.append(cfg)
    return out


def score(result):
    if result['fills'] < 20 or result['martingale_adds'] < 1 or result['liquidations']:
        return -1000.+result['return_pct']
    return result['return_pct']-.75*result['max_drawdown_pct']-10*result['halted']


def tune(count=300, shortlist=10):
    outdir = ROOT / 'reports'
    outdir.mkdir(exist_ok=True)
    data = make_dataset('1m', 'nanojev', start='2026-03-01', end='2026-07-01')
    configs = candidates(count)
    trials = []
    started = time.perf_counter()
    for i, cfg in enumerate(configs):
        r = run_backtest(data, cfg)
        trials.append({'trial': i, 'parameters': cfg.to_dict(), 'train_1m': r, 'train_score': score(r)})
        if (i+1) % 25 == 0:
            print(json.dumps({'stage': 'train_1m', 'done': i+1, 'count': count, 'best_score': max(t['train_score'] for t in trials), 'elapsed_s': round(time.perf_counter()-started, 1)}), flush=True)
    ranked = sorted(trials, key=lambda t: t['train_score'], reverse=True)[:max(30, shortlist*3)]
    val = make_dataset('1m', 'nanojev', start='2026-07-01', end='2026-09-01')
    for t in ranked:
        t['validation_1m'] = run_backtest(val, configs[t['trial']])
        t['validation_score'] = score(t['validation_1m'])
    finalists = sorted(ranked, key=lambda t: .25*t['train_score']+.75*t['validation_score'], reverse=True)[:shortlist]
    write_json(outdir / 'tuning_trials.json', trials)
    pd.DataFrame([{'trial': t['trial'], **t['parameters'],
                   **{f'train_{k}': v for k, v in t['train_1m'].items()},
                   **{f'val_{k}': v for k, v in t.get('validation_1m', {}).items()}}
                  for t in trials]).to_csv(outdir / 'tuning_trials.csv', index=False)
    del data, val
    print(json.dumps({'stage': 'load_1s_validation', 'finalists': [t['trial'] for t in finalists]}), flush=True)
    val = make_dataset('1s', 'nanojev', start='2026-07-01', end='2026-09-01')
    for t in finalists:
        t['validation_1s'] = run_backtest(val, configs[t['trial']])
        t['final_score'] = .25*t['train_score']+.75*score(t['validation_1s'])
        print(json.dumps({'stage': 'validation_1s', 'trial': t['trial'], 'return_pct': t['validation_1s']['return_pct'], 'dd': t['validation_1s']['max_drawdown_pct'], 'score': t['final_score']}), flush=True)
    finalists.sort(key=lambda t: t['final_score'], reverse=True)
    selected = finalists[0]
    config_path = outdir / 'final_parameters.json'
    configs[selected['trial']].save(config_path)
    write_json(outdir / 'selection_freeze.json', {
        'frozen_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'seed': 20260923,
        'trial_count': count, 'selected_trial': selected['trial'], 'finalists': finalists,
        'selection_rule': 'Candidates must fund both initial legs plus one martingale add at 60k and 80k prices under pre-change minimums. Top training 1m candidates; shortlist by 25% train +75% Jul-Aug validation score; confirm same score using 1s Jul-Aug; score=return%-0.75*maxDD%-10*halt, min20 fills and one actual add, no liquidation',
        'preliminary_search': '300 earlier exploratory trials retained under reports/preliminary; excluded after fixing gap limits and discovering configurations unable to finance martingale adds. No September evaluation occurred.',
        'last_price_allowed_for_selection': '2026-08-31T23:59:59Z',
        'parameter_sha256': hashlib.sha256(config_path.read_bytes()).hexdigest(),
        'model_head_sha256': hashlib.sha256((ROOT / 'models/event_head.pt').read_bytes()).hexdigest(),
        'engine_sha256': hashlib.sha256((ROOT / 'src/jevmesh/engine.py').read_bytes()).hexdigest(),
        'holdout': '2026-09-01 to 2026-09-21 UTC inclusive; not evaluated by this script'})
    print(json.dumps({'frozen_trial': selected['trial'], 'parameters': selected['parameters']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--trials', type=int, default=300)
    parser.add_argument('--shortlist', type=int, default=10)
    args = parser.parse_args()
    tune(args.trials, args.shortlist)
