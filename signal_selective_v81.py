"""BTC V8.1 selective fusion: preserve V6, gate AMD tightly.

Paper/backtest only; never places exchange orders.
Uses only completed 1h, 15m, and 5m candles.
"""
import math
from dataclasses import replace
from market_regime_v6 import Signal, classify, decide as decide_v6
from signal_amd_v71 import decide_amd

def decide_selective(hour, m15_window, prev15, m5, prev5, *,
                     fee_per_side=0.001, slippage_per_side=0.0002):
    m15 = m15_window.iloc[-1]
    kw = dict(fee_per_side=fee_per_side, slippage_per_side=slippage_per_side)
    base = decide_v6(hour, m15, prev15, m5, prev5, **kw)
    regime = classify(hour)
    try:
        if not all(math.isfinite(float(getattr(r, k))) for r, keys in (
            (m15, ("close", "atr", "ema20", "ema50", "macdh", "adx")),
            (m5, ("close", "atr", "high", "low"))) for k in keys):
            return Signal(regime, "WAIT", "V8.1: incomplete indicators")
        if m15.close <= 0 or m15.atr <= 0 or m5.atr <= 0:
            return Signal(regime, "WAIT", "V8.1: invalid ATR")
        if m15.atr / m15.close > 0.015 or m5.high - m5.low > 2.5 * m5.atr:
            return Signal(regime, "WAIT", "V8.1: V7 volatility protection")
        if base.side in ("LONG", "SHORT"):
            # Keep validated V6 entry logic and 2.2R target; do not apply
            # V7's 2.5R gate which suppressed all V7 trades.
            return replace(base, reason="V8.1: V6 entry with V7 volatility protection")
        # AMD cannot enter in range/transition; must agree with both 1h
        # direction and 15m EMA/MACD/ADX trend, and pass net 2.5R.
        if regime not in ("BULL", "BEAR"):
            return Signal(regime, "WAIT", "V8.1: AMD disabled outside trends")
        amd = decide_amd(hour, m15_window, m5, prev5, **kw)
        expected = "LONG" if regime == "BULL" else "SHORT"
        if amd.side != expected:
            return Signal(regime, "WAIT", "V8.1: no aligned AMD entry")
        aligned = (m15.ema20 > m15.ema50 and m15.macdh > 0 if expected == "LONG"
                   else m15.ema20 < m15.ema50 and m15.macdh < 0)
        if not aligned or m15.adx < 23:
            return Signal(regime, "WAIT", "V8.1: AMD 15m trend not confirmed")
        cost = float(m5.close) * 2 * (fee_per_side + slippage_per_side)
        if amd.stop_distance <= 0 or amd.target_distance < 2.5 * amd.stop_distance + cost:
            return Signal(regime, "WAIT", "V8.1: AMD net reward/risk too low")
        return replace(amd, reason="V8.1: AMD sweep + V6 regime + V7 trend/risk confirmed")
    except (AttributeError, TypeError, ValueError, OverflowError):
        return Signal(regime, "WAIT", "V8.1: malformed indicators")
