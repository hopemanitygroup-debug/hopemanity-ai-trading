"""BTC V7 conservative signal overlay on V6. Paper-only, no trained ML."""
from dataclasses import replace
import math
from market_regime_v6 import decide

def decide_v7(hour, m15, prev15, m5, prev5, *, fee_per_side=.001, slippage_per_side=.0002):
    s = decide(hour, m15, prev15, m5, prev5,
               fee_per_side=fee_per_side, slippage_per_side=slippage_per_side)
    if s.side == "WAIT":
        return s
    # Trend confirmation across 1h and 15m; do not trade against 15m momentum.
    if s.regime in ("BULL", "BEAR"):
        if not all(math.isfinite(float(getattr(m15, k))) for k in ("ema20", "ema50", "adx", "macdh")):
            return replace(s, side="WAIT", reason="Missing trend confirmation")
        up = s.side == "LONG"
        aligned = (m15.ema20 > m15.ema50 and m15.macdh > 0 if up
                   else m15.ema20 < m15.ema50 and m15.macdh < 0)
        if not aligned or m15.adx < 20:
            return replace(s, side="WAIT", reason="15m trend not aligned")
    else:
        # V6 range trades had poor cost-adjusted expectancy; disable pending validation.
        return replace(s, side="WAIT", reason="Range trading disabled in V7 baseline")
    # Demand stronger cost-adjusted reward/risk, plus no extreme 5m candle.
    cost = float(m5.close) * 2 * (fee_per_side + slippage_per_side)
    if s.target_distance < 2.5 * s.stop_distance + cost:
        return replace(s, side="WAIT", reason="Net reward/risk too low")
    if (m5.high - m5.low) > 2.5 * m5.atr:
        return replace(s, side="WAIT", reason="5m volatility spike")
    return replace(s, reason="V7 aligned trend and cost-adjusted reward")
