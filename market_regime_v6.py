"""Deterministic BTC V6 regime-aware signal engine (paper/backtest only).

Inputs must be CLOSED 1h, 15m and 5m indicator rows; caller enforces
timestamp alignment. No ML model or real exchange orders.
"""
from dataclasses import dataclass
import math

@dataclass(frozen=True)
class Signal:
    regime: str
    side: str
    reason: str
    stop_distance: float = 0.0
    target_distance: float = 0.0

def _valid(row, fields):
    try:
        return all(math.isfinite(float(getattr(row, k))) for k in fields)
    except (AttributeError, ValueError, TypeError):
        return False

def classify(hour):
    fields = ("close", "ema20", "ema50", "adx", "atr")
    if not _valid(hour, fields) or hour.close <= 0 or hour.atr <= 0:
        return "UNKNOWN"
    if hour.adx >= 25 and hour.close > hour.ema20 > hour.ema50:
        return "BULL"
    if hour.adx >= 25 and hour.close < hour.ema20 < hour.ema50:
        return "BEAR"
    if hour.adx < 20:
        return "RANGE"
    return "TRANSITION"

def decide(hour, m15, prev15, m5, prev5, *,
           fee_per_side=0.001, slippage_per_side=0.0002):
    regime = classify(hour)
    required = ("open", "high", "low", "close", "rsi", "atr", "vol_ma", "volume", "bb_low", "bb_high", "bb_mid")
    if regime in ("UNKNOWN", "TRANSITION") or not all(
        _valid(r, required) for r in (m15, prev15, m5, prev5)
    ):
        return Signal(regime, "WAIT", "Uncertain regime or incomplete indicators")
    if m15.atr <= 0 or m5.atr <= 0 or m15.vol_ma <= 0 or m5.vol_ma <= 0:
        return Signal(regime, "WAIT", "Invalid ATR or volume baseline")
    if m15.bb_high <= m15.bb_low:
        return Signal(regime, "WAIT", "Invalid range boundaries")
    # Avoid volatility shocks: 15m ATR > 1.5% of price.
    if m15.atr / m15.close > 0.015:
        return Signal(regime, "WAIT", "Volatility shock")
    side = "WAIT"
    stop = target = 0.0
    if regime in ("BULL", "BEAR"):
        up = regime == "BULL"
        vol = m15.volume >= 1.1 * m15.vol_ma and m5.volume >= 1.1 * m5.vol_ma
        pullback = (m15.low <= m15.bb_mid if up else m15.high >= m15.bb_mid)
        reversal = (prev15.rsi < 48 and m15.rsi > prev15.rsi and m15.close > m15.open
                    if up else prev15.rsi > 52 and m15.rsi < prev15.rsi and m15.close < m15.open)
        momentum = (m5.close > m5.open and m5.close > prev5.close
                    if up else m5.close < m5.open and m5.close < prev5.close)
        if vol and pullback and reversal and momentum:
            side = "LONG" if up else "SHORT"
            stop = 1.8 * m15.atr
            target = 2.2 * stop
    else:
        # Mean reversion is disabled if the channel is too narrow to pay costs.
        width = m15.bb_high - m15.bb_low
        if width / m15.close < 0.006:
            return Signal(regime, "WAIT", "Range too narrow after costs")
        vol = m5.volume >= 0.8 * m5.vol_ma
        if vol and m15.low <= m15.bb_low * 1.002 and prev15.rsi < 35 and m15.rsi > prev15.rsi and m5.close > m5.open:
            side = "LONG"
            stop = min(1.2 * m15.atr, width * 0.35)
            target = max(0.0, m15.bb_mid - m5.close)
        elif vol and m15.high >= m15.bb_high * 0.998 and prev15.rsi > 65 and m15.rsi < prev15.rsi and m5.close < m5.open:
            side = "SHORT"
            stop = min(1.2 * m15.atr, width * 0.35)
            target = max(0.0, m5.close - m15.bb_mid)
    if side == "WAIT":
        return Signal(regime, side, "Setup not confirmed")
    # Conservative round-trip cost hurdle; ensure target > 1.5x risk.
    round_trip_cost = m5.close * 2 * (fee_per_side + slippage_per_side)
    if stop <= 0 or target <= max(1.5 * stop, 2 * round_trip_cost):
        return Signal(regime, "WAIT", "Insufficient reward after fees/slippage")
    return Signal(regime, side, "Confirmed closed-candle setup", stop, target)
