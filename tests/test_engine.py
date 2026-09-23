from dataclasses import replace
import numpy as np
import pytest
from jevmesh.config import StrategyConfig
from jevmesh.engine import *


def simple(prices, signals=None, funding=None, **overrides):
    times = np.arange(len(prices), dtype=np.int64)*1000+1788220800000
    bars = np.repeat(np.asarray(prices, dtype=float)[:, None], 8, axis=1)
    data = dict(times=times, bars=bars, funding=np.zeros(len(prices)) if funding is None else funding,
                signals=np.zeros(len(prices)) if signals is None else signals,
                trends=np.zeros(len(prices)), ranges=np.zeros(len(prices)))
    cfg = replace(StrategyConfig(), base_notional=100, grid_step=.02, take_profit=.02, **overrides)
    return data, cfg


def state(wallet=100):
    s = np.zeros(STATE_SIZE)
    s[W] = s[PEAK] = s[MINEQ] = wallet
    return s


def test_flat_price_charges_all_four_legs_and_slippage():
    d, c = simple([60000, 60000])
    r = run_backtest(d, c)
    qty = .002
    expected_fee = 4*qty*60000*c.taker_fee
    expected_slip = 4*qty*60000*c.slippage_bps/10000
    assert r['fees'] == pytest.approx(expected_fee)
    assert r['final_equity'] == pytest.approx(100-expected_fee-expected_slip)
    assert r['fills'] == 4


def test_funding_nets_equal_hedge():
    d, c = simple([60000, 60000], funding=np.array([0., .01]))
    assert run_backtest(d, c)['funding_net'] == 0


def test_minimum_notional_historical_change_and_quantity_rounding():
    p = config_vector(StrategyConfig())
    assert order_qty(50, 60000, 1776162599999, p) == pytest.approx(.002)
    assert order_qty(50, 60000, 1776162600000, p) == pytest.approx(.001)
    assert order_qty(75, 60000, 1788220800000, p) == pytest.approx(.002)


def test_margin_rejects_excess_inventory_without_deposit():
    s = state()
    p = config_vector(StrategyConfig())
    assert not open_leg(s, 1, 60000, 60000, .02, 1788220800000, p)
    assert s[W] == 100 and s[QL] == 0 and s[REJECT] == 1


def test_event_flattens_both_sides_and_cooldown_blocks_reentry():
    d, c = simple([60000]*5, signals=np.array([0., .99, 0., 0., 0.]))
    r, curve = run_backtest(d, c, record=True)
    assert r['event_stops'] == 1
    assert curve.loc[1:, ['long_qty', 'short_qty']].to_numpy().sum() == 0
    assert r['fills'] == 4


def test_adverse_gap_liquidates_before_ordinary_stop():
    s = state()
    s[QL], s[AL], s[CW] = .01, 60000, 100
    p = config_vector(StrategyConfig())
    assert check_risk(s, 40000, 40000, 1788220800000, p)
    assert s[LIQ] == 1 and s[HALT] == 1 and s[W] == 0
    assert s[QL] == 0 and s[QS] == 0


def test_continuous_mark_barrier_closes_before_liquidation():
    s = state()
    s[QL], s[AL], s[LL], s[OL], s[CW], s[NL] = .005, 60000, 60000, .005, 100, 8
    p = config_vector(StrategyConfig())
    segment(s, 60000, 40000, 60000, 40000, 1788220800000, p, np.zeros(2, dtype=np.bool_), np.zeros(2, dtype=np.bool_))
    assert s[LIQ] == 0 and s[RSTOP] == 1 and s[QL] == 0
    assert 87 < s[W] < 88


def test_mark_gap_alone_can_liquidate():
    s = state()
    s[QL], s[AL], s[CW] = .01, 60000, 100
    p = config_vector(StrategyConfig())
    assert check_risk(s, 60000, 49000, 1788220800000, p)
    assert s[LIQ] == 1


def test_insufficient_margin_opens_neither_initial_leg():
    d, c = simple([60000], leverage=1)
    r = run_backtest(d, c)
    assert r['fills'] == 0 and r['final_equity'] == 100


def test_user_cap_cannot_be_relaxed():
    with pytest.raises(ValueError):
        replace(StrategyConfig(), leverage=11).validate()
    with pytest.raises(ValueError):
        replace(StrategyConfig(), initial_equity=1000).validate()


def test_resting_take_profit_fills_when_next_trade_gaps_through_limit():
    s = state()
    s[QL], s[AL], s[LL], s[OL], s[CW] = .002, 60000, 60000, .002, 100
    p = config_vector(StrategyConfig())
    run_path(s, np.full(8, 61000.), 1788220800000, p, True)
    assert s[QL] == 0 and s[TPS] == 1
    assert s[W] < 102  # Filled at the resting price, not gifted the gap improvement.
