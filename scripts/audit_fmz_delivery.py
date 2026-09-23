"""Check that delivered files reconcile to the frozen simulation, without rerunning it."""
from pathlib import Path
import hashlib
import json
import re
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/fmz_v2'


def main():
    freeze = json.loads((OUT / 'selection_freeze.json').read_text(encoding='utf-8'))
    results = json.loads((OUT / 'evaluation.json').read_text(encoding='utf-8'))
    checks = {}
    for path, expected in freeze['sources'].items():
        assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == expected, path
    for artifact in freeze['artifacts'].values():
        assert hashlib.sha256((OUT / artifact['file']).read_bytes()).hexdigest() == artifact['parameter_sha256']
    checks['frozen_sources_and_parameters'] = True
    assert hashlib.sha256((OUT / 'selection_freeze.json').read_bytes()).hexdigest() == results['selection_freeze_sha256']
    for name, metrics in results['full_1s'].items():
        curve = pd.read_csv(OUT / f'full_1s_{name}_equity.csv')
        decisions = pd.read_csv(OUT / f'full_1s_{name}_decisions.csv')
        assert len(curve) == 295200
        assert np.all(np.diff(curve.timestamp.to_numpy()) == 60000)
        assert abs(curve.equity.iloc[-1]-metrics['final_equity']) < 1e-8
        assert abs(curve.wallet.iloc[-1]-metrics['final_equity']) < 1e-8
        assert curve.long_qty.iloc[-1] == curve.short_qty.iloc[-1] == 0
        assert int(curve.fills.sum()) == metrics['fills']
        assert 0 <= metrics['fills']-decisions.fill_count.sum() <= 2
        assert decisions.fee.sum() <= metrics['fees']+1e-8
        assert decisions.timestamp.is_monotonic_increasing
        eq = curve.equity.to_numpy()
        recorded_dd = 100*(1-eq/np.maximum.accumulate(np.maximum(eq, 100))).max()
        assert recorded_dd <= metrics['max_drawdown_pct']+1e-7
        checks[f'curve_and_trace_{name}'] = True
    monthly = pd.read_csv(OUT / 'monthly_continuous.csv')
    assert abs(monthly.net_profit.sum()-results['full_1s']['selected']['net_profit']) < 1e-8
    checks['continuous_monthly_profit_reconciles'] = True
    candidates = json.loads((ROOT / freeze['search'] / 'candidate_parameters.json').read_text(encoding='utf-8'))
    assert len({json.dumps(c, sort_keys=True) for c in candidates}) == len(candidates) == 3000
    checks['3000_unique_candidates'] = True
    events = pd.read_json(ROOT / 'data/processed/event_predictions.jsonl', lines=True)
    assert (events.input_last_market_ms < events.decision_ms).all()
    assert np.allclose(events[['p_up', 'p_range', 'p_down']].sum(axis=1), 1., atol=2e-5)
    checks['event_inputs_precede_decisions'] = True
    for path in [OUT / 'final_report_zh.md', OUT / 'source_review/review_zh.md']:
        links = re.findall(r'\]\(([^)]+)\)', path.read_text(encoding='utf-8'))
        for link in links:
            if not link.startswith(('https:', 'http:', '#')):
                assert (path.parent / link).exists(), link
    checks['report_local_links_exist'] = True
    manifest = json.loads((OUT / 'source_review/source_manifest.json').read_text(encoding='utf-8'))
    assert hashlib.sha256(Path(manifest['source']).read_bytes()).hexdigest() == manifest['sha256']
    checks['user_original_source_unchanged'] = True
    assert any(x['liquidation'] for x in results['synthetic_gap_diagnostics'])
    checks['liquidation_exercised_by_adverse_cases'] = True
    receipt = {'passed': True, 'checks': checks,
        'report_sha256': hashlib.sha256((OUT / 'final_report_zh.md').read_bytes()).hexdigest(),
        'evaluation_sha256': hashlib.sha256((OUT / 'evaluation.json').read_bytes()).hexdigest()}
    (OUT / 'delivery_audit.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
