from types import SimpleNamespace
from unittest.mock import patch
import pandas as pd
from market_regime_v6 import Signal
from signal_selective_v81 import decide_selective

WAIT=Signal("BULL","WAIT","no setup")
LONG=Signal("BULL","LONG","entry",1.,3.)

def row(**kw):
    d=dict(open=99.,close=100.,high=101.,low=99.,atr=1.,
           ema20=101.,ema50=100.,macdh=0.5,adx=30.)
    d.update(kw)
    return SimpleNamespace(**d)

def evaluate(v6,amd,*,h=None,m=None,f=None):
    with patch("signal_selective_v81.decide_v6",return_value=v6),patch("signal_selective_v81.decide_amd",return_value=amd):
        return decide_selective(h or row(close=103.,ema20=102.,ema50=100.),pd.DataFrame([vars(m or row()) for _ in range(13)]),row(),f or row(),row())

def test_preserves_v6_without_amd():
    assert evaluate(LONG,WAIT).side=="LONG"

def test_rejects_amd_when_not_25r_after_costs():
    assert evaluate(WAIT,Signal("BULL","LONG","low reward",1.,2.)).side=="WAIT"

def test_accepts_confirmed_amd():
    assert evaluate(WAIT,Signal("BULL","LONG","sweep",1.,4.)).side=="LONG"

def test_rejects_amd_against_15m_trend():
    assert evaluate(WAIT,Signal("BULL","LONG","sweep",1.,4.),m=row(ema20=99.)).side=="WAIT"

def test_volatility_protection_blocks_v6():
    assert evaluate(LONG,WAIT,f=row(high=105.,low=99.)).side=="WAIT"

def test_range_does_not_open_amd():
    assert evaluate(WAIT,Signal("RANGE","LONG","sweep",1.,4.),h=row(adx=10.)).side=="WAIT"
