from types import SimpleNamespace as R
from market_regime_v6 import classify, decide

def row(**kwargs):
    defaults = dict(open=100, high=102, low=98, close=100, ema20=100,
                    ema50=100, adx=15, atr=1, rsi=50, vol_ma=100,
                    volume=120, bb_low=96, bb_mid=100, bb_high=104)
    defaults.update(kwargs)
    return R(**defaults)

def test_regimes():
    assert classify(row(close=105, ema20=103, ema50=101, adx=30)) == "BULL"
    assert classify(row(close=95, ema20=97, ema50=99, adx=30)) == "BEAR"
    assert classify(row(adx=12)) == "RANGE"
    assert classify(row(adx=22)) == "TRANSITION"

def test_wait_on_transition():
    r = row()
    assert decide(row(adx=22), r, r, r, r).side == "WAIT"

def test_bull_entry():
    h = row(close=105, ema20=103, ema50=101, adx=30)
    m = row(open=99, close=101, low=99, rsi=49, atr=1)
    p = row(rsi=43)
    f = row(open=100, close=101, volume=150)
    assert decide(h, m, p, f, row(close=100)).side == "LONG"

def test_bear_entry():
    h = row(close=95, ema20=97, ema50=99, adx=30)
    m = row(open=101, close=99, high=101, rsi=50)
    p = row(rsi=57)
    f = row(open=100, close=99)
    assert decide(h, m, p, f, row(close=100)).side == "SHORT"

def test_range_too_narrow():
    h = row(adx=10)
    m = row(bb_low=99.8, bb_high=100.2)
    assert decide(h, m, row(), row(), row()).side == "WAIT"
