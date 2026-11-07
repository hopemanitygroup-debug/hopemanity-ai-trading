"""Unit tests for the V8 hybrid overlay (synthetic, no market data)."""
from types import SimpleNamespace
from unittest.mock import patch
import pandas as pd
from market_regime_v6 import Signal
from signal_hybrid_v8 import decide_hybrid

def row(**kwargs):
    d = dict(close=100., open=99., high=101., low=99., atr=1.,
             ema20=101., ema50=100., adx=25., macdh=0.5)
    d.update(kwargs)
    return SimpleNamespace(**d)

def window(**kwargs):
    return pd.DataFrame([vars(row(**kwargs)) for _ in range(13)])

def test_wait_from_amd_is_preserved():
    with patch("signal_hybrid_v8.decide_amd", return_value=Signal("BULL", "WAIT", "no sweep")):
        s = decide_hybrid(row(), window(), row(), row())
    assert s.side == "WAIT"

def test_hybrid_accepts_confirmed_long():
    with patch("signal_hybrid_v8.decide_amd", return_value=Signal("BULL", "LONG", "AMD", 1., 3.)):
        s = decide_hybrid(row(), window(), row(), row())
    assert s.side == "LONG"

def test_hybrid_rejects_unaligned_trend():
    with patch("signal_hybrid_v8.decide_amd", return_value=Signal("BULL", "LONG", "AMD", 1., 3.)):
        s = decide_hybrid(row(), window(ema20=99., ema50=100.), row(), row())
    assert s.side == "WAIT" and "trend" in s.reason

def test_hybrid_rejects_weak_reward():
    with patch("signal_hybrid_v8.decide_amd", return_value=Signal("BULL", "LONG", "AMD", 1., 2.)):
        s = decide_hybrid(row(), window(), row(), row())
    assert s.side == "WAIT" and "reward" in s.reason
