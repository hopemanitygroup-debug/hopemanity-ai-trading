from types import SimpleNamespace
import pandas as pd
from signal_amd_v71 import decide_amd

def row(**kw):
    defaults = dict(open=100, high=101, low=99, close=100, atr=1,
                    volume=120, vol_ma=100)
    defaults.update(kw)
    return SimpleNamespace(**defaults)

def test_wait_on_insufficient_history():
    s = decide_amd(row(), pd.DataFrame(), row(), row())
    assert s.side == "WAIT"

def test_wait_on_no_sweep():
    frame = pd.DataFrame([vars(row()) for _ in range(13)])
    s = decide_amd(row(), frame, row(), row())
    assert s.side == "WAIT"

def test_no_lookahead_signal_requires_closed_reclaim():
    frame = pd.DataFrame([vars(row()) for _ in range(12)] +
                         [vars(row(low=97, close=98))])
    s = decide_amd(row(), frame, row(), row())
    assert s.side == "WAIT"
