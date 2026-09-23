import argparse
import json
from pathlib import Path
from .config import StrategyConfig
from .dataset import make_dataset
from .engine import run_backtest


def main():
    parser = argparse.ArgumentParser(description='BTCUSDT perpetual research and paper replay; no live orders')
    sub = parser.add_subparsers(dest='command', required=True)
    back = sub.add_parser('backtest', help='Simulate the grid, event stops and mark liquidation')
    back.add_argument('--params', default='reports/final_parameters.json')
    back.add_argument('--resolution', choices=['1s', '1m'], default='1s')
    back.add_argument('--signals', choices=['nanojev', 'none', 'rules', 'original_nanojev'], default='nanojev')
    back.add_argument('--start', default='2026-03-01')
    back.add_argument('--end', default='2026-09-22', help='Exclusive UTC date')
    back.add_argument('--out', default='reports/manual_backtest')
    back.add_argument('--path', type=int, choices=[0, 1, 2], default=0)
    args = parser.parse_args()
    cfg = StrategyConfig.load(args.params)
    data = make_dataset(args.resolution, args.signals, cfg.event_delay_seconds, args.start, args.end)
    result, curve = run_backtest(data, cfg, path_mode=args.path, record=True)
    path = Path(args.out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.with_suffix('.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    curve.to_csv(path.with_suffix('.csv'), index=False)
    print(json.dumps(result, indent=2))
