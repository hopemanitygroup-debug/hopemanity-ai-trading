"""V6 + BTC scalping hybrid paper backtest; no exchange orders.
Uses V6's historical spot candle loader and indicators for apples-to-apples comparison.
"""
import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from backtest_v6 import history, indicators, trend, setup, entry

FEE = 0.001
SLIP = 0.0002
STARTING_CASH = 1000.0
LEVERAGE_CAP = 50
RISK_USD = 10.0
DAILY_LOSS_LIMIT = 30.0

def backtest(days, mode='relaxed'):
    frames = {tf: indicators(history(None, "BTC/USDT", tf, days)) for tf in ("5m", "15m", "1h")}
    five, fifteen, hourly = (frames[tf] for tf in ("5m", "15m", "1h"))
    cutoff = int(five.index.max() - days * 86400000)
    cash, position, peak, dd = STARTING_CASH, None, STARTING_CASH, 0.0
    trades, daily_losses = [], {}
    diagnostics = {'eligible':0,'trend':0,'setup':0,'entry':0,'breakout':0,'combined':0}
    times = five.index.to_numpy()
    for i in range(60, len(five)):
        t = int(times[i])
        if t < cutoff:
            continue
        bar = five.iloc[i]
        # Entry signals use only closed candles; execute at current bar open.
        # Existing position is evaluated from this bar's OHLC.
        exited = False
        if position:
            p = position
            stop_hit = bar.low <= p["stop"] if p["side"] == 1 else bar.high >= p["stop"]
            take_hit = bar.high >= p["target"] if p["side"] == 1 else bar.low <= p["target"]
            if stop_hit or take_hit:
                level = p["stop"] if stop_hit else p["target"]
                if stop_hit:
                    level = min(level, bar.open) if p["side"] == 1 else max(level, bar.open)
                price = level * (1 - p["side"] * SLIP)
                gross = p["side"] * p["qty"] * (price - p["entry"])
                exit_fee = abs(price * p["qty"]) * FEE
                pnl = gross - p["entry_fee"] - exit_fee
                cash += gross - exit_fee
                day = pd.to_datetime(t, unit="ms", utc=True).date().isoformat()
                daily_losses[day] = daily_losses.get(day, 0.0) + min(0.0, pnl)
                trades.append({"opened": p["opened"], "closed": t, "side": "LONG" if p["side"] == 1 else "SHORT",
                               "net": round(pnl, 5), "fees": round(p["entry_fee"] + exit_fee, 5),
                               "reason": "SL" if stop_hit else "TP"})
                position, exited = None, True
        if position is None and not exited and cash > 0:
            day = pd.to_datetime(t, unit="ms", utc=True).date().isoformat()
            if daily_losses.get(day, 0) > -DAILY_LOSS_LIMIT:
                h = hourly[hourly.index + 3600000 <= t]
                m = fifteen[fifteen.index + 900000 <= t]
                f = five[five.index + 300000 <= t]
                if len(h) >= 60 and len(m) >= 60 and len(f) >= 60:
                    direction = trend(h.iloc[-1])
                    if direction:
                        diagnostics["trend"] += 1
                        last, prev = f.iloc[-1], f.iloc[-2]
                        # Combine V6's 1h ADX/EMA regime and 15m pullback with
                        # scalping's 5m breakout/retest and V6 momentum confirmation.
                        window = f.iloc[-22:-2]
                        if len(window) == 20:
                            resistance, support = window.high.max(), window.low.min()
                            breakout = (prev.close > resistance and last.low <= resistance and
                                        last.close > resistance and last.close > last.open) if direction == "LONG" else (
                                        prev.close < support and last.high >= support and
                                        last.close < support and last.close < last.open)
                            strict_setup = setup(m.iloc[-1], m.iloc[-2], direction)
                            strict_entry = entry(last, prev, direction)
                            diagnostics["eligible"] += 1
                            diagnostics["setup"] += int(strict_setup)
                            diagnostics["entry"] += int(strict_entry)
                            diagnostics["breakout"] += int(breakout)
                            if mode == "strict":
                                confirmed = strict_setup and strict_entry and breakout
                            else:
                                # Trend-following 15m direction + 5m momentum; no simultaneous pullback/retest requirement.
                                mlast = m.iloc[-1]
                                aligned = (mlast.ema20 > mlast.ema50 if direction == "LONG" else mlast.ema20 < mlast.ema50)
                                momentum = (last.close > last.ema9 and last.rsi >= 51 and last.close > last.open if direction == "LONG"
                                            else last.close < last.ema9 and last.rsi <= 49 and last.close < last.open)
                                volume_ok = last.volume > last.vol_ma * 0.85
                                confirmed = aligned and momentum and volume_ok
                            diagnostics["combined"] += int(confirmed)
                            atr = last.atr
                            if confirmed and np.isfinite(atr) and atr > 0:
                                side = 1 if direction == "LONG" else -1
                                price = bar.open * (1 + side * SLIP)
                                distance = max(0.8 * atr, price * 0.0005)
                                # Approximate all-in stop risk with fees/slippage.
                                risk_fraction = distance / price + 2 * (FEE + SLIP)
                                notional = min(cash * LEVERAGE_CAP, min(RISK_USD, cash * 0.01) / risk_fraction)
                                if notional > 0 and price - distance > 0:
                                    qty = notional / price
                                    fee = notional * FEE
                                    cash -= fee
                                    position = {"opened": t, "side": side, "entry": price, "qty": qty,
                                                "entry_fee": fee, "stop": price - side * distance,
                                                "target": price + side * 2 * distance}
        floating = 0 if position is None else position["side"] * position["qty"] * (bar.close - position["entry"])
        value = cash + floating
        peak = max(peak, value)
        dd = max(dd, (peak - value) / peak * 100 if peak > 0 else 0)
    floating = 0 if position is None else position["side"] * position["qty"] * (five.iloc[-1].close - position["entry"])
    ending = cash + floating
    return {"days": days, "starting_cash": STARTING_CASH, "ending_equity": round(ending, 2),
            "pnl": round(ending - STARTING_CASH, 2), "closed_trades": len(trades),
            "wins": sum(t["net"] > 0 for t in trades),
            "max_drawdown_pct": round(dd, 3), "open_position": position is not None,
            "fees_closed": round(sum(t["fees"] for t in trades), 2), "mode": mode, "diagnostics": diagnostics}, trades

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="results-hybrid")
    parser.add_argument("--mode", choices=["strict","relaxed"], default="relaxed")
    args = parser.parse_args()
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    results, trades = [], []
    for days in (7, 30):
        result, journal = backtest(days, args.mode)
        results.append(result)
        trades.extend([dict(days=days, **item) for item in journal])
    pd.DataFrame(results).to_csv(output / "summary.csv", index=False)
    pd.DataFrame(trades, columns=["days","opened","closed","side","net","fees","reason"]).to_csv(output / "trades.csv", index=False)
    (output / "summary.json").write_text(json.dumps({"strategy": "V6+scalping V2", "source": "Binance spot historical archives",
        "fee_per_side": FEE, "slippage_per_side": SLIP, "funding_modeled": False,
        "results": results}, indent=2, default=lambda x: x.item() if isinstance(x, np.generic) else str(x)), encoding="utf-8")
    print(pd.DataFrame(results).drop(columns=["diagnostics"]).to_string(index=False))
    print("Filter diagnostics:", json.dumps({str(x["days"]):x["diagnostics"] for x in results}))

if __name__ == "__main__":
    main()
