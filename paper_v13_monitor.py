#!/usr/bin/env python3
"""V13.6 server-side PAPER monitor. No API keys or real orders."""
import csv, io, json, urllib.request, urllib.error, zipfile
from datetime import datetime, timezone, timedelta, date
from pathlib import Path

STATE=Path("paper-v13-state.json")
ARCHIVE="https://data.binance.vision/data/futures/um/{period}/klines/BTCUSDT/4h/BTCUSDT-4h-{stamp}.zip"
FOUR_HOURS=4*3600*1000

def download(period, stamp):
    url=ARCHIVE.format(period=period,stamp=stamp)
    try:
        with urllib.request.urlopen(urllib.request.Request(url,headers={"User-Agent":"HopeManity-Paper/1.1"}),timeout=30) as response:
            payload=response.read()
    except urllib.error.HTTPError as exc:
        if exc.code==404:
            return None
        raise RuntimeError(f"Official Binance archive unavailable: HTTP {exc.code}") from exc
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        filenames=[name for name in archive.namelist() if name.endswith(".csv")]
        if len(filenames)!=1:raise RuntimeError("Unexpected archive contents")
        rows=list(csv.reader(io.TextIOWrapper(archive.open(filenames[0]),encoding="utf-8")))
    out=[]
    for x in rows:
        if not x or not x[0].isdigit():continue
        t=int(x[0]);o,h,l,c=map(float,x[1:5])
        if t<=0 or min(o,h,l,c)<=0 or l>min(o,c) or h<max(o,c):
            raise RuntimeError("Invalid OHLC values")
        out.append({"t":t,"o":o,"h":h,"l":l,"c":c})
    return out

def month_offset(today,offset):
    y=today.year;m=today.month+offset
    while m<=0:y-=1;m+=12
    while m>12:y+=1;m-=12
    return y,m

def bars():
    today=datetime.now(timezone.utc).date()
    items=[]
    # Four completed months provide EMA200 warm-up. The newest month
    # may not have a monthly ZIP until the first Monday of next month.
    for offset in range(-4,0):
        y,m=month_offset(today,offset)
        stamp=f"{y:04d}-{m:02d}"
        monthly=download("monthly",stamp)
        if monthly is not None:
            items.extend(monthly)
        else:
            d=date(y,m,1)
            while d.month==m:
                if d<today:
                    daily=download("daily",d.isoformat())
                    if daily is None:raise RuntimeError(f"Missing archived day {d}")
                    items.extend(daily)
                d+=timedelta(days=1)
    first=date(today.year,today.month,1)
    d=first
    while d<today:
        daily=download("daily",d.isoformat())
        if daily is None:
            # Archive normally publishes yesterday's file the next day.
            if d>=today-timedelta(days=2):break
            raise RuntimeError(f"Missing archived day {d}")
        items.extend(daily)
        d+=timedelta(days=1)
    by_time={x["t"]:x for x in items}
    candles=sorted(by_time.values(),key=lambda x:x["t"])
    now_ms=int(datetime.now(timezone.utc).timestamp()*1000)
    candles=[x for x in candles if x["t"]+FOUR_HOURS<=now_ms]
    if len(candles)<240:raise RuntimeError("Insufficient official archive history")
    for p,n in zip(candles,candles[1:]):
        if n["t"]-p["t"]!=FOUR_HOURS:
            raise RuntimeError("Missing or duplicate 4H archive candles; no paper trades")
    # Do not silently treat stale archive data as live trading.
    if now_ms-(candles[-1]["t"]+FOUR_HOURS)>72*3600*1000:
        raise RuntimeError("Archive is stale (>72h); no paper trades")
    return candles
def indicators(b):
    e=b[0]["c"]; emas=[]; ats=[]
    for i,x in enumerate(b):
        e+=2/201*(x["c"]-e);emas.append(e)
        terms=[max(b[j]["h"]-b[j]["l"],abs(b[j]["h"]-b[j-1]["c"]),abs(b[j]["l"]-b[j-1]["c"]))
               for j in range(max(1,i-13),i+1)]
        ats.append(sum(terms)/len(terms) if terms else x["h"]-x["l"])
    return emas,ats
def initial():
    return {"last":0,"position":None,"realized_pnl":0.0,"fees":0.0,"events":[],"last_check":None,"status":"WAIT"}
def main():
    b=bars()
    if len(b)<240: raise RuntimeError("Insufficient 4h bars")
    emas,ats=indicators(b)
    s=json.loads(STATE.read_text()) if STATE.exists() else initial()
    if not s.get("last"):
        s["last"]=b[-1]["t"] # no historical orders on first startup
        s["status"]="INITIALIZED_WAIT"
    else:
        unseen=[i for i,x in enumerate(b) if x["t"]>s["last"]]
        if unseen and unseen[0]==0: raise RuntimeError("History gap >500 bars; refusing to simulate")
        for i in unseen:
            x=b[i];p=s["position"]
            if p:
                exit_price=None;reason=None
                if x["o"]>=p["stop"]:exit_price=x["o"];reason="SL_GAP"
                elif x["h"]>=p["stop"]:exit_price=p["stop"];reason="SL"
                elif x["l"]<=p["target"]:exit_price=p["target"];reason="TP"
                elif x["t"]-p["opened_bar"]>=6*4*3600*1000:
                    exit_price=x["o"];reason="TIME"
                if exit_price is not None:
                    fee=p["notional"]*.0015*(1+exit_price/p["entry"])
                    pnl=p["notional"]*(1-exit_price/p["entry"])-fee
                    s["fees"]+=fee;s["realized_pnl"]+=pnl
                    s["events"].append({"time":x["t"],"event":reason,"price":exit_price,"notional":p["notional"],"net_pnl":pnl})
                    s["position"]=None
            # Evaluate only the previously closed candle, enter on current open.
            if s["position"] is None and i>=231:
                prev=b[i-1];hi=max(z["h"] for z in b[i-21:i-1])
                signal=(prev["h"]>hi and prev["c"]<hi and prev["c"]<prev["o"]
                    and prev["c"]<emas[i-1] and emas[i-1]<emas[i-31]
                    and ats[i-1]/prev["c"]<.012)
                if signal:
                    dist=1.5*ats[i-1];entry=x["o"];notional=min(100000,600*entry/dist)
                    s["position"]={"entry":entry,"stop":entry+dist,"target":entry-1.5*dist,
                                   "notional":notional,"opened_bar":x["t"]}
                    s["events"].append({"time":x["t"],"event":"OPEN_SHORT","price":entry,"notional":notional,"net_pnl":None})
            s["last"]=x["t"]
        s["status"]="PAPER_ACTIVE" if s["position"] else "WAIT"
    s["events"]=s["events"][-300:]
    s["last_check"]=datetime.now(timezone.utc).isoformat()
    s["data_source"]="Binance official USD-M futures daily/monthly archive (delayed)"
    s["data_delay_warning"]="Daily archives are published next day; not live execution"
    s["last_price"]=b[-1]["c"]
    s["last_candle"]=b[-1]["t"]
    STATE.write_text(json.dumps(s,indent=2,ensure_ascii=False)+"\n")
    print(json.dumps({"status":s["status"],"events":len(s["events"]),"last":s["last"],"pnl":s["realized_pnl"]}))
if __name__=="__main__":main()
