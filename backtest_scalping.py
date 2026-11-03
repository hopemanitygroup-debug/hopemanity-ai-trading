"""Paper-only BTC scalping backtest. Input: CSV of CLOSED 5m UTC candles.
Columns: timestamp,open,high,low,close,volume. Timestamp ISO8601 or epoch ms.
Run: python backtest_scalping.py --csv btc_5m.csv --days 7
No exchange orders are placed.
"""
import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from btc_scalping import Candle, signal

def timestamp(value):
    try:
        n = float(value)
        return datetime.fromtimestamp(n / (1000 if n > 1e11 else 1), tz=timezone.utc)
    except ValueError:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)

def load(path):
    out = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            t = timestamp(row["timestamp"])
            c = Candle(*(float(row[k]) for k in ("open","high","low","close","volume")))
            if not (c.low <= min(c.open,c.close) <= max(c.open,c.close) <= c.high and c.volume >= 0):
                raise ValueError("Invalid OHLCV at " + str(t))
            out.append((t,c))
    out.sort(key=lambda x:x[0])
    if len(set(t for t,_ in out)) != len(out):
        raise ValueError("Duplicate timestamps")
    return out

def aggregate(window):
    return Candle(window[0].open,max(c.high for c in window),
                  min(c.low for c in window),window[-1].close,
                  sum(c.volume for c in window))

def run(rows, days, initial=1000, leverage=50, fee=0.0005, slip=0.0002):
    if days not in (7,30):
        raise ValueError("Days must be 7 or 30")
    if not rows:
        raise ValueError("Empty input")
    end = rows[-1][0]
    start = end.timestamp() - days*86400
    equity = initial
    peak = initial
    max_dd = 0
    trades = []
    active = None
    fifteen = []
    five = []
    last_bucket = None
    current_bucket = []
    daily_loss = {}
    # Feed warmup from before evaluation period, never future candles.
    for i,(t,c) in enumerate(rows):
        bucket = int(t.timestamp()) // 900
        if last_bucket is not None and bucket != last_bucket:
            if len(current_bucket) == 3:
                fifteen.append(aggregate(current_bucket))
            current_bucket = []
        last_bucket = bucket
        current_bucket.append(c)
        five.append(c)
        # Simulate active stop/target on subsequent candles only.
        if active is not None:
            side = active["side"]
            stop_hit = c.low <= active["stop"] if side=="LONG" else c.high >= active["stop"]
            target_hit = c.high >= active["target"] if side=="LONG" else c.low <= active["target"]
            if stop_hit or target_hit:
                # Conservative: when both touched, assume stop first.
                exit_price = active["stop"] if stop_hit else active["target"]
                exit_price *= 1-slip if side=="LONG" else 1+slip
                signed = 1 if side=="LONG" else -1
                gross = signed * active["qty"] * (exit_price-active["entry"])
                costs = fee*active["qty"]*(active["entry"]+exit_price)
                pnl = gross-costs
                equity += pnl
                day = t.date().isoformat()
                daily_loss[day] = daily_loss.get(day,0)+min(0,pnl)
                trades.append(dict(opened=active["opened"],closed=t.isoformat(),side=side,pnl=round(pnl,4),reason="STOP" if stop_hit else "TARGET"))
                peak = max(peak,equity)
                max_dd = max(max_dd,(peak-equity)/peak if peak else 0)
                active = None
        if t.timestamp() < start or active is not None or len(current_bucket)!=3:
            continue
        if daily_loss.get(t.date().isoformat(),0) <= -30 or equity <= 0:
            continue
        s = signal(five, fifteen, equity=equity,leverage=leverage,risk_usd=min(10,equity*0.01),fee_per_side=fee,slippage_per_side=slip)
        if s.action in ("LONG","SHORT"):
            # Fill at next candle open, not the signal candle close.
            if i+1 >= len(rows):
                continue
            nt,nc = rows[i+1]
            if (nt-t).total_seconds()!=300:
                continue
            entry = nc.open*(1+slip if s.action=="LONG" else 1-slip)
            distance = abs(s.entry-s.stop)
            stop = entry-distance if s.action=="LONG" else entry+distance
            target = entry+2*distance if s.action=="LONG" else entry-2*distance
            active = dict(side=s.action,entry=entry,qty=s.notional/entry,stop=stop,target=target,opened=nt.isoformat())
    wins = sum(x["pnl"]>0 for x in trades)
    return dict(days=days,trades=len(trades),wins=wins,win_rate=round(wins/len(trades)*100,2) if trades else 0,
                realized_pnl=round(equity-initial,2),ending_equity=round(equity,2),
                max_drawdown_pct=round(max_dd*100,2),open_position=bool(active),
                note="Open position excluded from realized PnL; funding not modeled",
                journal=trades)

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--csv",required=True)
    p.add_argument("--days",type=int,choices=[7,30],default=7)
    p.add_argument("--out",default="")
    args=p.parse_args()
    result=run(load(args.csv),args.days)
    output=json.dumps(result,indent=2)
    if args.out:
        Path(args.out).write_text(output)
    print(output)
