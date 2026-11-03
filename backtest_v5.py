"""MTF Pro V5 reproducible historical paper backtest. No real orders."""
import argparse
import json
import time
import io
import zipfile
import requests
from datetime import timedelta
from datetime import datetime, timezone
from pathlib import Path

import ccxt
import numpy as np
import pandas as pd
import ta

FEE = 0.001
SLIP = 0.0002
CAPITAL = 10000.0
NOTIONAL = 1000.0
PAIRS = ["BTC/USDT", "ETH/USDT", "BNB/USDT"]
PERIODS = [7, 30]


def history(ex, symbol, tf, days):
    """Download Binance public historical ZIP archives; no trading API access."""
    pair = symbol.replace("/", "")
    today = datetime.now(timezone.utc).date()
    first = today - timedelta(days=days + 8)
    frames = []
    session = requests.Session()
    for offset in range((today - first).days + 1):
        day = first + timedelta(days=offset)
        name = f"{pair}-{tf}-{day.isoformat()}.zip"
        url = f"https://data.binance.vision/data/spot/daily/klines/{pair}/{tf}/{name}"
        try:
            response = session.get(url, timeout=30)
            if response.status_code == 404:
                continue
            response.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
                csv_name = next(n for n in archive.namelist() if n.endswith(".csv"))
                raw = pd.read_csv(archive.open(csv_name), header=None)
                if isinstance(raw.iloc[0, 0], str) and not raw.iloc[0, 0].isdigit():
                    raw = raw.iloc[1:]
                if raw.empty:
                    continue
                raw = raw.iloc[:, :6]
                raw.columns = ["ts", "open", "high", "low", "close", "volume"]
                frames.append(raw)
        except (requests.RequestException, zipfile.BadZipFile, ValueError) as err:
            raise RuntimeError(f"Historical archive unavailable: {url}: {err}") from err
    if not frames:
        raise RuntimeError(f"No historical archive candles for {symbol} {tf}")
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_numeric(df["ts"], errors="coerce")
    # Binance historical archives may use microseconds for timestamps.
    df.loc[df["ts"] > 1e14, "ts"] /= 1000
    df = df.dropna(subset=["ts"]).drop_duplicates("ts").sort_values("ts").set_index("ts")
    duration = {"5m": 300000, "15m": 900000, "1h": 3600000}[tf]
    now = int(datetime.now(timezone.utc).timestamp() * 1000)
    df = df[df.index + duration <= now]
    return df.astype(float)


def indicators(df):
    x = df.copy()
    x["ema9"] = ta.trend.ema_indicator(x.close, window=9)
    x["ema20"] = ta.trend.ema_indicator(x.close, window=20)
    x["ema50"] = ta.trend.ema_indicator(x.close, window=50)
    x["rsi"] = ta.momentum.rsi(x.close, window=14)
    x["adx"] = ta.trend.adx(x.high, x.low, x.close, window=14)
    bb = ta.volatility.BollingerBands(x.close, window=20, window_dev=2)
    x["bb_low"], x["bb_mid"], x["bb_high"] = bb.bollinger_lband(), bb.bollinger_mavg(), bb.bollinger_hband()
    x["macdh"] = ta.trend.MACD(x.close).macd_diff()
    x["atr"] = ta.volatility.average_true_range(x.high, x.low, x.close, window=14)
    x["vol_ma"] = x.volume.rolling(20).mean()
    return x


def trend(h):
    if h.adx < 23:
        return None
    if h.close > h.ema50 and h.ema20 > h.ema50:
        return "LONG"
    if h.close < h.ema50 and h.ema20 < h.ema50:
        return "SHORT"
    return None


def setup(last, prev, direction):
    if last.volume < last.vol_ma:
        return False
    if direction == "LONG":
        pullback = last.low <= last.bb_low * 1.008 or (last.low <= last.bb_mid and last.close < last.bb_mid)
        return pullback and prev.rsi < 47 and last.rsi > prev.rsi and last.close > last.open
    pullback = last.high >= last.bb_high * 0.992 or (last.high >= last.bb_mid and last.close > last.bb_mid)
    return pullback and prev.rsi > 53 and last.rsi < prev.rsi and last.close < last.open


def entry(last, prev, direction):
    if last.volume <= last.vol_ma:
        return False
    if direction == "LONG":
        return (last.close > last.ema9 or last.close > prev.high) and last.macdh > prev.macdh and last.macdh > 0 and last.close > last.open
    return (last.close < last.ema9 or last.close < prev.low) and last.macdh < prev.macdh and last.macdh < 0 and last.close < last.open


def test(ex, symbol, days):
    frames = {tf: indicators(history(ex, symbol, tf, days)) for tf in ("5m", "15m", "1h")}
    five, fifteen, hour = frames["5m"], frames["15m"], frames["1h"]
    # Signals at the next 5m open, using only bars already CLOSED by then.
    start = five.index.max() - days * 86400000
    trades, equity = [], []
    balance, position = CAPITAL, None
    for t, bar in five.iterrows():
        if t < start:
            continue
        # Resolve stops/targets using current 5m high/low; pessimistic SL-first on both touched.
        if position:
            p = position
            stop_hit = bar.low <= p["stop"] if p["side"] == "LONG" else bar.high >= p["stop"]
            take_hit = bar.high >= p["target"] if p["side"] == "LONG" else bar.low <= p["target"]
            if stop_hit or take_hit:
                level = p["stop"] if stop_hit else p["target"]
                # Gap at bar open is worse than stop where applicable.
                if stop_hit:
                    level = min(level, bar.open) if p["side"] == "LONG" else max(level, bar.open)
                exit_price = level * (1 - SLIP if p["side"] == "LONG" else 1 + SLIP)
                gross = (exit_price - p["entry"]) * p["qty"] * (1 if p["side"] == "LONG" else -1)
                exit_fee = abs(exit_price * p["qty"]) * FEE
                net = gross - p["entry_fee"] - exit_fee
                balance += gross - exit_fee
                trades.append(dict(pair=symbol, days=days, opened=p["opened"], closed=int(t), side=p["side"], entry=p["entry"], exit=exit_price, net=net, fees=p["entry_fee"] + exit_fee, reason="SL" if stop_hit else "TP"))
                position = None
        if position is None:
            h = hour[hour.index + 3600000 <= t]
            m = fifteen[fifteen.index + 900000 <= t]
            f = five[five.index + 300000 <= t]
            if len(h) >= 60 and len(m) >= 60 and len(f) >= 60:
                direction = trend(h.iloc[-1])
                if direction and setup(m.iloc[-1], m.iloc[-2], direction) and entry(f.iloc[-1], f.iloc[-2], direction):
                    atr = m.iloc[-1].atr
                    if np.isfinite(atr) and atr > 0:
                        price = bar.open * (1 + SLIP if direction == "LONG" else 1 - SLIP)
                        qty = NOTIONAL / price
                        fee = NOTIONAL * FEE
                        balance -= fee
                        sign = 1 if direction == "LONG" else -1
                        position = dict(opened=int(t), side=direction, entry=price, qty=qty, entry_fee=fee, stop=price-sign*1.5*atr, target=price+sign*2.5*1.5*atr)
        unrealized = 0 if position is None else (bar.close-position["entry"])*position["qty"]*(1 if position["side"]=="LONG" else -1)
        equity.append(balance + unrealized)
    # Mark-to-market any open position; no fictitious close.
    e = equity[-1] if equity else CAPITAL
    peak, dd = CAPITAL, 0.0
    for value in equity:
        peak = max(peak, value)
        dd = max(dd, (peak-value)/peak*100)
    return dict(pair=symbol, days=days, equity=round(e, 2), pnl=round(e-CAPITAL, 2), return_pct=round((e/CAPITAL-1)*100, 3), closed_trades=len(trades), wins=sum(t["net"]>0 for t in trades), win_rate_pct=round(100*sum(t["net"]>0 for t in trades)/len(trades), 2) if trades else 0, max_drawdown_pct=round(dd, 3), fees_closed=round(sum(t["fees"] for t in trades), 2), open_position=bool(position)), trades


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="results")
    args = parser.parse_args()
    ex = None  # Historical archive source; no exchange API calls
    results, trades = [], []
    for pair in PAIRS:
        for days in PERIODS:
            print(f"Testing {pair} {days}d", flush=True)
            result, journal = test(ex, pair, days)
            results.append(result)
            trades.extend(journal)
    dest = Path(args.out)
    dest.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(results).to_csv(dest / "summary.csv", index=False)
    pd.DataFrame(trades, columns=["pair","days","opened","closed","side","entry","exit","net","fees","reason"]).to_csv(dest / "trades.csv", index=False)
    print(pd.DataFrame(results).to_string(index=False), flush=True)
    (dest / "summary.json").write_text(json.dumps({"generated_utc": datetime.now(timezone.utc).isoformat(), "assumptions": {"capital_per_pair": CAPITAL, "notional": NOTIONAL, "fee_per_side": FEE, "slippage_per_side": SLIP, "stop_first_if_both_hit": True, "closed_candles_only": True}, "results": results}, ensure_ascii=False, indent=2, default=lambda x: x.item() if isinstance(x, np.generic) else str(x)), encoding="utf-8")
    print(pd.DataFrame(results).to_string(index=False))


if __name__ == "__main__":
    main()
