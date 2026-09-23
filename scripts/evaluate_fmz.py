"""Fixed-parameter second-resolution evaluation, ablations and adverse scenarios."""
from pathlib import Path
from dataclasses import replace
import datetime as dt
import gc
import hashlib
import json
import sys
import time
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from jevmesh.fmz_config import FMZConfig
from jevmesh.fmz_data import load_fmz_data, add_news
from jevmesh.fmz_engine import backtest_fmz, fmz_risk, fmz_vectors, FMZ_STATE_SIZE
from jevmesh.engine import W, QL, QS, AL, AS, CW, PEAK, MINEQ, LIQ, HALT, equity

OUT = ROOT / 'reports/fmz_v2'


def write(path, value):
    Path(path).write_text(json.dumps(value, indent=2), encoding='utf-8')


def daily_equity(curve):
    s = pd.Series(curve.equity.to_numpy(), index=pd.to_datetime(curve.timestamp, unit='ms', utc=True))
    end = s.resample('1D').last()
    start = end.shift(1).fillna(100.)
    return pd.DataFrame({'equity': end, 'profit_usdt': end-start, 'return': end/start-1})


def conditional_scenario(daily):
    values = daily['return'].to_numpy()
    rng = np.random.default_rng(20260924)
    starts = rng.integers(0, len(values), size=(10000, 10))
    indices = ((starts[:, :, None]+np.arange(3)) % len(values)).reshape(10000, 30)
    paths = 100*np.cumprod(1+values[indices], axis=1)
    finals = paths[:, -1]
    peaks = np.maximum.accumulate(np.column_stack([np.full(10000, 100.), paths]), axis=1)[:, 1:]
    dd = (1-paths/peaks).max(axis=1)
    return {'method': '10000 circular 3-day moving-block resamples of July-August fixed-parameter daily returns; 30 days per scenario',
        'sample_days': len(values), 'median_final_equity': float(np.median(finals)),
        'mean_final_equity': float(finals.mean()), 'p05_final_equity': float(np.quantile(finals, .05)),
        'p95_final_equity': float(np.quantile(finals, .95)),
        'empirical_profitable_fraction': float((finals > 100).mean()),
        'p95_drawdown_pct': float(np.quantile(dd, .95)*100),
        'warning': 'Sample-conditioned scenarios, not forecasts or a confidence interval. July-August participated in parameter selection. No re-simulation of lot constraints, order states, regime changes or latency; future profit probability is not identified.'}


def shocks(cfg):
    p, q = fmz_vectors(cfg)
    rows = []
    for long_qty, short_qty in [(.005, .001), (.001, .005), (.003, .003)]:
        for gap in [-.5, -.4, -.2, -.1, -.05, .05, .1, .2, .4, .5]:
            s = np.zeros(FMZ_STATE_SIZE)
            s[W] = s[CW] = s[PEAK] = s[MINEQ] = 100.
            s[QL], s[QS], s[AL], s[AS] = long_qty, short_qty, 60000., 60000.
            price = 60000*(1+gap)
            fmz_risk(s, price, price, 1788220800000, p, q)
            rows.append({'long_qty': long_qty, 'short_qty': short_qty, 'gap_pct': gap*100,
                'liquidation': bool(s[LIQ]), 'halted': bool(s[HALT]), 'equity': float(equity(s, price)),
                'scope': 'Synthetic asymmetric inventory diagnostic, not an observed strategy position'})
    assert any(x['liquidation'] for x in rows), 'The stress suite must actually exercise liquidation'
    return rows


def neighbors(cfg):
    result = {}
    for field in ['base_spacing', 'profit_target', 'trend_enter', 'directional_stop', 'directional_trail']:
        for factor in [.9, 1.1]:
            result[f'{field}_x{factor}'] = replace(cfg, **{field: getattr(cfg, field)*factor})
    for delta in [-.05, .05]:
        result[f'ratio_{delta:+.2f}'] = replace(cfg, ratio=max(1., min(2., cfg.ratio+delta)))
    for delta in [-.02, .02]:
        result[f'jev_threshold_{delta:+.2f}'] = replace(cfg, jev_breakout_threshold=cfg.jev_breakout_threshold+delta)
    for factor in [.5, 2.]:
        result[f'jev_hold_x{factor}'] = replace(cfg, jev_hold_seconds=cfg.jev_hold_seconds*factor)
    for seconds in [2., 5.]:
        result[f'poll_{seconds:g}s'] = replace(cfg, poll_seconds=seconds)
    result['smaller_budget'] = replace(cfg, gross_utilization=cfg.gross_utilization*.9)
    result['one_fewer_add'] = replace(cfg, max_adds=max(0, cfg.max_adds-1))
    return result


def main():
    freeze = json.loads((OUT / 'selection_freeze.json').read_text(encoding='utf-8'))
    for name, expected in freeze['sources'].items():
        assert hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == expected, f'Changed after freeze: {name}'
    configurations = {}
    for name, artifact in freeze['artifacts'].items():
        path = OUT / artifact['file']
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact['parameter_sha256']
        configurations[name] = FMZConfig.load(path)
    cfg = configurations['selected']
    results = {'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'selection_freeze_sha256': hashlib.sha256((OUT / 'selection_freeze.json').read_bytes()).hexdigest(),
        'full_1s': {}, 'september_1s': {}, 'stress_full_1s': {}, 'neighborhood_1m': {}}
    result_path = OUT / 'evaluation.json'
    started = time.perf_counter()

    def save_run(data, name, candidate, section, record=False, **kwargs):
        result = backtest_fmz(data, candidate, record=record, **kwargs)
        if record:
            metrics, curve = result
            curve.to_csv(OUT / f'{section}_{name}_equity.csv', index=False)
            curve.attrs['decisions'].to_csv(OUT / f'{section}_{name}_decisions.csv', index=False)
        else:
            metrics, curve = result, None
        results[section][name] = metrics
        write(result_path, results)
        print(json.dumps({'section': section, 'variant': name, 'return_pct': metrics['return_pct'],
            'dd': metrics['max_drawdown_pct'], 'liquidations': metrics['liquidations'],
            'elapsed_s': round(time.perf_counter()-started, 1)}), flush=True)
        return metrics, curve

    print(json.dumps({'loading': 'full 205 days of actual 1s OHLC'}), flush=True)
    data = load_fmz_data('1s', delay=cfg.news_delay_seconds)
    variants = dict(configurations)
    variants.update({'no_jev': replace(cfg, jev_action=0), 'no_trend': replace(cfg, controller=0),
        'no_opposite_close': replace(cfg, trend_liquidate_opposite=False),
        'original_adapted': replace(FMZConfig(), controller=0, jev_action=0)})
    if 'event_flatten' in configurations:
        variants['event_flatten_no_jev'] = replace(configurations['event_flatten'], jev_action=0)
    for name, candidate in variants.items():
        _, curve = save_run(data, name, candidate, 'full_1s', True)
        if name == 'selected':
            daily = daily_equity(curve)
            daily.to_csv(OUT / 'daily_continuous.csv')
            monthly = daily.groupby(daily.index.strftime('%Y-%m')).agg(final_equity=('equity', 'last'), net_profit=('profit_usdt', 'sum'))
            monthly['start_equity'] = monthly.final_equity.shift(1).fillna(100.)
            monthly['return_pct'] = 100*monthly.net_profit/monthly.start_equity
            monthly.to_csv(OUT / 'monthly_continuous.csv')
            results['monthly_continuous'] = monthly.reset_index(names='month').to_dict('records')
            c = curve.copy()
            c.attrs = {}
            states = c.regime.value_counts()
            results['observed_minute_states'] = {str(int(k)): int(v) for k, v in states.items()}
        del curve
    for name in configurations:
        save_run(data, name, configurations[name], 'september_1s', True, start='2026-09-01')
    results['validation_diagnostic'] = {}
    _, curve = save_run(data, 'selected', cfg, 'validation_diagnostic', True,
                         start='2026-07-01', end='2026-09-01')
    results['conditional_30day_scenario'] = conditional_scenario(daily_equity(curve))
    del curve
    stresses = {
        'double_fees': replace(cfg, maker_fee=cfg.maker_fee*2, taker_fee=cfg.taker_fee*2),
        'all_fills_taker_fee': replace(cfg, maker_fee=cfg.taker_fee),
        'slippage_10bps': replace(cfg, slippage_bps=10),
        'combined_double_fees_10bps': replace(cfg, maker_fee=cfg.maker_fee*2, taker_fee=cfg.taker_fee*2, slippage_bps=10),
        'maintenance_1pct': replace(cfg, maintenance_rate=.01),
        'news_delay_30s': replace(cfg, news_delay_seconds=30),
        'news_delay_60s': replace(cfg, news_delay_seconds=60),
        'news_delay_300s': replace(cfg, news_delay_seconds=300),
        'quote_refresh_2s': replace(cfg, poll_seconds=2),
        'quote_refresh_5s': replace(cfg, poll_seconds=5),
        'intrasecond_high_first': replace(cfg, intrabar_mode=1),
        'intrasecond_low_first': replace(cfg, intrabar_mode=2),
    }
    current_delay = cfg.news_delay_seconds
    for name, candidate in stresses.items():
        if current_delay != candidate.news_delay_seconds:
            data['news'] = add_news(data['times'], candidate.news_delay_seconds)
            current_delay = candidate.news_delay_seconds
        save_run(data, name, candidate, 'stress_full_1s')
    del data
    gc.collect()
    minute = load_fmz_data('1m', delay=cfg.news_delay_seconds)
    results['actual_1m_mark'] = {}
    for name, candidate in configurations.items():
        save_run(minute, name, candidate, 'actual_1m_mark')
    for name, candidate in neighbors(cfg).items():
        results['neighborhood_1m'][name] = {
            'changed': {k: v for k, v in candidate.to_dict().items() if v != cfg.to_dict()[k]},
            'pre_september': backtest_fmz(minute, candidate, end='2026-09-01'),
            'full_period': backtest_fmz(minute, candidate)}
    results['synthetic_gap_diagnostics'] = shocks(cfg)
    previous = json.loads((ROOT / 'reports/evaluation.json').read_text(encoding='utf-8'))
    results['model_september'] = previous['model_holdout']
    results['model_prior_baseline_september'] = previous['prior_baseline_holdout']
    results['completed_at_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
    write(result_path, results)
    print(json.dumps({'completed': True, 'elapsed_s': round(time.perf_counter()-started, 1),
        'selected_return_pct': results['full_1s']['selected']['return_pct']}), flush=True)


if __name__ == '__main__':
    main()
