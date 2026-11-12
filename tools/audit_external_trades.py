#!/usr/bin/env python3
"""Offline CSV stress test; not an independent market-tick replay."""
import argparse, csv, json
from collections import Counter
from datetime import datetime

def audit(path, btc_per_lot=1.0):
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        raise ValueError("No trades")
    required = {"Side","Opened (UTC)","Closed (UTC)","Lots","Open price","Close price","Profit USD"}
    if not required.issubset(rows[0]):
        raise ValueError("Missing required columns")
    if btc_per_lot <= 0:
        raise ValueError("btc_per_lot must be positive")
    gross = turnover = 0.0
    wins = losses = flat = negative = mismatches = 0
    durations, minute_counts = [], Counter()
    for r in rows:
        side = r["Side"].lower().strip()
        if side not in ("buy","sell"):
            raise ValueError("Invalid side")
        lots, entry, close, profit = [float(r[k]) for k in ("Lots","Open price","Close price","Profit USD")]
        if min(lots, entry, close) <= 0:
            raise ValueError("Invalid price or lot")
        start = datetime.fromisoformat(r["Opened (UTC)"].replace("Z","+00:00"))
        end = datetime.fromisoformat(r["Closed (UTC)"].replace("Z","+00:00"))
        seconds = (end-start).total_seconds()
        negative += seconds < 0
        durations.append(seconds)
        minute_counts[start.strftime("%Y-%m-%dT%H:%MZ")] += 1
        gross += profit
        turnover += lots*entry*btc_per_lot
        wins += profit > 0
        losses += profit < 0
        flat += profit == 0
        expected = (close-entry)*lots*btc_per_lot*(1 if side=="buy" else -1)
        mismatches += abs(expected-profit) > max(0.02, abs(profit)*0.05)
    durations.sort()
    duplicates = len(rows)-len({tuple(sorted(r.items())) for r in rows})
    return {
        "warning":"Unverified platform CSV. No independent exchange fill or tick validation.",
        "trades":len(rows),"duplicates":duplicates,"wins":wins,"losses":losses,"flat":flat,
        "reported_pnl_usd":round(gross,2),"negative_durations":negative,
        "median_seconds":durations[len(durations)//2],
        "under_100ms":sum(x<0.1 for x in durations),
        "under_1s":sum(x<1 for x in durations),
        "max_opens_per_minute":max(minute_counts.values()),
        "assumed_btc_per_lot":btc_per_lot,
        "price_profit_mismatches":mismatches,
        "break_even_extra_roundtrip_bps":round(gross/turnover*10000,4),
        "scenarios":[{"extra_roundtrip_bps":bps,"net_usd":round(gross-turnover*bps/10000,2)} for bps in (0,0.5,1,2,5,10)]
    }

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("csv_file")
    p.add_argument("--btc-per-lot",type=float,default=1.0)
    args=p.parse_args()
    print(json.dumps(audit(args.csv_file,args.btc_per_lot),indent=2))
