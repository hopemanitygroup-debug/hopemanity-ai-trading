# HopeManity AI Trading — PAPER DEMO

Static browser demo for BTCUSDT, ETHUSDT, BNBUSDT with Binance public REST candles on 5m, 15m, 1h.

Open **index.html** in a browser or enable GitHub Pages from main / root. No API keys, exchange orders, or real money.

## Logic
EMA20/EMA50 + RSI14 on **closed candles**; ATR14 for stop/target. LONG or SHORT only if the 1h direction matches at least one other timeframe. Otherwise WAIT. Initial $10,000 virtual cash per pair. One position per pair, $1,000 notional, no leverage. Simulated 0.1% per-side fees and 0.02% slippage. PAPER AUTO polls every 60 seconds. Trades and balances are **in-memory only** and reset on reload.

## Limitations
This is deterministic indicator analysis, **not a trained AI model**. Public Binance API may be blocked by browser CORS, region or network. No backend or reliable always-on execution. Stops and targets are checked only on observed prices; no intrabar execution or realistic order-book fill modeling. Not investment advice. Do not use for live trading.


## Fast Grid PAPER

Open **grid-scalp-paper.html** or use the link on **index.html**. The page listens to Binance's public BTCUSDT trade stream and simulates up to 20 Buy Stop and 20 Sell Stop levels, closing each side as a basket. It includes virtual fees, slippage, a per-basket loss cap, a session loss stop, and an exposure limit. It sends no exchange orders and requires no API keys. Settings and paper history reset when the page is reloaded. This is an illustrative paper simulation, not a validated trading strategy or a live trading system.
