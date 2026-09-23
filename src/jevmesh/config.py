from dataclasses import dataclass, asdict
import json
from pathlib import Path


@dataclass(frozen=True)
class StrategyConfig:
    initial_equity: float = 100.0
    leverage: float = 10.0
    gross_utilization: float = 0.80
    base_notional: float = 75.0
    multiplier: float = 1.35
    max_adds: int = 3
    grid_step: float = 0.004
    take_profit: float = 0.003
    basket_stop: float = 0.12
    basket_take_profit: float = 0.015
    max_cycle_minutes: float = 240.0
    cooldown_minutes: float = 60.0
    equity_halt_drawdown: float = 0.35
    event_threshold: float = 0.80
    event_hold_minutes: float = 30.0
    event_delay_seconds: float = 5.0
    trend_stop: float = 0.015
    vol_stop: float = 0.012
    maker_fee: float = 0.0002
    taker_fee: float = 0.0005
    slippage_bps: float = 2.0
    maintenance_rate: float = 0.004
    liquidation_fee: float = 0.0125
    qty_step: float = 0.001
    min_qty: float = 0.001
    min_notional_pre_change: float = 100.0
    min_notional_post_change: float = 50.0
    min_notional_change_ms: int = 1776162600000  # 2026-04-14 10:30 UTC; conservative end of rollout.
    tick_size: float = 0.10

    def validate(self):
        if self.initial_equity != 100:
            raise ValueError("This experiment fixes initial capital at 100 USDT")
        if not 1 <= self.leverage <= 10:
            raise ValueError("Leverage must be in [1,10]")
        if not 0 < self.gross_utilization <= 1:
            raise ValueError("Invalid gross margin utilization")
        if not 0 <= self.max_adds <= 8 or not 1 <= self.multiplier <= 2:
            raise ValueError("Unsupported martingale inventory limits")
        if not 0 < self.basket_stop < 1 or not 0 < self.equity_halt_drawdown < 1:
            raise ValueError("Loss limits must be fractions in (0,1)")
        if self.grid_step <= 0 or self.take_profit <= 0:
            raise ValueError("Grid and take-profit distances must be positive")
        if min(self.maker_fee, self.taker_fee, self.slippage_bps, self.maintenance_rate, self.liquidation_fee) < 0:
            raise ValueError("Costs and maintenance cannot be negative")
        return self

    def to_dict(self):
        return asdict(self)

    def save(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text(encoding="utf-8"))).validate()
