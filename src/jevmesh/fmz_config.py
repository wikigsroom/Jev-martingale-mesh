"""BTC adaptation of the supplied anchored FMZ grid, with explicit controllers."""
from dataclasses import dataclass, asdict
from pathlib import Path
import json
from .config import StrategyConfig


@dataclass(frozen=True)
class FMZConfig:
    # Classical FMZ strategy parameters, in the source's units.
    base_spacing: float = .05
    base_amount_rate: float = .03
    base_amount_min: float = 20.
    ratio: float = 1.25
    profit_target: float = .05
    warning_index: int = 3
    max_loss_notional_multiple: float = 3.
    max_adds: int = 5
    leverage: float = 10.
    gross_utilization: float = .85
    basket_stop: float = .4
    account_drawdown_stop: float = .5
    stop_cooldown_minutes: float = 60.
    reentry_delay_seconds: float = 1.
    poll_seconds: float = 1.
    # 0=classical, 1=post-TP switch, 2=continuous hybrid, 3=range only, 4=trend only.
    controller: int = 2
    ema_fast_minutes: int = 60
    ema_slow_minutes: int = 720
    trend_enter: float = .003
    trend_exit_fraction: float = .3
    trend_confirm_minutes: float = 5.
    trend_liquidate_opposite: bool = True
    allow_countertrend_add: bool = False
    directional_stop: float = .025
    directional_trail: float = .035
    # 0=off, 1=flatten/pause, 2=directional veto/close, 3=stop additions/reentry.
    jev_action: int = 1
    jev_breakout_threshold: float = .4
    jev_direction_threshold: float = .1
    jev_hold_seconds: float = 60.
    news_delay_seconds: float = 5.
    # Matching and cost assumptions remain explicit and never optimized away.
    maker_fee: float = .0002
    taker_fee: float = .0005
    slippage_bps: float = 2.
    maintenance_rate: float = .004
    liquidation_fee: float = .0125
    intrabar_mode: int = 0

    def validate(self):
        assert 1 <= self.leverage <= 10
        assert 0 < self.gross_utilization <= 1
        assert .001 <= self.base_spacing <= .2 and 0 < self.profit_target <= .2
        assert 1 <= self.ratio <= 2 and 0 <= self.max_adds <= 12
        assert 0 < self.basket_stop < 1 and 0 < self.account_drawdown_stop < 1
        assert self.controller in range(5) and self.jev_action in range(4)
        assert self.ema_fast_minutes < self.ema_slow_minutes
        assert self.news_delay_seconds >= 5  # Stored model input was constructed at +5s.
        assert self.reentry_delay_seconds >= 1
        assert self.poll_seconds >= 1
        return self

    def base_config(self):
        return StrategyConfig(leverage=self.leverage, gross_utilization=self.gross_utilization,
            basket_stop=self.basket_stop, equity_halt_drawdown=self.account_drawdown_stop,
            cooldown_minutes=self.stop_cooldown_minutes, maker_fee=self.maker_fee,
            taker_fee=self.taker_fee, slippage_bps=self.slippage_bps,
            maintenance_rate=self.maintenance_rate, liquidation_fee=self.liquidation_fee)

    def to_dict(self):
        return asdict(self)

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding='utf-8')

    @classmethod
    def load(cls, path):
        return cls(**json.loads(Path(path).read_text(encoding='utf-8'))).validate()
