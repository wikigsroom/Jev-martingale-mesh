"""Run the supplied-source adaptation without any live exchange connection."""
from pathlib import Path
from dataclasses import replace
import argparse
import json
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from jevmesh.fmz_config import FMZConfig
from jevmesh.fmz_data import load_fmz_data
from jevmesh.fmz_engine import backtest_fmz

parser = argparse.ArgumentParser()
parser.add_argument('--params', default='reports/fmz_v2/final_parameters.json')
parser.add_argument('--resolution', choices=['1s', '1m'], default='1s')
parser.add_argument('--start', default='2026-03-01')
parser.add_argument('--end', default='2026-09-22')
parser.add_argument('--variant', choices=['selected', 'no_jev', 'no_trend', 'original', 'no_opposite_close'], default='selected')
parser.add_argument('--out', default='reports/fmz_v2/manual_run')
args = parser.parse_args()
cfg = FMZConfig.load(args.params)
if args.variant == 'no_jev': cfg = replace(cfg, jev_action=0)
if args.variant == 'no_trend': cfg = replace(cfg, controller=0)
if args.variant == 'original': cfg = replace(FMZConfig(), controller=0, jev_action=0)
if args.variant == 'no_opposite_close': cfg = replace(cfg, trend_liquidate_opposite=False)
data = load_fmz_data(args.resolution, args.start, args.end, cfg.news_delay_seconds)
result, curve = backtest_fmz(data, cfg, record=True)
out = Path(args.out)
out.parent.mkdir(parents=True, exist_ok=True)
out.with_suffix('.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
curve.to_csv(out.with_suffix('.csv'), index=False)
curve.attrs['decisions'].to_csv(out.parent / (out.name+'_decisions.csv'), index=False)
print(json.dumps(result, indent=2))
