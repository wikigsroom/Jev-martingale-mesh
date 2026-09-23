"""Evaluate only after a parameter freeze, with no optimization on September."""
from pathlib import Path
from dataclasses import replace
import argparse
import gc
import hashlib
import json
import sys
import numpy as np
import pandas as pd
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from jevmesh.config import StrategyConfig
from jevmesh.dataset import make_dataset, add_signals
from jevmesh.engine import run_backtest, config_vector, check_risk, W, QL, QS, AL, AS, CW, PEAK, MINEQ, LIQ, HALT, STATE_SIZE
from jevmesh.data import write_json


def classification_metrics(probs, labels):
    return {'n': len(labels), 'accuracy': float((probs.argmax(1) == labels).mean()),
            'log_loss': float(-np.log(probs[np.arange(len(labels)), labels].clip(1e-12)).mean()),
            'brier': float(((probs-np.eye(3)[labels])**2).sum(1).mean())}


def daily_returns(curve):
    series = pd.Series(curve.equity.to_numpy(), index=pd.to_datetime(curve.timestamp, unit='ms', utc=True))
    end = series.resample('1D').last()
    first = end.shift(1).fillna(100.)
    return pd.DataFrame({'equity': end, 'profit_usdt': end-first, 'return': end/first-1})


def bootstrap_scenario(daily):
    # Moving-block sampling, not a fitted predictive market model.
    values = daily['return'].to_numpy()
    rng = np.random.default_rng(20260923)
    paths = []
    for _ in range(10000):
        starts = rng.integers(0, len(values), size=10)
        idx = ((starts[:, None]+np.arange(3)) % len(values)).reshape(-1)
        paths.append(100*np.prod(1+values[idx]))
    a = np.asarray(paths)
    return {'method': 'Circular moving blocks of 3 observed holdout days; 10 blocks form a conditional 30-day scenario; 10000 resamples',
            'sample_days': len(values), 'median_final_equity': float(np.median(a)),
            'mean_final_equity': float(a.mean()), 'p05_final_equity': float(np.percentile(a, 5)),
            'p95_final_equity': float(np.percentile(a, 95)), 'fraction_profitable': float((a > 100).mean()),
            'warning': 'These are empirical scenarios, not confidence in future profits. Only 21 days; regime shifts, news collection latency and order-book execution are absent. Fixed lot-size constraints are not re-simulated in resampled paths.'}


def shock_cases(cfg):
    rows = []
    # Diagnostic snapshots, deliberately including asymmetric inventories.
    for ql, qs in [(.006, .002), (.002, .006), (.004, .004)]:
        for jump in [-.5, -.4, -.2, -.1, -.05, .05, .1, .2, .4, .5]:
            s = np.zeros(STATE_SIZE)
            s[W] = s[CW] = s[PEAK] = s[MINEQ] = 100.
            s[QL], s[QS], s[AL], s[AS] = ql, qs, 60000., 60000.
            price = 60000*(1+jump)
            check_risk(s, price, price, 1788220800000, config_vector(cfg))
            eq = s[W]+s[QL]*(price-s[AL])+s[QS]*(s[AS]-price)
            rows.append({'long_qty': ql, 'short_qty': qs, 'instantaneous_gap_pct': jump*100,
                         'liquidation': bool(s[LIQ]), 'halted': bool(s[HALT]), 'remaining_equity': float(eq),
                         'note': 'Synthetic inventory diagnostic, not a claim that the selected strategy held this snapshot'})
    return rows


def evaluate():
    out = ROOT / 'reports'
    freeze = json.loads((out / 'selection_freeze.json').read_text(encoding='utf-8'))
    param = out / 'final_parameters.json'
    if hashlib.sha256(param.read_bytes()).hexdigest() != freeze['parameter_sha256']:
        raise ValueError('Parameters differ from selection freeze')
    if hashlib.sha256((ROOT / 'src/jevmesh/engine.py').read_bytes()).hexdigest() != freeze['engine_sha256']:
        raise ValueError('Engine changed after freeze; selection needs an auditable rerun')
    if hashlib.sha256((ROOT / 'models/event_head.pt').read_bytes()).hexdigest() != freeze['model_head_sha256']:
        raise ValueError('Financial head changed after parameter freeze')
    cfg = StrategyConfig.load(param)
    active_cfg = StrategyConfig.load(out / 'active_event_parameters.json')
    active_freeze = json.loads((out / 'active_event_freeze.json').read_text(encoding='utf-8'))
    if hashlib.sha256((out / 'active_event_parameters.json').read_bytes()).hexdigest() != active_freeze['parameter_sha256']:
        raise ValueError('Active event candidate changed after freeze')
    results = {'parameter_sha256': freeze['parameter_sha256'], 'holdout': {}, 'full_period': {}, 'stress_holdout': {}}
    hold = make_dataset('1s', 'nanojev', start='2026-09-01', end='2026-09-22')
    for kind in ['nanojev', 'none', 'rules', 'original_nanojev', 'active_event_nanojev']:
        hold['signals'] = add_signals(hold['times'], 'nanojev' if kind == 'active_event_nanojev' else kind, cfg.event_delay_seconds)
        run_cfg = active_cfg if kind == 'active_event_nanojev' else (cfg if kind != 'rules' else replace(cfg, event_threshold=.8))
        r, curve = run_backtest(hold, run_cfg, record=True)
        results['holdout'][kind] = r
        curve.to_csv(out / f'holdout_{kind}_equity.csv', index=False)
        if kind == 'nanojev':
            daily = daily_returns(curve)
            daily.to_csv(out / 'holdout_daily.csv')
            results['conditional_30d_scenario'] = bootstrap_scenario(daily)
        print(json.dumps({'period': 'holdout_1s', 'variant': kind, **r}), flush=True)
    hold['signals'] = add_signals(hold['times'], 'nanojev', cfg.event_delay_seconds)
    stresses = {
        'double_fees': replace(cfg, maker_fee=cfg.maker_fee*2, taker_fee=cfg.taker_fee*2),
        'slippage_10bps': replace(cfg, slippage_bps=10),
        'maintenance_1pct': replace(cfg, maintenance_rate=.01),
        'latency_30s': replace(cfg, event_delay_seconds=30),
        'latency_60s': replace(cfg, event_delay_seconds=60),
        'latency_300s': replace(cfg, event_delay_seconds=300),
    }
    for name, scfg in stresses.items():
        hold['signals'] = add_signals(hold['times'], 'nanojev', scfg.event_delay_seconds)
        results['stress_holdout'][name] = run_backtest(hold, scfg)
        print(json.dumps({'stress': name, 'return_pct': results['stress_holdout'][name]['return_pct']}), flush=True)
    hold['signals'] = add_signals(hold['times'], 'nanojev', cfg.event_delay_seconds)
    for mode, name in [(1, 'intrasecond_high_first'), (2, 'intrasecond_low_first')]:
        results['stress_holdout'][name] = run_backtest(hold, cfg, path_mode=mode)
    del hold, curve
    gc.collect()
    minute = make_dataset('1m', 'nanojev')
    results['actual_1m_mark_check'] = run_backtest(minute, cfg)
    results['actual_1m_mark_holdout'] = run_backtest(minute, cfg, start='2026-09-01')
    del minute
    print(json.dumps({'stage': 'loading_full_1s', 'days': 205}), flush=True)
    full = make_dataset('1s', 'nanojev')
    for kind in ['nanojev', 'none', 'rules', 'original_nanojev', 'active_event_nanojev']:
        full['signals'] = add_signals(full['times'], 'nanojev' if kind == 'active_event_nanojev' else kind, cfg.event_delay_seconds)
        run_cfg = active_cfg if kind == 'active_event_nanojev' else (cfg if kind != 'rules' else replace(cfg, event_threshold=.8))
        r, curve = run_backtest(full, run_cfg, record=True)
        results['full_period'][kind] = r
        curve.to_csv(out / f'full_{kind}_equity.csv', index=False)
        if kind == 'nanojev':
            day = daily_returns(curve)
            day.to_csv(out / 'full_daily.csv')
            monthly = day.groupby(day.index.strftime('%Y-%m')).agg(final_equity=('equity', 'last'), net_profit=('profit_usdt', 'sum'))
            monthly.to_csv(out / 'monthly_continuous.csv')
            results['monthly_continuous'] = monthly.reset_index().to_dict('records')
        print(json.dumps({'period': 'full_1s', 'variant': kind, **r}), flush=True)
    del full, curve
    events = pd.read_json(ROOT / 'data/processed/event_predictions.jsonl', lines=True)
    hold_events = events[events.decision_ms >= int(pd.Timestamp('2026-09-01', tz='UTC').timestamp()*1000)]
    probs = hold_events[['p_up', 'p_range', 'p_down']].to_numpy()
    labels = hold_events.label.to_numpy(dtype=int)
    results['model_holdout'] = classification_metrics(probs, labels)
    fit = events[events.decision_ms < int(pd.Timestamp('2026-06-01', tz='UTC').timestamp()*1000)]
    prior = np.bincount(fit.label.to_numpy(dtype=int), minlength=3)/len(fit)
    results['prior_baseline_holdout'] = classification_metrics(np.tile(prior, (len(labels), 1)), labels)
    results['event_signal_counts'] = {'total': len(events), 'holdout': len(hold_events),
        'above_threshold_total': int((events.p_breakout >= cfg.event_threshold).sum()),
        'above_threshold_holdout': int((hold_events.p_breakout >= cfg.event_threshold).sum())}
    results['synthetic_gap_diagnostics'] = shock_cases(cfg)
    write_json(out / 'evaluation.json', results)
    print(json.dumps({'done': True, 'result_file': str(out / 'evaluation.json')}), flush=True)


def supplemental():
    out = ROOT / 'reports'
    results = json.loads((out / 'evaluation.json').read_text(encoding='utf-8'))
    cfg = StrategyConfig.load(out / 'active_event_parameters.json')
    freeze = json.loads((out / 'active_event_freeze.json').read_text(encoding='utf-8'))
    assert hashlib.sha256((out / 'active_event_parameters.json').read_bytes()).hexdigest() == freeze['parameter_sha256']
    hold = make_dataset('1s', 'none', start='2026-09-01', end='2026-09-22')
    r, curve = run_backtest(hold, cfg, record=True)
    results['holdout']['active_event_none'] = r
    curve.to_csv(out / 'holdout_active_event_none_equity.csv', index=False)
    results['active_event_stress_holdout'] = {}
    for delay in [30, 60, 300]:
        hold['signals'] = add_signals(hold['times'], 'nanojev', delay)
        r = run_backtest(hold, replace(cfg, event_delay_seconds=delay))
        results['active_event_stress_holdout'][f'latency_{delay}s'] = r
    del hold, curve
    gc.collect()
    data = make_dataset('1s', 'none')
    r, curve = run_backtest(data, cfg, record=True)
    results['full_period']['active_event_none'] = r
    curve.to_csv(out / 'full_active_event_none_equity.csv', index=False)
    results['synthetic_gap_diagnostics'] = shock_cases(StrategyConfig.load(out / 'final_parameters.json'))
    assert any(x['liquidation'] for x in results['synthetic_gap_diagnostics'])
    results['supplemental_scope'] = 'Fixed-parameter no-news ablation for active candidate and latency stress; no tuning after holdout'
    write_json(out / 'evaluation.json', results)
    print(json.dumps({'active_no_news_holdout': results['holdout']['active_event_none'],
                      'synthetic_liquidation_cases': sum(x['liquidation'] for x in results['synthetic_gap_diagnostics'])}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--supplemental', action='store_true')
    args = parser.parse_args()
    if not args.supplemental:
        evaluate()
    supplemental()
