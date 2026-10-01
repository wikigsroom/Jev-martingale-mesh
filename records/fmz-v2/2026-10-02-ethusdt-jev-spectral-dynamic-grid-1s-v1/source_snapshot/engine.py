from __future__ import annotations

from dataclasses import asdict, dataclass
import sys
import time

import numpy as np
import pandas as pd
from numba import njit

from .data import ROOT, millis

sys.path.insert(0, str(ROOT / "src"))
from jevmesh import engine as accounting
from jevmesh import fmz_engine as execution
from jevmesh.fmz_config import FMZConfig

UPDATES, EVENT_ROWS, EVENT_USED, EVENT_BLOCKS, EVENT_CLOSES, GRID_SUM, GRID_MIN, GRID_MAX = range(77, 85)
EVENT_GRID_UPDATES, EVENT_GRID_DELAY_SUM, EVENT_GRID_DELAY_MAX, EVENT_GRID_NOCHANGE = range(85, 89)
DYNAMIC_SIZE = 89
CURVE_COLUMNS = ["timestamp", "equity", "wallet", "long_qty", "short_qty", "regime", "fills",
                 "spacing", "take_profit", "multiplier", "max_adds", "sigma30", "event_logvol",
                 "event_direction", "martingale_adds", "event_closes"]


@dataclass(frozen=True)
class DynamicConfig:
    name: str = "dynamic"
    mode: int = 3
    vol_multiplier: float = 2.0
    minimum_spacing: float = .004
    maximum_spacing: float = .06
    fixed_spacing: float = .015
    profit_fraction: float = .8
    multiplier: float = 1.35
    max_adds: int = 3
    trend_entry: float = 2.0
    trend_confirmation_minutes: float = 15.0
    trend_exit_fraction: float = .5
    event_strength: float = 1.0
    event_hold_seconds: float = 1800.0
    event_delay_seconds: float = 5.0
    event_direction_gate: bool = False
    event_block_logvol: float = .25
    parameter_hysteresis: float = .15
    requote_seconds: float = 300.0
    initial_equity: float = 1000.0
    base_amount_min: float = 130.0
    base_amount_rate: float = .1
    max_leverage: float = 10.0
    reentry_seconds: float = 30.0
    maker_fee: float = .0002
    taker_fee: float = .0005
    slippage_bps: float = 2.0

    def validate(self):
        if self.mode not in range(4) or not 1.0 < self.multiplier <= 2.0 or not 1 <= self.max_adds <= 6:
            raise ValueError("An actual finite martingale and valid control mode are required")
        if not 0 < self.minimum_spacing <= self.fixed_spacing <= self.maximum_spacing <= .2:
            raise ValueError("Invalid grid spacing bounds")
        if self.initial_equity != 1000.0 or self.base_amount_min < 130.0 or self.event_delay_seconds < 5:
            raise ValueError("Experiment capital, order minimum, or event timing changed")
        if self.profit_fraction <= 0 or self.vol_multiplier <= 0 or self.event_hold_seconds <= 0 or self.event_block_logvol < 0:
            raise ValueError("Invalid adaptation parameters")
        return self

    def to_dict(self):
        return asdict(self)

    def vectors(self):
        self.validate()
        config = FMZConfig(initial_equity=self.initial_equity, base_spacing=self.fixed_spacing,
                           base_amount_min=self.base_amount_min, min_trade_notional=130.0,
                           base_amount_rate=self.base_amount_rate, ratio=self.multiplier,
                           profit_target=self.fixed_spacing * self.profit_fraction,
                           max_adds=self.max_adds, leverage=self.max_leverage, gross_utilization=1.0,
                           account_drawdown_stop=0.0, basket_stop=.999999,
                           max_loss_notional_multiple=1e9, warning_index=99,
                           stop_cooldown_minutes=0.0, reentry_delay_seconds=self.reentry_seconds,
                           poll_seconds=1.0, controller=0, jev_action=0,
                           maker_fee=self.maker_fee, taker_fee=self.taker_fee, slippage_bps=self.slippage_bps,
                           directional_stop=.05, directional_trail=.05)
        parameters, grid = execution.fmz_vectors(config)
        parameters[28] = .01
        settings = np.array([self.mode, self.vol_multiplier, self.minimum_spacing, self.maximum_spacing,
                             self.fixed_spacing, self.profit_fraction, self.multiplier, self.max_adds,
                             self.trend_entry, self.trend_confirmation_minutes, self.trend_exit_fraction,
                             self.event_strength, self.event_hold_seconds, self.parameter_hysteresis,
                             self.requote_seconds, self.event_direction_gate, self.event_block_logvol], dtype=np.float64)
        return parameters, grid, settings


@njit(cache=True)
def adaptive_parameters(feature, event_logvol, settings, cost_floor):
    if settings[0] == 0:
        return settings[4], max(cost_floor, settings[4] * settings[5]), settings[6], settings[7]
    trend_pressure = min(2.0, abs(feature[2]) / max(settings[8], .5))
    event_factor = np.exp(event_logvol * settings[11])
    shock = max(0.0, feature[1] - 1.5) + max(0.0, event_factor - 1.0)
    spacing = feature[0] * settings[1] * event_factor * (1.0 + .2 * trend_pressure)
    spacing = min(settings[3], max(settings[2], cost_floor, spacing))
    multiplier = 1.0 + (settings[6] - 1.0) / (1.0 + trend_pressure + shock * 2.0)
    additions = max(1.0, settings[7] - int(shock >= .5) - int(trend_pressure >= 1.5))
    return spacing, max(cost_floor, spacing * settings[5]), multiplier, additions


@njit(cache=True)
def update_trend(state, trend_z, price, mark, timestamp, parameters, grid, settings):
    current = int(state[execution.REGIME])
    enter = settings[8]
    if current != 0 and current * trend_z >= enter * settings[10]:
        desired = current
    elif abs(trend_z) >= enter:
        desired = 1 if trend_z > 0 else -1
    else:
        desired = 0
    if desired != state[execution.PENDING]:
        state[execution.PENDING] = desired
        state[execution.PENDING_AT] = timestamp
    if desired == current or timestamp - state[execution.PENDING_AT] < settings[9] * 60_000:
        return
    state[execution.REGIME] = desired
    state[execution.SWITCHES] += 1
    execution.clear_adds(state)
    if desired != 0:
        opposite_quantity = state[accounting.QS] if desired == 1 else state[accounting.QL]
        if opposite_quantity > 0:
            execution.close_side(state, -desired, price, timestamp, parameters, grid, False, 1)
            state[execution.CONTRA_CLOSES] += 1
        state[execution.BEST_L if desired == 1 else execution.BEST_S] = mark


@njit(cache=True)
def install_quotes(state, side, price, mark, timestamp, parameters, grid, allowed):
    quantity_index = accounting.QL if side == 1 else accounting.QS
    if state[quantity_index] <= 0:
        return
    order_qty_index = execution.OQ_L if side == 1 else execution.OQ_S
    order_price_index = execution.OP_L if side == 1 else execution.OP_S
    profit_index = execution.TP_L if side == 1 else execution.TP_S
    ready_index = execution.QUOTE_READY_L if side == 1 else execution.QUOTE_READY_S
    count_index = accounting.NL if side == 1 else accounting.NS
    if not allowed or state[count_index] >= grid[execution.MAX_ADDS]:
        state[order_qty_index] = state[order_price_index] = 0.0
    if timestamp < state[ready_index]:
        return
    average_index = accounting.AL if side == 1 else accounting.AS
    if state[profit_index] == 0:
        profit_price = state[average_index] * (1.0 + side * grid[execution.PROFIT])
        ticks = profit_price / parameters[28]
        state[profit_index] = (np.ceil(ticks) if side == 1 else np.floor(ticks)) * parameters[28]
        state[execution.TP_TIME_L if side == 1 else execution.TP_TIME_S] = timestamp
    if not allowed or state[count_index] >= grid[execution.MAX_ADDS] or state[order_qty_index] > 0:
        return
    last_fill_index = accounting.LL if side == 1 else accounting.LS
    last_qty_index = accounting.OL if side == 1 else accounting.OS
    level = state[last_fill_index] * (1.0 - side * grid[execution.SPACING])
    bound = price * (1.0 - side * grid[execution.SPACING] * .25)
    level = min(level, bound) if side == 1 else max(level, bound)
    ticks = level / parameters[28]
    level = (np.floor(ticks) if side == 1 else np.ceil(ticks)) * parameters[28]
    if level <= 0:
        return
    target = state[last_qty_index] * grid[execution.RATIO] * level
    quantity = execution.fmz_order_qty(target, level, timestamp, parameters, grid)
    other_reserved = (state[execution.OQ_S] * state[execution.OP_S] if side == 1
                      else state[execution.OQ_L] * state[execution.OP_L])
    reserved = quantity * level + other_reserved
    budget = max(0.0, accounting.equity(state, mark) - reserved * parameters[18]) * parameters[1] * parameters[2]
    if (state[accounting.QL] + state[accounting.QS]) * mark + reserved > budget + 1e-9:
        state[accounting.REJECT] += 1
        return
    state[order_qty_index] = quantity
    state[order_price_index] = level
    state[execution.ADD_TIME_L if side == 1 else execution.ADD_TIME_S] = timestamp


@njit(cache=True)
def update_take_profit(state, side, timestamp, parameters, grid):
    quantity_index = accounting.QL if side == 1 else accounting.QS
    if state[quantity_index] <= 0:
        return
    average_index = accounting.AL if side == 1 else accounting.AS
    profit_index = execution.TP_L if side == 1 else execution.TP_S
    time_index = execution.TP_TIME_L if side == 1 else execution.TP_TIME_S
    target = state[average_index] * (1.0 + side * grid[execution.PROFIT])
    ticks = target / parameters[28]
    target = (np.ceil(ticks) if side == 1 else np.floor(ticks)) * parameters[28]
    if abs(target - state[profit_index]) >= parameters[28] * 0.5:
        state[profit_index] = target
        state[time_index] = timestamp


@njit(cache=True, nogil=True)
def dynamic_core(times, bars, funding, minute_index, features, event_times, event_predictions,
                 parameters, original_grid, settings, stride=0, path_mode=0):
    state = np.zeros(DYNAMIC_SIZE)
    state[accounting.W] = state[accounting.PEAK] = state[accounting.MINEQ] = parameters[0]
    state[GRID_MIN] = 1.0
    grid = original_grid.copy()
    curve_size = (len(times) + stride - 1) // stride if stride else 0
    curve = np.empty((curve_size, 16))
    decisions = np.empty((220_000 if stride else 0, 13))
    decision_count = 0
    event_index = 0
    last_minute = -1
    last_requote = 0
    pending_event_at = -1
    event_until = 0.0
    event_logvol = 0.0
    event_direction = 0.0
    recorded_fills = 0.0
    cost_floor = 2.0 * (parameters[18] + parameters[19] + parameters[20] / 10000)
    feature = features[minute_index[0]]
    for index in range(len(times)):
        timestamp = times[index]
        bar = bars[index]
        before_fills = state[accounting.FILLS]
        before_adds = state[accounting.MADDS]
        before_events = state[EVENT_ROWS]
        before_regime = state[execution.REGIME]
        payment = (state[accounting.QS] - state[accounting.QL]) * bar[4] * funding[index]
        state[accounting.W] += payment
        state[accounting.FUND] += payment
        execution.fmz_risk(state, bar[0], bar[4], timestamp, parameters, grid)
        minute_changed = minute_index[index] != last_minute
        if minute_changed:
            last_minute = minute_index[index]
            feature = features[last_minute]
        event_changed = False
        while event_index < len(event_times) and event_times[event_index] <= timestamp:
            if event_times[event_index] >= times[0] and settings[0] >= 2:
                state[EVENT_ROWS] += 1
                prediction = event_predictions[event_index]
                if abs(prediction[0]) + abs(prediction[1]) > 1e-10:
                    event_logvol, event_direction = prediction[0], prediction[1]
                    event_until = timestamp + settings[12] * 1000
                    state[EVENT_USED] += 1
                    event_changed = True
                    pending_event_at = timestamp
            event_index += 1
        remaining = max(0.0, min(1.0, (event_until - timestamp) / (settings[12] * 1000)))
        effective_logvol = event_logvol * remaining
        effective_direction = event_direction * remaining
        event_risk = settings[0] >= 2 and effective_logvol >= settings[16]
        if minute_changed or event_changed:
            spacing, profit, multiplier, additions = adaptive_parameters(feature, effective_logvol, settings, cost_floor)
            relative_change = abs(spacing / grid[execution.SPACING] - 1.0)
            profit_change = abs(profit / grid[execution.PROFIT] - 1.0)
            changed = (relative_change >= settings[13] or profit_change >= settings[13]
                       or additions != grid[execution.MAX_ADDS]
                       or abs(multiplier - grid[execution.RATIO]) >= .1)
            if event_changed and not changed:
                state[EVENT_GRID_NOCHANGE] += 1
            if index == 0 or (changed and timestamp - last_requote >= settings[14] * 1000):
                grid[execution.SPACING], grid[execution.PROFIT] = spacing, profit
                grid[execution.RATIO], grid[execution.MAX_ADDS] = multiplier, additions
                execution.clear_adds(state)
                update_take_profit(state, 1, timestamp, parameters, grid)
                update_take_profit(state, -1, timestamp, parameters, grid)
                state[execution.QUOTE_READY_L] = max(state[execution.QUOTE_READY_L], timestamp + 1000)
                state[execution.QUOTE_READY_S] = max(state[execution.QUOTE_READY_S], timestamp + 1000)
                last_requote = timestamp
                state[UPDATES] += 1
                if event_changed:
                    delay = max(0.0, timestamp - pending_event_at)
                    state[EVENT_GRID_UPDATES] += 1
                    state[EVENT_GRID_DELAY_SUM] += delay
                    state[EVENT_GRID_DELAY_MAX] = max(state[EVENT_GRID_DELAY_MAX], delay)
                    pending_event_at = -1
            if settings[0] > 0:
                grid[execution.DIR_STOP] = max(.02, min(.12, grid[execution.SPACING] * 4.0))
                grid[execution.DIR_TRAIL] = max(.02, min(.10, grid[execution.SPACING] * 3.0))
            trend_control = feature[8] if len(feature) > 8 else feature[2]
            update_trend(state, trend_control, bar[0], bar[4], timestamp, parameters, grid, settings)
        event_side = 0
        if settings[15] and settings[0] >= 2 and abs(effective_direction) >= .75 and effective_direction * feature[2] > 0 and abs(feature[2]) >= 1.0:
            event_side = 1 if effective_direction > 0 else -1
            if effective_logvol > .3 and abs(effective_direction) > 1.25:
                opposite_qty = state[accounting.QS] if event_side == 1 else state[accounting.QL]
                if opposite_qty > 0:
                    execution.close_side(state, -event_side, bar[0], timestamp, parameters, grid, False, 1)
                    state[EVENT_CLOSES] += 1
        allowed = np.zeros(2, dtype=np.bool_)
        for side_index in range(2):
            side = 1 if side_index == 0 else -1
            allowed[side_index] = (feature[0] > 0 and not event_risk and state[accounting.HALT] == 0
                                   and timestamp >= state[accounting.RESUME]
                                   and (state[execution.REGIME] == 0 or state[execution.REGIME] == side))
            if event_side != 0 and side != event_side:
                if allowed[side_index]:
                    state[EVENT_BLOCKS] += 1
                allowed[side_index] = False
        target = max(accounting.equity(state, bar[4]) * grid[execution.RATE], grid[execution.BASEMIN])
        quantity = execution.fmz_order_qty(target, bar[0], timestamp, parameters, grid)
        if state[accounting.QL] + state[accounting.QS] == 0 and allowed[0] and allowed[1]:
            ready = max(state[execution.READY_L], state[execution.READY_S])
            budget = (state[accounting.W] - 2 * quantity * bar[0] * (parameters[19] + parameters[20] / 10000)) * parameters[1] * parameters[2]
            if timestamp >= ready and 2 * quantity * bar[4] <= budget:
                execution.open_side(state, 1, bar[0], bar[4], timestamp, parameters, grid, quantity)
                execution.open_side(state, -1, bar[0], bar[4], timestamp, parameters, grid, quantity)
        else:
            for side_index in range(2):
                side = 1 if side_index == 0 else -1
                quantity_index = accounting.QL if side == 1 else accounting.QS
                ready_index = execution.READY_L if side == 1 else execution.READY_S
                if state[quantity_index] == 0 and allowed[side_index] and timestamp >= state[ready_index]:
                    execution.open_side(state, side, bar[0], bar[4], timestamp, parameters, grid, quantity)
        for side_index in range(2):
            install_quotes(state, 1 if side_index == 0 else -1, bar[0], bar[4], timestamp,
                           parameters, grid, allowed[side_index] and not event_risk)
        if state[accounting.QL] + state[accounting.QS] > 0:
            if path_mode == 0:
                alternate = state.copy()
                execution.fmz_path(state, bar, timestamp, parameters, grid, True)
                execution.fmz_path(alternate, bar, timestamp, parameters, grid, False)
                if accounting.equity(alternate, bar[7]) < accounting.equity(state, bar[7]):
                    state = alternate
            else:
                execution.fmz_path(state, bar, timestamp, parameters, grid, path_mode == 1)
        accounting.track(state, bar[7])
        state[GRID_SUM] += grid[execution.SPACING]
        state[GRID_MIN] = min(state[GRID_MIN], grid[execution.SPACING])
        state[GRID_MAX] = max(state[GRID_MAX], grid[execution.SPACING])
        if stride and (state[accounting.FILLS] != before_fills or state[EVENT_ROWS] != before_events or state[execution.REGIME] != before_regime):
            if decision_count >= len(decisions):
                raise ValueError("Decision trace capacity exceeded")
            decisions[decision_count] = (float(timestamp), accounting.equity(state, bar[7]),
                state[accounting.FILLS] - before_fills, state[accounting.MADDS] - before_adds,
                state[EVENT_ROWS] - before_events, state[execution.REGIME], grid[execution.SPACING],
                grid[execution.RATIO], grid[execution.MAX_ADDS], effective_logvol, effective_direction,
                state[accounting.QL], state[accounting.QS])
            decision_count += 1
        if stride and ((index + 1) % stride == 0 or index == len(times) - 1):
            curve[index // stride] = (float(timestamp), accounting.equity(state, bar[7]), state[accounting.W],
                state[accounting.QL], state[accounting.QS], state[execution.REGIME],
                state[accounting.FILLS] - recorded_fills, grid[execution.SPACING], grid[execution.PROFIT],
                grid[execution.RATIO], grid[execution.MAX_ADDS], feature[0], effective_logvol,
                effective_direction, state[accounting.MADDS], state[EVENT_CLOSES])
            recorded_fills = state[accounting.FILLS]
    if state[accounting.QL] + state[accounting.QS] > 0:
        accounting.flatten(state, bars[-1, 3], times[-1], parameters, 0)
        accounting.track(state, bars[-1, 7])
        if stride:
            curve[-1, 1:3] = state[accounting.W]
            curve[-1, 3:5] = 0.0
            curve[-1, 6] += state[accounting.FILLS] - recorded_fills
    return state, curve, decisions[:decision_count]


def backtest(data, features, events, config, start, end, record=False, path_mode=0):
    if data["resolution"] != "1s":
        raise ValueError("Minute-return simulations are prohibited in this experiment")
    begin = int(np.searchsorted(data["times"], millis(start)))
    finish = int(np.searchsorted(data["times"], millis(end)))
    if finish <= begin or int(data["times"][begin]) != millis(start) or int(data["times"][finish - 1]) + 1000 != millis(end):
        raise ValueError("Incomplete requested second window")
    event_times = events["times"] + int((config.event_delay_seconds - 5) * 1000)
    event_begin = int(np.searchsorted(event_times, millis(start)))
    event_end = int(np.searchsorted(event_times, millis(end)))
    event_prediction = events["market" if config.mode == 2 else "jev"][event_begin:event_end]
    parameters, grid, settings = config.vectors()
    started = time.perf_counter()
    state, curve, decisions = dynamic_core(
        data["times"][begin:finish], data["bars"][begin:finish], data["funding"][begin:finish],
        data["minute_index"][begin:finish], features, event_times[event_begin:event_end], event_prediction,
        parameters, grid, settings, 60 if record else 0, path_mode)
    result = {"name": config.name, "start": start, "end_exclusive": end, "resolution": "1s",
              "initial_equity": config.initial_equity, "final_equity": float(state[accounting.W]),
              "return_pct": float((state[accounting.W] / config.initial_equity - 1) * 100),
              "max_drawdown_pct": float(state[accounting.MAXDD] * 100),
              "fills": int(state[accounting.FILLS]), "martingale_adds": int(state[accounting.MADDS]),
              "take_profits": int(state[accounting.TPS]), "fees": float(state[accounting.FEES]),
              "funding_net": float(state[accounting.FUND]), "liquidations": int(state[accounting.LIQ]),
              "halted": bool(state[accounting.HALT]), "turnover": float(state[accounting.TURN]),
              "trend_switches": int(state[execution.SWITCHES]), "trend_leg_closes": int(state[execution.CONTRA_CLOSES]),
              "directional_stops": int(state[execution.DIR_STOPS]), "parameter_updates": int(state[UPDATES]),
              "event_rows": int(state[EVENT_ROWS]), "event_used": int(state[EVENT_USED]),
              "event_veto_seconds": int(state[EVENT_BLOCKS]), "event_closes": int(state[EVENT_CLOSES]),
              "event_grid_updates": int(state[EVENT_GRID_UPDATES]),
              "event_grid_nochange": int(state[EVENT_GRID_NOCHANGE]),
              "event_to_grid_update_mean_seconds": (float(state[EVENT_GRID_DELAY_SUM] / state[EVENT_GRID_UPDATES] / 1000)
                                                    if state[EVENT_GRID_UPDATES] else None),
              "event_to_grid_update_max_seconds": float(state[EVENT_GRID_DELAY_MAX] / 1000),
              "event_availability_to_grid_mean_seconds": (float(config.event_delay_seconds
                    + state[EVENT_GRID_DELAY_SUM] / state[EVENT_GRID_UPDATES] / 1000)
                    if state[EVENT_GRID_UPDATES] else None),
              "grid_mean_pct": float(state[GRID_SUM] / (finish - begin) * 100),
              "grid_min_pct": float(state[GRID_MIN] * 100), "grid_max_pct": float(state[GRID_MAX] * 100),
              "peak_gross_notional": float(state[accounting.PGROSS]), "bars": finish - begin,
              "elapsed_seconds": time.perf_counter() - started}
    if record:
        frame = pd.DataFrame(curve, columns=CURVE_COLUMNS)
        trace = pd.DataFrame(decisions, columns=["timestamp", "equity", "fills", "adds", "events", "regime",
                                               "spacing", "multiplier", "max_adds", "event_logvol",
                                               "event_direction", "long_qty", "short_qty"])
        return result, frame, trace
    return result
