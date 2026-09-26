from dataclasses import replace
import numpy as np
import pytest
from jevmesh.fmz_config import FMZConfig
from jevmesh.fmz_engine import *


def initialized():
    s = np.zeros(FMZ_STATE_SIZE)
    s[W] = s[PEAK] = s[MINEQ] = 100.
    return s


def fixture(prices, cfg, news=None):
    times = 1788220800000+np.arange(len(prices), dtype=np.int64)*1000
    features = np.full((len(prices), 11), 60000.)
    return {'times': times, 'bars': np.repeat(np.array(prices, dtype=float)[:, None], 8, axis=1),
            'funding': np.zeros(len(prices)), 'news': np.zeros((len(prices), 2), dtype=np.float32) if news is None else news,
            'minute_index': np.arange(len(prices), dtype=np.int32), 'features': features, 'resolution': '1m'}


def test_linear_grid_stays_anchored_after_add():
    c = replace(FMZConfig(), controller=0, jev_action=0, max_loss_notional_multiple=9)
    p, q = fmz_vectors(c)
    s = initialized()
    ts = 1788220800000
    open_side(s, 1, 60000, 60000, ts, p, q, .002)
    refresh_quotes(s, 1, 60000, ts+1000, p, q, True)
    assert s[OP_L] == 57000
    assert s[OQ_L] == pytest.approx(.003)
    fmz_segment(s, 60000, 56500, 60000, 56500, ts+1000, p, q, np.zeros(2, dtype=np.bool_))
    refresh_quotes(s, 1, 56500, ts+2000, p, q, True)
    assert s[NL] == 1 and s[OP_L] == 54000
    assert s[OQ_L] == pytest.approx(.004)


def test_zero_account_drawdown_limit_disables_fmz_path_barrier():
    cfg = replace(FMZConfig(), account_drawdown_stop=0, controller=0, jev_action=0)
    p, q = fmz_vectors(cfg)
    s = initialized()
    s[QL], s[AL] = .001, 60000
    fmz_segment(s, 60000, 50000, 60000, 50000, 1788220800000, p, q,
                np.ones(2, dtype=np.bool_))
    assert s[QL] == .001 and s[HALT] == 0


def test_long_and_short_spacing_are_independent():
    p, q = fmz_vectors(FMZConfig())
    s = initialized()
    ts = 1788220800000
    open_side(s, 1, 60000, 60000, ts, p, q, .001)
    open_side(s, -1, 65000, 65000, ts+1000, p, q, .001)
    assert s[GS_L] == 3000 and s[GS_S] == 3250


def test_quote_rejection_does_not_advance_martingale_level():
    c = replace(FMZConfig(), controller=0, max_loss_notional_multiple=20, gross_utilization=.2)
    p, q = fmz_vectors(c)
    s = initialized()
    ts = 1788220800000
    open_side(s, 1, 60000, 60000, ts, p, q, .002)
    refresh_quotes(s, 1, 60000, ts+1000, p, q, True)
    raw = s[RAW_L]
    fmz_segment(s, 60000, 56500, 60000, 56500, ts+1000, p, q, np.zeros(2, dtype=np.bool_))
    assert s[REJECT] >= 1 and s[NL] == 0 and s[RAW_L] == raw


def test_every_fmz_order_uses_strict_trade_notional_floor():
    c = replace(FMZConfig(), base_amount_min=20, min_trade_notional=130,
                controller=0, max_adds=1, max_loss_notional_multiple=20)
    p, q = fmz_vectors(c)
    ts = 1788220800000
    assert fmz_order_qty(1, 60000, ts, p, q)*60000 > 100
    s = initialized()
    assert open_side(s, 1, 60000, 60000, ts, p, q, .002)
    refresh_quotes(s, 1, 60000, ts+1000, p, q, True)
    assert s[OQ_L]*s[OP_L] > 100


def test_post_tp_controller_closes_loser_and_reopens_only_winner():
    c = replace(FMZConfig(), controller=1, profit_target=.01, trend_confirm_minutes=0, jev_action=0)
    d = fixture([60000, 60000, 60700, 60700, 60700], c)
    d['features'][:, 2] = 61000  # EMA 60 above slow EMA 720.
    d['features'][:, 9] = 61000
    r, curve = backtest_fmz(d, c, record=True)
    assert r['post_tp_switches'] == 1 and r['opposite_closes'] == 1
    assert curve.loc[3, 'long_qty'] > 0 and curve.loc[3, 'short_qty'] == 0
    assert curve.loc[3, 'regime'] == 1


def test_trend_only_mode_can_open_single_leg_under_low_gross_cap():
    c = replace(FMZConfig(), initial_equity=1000, gross_utilization=.15, controller=4,
                ema_fast_minutes=60, ema_slow_minutes=120,
                trend_enter=.005, trend_confirm_minutes=0,
                max_adds=0, jev_action=0)
    d = fixture([60000, 60000, 60000], c)
    d['features'][:, 2] = 61000  # EMA 60 minutes.
    d['features'][:, 3] = 60000  # EMA 120 minutes.
    d['features'][:, 9] = 61000  # Last completed close.
    r, curve = backtest_fmz(d, c, record=True)
    assert r['fills'] >= 1
    assert curve.loc[0, 'long_qty'] > 0 and curve.loc[0, 'short_qty'] == 0


def test_jev_flatten_stops_both_and_blocks_immediate_reentry():
    c = replace(FMZConfig(), controller=0, jev_breakout_threshold=.3)
    news = np.zeros((5, 2), dtype=np.float32)
    news[2] = [.8, .1]
    d = fixture([60000]*5, c, news)
    r, curve = backtest_fmz(d, c, record=True)
    assert r['event_flatten_count'] == 1
    assert curve.loc[2:, ['long_qty', 'short_qty']].to_numpy().sum() == 0


def test_jev_directional_control_closes_only_opposite_side():
    c = replace(FMZConfig(), controller=0, jev_action=2, jev_breakout_threshold=.3, jev_direction_threshold=.05)
    news = np.zeros((5, 2), dtype=np.float32)
    news[2] = [.8, .2]
    d = fixture([60000]*5, c, news)
    r, curve = backtest_fmz(d, c, record=True)
    assert r['jev_forced_leg_closes'] == 1
    assert curve.loc[2, 'long_qty'] > 0 and curve.loc[2, 'short_qty'] == 0


def test_jev_directional_full_pause_closes_opposite_and_blocks_both_sides():
    c = replace(FMZConfig(), controller=0, jev_action=4, jev_breakout_threshold=.3, jev_direction_threshold=.05)
    news = np.zeros((6, 2), dtype=np.float32)
    news[2] = [.8, .2]
    d = fixture([60000]*6, c, news)
    r, curve = backtest_fmz(d, c, record=True)
    assert r['jev_forced_leg_closes'] == 1
    assert curve.loc[2, 'long_qty'] > 0 and curve.loc[2, 'short_qty'] == 0
    assert curve.loc[3:4, 'fills'].sum() == 0


def test_one_sided_risk_guard_closes_opposite_and_locks_direction():
    c = replace(FMZConfig(), controller=0, jev_action=0, risk_guard=1,
                risk_fast_minutes=15, risk_slow_minutes=60,
                risk_enter=.002, risk_confirm_minutes=0)
    p, q = fmz_vectors(c)
    s = initialized()
    ts = 1788220800000
    assert open_side(s, 1, 60000, 60000, ts, p, q, .001)
    assert open_side(s, -1, 60000, 60000, ts, p, q, .001)
    feature = np.full(11, 60000.)
    feature[0] = 61000.  # EMA 15 minutes.
    feature[2] = 60000.  # EMA 60 minutes.
    feature[9] = 61000.  # Last completed close.
    update_risk_guard(s, feature, 60000, 60000, ts, p, q)
    assert s[RISK_REGIME] == 1
    assert s[QS] == 0
    assert s[RISK_CLOSES] == 1


def test_equity_fee_conservation_on_unchanged_hedge():
    c = replace(FMZConfig(), controller=0, jev_action=0)
    d = fixture([60000]*5, c)
    r = backtest_fmz(d, c)
    assert r['final_equity'] == pytest.approx(100-4*.003*60000*(c.taker_fee+c.slippage_bps/10000))


def test_gap_liquidation_stops_everything_without_topup():
    p, q = fmz_vectors(FMZConfig())
    s = initialized()
    s[QL], s[AL], s[CW] = .01, 60000, 100
    s[OQ_L], s[TP_L] = .002, 70000
    fmz_risk(s, 40000, 40000, 1788220800000, p, q)
    assert s[LIQ] == 1 and s[HALT] == 1 and s[W] == 0
    assert s[OQ_L] == 0 and s[TP_L] == 0


def test_trend_uses_completed_minute_features_not_current_future_close():
    c = replace(FMZConfig(), controller=4, trend_confirm_minutes=0, jev_action=0)
    d = fixture([60000]*4, c)
    # A future feature row is extreme but not referenced until its timestamp.
    d['features'][3, 2] = 70000
    d['features'][3, 9] = 70000
    r, curve = backtest_fmz(d, c, record=True)
    assert curve.loc[:2, 'fills'].sum() == 0


def test_leverage_cap_is_retained():
    with pytest.raises(AssertionError):
        replace(FMZConfig(), leverage=20).validate()


def test_initial_equity_is_passed_through_and_reported():
    c = replace(FMZConfig(), initial_equity=1000, controller=4, jev_action=0)
    p, _ = fmz_vectors(c)
    assert p[0] == 1000
    r = backtest_fmz(fixture([60000, 60000], c), c)
    assert r['initial_equity'] == 1000


def test_new_marketable_limit_is_a_taker_and_respects_its_price_limit():
    c = replace(FMZConfig(), controller=0, jev_action=0, max_loss_notional_multiple=9)
    p, q = fmz_vectors(c)
    s = initialized()
    ts = 1788220800000
    open_side(s, 1, 60000, 60000, ts, p, q, .002)
    old_fee = s[FEES]
    refresh_quotes(s, 1, 56000, ts+1000, p, q, True)
    fmz_path(s, np.full(8, 56000.), ts+1000, p, q, True)
    assert s[NL] == 1
    assert s[FEES]-old_fee == pytest.approx(.003*56000*1.0002*c.taker_fee)


def test_existing_resting_limit_uses_maker_fee_on_a_gap():
    c = replace(FMZConfig(), controller=0, jev_action=0, max_loss_notional_multiple=9)
    p, q = fmz_vectors(c)
    s = initialized()
    ts = 1788220800000
    open_side(s, 1, 60000, 60000, ts, p, q, .002)
    old_fee = s[FEES]
    refresh_quotes(s, 1, 60000, ts+1000, p, q, True)
    fmz_path(s, np.full(8, 56000.), ts+2000, p, q, True)
    assert s[FEES]-old_fee == pytest.approx(.003*57000*c.maker_fee)


def test_both_sides_take_profit_respects_reentry_delay():
    c = replace(FMZConfig(), controller=0, jev_action=0, profit_target=.01, reentry_delay_seconds=30)
    d = fixture([60000]*3, c)
    d['bars'][1] = [60000, 61000, 59000, 60000, 60000, 61000, 59000, 60000]
    r, curve = backtest_fmz(d, c, record=True)
    assert r['take_profit_fills'] == 2 and r['fills'] == 4
    assert curve.loc[2, 'long_qty'] == 0 and curve.loc[2, 'short_qty'] == 0


def test_jev_add_veto_keeps_existing_take_profit_live():
    c = replace(FMZConfig(), controller=0, jev_action=3, jev_breakout_threshold=.3, max_loss_notional_multiple=9)
    news = np.zeros((5, 2), dtype=np.float32)
    news[2] = [.9, .1]
    d = fixture([60000, 60000, 61900, 63050, 63050], c, news)
    r, curve = backtest_fmz(d, c, record=True)
    assert r['take_profit_fills'] == 1
    assert curve.loc[3, 'long_qty'] == 0 and curve.loc[3, 'short_qty'] > 0


def test_both_pending_adds_reserve_margin_and_rejection_keeps_tp():
    c = replace(FMZConfig(), gross_utilization=.6, controller=0, jev_action=0)
    p, q = fmz_vectors(c)
    s = initialized()
    ts = 1788220800000
    open_side(s, 1, 60000, 60000, ts, p, q, .002)
    open_side(s, -1, 60000, 60000, ts, p, q, .002)
    refresh_quotes(s, 1, 60000, ts+1000, p, q, True)
    refresh_quotes(s, -1, 60000, ts+1000, p, q, True)
    assert s[OQ_L] == pytest.approx(.003)
    assert s[OQ_S] == 0 and s[TP_S] > 0 and s[REJECT] == 1
    assert (s[QL]+s[QS])*60000+s[OQ_L]*s[OP_L] < equity(s, 60000)*6


def test_reopening_side_cannot_spend_other_sides_reserved_margin():
    c = replace(FMZConfig(), gross_utilization=.6, controller=0, jev_action=0, max_loss_notional_multiple=9)
    p, q = fmz_vectors(c)
    s = initialized()
    ts = 1788220800000
    assert open_side(s, 1, 60000, 60000, ts, p, q, .002)
    refresh_quotes(s, 1, 60000, ts+1000, p, q, True)
    assert not open_side(s, -1, 60000, 60000, ts+1000, p, q, .006)
    assert s[QS] == 0 and s[OQ_L] > 0
