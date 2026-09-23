"""Anchored linear FMZ grid with post-TP/continuous trend control and real Jev.

The original user source is retained separately. This implementation corrects its
order-state, shared-grid, quantity, equity and asymmetric emergency-stop defects.
It is a simulator, not a connection to a live exchange.
"""
import numpy as np
import pandas as pd
from numba import njit
from .engine import (equity, track, close_leg, flatten, check_risk, open_leg, order_qty,
    config_vector, W, QL, QS, AL, AS, NL, NS, CW, CT, RESUME, PEAK, HALT, FEES, FUND,
    LIQ, CYCLES, TPS, ESTOP, RSTOP, REJECT, PGROSS, MINEQ, TURN, FILLS, MAXDD, MADDS)
from .fmz_config import FMZConfig
from .fmz_data import EMA_WINDOWS

ANCL, ANCS, GS_L, GS_S, TP_L, TP_S, OQ_L, OQ_S, OP_L, OP_S, READY_L, READY_S, WI_L, WI_S, RAW_L, RAW_S, REGIME, PENDING, PENDING_AT, NEWS_UNTIL, NEWS_DIR, LAST_TP, TREND_ENTRIES, CONTRA_CLOSES, JEV_ACTIONS, JEV_VETOES, WARN_BLOCKS, BEST_L, BEST_S, SWITCHES, POST_TP_SWITCHES, CAP_STOPS, DIR_STOPS = range(31, 64)
JEV_LEG_CLOSES = 64
ADD_TIME_L, ADD_TIME_S, TP_TIME_L, TP_TIME_S = range(65, 69)
QUOTE_READY_L, QUOTE_READY_S = 69, 70
FMZ_STATE_SIZE = 71
SPACING, RATE, BASEMIN, RATIO, PROFIT, WARNING, CAP, MAX_ADDS, REENTRY, CONTROL, FAST, SLOW, ENTER, EXIT, CONFIRM, CLOSE_OPPOSITE, COUNTERADD, DIR_STOP, DIR_TRAIL, JEV_ACTION, JEV_THRESHOLD, JEV_DIRECTION, JEV_HOLD = range(23)
POLL = 23


@njit(cache=True)
def clear_quotes(s, side=0):
    if side >= 0:
        s[TP_L] = s[OQ_L] = s[OP_L] = 0.
    if side <= 0:
        s[TP_S] = s[OQ_S] = s[OP_S] = 0.


@njit(cache=True)
def clear_adds(s, side=0):
    if side >= 0:
        s[OQ_L] = s[OP_L] = 0.
    if side <= 0:
        s[OQ_S] = s[OP_S] = 0.


@njit(cache=True)
def close_side(s, side, price, ts, p, q, maker=False, reason=0):
    if (s[QL] if side == 1 else s[QS]) <= 0:
        return
    close_leg(s, side, price, p, maker)
    clear_quotes(s, side)
    ready = READY_L if side == 1 else READY_S
    s[ready] = ts+(q[REENTRY]*1000 if reason == 0 else p[11]*60000)
    if reason == 0:
        s[TPS] += 1
        s[LAST_TP] = side
    if s[QL]+s[QS] <= 0:
        s[CW] = s[CT] = 0.
        s[CYCLES] += 1


@njit(cache=True)
def fmz_risk(s, price, mark, ts, p, q):
    if check_risk(s, price, mark, ts, p):
        clear_quotes(s)
        s[READY_L] = s[READY_S] = s[RESUME]
        return True
    for side in (1, -1):
        qi, ai, bi = (QL, AL, BEST_L) if side == 1 else (QS, AS, BEST_S)
        if s[qi] <= 0:
            continue
        if s[qi]*mark >= p[0]*q[CAP]-1e-9:
            close_side(s, side, price, ts, p, q, False, 1)
            s[CAP_STOPS] += 1
            s[RSTOP] += 1
            continue
        if s[REGIME] == side:
            if side == 1:
                s[bi] = max(s[bi], mark)
            elif s[bi] == 0:
                s[bi] = mark
            else:
                s[bi] = min(s[bi], mark)
            loss = -side*(mark/s[ai]-1)
            retreat = -side*(mark/s[bi]-1)
            if loss >= q[DIR_STOP] or retreat >= q[DIR_TRAIL]:
                close_side(s, side, price, ts, p, q, False, 1)
                s[DIR_STOPS] += 1
                s[RSTOP] += 1
    track(s, mark)
    return s[QL]+s[QS] == 0


@njit(cache=True)
def open_side(s, side, price, mark, ts, p, q, qty):
    was_flat = s[QL]+s[QS] == 0
    cycle_equity = equity(s, mark)
    reserved = s[OQ_L]*s[OP_L]+s[OQ_S]*s[OP_S]
    px = price*(1+side*p[20]/10000)
    after_eq = cycle_equity-qty*px*p[19]+side*qty*(mark-px)
    if (s[QL]+s[QS]+qty)*mark+reserved > max(0., after_eq-reserved*p[18])*p[1]*p[2]+1e-9:
        s[REJECT] += 1
        return False
    if not open_leg(s, side, price, mark, qty, ts, p, False):
        return False
    if was_flat:
        s[CW], s[CT] = cycle_equity, ts
    if side == 1:
        s[ANCL], s[GS_L], s[RAW_L], s[WI_L], s[BEST_L] = price, price*q[SPACING], qty, 0., mark
        s[QUOTE_READY_L] = ts+q[POLL]*1000
    else:
        s[ANCS], s[GS_S], s[RAW_S], s[WI_S], s[BEST_S] = price, price*q[SPACING], qty, 0., mark
        s[QUOTE_READY_S] = ts+q[POLL]*1000
    if s[REGIME] == side:
        s[TREND_ENTRIES] += 1
    return True


@njit(cache=True)
def open_add(s, side, price, mark, qty, ts, p, maker):
    reserved = s[OQ_S]*s[OP_S] if side == 1 else s[OQ_L]*s[OP_L]
    px = price if maker else price*(1+side*p[20]/10000)
    after_eq = equity(s, mark)-qty*px*(p[18] if maker else p[19])+side*qty*(mark-px)
    if (s[QL]+s[QS]+qty)*mark+reserved > max(0., after_eq-reserved*p[18])*p[1]*p[2]+1e-9:
        s[REJECT] += 1
        return False
    return open_leg(s, side, price, mark, qty, ts, p, maker)


@njit(cache=True)
def refresh_quotes(s, side, price, ts, p, q, may_add, mark=0.):
    qi, ai, ni, anchor, spacing, raw, oq, op, tp, ready, wi = (QL, AL, NL, ANCL, GS_L, RAW_L, OQ_L, OP_L, TP_L, READY_L, WI_L) if side == 1 else (QS, AS, NS, ANCS, GS_S, RAW_S, OQ_S, OP_S, TP_S, READY_S, WI_S)
    if s[qi] <= 0 or ts < s[QUOTE_READY_L if side == 1 else QUOTE_READY_S]:
        return
    if not may_add:
        s[oq] = s[op] = 0.
    # The source only refreshes its average-price TP when installing the next add.
    # We also maintain a TP at the finite last level, instead of losing protection.
    if s[oq] == 0 and (s[tp] == 0 or may_add):
        target = s[ai]+side*price*q[PROFIT]
        target = (np.ceil(target/p[28]) if side == 1 else np.floor(target/p[28]))*p[28]
        if s[tp] == 0:
            s[tp] = target
            s[TP_TIME_L if side == 1 else TP_TIME_S] = ts
        if may_add and s[ni] < q[MAX_ADDS]:
            index = s[ni]+1
            level = s[anchor]-side*index*s[spacing]
            level = (np.floor(level/p[28]) if side == 1 else np.ceil(level/p[28]))*p[28]
            if level > 0:
                qty = order_qty(s[raw]*q[RATIO]*level, level, ts, p)
                other_reserved = s[OQ_S]*s[OP_S] if side == 1 else s[OQ_L]*s[OP_L]
                reference = mark if mark > 0 else price
                gross = (s[QL]+s[QS])*reference
                reserved = other_reserved+qty*level
                budget = max(0., equity(s, reference)-reserved*p[18])*p[1]*p[2]
                if gross+reserved > budget+1e-9:
                    s[REJECT] += 1
                    return
                s[op] = level
                s[oq] = qty
                s[ADD_TIME_L if side == 1 else ADD_TIME_S] = ts
                s[wi] = index
                s[tp] = target
                s[TP_TIME_L if side == 1 else TP_TIME_S] = ts


@njit(cache=True)
def fmz_segment(s, start, end, ms, me, ts, p, q, filled):
    price, mark = start, ms
    for _ in range(12):
        if fmz_risk(s, price, mark, ts, p, q):
            return
        if abs(end-price) < 1e-12:
            fmz_risk(s, end, me, ts, p, q)
            return
        fraction, action, level = 1.000001, 0, end
        eq0, eq1 = equity(s, mark), equity(s, me)
        gross0, gross1 = (s[QL]+s[QS])*mark, (s[QL]+s[QS])*me
        for kind in range(3):
            if kind == 0:
                a, b = eq0-gross0*p[21], eq1-gross1*p[21]
            elif kind == 1 and s[CW] > 0:
                a, b = eq0-s[CW]*(1-p[8]), eq1-s[CW]*(1-p[8])
            else:
                a, b = eq0-s[PEAK]*(1-p[12]), eq1-s[PEAK]*(1-p[12])
            if a > 0 and b <= 0:
                f = a/(a-b)
                if f < fraction:
                    fraction, level, action = f, price+(end-price)*f, 5
        for idx in range(2):
            side = 1 if idx == 0 else -1
            qi, ai, best, oq, op, tp = (QL, AL, BEST_L, OQ_L, OP_L, TP_L) if side == 1 else (QS, AS, BEST_S, OQ_S, OP_S, TP_S)
            if s[qi] <= 0:
                continue
            if s[REGIME] == side and abs(me-mark) > 1e-12:
                stop = s[ai]*(1-side*q[DIR_STOP])
                trail = s[best]*(1-side*q[DIR_TRAIL])
                for barrier in (stop, trail):
                    f = (barrier-mark)/(me-mark)
                    if side*(me-mark) < 0 and f >= 0 and f <= 1 and f < fraction:
                        fraction, level, action = f, price+(end-price)*f, 6+idx
            if filled[idx]:
                continue
            for add in range(2):
                target = s[tp] if add == 0 else s[op]
                if target <= 0 or (add == 1 and s[oq] <= 0):
                    continue
                f = (target-price)/(end-price)
                correct_direction = side*(end-price) > 0 if add == 0 else side*(end-price) < 0
                if correct_direction and f >= -1e-9 and f < 1-1e-10 and f < fraction:
                    fraction, level = max(0., f), target
                    action = 1+idx if add == 0 else 3+idx
        if action == 0:
            fmz_risk(s, end, me, ts, p, q)
            return
        m = mark+(me-mark)*fraction
        if action == 5:
            fmz_risk(s, level, m+(me-m)*1e-8, ts, p, q)
            return
        if action >= 6:
            close_side(s, 1 if action == 6 else -1, level, ts, p, q, False, 1)
            s[DIR_STOPS] += 1
            s[RSTOP] += 1
        elif action <= 2:
            side = 1 if action == 1 else -1
            close_side(s, side, level, ts, p, q, True, 0)
            filled[action-1] = True
        else:
            side = 1 if action == 3 else -1
            oq, op, raw, ready = (OQ_L, OP_L, RAW_L, READY_L) if side == 1 else (OQ_S, OP_S, RAW_S, READY_S)
            if open_add(s, side, level, m, s[oq], ts, p, True):
                s[raw] *= q[RATIO]
                s[oq] = s[op] = 0.
                s[TP_L if side == 1 else TP_S] = 0.
                s[QUOTE_READY_L if side == 1 else QUOTE_READY_S] = ts+q[POLL]*1000
            else:
                # A failed preflight cannot remain a fictitious resting order.
                s[oq] = s[op] = 0.
            filled[0 if side == 1 else 1] = True
        f = min(1., fraction+1e-9)
        price, mark = price+(end-price)*f, mark+(me-mark)*f


@njit(cache=True)
def fmz_path(s, bar, ts, p, q, high_first):
    filled = np.zeros(2, dtype=np.bool_)
    # Existing resting orders crossed by an opening gap use their resting price.
    for idx in range(2):
        side = 1 if idx == 0 else -1
        qi, oq, op, tp, raw, ready = (QL, OQ_L, OP_L, TP_L, RAW_L, READY_L) if side == 1 else (QS, OQ_S, OP_S, TP_S, RAW_S, READY_S)
        if s[qi] <= 0:
            continue
        if s[tp] > 0 and side*(bar[0]-s[tp]) > 1e-9:
            fresh = s[TP_TIME_L if side == 1 else TP_TIME_S] == ts
            if fresh:
                slipped = bar[0]*(1-side*p[20]/10000)
                fill_price = max(slipped, s[tp]) if side == 1 else min(slipped, s[tp])
                close_side(s, side, fill_price/(1-side*p[20]/10000), ts, p, q, False, 0)
            else:
                close_side(s, side, s[tp], ts, p, q, True, 0)
            filled[idx] = True
        elif s[oq] > 0 and side*(bar[0]-s[op]) < -1e-9:
            fresh = s[ADD_TIME_L if side == 1 else ADD_TIME_S] == ts
            fill_input = s[op]
            if fresh:
                slipped = bar[0]*(1+side*p[20]/10000)
                executed = min(slipped, s[op]) if side == 1 else max(slipped, s[op])
                fill_input = executed/(1+side*p[20]/10000)
            if open_add(s, side, fill_input, bar[4], s[oq], ts, p, not fresh):
                s[raw] *= q[RATIO]
                s[oq] = s[op] = 0.
                s[TP_L if side == 1 else TP_S] = 0.
                s[QUOTE_READY_L if side == 1 else QUOTE_READY_S] = ts+q[POLL]*1000
            else:
                s[oq] = s[op] = 0.
            filled[idx] = True
    first, second = (1, 2) if high_first else (2, 1)
    fmz_segment(s, bar[0], bar[first], bar[4], bar[first+4], ts, p, q, filled)
    fmz_segment(s, bar[first], bar[second], bar[first+4], bar[second+4], ts, p, q, filled)
    fmz_segment(s, bar[second], bar[3], bar[second+4], bar[7], ts, p, q, filled)
    track(s, bar[7])


@njit(cache=True)
def update_controller(s, feature, price, mark, ts, p, q):
    control = int(q[CONTROL])
    if control == 0:
        return True
    fast, slow, last = feature[int(q[FAST])], feature[int(q[SLOW])], feature[9]
    if not np.isfinite(fast) or not np.isfinite(slow):
        return False
    spread = fast/slow-1.
    threshold = q[ENTER]
    if s[REGIME] == 1 and spread > q[ENTER]*q[EXIT] and last > slow:
        raw = 1
    elif s[REGIME] == -1 and spread < -q[ENTER]*q[EXIT] and last < slow:
        raw = -1
    elif spread > threshold and last > slow:
        raw = 1
    elif spread < -threshold and last < slow:
        raw = -1
    else:
        raw = 0
    desired = raw
    if control == 1:
        if s[REGIME] == 0:
            if not ((s[LAST_TP] == raw or s[PENDING] == raw) and raw != 0):
                desired = 0
    elif control == 3:
        desired = 2 if raw != 0 else 0
    elif control == 4:
        desired = raw if raw != 0 else 2
    if desired != s[PENDING]:
        s[PENDING], s[PENDING_AT] = desired, ts
    if desired != s[REGIME] and ts-s[PENDING_AT] >= q[CONFIRM]*60000:
        s[REGIME] = desired
        s[SWITCHES] += 1
        if control == 1 and desired in (-1, 1):
            s[POST_TP_SWITCHES] += 1
        if desired == 2:
            if s[QL]+s[QS] > 0:
                flatten(s, price, ts, p, 2)
                clear_quotes(s)
        elif desired in (-1, 1):
            contra = -desired
            clear_adds(s, contra)
            if q[CLOSE_OPPOSITE] and (s[QL] if contra == 1 else s[QS]) > 0:
                close_side(s, contra, price, ts, p, q, False, 1)
                s[CONTRA_CLOSES] += 1
            if desired == 1:
                s[BEST_L] = mark
            else:
                s[BEST_S] = mark
    s[LAST_TP] = 0.
    return True


@njit(cache=True)
def fmz_core(times, bars, funding, news, minute_index, features, p, q, path_mode=0, stride=0):
    s = np.zeros(FMZ_STATE_SIZE)
    s[W] = s[PEAK] = s[MINEQ] = p[0]
    size = (len(times)+stride-1)//stride if stride else 0
    curve = np.empty((size, 9))
    decisions = np.empty((200000 if stride else 0, 16))
    decision_count = 0
    recorded_fills = 0.
    for i in range(len(times)):
        ts, b = times[i], bars[i]
        before_l, before_s, before_mode = s[QL], s[QS], s[REGIME]
        before_fill, before_tp, before_add = s[FILLS], s[TPS], s[MADDS]
        before_jev, before_contra, before_fee = s[JEV_ACTIONS], s[CONTRA_CLOSES]+s[JEV_LEG_CLOSES], s[FEES]
        payment = (s[QS]-s[QL])*b[4]*funding[i]
        s[W] += payment
        s[FUND] += payment
        fmz_risk(s, b[0], b[4], ts, p, q)
        ready = update_controller(s, features[minute_index[i]], b[0], b[4], ts, p, q)
        if q[JEV_ACTION] > 0 and news[i, 0] >= q[JEV_THRESHOLD] and news[i, 0] > 0:
            s[JEV_ACTIONS] += 1
            s[NEWS_UNTIL] = max(s[NEWS_UNTIL], ts+q[JEV_HOLD]*1000)
            s[NEWS_DIR] = 0
            if q[JEV_ACTION] == 1:
                if s[QL]+s[QS] > 0:
                    flatten(s, b[0], ts, p, 1)
                    clear_quotes(s)
                s[RESUME] = max(s[RESUME], s[NEWS_UNTIL])
            elif q[JEV_ACTION] == 2 and abs(news[i, 1]) >= q[JEV_DIRECTION]:
                direction = 1 if news[i, 1] > 0 else -1
                s[NEWS_DIR] = direction
                if (s[QL] if direction == -1 else s[QS]) > 0:
                    close_side(s, -direction, b[0], ts, p, q, False, 1)
                    s[JEV_LEG_CLOSES] += 1
            if s[NEWS_DIR]:
                clear_adds(s, -int(s[NEWS_DIR]))
            else:
                clear_adds(s)
        blocked_news = ts < s[NEWS_UNTIL] and q[JEV_ACTION] > 0
        allowed = np.zeros(2, dtype=np.bool_)
        for idx in range(2):
            side = 1 if idx == 0 else -1
            allowed[idx] = ready and s[HALT] == 0 and ts >= s[RESUME] and (s[REGIME] == 0 or s[REGIME] == side)
            if blocked_news and (q[JEV_ACTION] in (1, 3) or s[NEWS_DIR] == 0 or s[NEWS_DIR] != side):
                if allowed[idx]:
                    s[JEV_VETOES] += 1
                allowed[idx] = False
        target = max(equity(s, b[4])*q[RATE], q[BASEMIN])*q[RATIO]
        qty = order_qty(target, b[0], ts, p)
        if s[QL]+s[QS] == 0 and allowed[0] and allowed[1] and ts >= max(s[READY_L], s[READY_S]):
            # Two initial legs must fit atomically, including costs and slippage.
            budget = (s[W]-2*qty*b[0]*(p[19]+p[20]/10000))*p[1]*p[2]
            if 2*qty*b[4] <= budget:
                open_side(s, 1, b[0], b[4], ts, p, q, qty)
                open_side(s, -1, b[0], b[4], ts, p, q, qty)
        else:
            for idx in range(2):
                side = 1 if idx == 0 else -1
                qi, ri, opposite_warning = (QL, READY_L, WI_S) if side == 1 else (QS, READY_S, WI_L)
                if s[qi] == 0 and allowed[idx] and ts >= s[ri]:
                    if s[REGIME] == 0 and s[opposite_warning] > q[WARNING]:
                        s[WARN_BLOCKS] += 1
                    else:
                        open_side(s, side, b[0], b[4], ts, p, q, qty)
        for idx in range(2):
            side = 1 if idx == 0 else -1
            may_add = allowed[idx] or (q[COUNTERADD] and s[REGIME] in (-1, 1) and not blocked_news and s[HALT] == 0 and ts >= s[RESUME])
            refresh_quotes(s, side, b[0], ts, p, q, may_add, b[4])
        if s[QL]+s[QS] > 0:
            if path_mode == 0:
                alternate = s.copy()
                fmz_path(s, b, ts, p, q, True)
                fmz_path(alternate, b, ts, p, q, False)
                if equity(alternate, b[7]) < equity(s, b[7]):
                    s = alternate
            else:
                fmz_path(s, b, ts, p, q, path_mode == 1)
        track(s, b[7])
        if stride and (s[FILLS] != before_fill or s[REGIME] != before_mode or s[JEV_ACTIONS] != before_jev):
            if decision_count >= len(decisions):
                raise ValueError('Decision trace capacity exceeded; refusing a silently truncated audit')
            decisions[decision_count] = (float(ts), b[0], b[3], before_l, before_s, s[QL], s[QS],
                before_mode, s[REGIME], s[FILLS]-before_fill, s[TPS]-before_tp, s[MADDS]-before_add,
                s[JEV_ACTIONS]-before_jev, s[CONTRA_CLOSES]+s[JEV_LEG_CLOSES]-before_contra,
                s[FEES]-before_fee, equity(s, b[7]))
            decision_count += 1
        if stride and ((i+1) % stride == 0 or i == len(times)-1):
            j = i//stride
            curve[j] = (float(ts), equity(s, b[7]), s[QL], s[QS], s[REGIME], s[FILLS]-recorded_fills, s[W], s[NL], s[NS])
            recorded_fills = s[FILLS]
    if s[QL]+s[QS] > 0:
        flatten(s, bars[-1, 3], times[-1], p, 0)
        clear_quotes(s)
        track(s, bars[-1, 7])
        if stride:
            curve[-1, 1] = s[W]
            curve[-1, 2:4] = 0.
            curve[-1, 5] += s[FILLS]-recorded_fills
            curve[-1, 6] = s[W]
            curve[-1, 7:9] = 0.
    return s, curve, decisions[:decision_count]


def fmz_vectors(cfg):
    cfg.validate()
    p = config_vector(cfg.base_config())
    q = np.array([cfg.base_spacing, cfg.base_amount_rate, cfg.base_amount_min, cfg.ratio,
        cfg.profit_target, cfg.warning_index, cfg.max_loss_notional_multiple, cfg.max_adds,
        cfg.reentry_delay_seconds, cfg.controller, EMA_WINDOWS.index(cfg.ema_fast_minutes),
        EMA_WINDOWS.index(cfg.ema_slow_minutes), cfg.trend_enter, cfg.trend_exit_fraction,
        cfg.trend_confirm_minutes, cfg.trend_liquidate_opposite, cfg.allow_countertrend_add,
        cfg.directional_stop, cfg.directional_trail, cfg.jev_action, cfg.jev_breakout_threshold,
        cfg.jev_direction_threshold, cfg.jev_hold_seconds, cfg.poll_seconds], dtype=np.float64)
    return p, q


def backtest_fmz(data, cfg, start=None, end=None, record=False, path_mode=None):
    def ms(value):
        return int(pd.Timestamp(value, tz='UTC').timestamp()*1000)
    a = 0 if start is None else int(np.searchsorted(data['times'], ms(start)))
    b = len(data['times']) if end is None else int(np.searchsorted(data['times'], ms(end)))
    if b <= a:
        raise ValueError('Empty simulation interval')
    arrays = [np.ascontiguousarray(data[k][a:b]) for k in ['times', 'bars', 'funding', 'news', 'minute_index']]
    p, q = fmz_vectors(cfg)
    stride = (60 if data['resolution'] == '1s' else 1) if record else 0
    s, curve, decisions = fmz_core(*arrays, data['features'], p, q, cfg.intrabar_mode if path_mode is None else path_mode, stride)
    result = {'initial_equity': 100., 'final_equity': float(s[W]), 'net_profit': float(s[W]-100),
        'return_pct': float(s[W]-100), 'max_drawdown_pct': float(s[MAXDD]*100),
        'fees': float(s[FEES]), 'funding_net': float(s[FUND]), 'liquidations': int(s[LIQ]),
        'halted': bool(s[HALT]), 'fills': int(s[FILLS]), 'martingale_adds': int(s[MADDS]),
        'take_profit_fills': int(s[TPS]), 'cycles': int(s[CYCLES]), 'risk_stops': int(s[RSTOP]),
        'event_flatten_count': int(s[ESTOP]), 'jev_signals': int(s[JEV_ACTIONS]),
        'jev_forced_leg_closes': int(s[JEV_LEG_CLOSES]), 'jev_veto_checks': int(s[JEV_VETOES]),
        'trend_entries': int(s[TREND_ENTRIES]), 'opposite_closes': int(s[CONTRA_CLOSES]),
        'regime_switches': int(s[SWITCHES]), 'post_tp_switches': int(s[POST_TP_SWITCHES]),
        'notional_cap_stops': int(s[CAP_STOPS]), 'directional_stops': int(s[DIR_STOPS]),
        'local_margin_rejections': int(s[REJECT]), 'warning_blocks': int(s[WARN_BLOCKS]),
        'peak_gross_notional': float(s[PGROSS]), 'turnover': float(s[TURN]), 'min_equity': float(s[MINEQ]),
        'bars': b-a, 'start': pd.to_datetime(arrays[0][0], unit='ms', utc=True).isoformat(),
        'last_bar': pd.to_datetime(arrays[0][-1], unit='ms', utc=True).isoformat()}
    if record:
        frame = pd.DataFrame(curve, columns=['timestamp', 'equity', 'long_qty', 'short_qty', 'regime', 'fills', 'wallet', 'long_adds', 'short_adds'])
        frame.attrs['decisions'] = pd.DataFrame(decisions, columns=['timestamp', 'bar_open', 'bar_close',
            'before_long', 'before_short', 'after_long', 'after_short', 'before_regime', 'after_regime',
            'fill_count', 'tp_count', 'add_count', 'jev_trigger_count', 'opposite_close_count', 'fee', 'equity'])
        return result, frame
    return result
