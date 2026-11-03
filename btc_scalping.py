"""BTCUSDT 5m/15m paper-only scalping signals. No order execution."""
from dataclasses import dataclass
from statistics import mean
from typing import Sequence

@dataclass(frozen=True)
class Candle:
    open: float
    high: float
    low: float
    close: float
    volume: float

@dataclass(frozen=True)
class Signal:
    action: str
    reason: str
    entry: float = 0.0
    stop: float = 0.0
    target: float = 0.0
    notional: float = 0.0
    margin: float = 0.0
    estimated_risk: float = 0.0

def ema(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        raise ValueError("Not enough candles")
    value = mean(values[:period])
    alpha = 2 / (period + 1)
    for price in values[period:]:
        value = alpha * price + (1 - alpha) * value
    return value

def rsi(values: Sequence[float], period: int = 14) -> float:
    if len(values) < period + 1:
        raise ValueError("Not enough candles")
    diffs = [b-a for a,b in zip(values[-period-1:-1],values[-period:])]
    gain = mean([max(d,0) for d in diffs])
    loss = mean([max(-d,0) for d in diffs])
    return 100 if loss == 0 else 100 - 100 / (1 + gain/loss)

def atr(candles: Sequence[Candle], period: int = 14) -> float:
    if len(candles) < period + 1:
        raise ValueError("Not enough candles")
    tr = [max(c.high-c.low, abs(c.high-prev.close), abs(c.low-prev.close))
          for prev,c in zip(candles[-period-1:-1],candles[-period:])]
    return mean(tr)

def signal(five: Sequence[Candle], fifteen: Sequence[Candle],
           equity: float = 1000, leverage: float = 50,
           risk_usd: float = 10, fee_per_side: float = 0.0005,
           slippage_per_side: float = 0.0002) -> Signal:
    """Pass CLOSED candles only; caller must prevent duplicate signals per candle."""
    wait = lambda reason: Signal("WAIT", reason)
    if len(five) < 55 or len(fifteen) < 55:
        return wait("Need 55 closed candles on both timeframes")
    if equity <= 0 or risk_usd <= 0 or leverage <= 0:
        return wait("Invalid risk settings")
    if any(c.close <= 0 or c.high < c.low or c.volume < 0 for c in list(five)+list(fifteen)):
        return wait("Invalid candle data")
    closes = [c.close for c in fifteen]
    fast, slow = ema(closes,20), ema(closes,50)
    strength = rsi(closes)
    last = five[-1]
    prior = five[-2]
    resistance = max(c.high for c in five[-22:-2])
    support = min(c.low for c in five[-22:-2])
    vol_base = mean(c.volume for c in five[-21:-1])
    if last.volume <= vol_base * 1.2:
        return wait("Volume not confirmed")
    long_ok = fast > slow and strength > 52 and prior.close > resistance and last.low <= resistance and last.close > resistance and last.close > last.open
    short_ok = fast < slow and strength < 48 and prior.close < support and last.high >= support and last.close < support and last.close < last.open
    if not (long_ok or short_ok):
        return wait("No confirmed trend, breakout and retest")
    side = "LONG" if long_ok else "SHORT"
    entry = last.close
    distance = max(atr(five)*0.8, entry*0.0005)
    stop = entry-distance if long_ok else entry+distance
    target = entry+distance*2 if long_ok else entry-distance*2
    if target <= 0:
        return wait("Invalid target")
    # Conservative estimated stop cost: stop move + round-trip fees/slippage.
    cost_fraction = distance/entry + 2*(fee_per_side+slippage_per_side)
    notional = min(equity*leverage, risk_usd/cost_fraction)
    margin = notional/leverage
    if notional <= 0:
        return wait("Position too small")
    return Signal(side, "EMA20/50 + RSI + 5m breakout/retest + volume",
                  entry,stop,target,notional,margin,notional*cost_fraction)
