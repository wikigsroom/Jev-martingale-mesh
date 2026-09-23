"""Broad, reproducible FMZ parameter search; candidate selection uses pre-September."""
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path
import argparse
import datetime as dt
import hashlib
import json
import multiprocessing
import sys
import time
import numpy as np
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from jevmesh.fmz_config import FMZConfig
from jevmesh.fmz_data import load_fmz_data
from jevmesh.fmz_engine import backtest_fmz

DATA = None


def initialize():
    global DATA
    DATA = load_fmz_data('1m', end='2026-09-01')


def candidates(count, seed):
    rng = np.random.default_rng(seed)
    result = []
    # Include the safely adapted original, and small controlled changes.
    for control in [0, 1, 2, 4]:
        for action in [0, 1, 2, 3]:
            result.append(replace(FMZConfig(), controller=control, jev_action=action))
    while len(result) < count:
        pick = lambda a: float(rng.choice(a))
        control = int(rng.choice([0, 1, 2, 3, 4], p=[.15, .4, .25, .05, .15]))
        fast = int(pick([15, 30, 60, 120, 240]))
        slow = int(pick([x for x in [120, 240, 480, 720, 1440, 2880] if x > fast]))
        spacing = pick([.003, .005, .008, .012, .018, .025, .035, .05, .07, .1])
        cfg = replace(FMZConfig(), controller=control,
            base_spacing=spacing, profit_target=min(.15, max(.002, spacing*pick([.3, .5, .75, 1., 1.5, 2.]))),
            base_amount_rate=pick([.03, .1, .25, .5, .75, 1., 1.25]), base_amount_min=pick([10, 20, 30, 50]),
            ratio=pick([1.1, 1.2, 1.25, 1.35, 1.5, 1.75, 2.]), max_adds=int(pick([1, 2, 3, 4, 5, 6])),
            warning_index=int(pick([1, 2, 3, 4, 6, 99])), max_loss_notional_multiple=pick([2.5, 3, 4, 5, 7, 9]),
            leverage=pick([5, 7, 10]), gross_utilization=pick([.5, .7, .85, .95]),
            basket_stop=pick([.15, .25, .4, .6, .85]), account_drawdown_stop=pick([.25, .35, .5, .65]),
            stop_cooldown_minutes=pick([0, 1, 5, 15, 30, 60, 180, 360]),
            reentry_delay_seconds=pick([1, 5, 30, 120]), ema_fast_minutes=fast, ema_slow_minutes=slow,
            trend_enter=pick([.0005, .001, .002, .003, .005, .008, .012]), trend_exit_fraction=pick([0, .3, .6]),
            trend_confirm_minutes=pick([0, 3, 10, 30, 60]), trend_liquidate_opposite=bool(rng.random()<.8),
            directional_stop=pick([.005, .01, .015, .025, .04, .07, .12]),
            directional_trail=pick([.005, .01, .02, .04, .07, .12]),
            jev_action=int(pick([1, 2, 3])), jev_breakout_threshold=pick([.25, .3, .35, .38, .4, .42]),
            jev_direction_threshold=pick([0, .05, .1, .2]), jev_hold_seconds=pick([30, 60, 180, 300, 900]))
        feasible = True
        for price in [60000., 80000.]:
            target = max(100*cfg.base_amount_rate, cfg.base_amount_min)*cfg.ratio
            qty = np.ceil(max(target, 100.)/price/.001)*.001
            add = np.ceil(qty*cfg.ratio/.001)*.001
            legs = 1 if control == 4 else 2
            if (legs*qty+add)*price > 99*cfg.leverage*cfg.gross_utilization or qty*price >= 100*cfg.max_loss_notional_multiple:
                feasible = False
        if feasible:
            result.append(cfg)
    return result[:count]


def objective(r):
    if r['liquidations'] or r['fills'] < 20 or r['martingale_adds'] < 1:
        return -10000.+r['return_pct']
    return r['return_pct']-.5*r['max_drawdown_pct']-15*r['halted']


def trial(job):
    index, params = job
    cfg = FMZConfig(**params)
    r = backtest_fmz(DATA, cfg, end='2026-07-01')
    return {'trial': index, 'parameters': params, 'fit_1m': r, 'fit_score': objective(r)}


def validation(job):
    index, params = job
    cfg = FMZConfig(**params)
    r = backtest_fmz(DATA, cfg, start='2026-07-01')
    return index, r


def main(count, workers, seed, output):
    out = ROOT / output
    out.mkdir(parents=True, exist_ok=True)
    configs = candidates(count, seed)
    protocol = {'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(), 'seed': seed, 'count': count,
        'fit': ['2026-03-01', '2026-07-01'], 'validation': ['2026-07-01', '2026-09-01'],
        'september_status': 'Already observed in previous strategy research; not a pristine unseen sample',
        'selection': 'Rank training by profit-0.5*drawdown-15*halt. Validate top candidates across families; require real fills/adds and no liquidation. Confirm at 1s before final choice.',
        'engine_sha256': hashlib.sha256((ROOT / 'src/jevmesh/fmz_engine.py').read_bytes()).hexdigest(),
        'model_sha256': hashlib.sha256((ROOT / 'models/event_head.pt').read_bytes()).hexdigest()}
    (out / 'search_protocol.json').write_text(json.dumps(protocol, indent=2), encoding='utf-8')
    (out / 'candidate_parameters.json').write_text(json.dumps([c.to_dict() for c in configs], indent=2), encoding='utf-8')
    started = time.perf_counter()
    rows = []
    with ProcessPoolExecutor(max_workers=workers, initializer=initialize) as pool:
        futures = [pool.submit(trial, (i, c.to_dict())) for i, c in enumerate(configs)]
        for future in as_completed(futures):
            rows.append(future.result())
            if len(rows) % 100 == 0:
                best = max(rows, key=lambda x: x['fit_score'])
                print(json.dumps({'phase': 'fit', 'done': len(rows), 'total': count,
                    'positive': sum(r['fit_1m']['return_pct'] > 0 for r in rows), 'best_trial': best['trial'],
                    'best_return': best['fit_1m']['return_pct'], 'best_dd': best['fit_1m']['max_drawdown_pct'],
                    'elapsed_s': round(time.perf_counter()-started, 1)}), flush=True)
        ranked = sorted(rows, key=lambda r: r['fit_score'], reverse=True)
        ids = set(r['trial'] for r in ranked[:100])
        # Keep each controller family represented; do not turn this into a hidden
        # direction-only optimizer at the expense of the user's post-TP proposal.
        for control in range(5):
            ids.update(r['trial'] for r in [x for x in ranked if x['parameters']['controller'] == control][:20])
        ids.update(range(16))
        by_id = {r['trial']: r for r in rows}
        val_jobs = [pool.submit(validation, (i, configs[i].to_dict())) for i in sorted(ids)]
        for future in as_completed(val_jobs):
            i, r = future.result()
            row = by_id[i]
            row['validation_1m'] = r
            row['selection_score'] = .4*row['fit_score']+.6*objective(r)
            if row['fit_1m']['return_pct'] <= 0 or r['return_pct'] <= 0:
                row['selection_score'] -= 30
    rows.sort(key=lambda r: r['trial'])
    (out / 'trials.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    pd.DataFrame([{'trial': r['trial'], **r['parameters'], **{f'fit_{k}':v for k,v in r['fit_1m'].items()},
        **{f'val_{k}':v for k,v in r.get('validation_1m',{}).items()}, 'score':r.get('selection_score')}
        for r in rows]).to_csv(out / 'trials.csv', index=False)
    finalists = sorted([r for r in rows if 'validation_1m' in r], key=lambda r:r['selection_score'], reverse=True)
    (out / 'ranked_validation.json').write_text(json.dumps(finalists, indent=2), encoding='utf-8')
    print(json.dumps({'completed': count, 'elapsed_s': round(time.perf_counter()-started,1),
        'best': [{'trial':r['trial'], 'controller':r['parameters']['controller'],
                 'fit':r['fit_1m']['return_pct'], 'validation':r['validation_1m']['return_pct'],
                 'dd':r['validation_1m']['max_drawdown_pct'], 'jev_flat':r['validation_1m']['event_flatten_count'],
                 'jev_leg_closes':r['validation_1m']['jev_forced_leg_closes']} for r in finalists[:15]]}), flush=True)


if __name__ == '__main__':
    multiprocessing.freeze_support()
    parser = argparse.ArgumentParser()
    parser.add_argument('--trials', type=int, default=3000)
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--seed', type=int, default=20260924)
    parser.add_argument('--out', default='reports/fmz_v2/search_1')
    args = parser.parse_args()
    main(args.trials, args.workers, args.seed, args.out)
