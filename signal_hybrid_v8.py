"""BTC V8 hybrid: AMD liquidity sweep entry + V7 trend/risk confirmation.

Paper-only. Uses completed 1h/15m/5m candles; no exchange orders.
Unlike requiring two independent entry signals simultaneously, this combines
AMD's entry trigger with V7's trend, volatility and reward/risk safeguards.
"""
import math
from dataclasses import replace
from signal_amd_v71 import decide_amd

def decide_hybrid(hour, m15_window, m5, prev5, *,
                  fee_per_side=0.001, slippage_per_side=0.0002):
    signal = decide_amd(hour, m15_window, m5, prev5,
                        fee_per_side=fee_per_side,
                        slippage_per_side=slippage_per_side)
    if signal.side == "WAIT":
        return signal
    m15 = m15_window.iloc[-1]
    try:
        fields = ("ema20", "ema50", "adx", "macdh")
        if not all(math.isfinite(float(getattr(m15, k))) for k in fields):
            return replace(signal, side="WAIT", reason="HYBRID: missing 15m trend data")
        if not math.isfinite(float(m5.atr)) or m5.atr <= 0:
            return replace(signal, side="WAIT", reason="HYBRID: invalid 5m ATR")
        bullish = signal.side == "LONG"
        # V7 trend alignment (entry remains AMD-based).
        aligned = (m15.ema20 > m15.ema50 and m15.macdh > 0 if bullish
                   else m15.ema20 < m15.ema50 and m15.macdh < 0)
        if not aligned or m15.adx < 20:
            return replace(signal, side="WAIT", reason="HYBRID: 15m trend not aligned")
        if m5.high - m5.low > 2.5 * m5.atr:
            return replace(signal, side="WAIT", reason="HYBRID: 5m volatility spike")
        cost = float(m5.close) * 2 * (fee_per_side + slippage_per_side)
        # Stricter net R:R than AMD's 1.7, but avoid the V7 zero-trade
        # trap of requiring both independent signal triggers.
        if signal.target_distance < 2.0 * signal.stop_distance + cost:
            return replace(signal, side="WAIT", reason="HYBRID: reward/risk below 2:1 after costs")
        return replace(signal, reason="HYBRID: AMD sweep + V7 trend and risk confirmed")
    except (AttributeError, TypeError, ValueError, OverflowError):
        return replace(signal, side="WAIT", reason="HYBRID: invalid indicator data")
