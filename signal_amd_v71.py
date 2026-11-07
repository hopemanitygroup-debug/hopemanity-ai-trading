"""AMD BTC V7.1: accumulation -> liquidity sweep -> reclaim (closed candles only).

Research/backtest signal, not a live execution or ML model.
"""
import math
from market_regime_v6 import Signal, classify

def decide_amd(hour, m15_window, m5, prev5, *, fee_per_side=0.001, slippage_per_side=0.0002):
    regime = classify(hour)
    if len(m15_window) < 13:
        return Signal(regime, "WAIT", "AMD: insufficient 15m history")
    base = m15_window.iloc[-13:-1]  # 12 preceding completed 15m candles
    sweep = m15_window.iloc[-1]     # latest completed 15m candle
    needed = ("open", "high", "low", "close", "atr", "volume", "vol_ma")
    try:
        rows = (sweep, m5, prev5)
        if not all(math.isfinite(float(getattr(r, k))) for r in rows for k in needed):
            return Signal(regime, "WAIT", "AMD: missing indicators")
        if not math.isfinite(float(hour.atr)) or hour.atr <= 0 or sweep.atr <= 0:
            return Signal(regime, "WAIT", "AMD: invalid ATR")
        upper, lower = float(base.high.max()), float(base.low.min())
        width = upper - lower
        if lower <= 0 or width <= 0:
            return Signal(regime, "WAIT", "AMD: invalid accumulation")
        # A bounded, reasonably quiet accumulation range, not an impulsive trend.
        if width / sweep.close < 0.0025 or width / sweep.close > 0.025:
            return Signal(regime, "WAIT", "AMD: accumulation width outside bounds")
        if sweep.atr / sweep.close > 0.012:
            return Signal(regime, "WAIT", "AMD: volatility shock")
        if sweep.vol_ma <= 0 or sweep.volume < 0.8 * sweep.vol_ma:
            return Signal(regime, "WAIT", "AMD: weak sweep volume")
        buffer = max(0.05 * sweep.atr, 0.0001 * sweep.close)
        # Manipulation: wick through prior range, close back inside.
        bullish = sweep.low < lower - buffer and lower < sweep.close < upper
        bearish = sweep.high > upper + buffer and lower < sweep.close < upper
        if bullish == bearish:
            return Signal(regime, "WAIT", "AMD: no unambiguous sweep and reclaim")
        side = "LONG" if bullish else "SHORT"
        # Distribution confirmation: completed 5m candle continues reclaim.
        confirmed = (m5.close > m5.open and m5.close > prev5.close and m5.close > lower
                     if bullish else
                     m5.close < m5.open and m5.close < prev5.close and m5.close < upper)
        if not confirmed:
            return Signal(regime, "WAIT", "AMD: no 5m distribution confirmation")
        # Avoid fighting a strong higher timeframe trend.
        if (regime == "BEAR" and bullish) or (regime == "BULL" and bearish):
            return Signal(regime, "WAIT", "AMD: against 1h trend")
        entry = float(m5.close)
        stop = (entry - (float(sweep.low) - buffer)) if bullish else ((float(sweep.high) + buffer) - entry)
        target = (upper - entry) if bullish else (entry - lower)
        cost = entry * 2 * (fee_per_side + slippage_per_side)
        if stop <= 0 or target <= 0 or target < 1.7 * stop + cost or target <= 2 * cost:
            return Signal(regime, "WAIT", "AMD: reward insufficient after costs")
        return Signal(regime, side, "AMD: sweep, reclaim, 5m distribution confirmed", stop, target)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return Signal(regime, "WAIT", "AMD: malformed indicator input")
