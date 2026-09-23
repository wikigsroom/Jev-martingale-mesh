"""Inventory/grid simulator with fees, funding, lot filters and mark liquidation.

Execution is an OHLC path simulation, NOT an exchange matching-engine replay.
The risk module uses mark prices. Two within-bar paths can be evaluated to expose
path dependence. Strategy observations use completed bars only.
"""
from dataclasses import asdict
import numpy as np
import pandas as pd
from numba import njit
from .config import StrategyConfig

# State: wallet; quantities L/S; avg prices L/S; last fills L/S; last order qty
# L/S; add counts L/S; cycle start wallet; cycle start timestamp; resume timestamp;
# equity peak; halted; fees; funding; liquidations; closed cycles; TP fills;
# event stops; risk stops; time stops; rejected adds; peak gross; minimum equity;
# turnover; fills; maximum drawdown.
W, QL, QS, AL, AS, LL, LS, OL, OS, NL, NS, CW, CT, RESUME, PEAK, HALT, FEES, FUND, LIQ, CYCLES, TPS, ESTOP, RSTOP, TSTOP, REJECT, PGROSS, MINEQ, TURN, FILLS, MAXDD = range(30)
MADDS = 30
STATE_SIZE = 31

# Parameter vector positions are kept explicit for portable Numba execution.
PARAMETERS = ["initial_equity", "leverage", "gross_utilization", "base_notional", "multiplier", "max_adds", "grid_step", "take_profit", "basket_stop", "basket_take_profit", "max_cycle_minutes", "cooldown_minutes", "equity_halt_drawdown", "event_threshold", "event_hold_minutes", "event_delay_seconds", "trend_stop", "vol_stop", "maker_fee", "taker_fee", "slippage_bps", "maintenance_rate", "liquidation_fee", "qty_step", "min_qty", "min_notional_pre_change", "min_notional_post_change", "min_notional_change_ms", "tick_size"]


@njit(cache=True)
def equity(s, price):
    return s[W] + s[QL] * (price-s[AL]) + s[QS] * (s[AS]-price)


@njit(cache=True)
def track(s, mark):
    eq = equity(s, mark)
    s[PEAK] = max(s[PEAK], eq)
    s[MINEQ] = min(s[MINEQ], eq)
    if s[PEAK] > 0:
        s[MAXDD] = max(s[MAXDD], 1-eq/s[PEAK])
    s[PGROSS] = max(s[PGROSS], (s[QL]+s[QS])*mark)


@njit(cache=True)
def close_leg(s, side, price, p, maker=False):
    qi, ai = (QL, AL) if side == 1 else (QS, AS)
    qty = s[qi]
    if qty <= 0:
        return
    # Buy closes short; sell closes long. Both pay the relevant fee.
    px = price if maker else price*(1-side*p[20]/10000)
    fee = qty*px*(p[18] if maker else p[19])
    s[W] += side*qty*(px-s[ai])-fee
    s[FEES] += fee
    s[TURN] += qty*px
    s[FILLS] += 1
    s[qi], s[ai] = 0., 0.
    if side == 1:
        s[NL], s[OL], s[LL] = 0., 0., 0.
    else:
        s[NS], s[OS], s[LS] = 0., 0., 0.


@njit(cache=True)
def flatten(s, price, ts, p, reason):
    close_leg(s, 1, price, p)
    close_leg(s, -1, price, p)
    s[CYCLES] += 1
    if reason == 1:
        s[ESTOP] += 1
    elif reason == 2:
        s[RSTOP] += 1
    elif reason == 3:
        s[TSTOP] += 1
    s[CW], s[CT] = 0., 0.
    s[RESUME] = ts+p[11]*60000


@njit(cache=True)
def check_risk(s, trade_price, mark, ts, p):
    track(s, mark)
    if s[QL]+s[QS] <= 0:
        return False
    eq = equity(s, mark)
    gross = (s[QL]+s[QS])*mark
    # Maintenance is charged on BOTH legs. No assumed hedge-margin offset.
    if eq <= gross*p[21]:
        liquidation_cost = gross*p[22]
        flatten(s, trade_price, ts, p, 2)
        s[W] = max(0., s[W]-liquidation_cost)
        s[FEES] += liquidation_cost
        s[LIQ] += 1
        s[HALT] = 1
        track(s, mark)
        return True
    hard_dd = eq <= s[PEAK]*(1-p[12])
    if (s[CW] > 0 and eq <= s[CW]*(1-p[8])) or hard_dd:
        flatten(s, trade_price, ts, p, 2)
        if hard_dd:
            s[HALT] = 1
        track(s, mark)
        return True
    return False


@njit(cache=True)
def order_qty(target, price, ts, p):
    minimum = p[25] if ts < p[27] else p[26]
    raw = max(target/price, minimum/price, p[24])
    return np.ceil((raw-1e-12)/p[23])*p[23]


@njit(cache=True)
def open_leg(s, side, price, mark, qty, ts, p, maker=False):
    eq = equity(s, mark)
    px = price if maker else price*(1+side*p[20]/10000)
    fee = qty*px*(p[18] if maker else p[19])
    # No deposit/reload and no borrowing of next round's capital.
    projected_gross = (s[QL]+s[QS]+qty)*mark
    immediate_pnl = side*qty*(mark-px)
    if projected_gross > max(0., eq-fee+immediate_pnl)*p[1]*p[2] + 1e-9:
        s[REJECT] += 1
        return False
    qi, ai, li, oi, ni = (QL, AL, LL, OL, NL) if side == 1 else (QS, AS, LS, OS, NS)
    old_qty = s[qi]
    s[ai] = (old_qty*s[ai]+qty*px)/(old_qty+qty)
    s[qi] += qty
    s[li], s[oi] = price, qty
    if old_qty > 0:
        s[ni] += 1
        s[MADDS] += 1
    s[W] -= fee
    s[FEES] += fee
    s[TURN] += qty*px
    s[FILLS] += 1
    track(s, mark)
    return True


@njit(cache=True)
def segment(s, start, end, mstart, mend, ts, p, added, closed):
    """Process crossed orders and risk barriers in price order, not at bar close."""
    current, cm = start, mstart
    for _ in range(12):
        if check_risk(s, current, cm, ts, p):
            return True
        if abs(end-current) < 1e-12:
            return check_risk(s, end, mend, ts, p)
        direction = 1 if end > current else -1
        best_fraction, level, action = 1.000001, end, 0
        # Stop barriers are solved on linearly interpolated mark prices.
        eq0, eq1 = equity(s, cm), equity(s, mend)
        gross0, gross1 = (s[QL]+s[QS])*cm, (s[QL]+s[QS])*mend
        for risk_kind in range(3):
            if risk_kind == 0:
                a, b = eq0-gross0*p[21], eq1-gross1*p[21]
            elif risk_kind == 1 and s[CW] > 0:
                a, b = eq0-s[CW]*(1-p[8]), eq1-s[CW]*(1-p[8])
            elif risk_kind == 2:
                a, b = eq0-s[PEAK]*(1-p[12]), eq1-s[PEAK]*(1-p[12])
            else:
                continue
            if a > 0 and b <= 0:
                f = a/(a-b)
                if f < best_fraction:
                    best_fraction, level, action = f, current+(end-current)*f, 5
        for side_index in range(2):
            side = 1 if side_index == 0 else -1
            qi, ai, li, oi, ni = (QL, AL, LL, OL, NL) if side == 1 else (QS, AS, LS, OS, NS)
            if s[qi] <= 0 or closed[side_index]:
                continue
            tp = s[ai]*(1+side*p[7])
            add = s[li]*(1-side*p[6])
            tp = (np.ceil(tp/p[28]) if side == 1 else np.floor(tp/p[28]))*p[28]
            add = (np.floor(add/p[28]) if side == 1 else np.ceil(add/p[28]))*p[28]
            for which in range(2):
                target = tp if which == 0 else add
                if which == 1 and (added[side_index] or s[ni] >= p[5]):
                    continue
                f = (target-current)/(end-current)
                valid_direction = direction == side if which == 0 else direction == -side
                # Require a strict trade-through, not mere touching of the limit.
                if valid_direction and f >= -1e-10 and f < 1-1e-10 and f < best_fraction:
                    best_fraction, level = max(0., f), target
                    action = (1 if side == 1 else 2) if which == 0 else (3 if side == 1 else 4)
        if action == 0:
            return check_risk(s, end, mend, ts, p)
        mark = cm+(mend-cm)*min(1., best_fraction)
        if action == 5:
            # An epsilon through the barrier avoids round-off skipping a stop.
            check_risk(s, level, mark+(mend-mark)*1e-8, ts, p)
            return True
        if check_risk(s, level, mark, ts, p):
            return True
        if action in (1, 2):
            side = 1 if action == 1 else -1
            close_leg(s, side, level, p, True)
            closed[0 if side == 1 else 1] = True
            s[TPS] += 1
        else:
            side = 1 if action == 3 else -1
            idx = 0 if side == 1 else 1
            oi = OL if side == 1 else OS
            qty = order_qty(s[oi]*p[4]*level, level, ts, p)
            open_leg(s, side, level, mark, qty, ts, p, True)
            added[idx] = True
        # Move a tiny amount beyond this fill to avoid repeatedly processing it.
        fstep = min(1., best_fraction+1e-9)
        current, cm = current+(end-current)*fstep, cm+(mend-cm)*fstep
    return check_risk(s, end, mend, ts, p)


@njit(cache=True)
def run_path(s, bar, ts, p, high_first):
    trade = np.empty(4)
    marks = np.empty(4)
    trade[0], trade[3] = bar[0], bar[3]
    marks[0], marks[3] = bar[4], bar[7]
    trade[1], trade[2] = (bar[1], bar[2]) if high_first else (bar[2], bar[1])
    marks[1], marks[2] = (bar[5], bar[6]) if high_first else (bar[6], bar[5])
    added, closed = np.zeros(2, dtype=np.bool_), np.zeros(2, dtype=np.bool_)
    # Resting limits can already be crossed at the next observed trade. Use the
    # resting limit (no favorable gap improvement), after the gap risk check.
    for idx in range(2):
        side = 1 if idx == 0 else -1
        qi, ai, li, oi, ni = (QL, AL, LL, OL, NL) if side == 1 else (QS, AS, LS, OS, NS)
        if s[qi] <= 0:
            continue
        tp = s[ai]*(1+side*p[7])
        tp = (np.ceil(tp/p[28]) if side == 1 else np.floor(tp/p[28]))*p[28]
        add = s[li]*(1-side*p[6])
        add = (np.floor(add/p[28]) if side == 1 else np.ceil(add/p[28]))*p[28]
        if side*(bar[0]-tp) > 1e-9:
            close_leg(s, side, tp, p, True)
            closed[idx] = True
            s[TPS] += 1
        elif side*(bar[0]-add) < -1e-9 and s[ni] < p[5]:
            qty = order_qty(s[oi]*p[4]*add, add, ts, p)
            open_leg(s, side, add, bar[4], qty, ts, p, True)
            added[idx] = True
    for j in range(3):
        if segment(s, trade[j], trade[j+1], marks[j], marks[j+1], ts, p, added, closed):
            break
    track(s, bar[7])


@njit(cache=True)
def simulate_core(times, bars, funding, signals, trends, ranges, p, path_mode=0, record=False, record_stride=1):
    s = np.zeros(STATE_SIZE)
    s[W], s[PEAK], s[MINEQ] = p[0], p[0], p[0]
    samples = (len(times)+record_stride-1)//record_stride
    curve = np.empty(samples) if record else np.empty(0)
    logs = np.empty((samples, 6)) if record else np.empty((0, 6))
    recorded_fills = 0.
    event_block = 0.
    for i in range(len(times)):
        ts, bar = times[i], bars[i]
        before_fills = s[FILLS]
        # Timestamp is rounded to funding settlement minute, not shifted to next bar.
        payment = (s[QS]-s[QL])*bar[4]*funding[i]
        s[W] += payment
        s[FUND] += payment
        check_risk(s, bar[0], bar[4], ts, p)
        if signals[i] >= p[13] and p[13] <= 1:
            event_block = max(event_block, ts+p[14]*60000)
            if s[QL]+s[QS] > 0:
                flatten(s, bar[0], ts, p, 1)
        price_gate = abs(trends[i]) > p[16] or ranges[i] > p[17]
        if price_gate and s[QL]+s[QS] > 0:
            flatten(s, bar[0], ts, p, 2)
        eq = equity(s, bar[4])
        if s[CW] > 0 and s[QL]+s[QS] > 0:
            if eq >= s[CW]*(1+p[9]):
                flatten(s, bar[0], ts, p, 0)
            elif ts-s[CT] >= p[10]*60000:
                flatten(s, bar[0], ts, p, 3)
        if s[HALT] == 0 and ts >= max(s[RESUME], event_block) and not price_gate:
            if s[QL]+s[QS] == 0:
                # Atomic preflight for TWO starting legs; reject both if either cannot fit.
                qty = order_qty(p[3], bar[0], ts, p)
                gross = 2*qty*bar[4]
                fees = 2*qty*bar[0]*(p[19]+p[20]/10000)
                if gross <= max(0., s[W]-fees)*p[1]*p[2]:
                    s[CW], s[CT] = s[W], ts
                    open_leg(s, 1, bar[0], bar[4], qty, ts, p)
                    open_leg(s, -1, bar[0], bar[4], qty, ts, p)
            else:
                for side in (1, -1):
                    if (side == 1 and s[QL] == 0) or (side == -1 and s[QS] == 0):
                        qty = order_qty(p[3], bar[0], ts, p)
                        open_leg(s, side, bar[0], bar[4], qty, ts, p)
        if s[QL]+s[QS] > 0:
            if path_mode == 0:
                # Conservative OHLC uncertainty envelope, expressly not a known path.
                other = s.copy()
                run_path(s, bar, ts, p, True)
                run_path(other, bar, ts, p, False)
                if equity(other, bar[7]) < equity(s, bar[7]):
                    s = other
            else:
                run_path(s, bar, ts, p, path_mode == 1)
        track(s, bar[7])
        if record and ((i+1) % record_stride == 0 or i == len(times)-1):
            j = i//record_stride
            curve[j] = equity(s, bar[7])
            logs[j, 0] = s[QL]
            logs[j, 1] = s[QS]
            logs[j, 2] = s[NL]
            logs[j, 3] = s[NS]
            logs[j, 4] = s[FILLS]-recorded_fills
            logs[j, 5] = s[W]
            recorded_fills = s[FILLS]
    if s[QL]+s[QS] > 0:
        flatten(s, bars[-1, 3], times[-1], p, 0)
        track(s, bars[-1, 7])
        if record:
            curve[-1] = s[W]
            logs[-1, :4] = 0.
            logs[-1, 4] += s[FILLS]-recorded_fills
            logs[-1, 5] = s[W]
    return s, curve, logs


def config_vector(config):
    config.validate()
    d = asdict(config)
    return np.array([d[k] for k in PARAMETERS], dtype=np.float64)


def run_backtest(dataset, config, start=None, end=None, path_mode=0, record=False, record_stride=None):
    times = dataset["times"]
    first = 0 if start is None else np.searchsorted(times, int(pd.Timestamp(start, tz="UTC").timestamp()*1000))
    last = len(times) if end is None else np.searchsorted(times, int(pd.Timestamp(end, tz="UTC").timestamp()*1000))
    if last <= first:
        raise ValueError("Empty backtest interval")
    arrays = [np.ascontiguousarray(dataset[k][first:last]) for k in ["times", "bars", "funding", "signals", "trends", "ranges"]]
    stride = record_stride or (60 if dataset.get('resolution') == '1s' else 1)
    s, curve, logs = simulate_core(*arrays, config_vector(config), path_mode, record, stride)
    result = {"final_equity": float(s[W]), "net_profit": float(s[W]-config.initial_equity),
              "return_pct": float((s[W]/config.initial_equity-1)*100), "max_drawdown_pct": float(s[MAXDD]*100),
              "fees": float(s[FEES]), "funding_net": float(s[FUND]), "liquidations": int(s[LIQ]),
              "cycles": int(s[CYCLES]), "take_profit_fills": int(s[TPS]), "event_stops": int(s[ESTOP]),
              "risk_stops": int(s[RSTOP]), "time_stops": int(s[TSTOP]), "rejected_orders": int(s[REJECT]),
              "peak_gross_notional": float(s[PGROSS]), "min_equity": float(s[MINEQ]),
              "turnover": float(s[TURN]), "fills": int(s[FILLS]), "halted": bool(s[HALT]),
              "martingale_adds": int(s[MADDS]),
              "start": pd.to_datetime(arrays[0][0], unit="ms", utc=True).isoformat(),
              "last_bar": pd.to_datetime(arrays[0][-1], unit="ms", utc=True).isoformat(), "bars": len(arrays[0])}
    if record:
        indices = np.minimum(np.arange(len(curve))*stride+stride-1, len(arrays[0])-1)
        return result, pd.DataFrame({"timestamp": arrays[0][indices], "equity": curve, "long_qty": logs[:, 0], "short_qty": logs[:, 1], "long_adds": logs[:, 2], "short_adds": logs[:, 3], "fills": logs[:, 4], "wallet": logs[:, 5]})
    return result
