from unittest.mock import patch
from types import SimpleNamespace
import pandas as pd
from market_regime_v6 import Signal
from signal_fusion_v8 import decide_fusion

def row(**kw):
    d=dict(close=100.,open=99.,high=101.,low=99.,atr=1.,ema20=101.,ema50=100.,adx=25.,macdh=0.5)
    d.update(kw)
    return SimpleNamespace(**d)

def run(v6, v7, amd, m5=None):
    with patch("signal_fusion_v8.decide_v6",return_value=v6), patch("signal_fusion_v8.decide_v7",return_value=v7), patch("signal_fusion_v8.decide_amd",return_value=amd):
        return decide_fusion(row(),pd.DataFrame([vars(row()) for _ in range(13)]),row(),m5 or row(),row())

WAIT=Signal("BULL","WAIT","none")
LONG=Signal("BULL","LONG","entry",1.,3.)

def test_v6_entry_survives_amd_wait_and_v7_wait():
    s=run(LONG,WAIT,WAIT)
    assert s.side=="LONG" and "V6" in s.reason

def test_amd_entry_survives_v6_wait():
    s=run(WAIT,WAIT,LONG)
    assert s.side=="LONG" and "AMD" in s.reason

def test_no_entry_when_both_wait():
    assert run(WAIT,WAIT,WAIT).side=="WAIT"

def test_v7_spike_blocks_trade():
    assert run(LONG,WAIT,WAIT,row(high=110.,low=99.,atr=1.)).side=="WAIT"
