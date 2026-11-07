"""V8 three-engine fusion: V6 regime + V7 risk + V7.1 AMD.

V6 remains an independent entry source (never require AMD for V6 trades).
AMD is an alternative entry source. V7 contributes 15m trend and volatility
safety checks, but its overly restrictive 2.5R filter is NOT copied.
All inputs are completed candles; paper/backtest only.
"""
import math
from dataclasses import replace
from market_regime_v6 import decide as decide_v6
from signal_v7 import decide_v7
from signal_amd_v71 import decide_amd

def decide_fusion(hour, m15_window, prev15, m5, prev5, *,
                  fee_per_side=0.001, slippage_per_side=0.0002):
    m15 = m15_window.iloc[-1]
    kw = dict(fee_per_side=fee_per_side, slippage_per_side=slippage_per_side)
    v6 = decide_v6(hour, m15, prev15, m5, prev5, **kw)
    v7 = decide_v7(hour, m15, prev15, m5, prev5, **kw)
    amd = decide_amd(hour, m15_window, m5, prev5, **kw)
    # Favor a validated V6 setup, and let AMD provide additional entries.
    # If both independently agree, take the more conservative stop/target.
    if v6.side in ("LONG", "SHORT"):
        if amd.side == v6.side:
            chosen = v6 if v6.target_distance / v6.stop_distance >= amd.target_distance / amd.stop_distance else amd
            source = "V6+AMD"
        else:
            chosen, source = v6, "V6"
    elif amd.side in ("LONG", "SHORT"):
        chosen, source = amd, "AMD"
    else:
        return replace(v6, reason=f"FUSION: no entry; V6={v6.reason}; AMD={amd.reason}")
    try:
        if not all(math.isfinite(float(getattr(m15, k))) for k in ("ema20", "ema50", "adx", "macdh")):
            return replace(chosen, side="WAIT", reason="FUSION: incomplete V7 trend indicators")
        if not math.isfinite(float(m5.atr)) or m5.atr <= 0:
            return replace(chosen, side="WAIT", reason="FUSION: invalid 5m ATR")
        # V7's 5m spike protection, without its 2.5R trade-suppressing gate.
        if m5.high - m5.low > 2.5 * m5.atr:
            return replace(chosen, side="WAIT", reason="FUSION: V7 volatility spike")
        if chosen.regime in ("BULL", "BEAR"):
            up = chosen.side == "LONG"
            aligned = (m15.ema20 > m15.ema50 and m15.macdh > 0 if up
                       else m15.ema20 < m15.ema50 and m15.macdh < 0)
            # V7 confirmation is informative; preserve original V6 setups
            # instead of imposing the zero-trade V7 gating behavior.
            confirmation = "V7 confirmed" if v7.side == chosen.side else ("trend aligned" if aligned else "V7 unconfirmed")
        else:
            confirmation = "range mode"
        cost = float(m5.close) * 2 * (fee_per_side + slippage_per_side)
        if chosen.stop_distance <= 0 or chosen.target_distance <= max(1.5 * chosen.stop_distance, 2 * cost):
            return replace(chosen, side="WAIT", reason="FUSION: insufficient reward after costs")
        return replace(chosen, reason=f"FUSION: {source}; {confirmation}")
    except (AttributeError, TypeError, ValueError, OverflowError, ZeroDivisionError):
        return replace(chosen, side="WAIT", reason="FUSION: invalid indicator data")
