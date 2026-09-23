"""Retain a candidate only if both pre-September segments survive higher costs."""
from pathlib import Path
from dataclasses import replace
import datetime as dt
import hashlib
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from jevmesh.fmz_config import FMZConfig
from jevmesh.fmz_data import load_fmz_data
from jevmesh.fmz_engine import backtest_fmz


def write(path, data):
    Path(path).write_text(json.dumps(data, indent=2), encoding='utf-8')


def main():
    out = ROOT / 'reports/fmz_v2'
    rows = json.loads((out / 'confirmed_candidates.json').read_text(encoding='utf-8'))
    original = json.loads((out / 'selection_freeze.json').read_text(encoding='utf-8'))
    candidates = [r for r in rows if r['eligible_1s'] and r['parameters']['controller'] in [1, 2]
                  and r['parameters']['jev_action'] > 0]
    protocol = {'created_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
        'reason': 'The initially selected candidate failed fixed-parameter cost stresses. This second selection round uses March-August only, but September had already been observed and is not a fresh holdout.',
        'trial_ids': [r['trial'] for r in candidates],
        'stress': 'Double maker/taker fees AND 10bps market-order slippage simultaneously',
        'eligibility': 'Positive fit and validation under base costs and combined stress, no halt/liquidation. Then confirm continuous March-August.',
        'ranking': 'Worst of fit/validation stressed return minus 0.5 drawdown, among post-TP survivors first; otherwise continuous hybrid survivors.'}
    write(out / 'cost_screen_protocol.json', protocol)
    print(json.dumps({'loading': 'pre-September 1s cost screen', 'trials': protocol['trial_ids']}), flush=True)
    data = load_fmz_data('1s', end='2026-09-01')
    checked = []
    for row in candidates:
        cfg = FMZConfig(**row['parameters'])
        stressed = replace(cfg, maker_fee=cfg.maker_fee*2, taker_fee=cfg.taker_fee*2, slippage_bps=10)
        copy = dict(row)
        for key, kwargs in [('stress_validation', {'start':'2026-07-01'}), ('stress_fit', {'end':'2026-07-01'})]:
            r = backtest_fmz(data, stressed, **kwargs)
            copy[key] = r
            print(json.dumps({'trial': row['trial'], 'stage': key, 'return_pct': r['return_pct'],
                             'dd': r['max_drawdown_pct'], 'halted': r['halted']}), flush=True)
        copy['cost_eligible'] = all(copy[k]['return_pct'] > 0 and not copy[k]['halted']
            and not copy[k]['liquidations'] for k in ['stress_fit', 'stress_validation'])
        copy['cost_score'] = min(copy[k]['return_pct']-.5*copy[k]['max_drawdown_pct']
                                for k in ['stress_fit', 'stress_validation'])
        if copy['cost_eligible']:
            copy['continuous_base'] = backtest_fmz(data, cfg)
            copy['continuous_stress'] = backtest_fmz(data, stressed)
            copy['cost_eligible'] = all(copy[k]['return_pct'] > 0 and not copy[k]['halted']
                and not copy[k]['liquidations'] for k in ['continuous_base', 'continuous_stress'])
        checked.append(copy)
        write(out / 'cost_screen.json', checked)
    survivors = sorted([r for r in checked if r['cost_eligible']],
        key=lambda r: (r['parameters']['controller']==1, r['cost_score']), reverse=True)
    if not survivors:
        print(json.dumps({'survivors': 0, 'conclusion': 'No higher-cost eligible candidate; retain and disclose the original cost-fragile result'}), flush=True)
        return
    best = survivors[0]
    cfg = FMZConfig(**best['parameters'])
    cfg.save(out / 'robust_parameters.json')
    freeze = dict(original)
    freeze['created_at_utc'] = dt.datetime.now(dt.timezone.utc).isoformat()
    freeze['selection_protocol'] = protocol
    freeze['supersedes_base_cost_winner_trial'] = original['artifacts']['selected']['trial']
    freeze['artifacts'] = {'selected': {'trial': best['trial'], 'file': 'robust_parameters.json',
        'parameter_sha256': hashlib.sha256((out / 'robust_parameters.json').read_bytes()).hexdigest(),
        'score_1s': best['score_1s'], 'cost_score': best['cost_score'], 'fit_1s': best['fit_1s'],
        'validation_1s': best['validation_1s'], 'pre_september_continuous_1s': best['continuous_base'],
        'pre_september_stress_1s': best['continuous_stress'],
        'pre_september_no_jev_1s': backtest_fmz(data, replace(cfg, jev_action=0))}}
    write(out / 'robust_selection_freeze.json', freeze)
    print(json.dumps({'survivors': len(survivors), 'selected_trial': best['trial'],
        'pre_september_base': best['continuous_base']['return_pct'],
        'pre_september_stress': best['continuous_stress']['return_pct']}), flush=True)


if __name__ == '__main__':
    main()
