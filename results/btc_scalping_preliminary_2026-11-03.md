# BTC scalping preliminary research run — 2026-11-03

Source: Binance USD-M BTCUSDT 5m OHLCV via connected Binance public market data API.
Sample: 8,929 continuous candles, 2026-10-03 18:15 UTC through 2026-11-03 18:15 UTC, no detected 5m gaps.

Independent in-memory simulation of strategy rules from btc_scalping.py (not execution of repository backtest_scalping.py). Evaluation on completed 15m candles, with next 5m open entry, stop-first if stop and target in same bar, one open position maximum. Fee 0.05% each side, slippage 0.02% each side. Initial equity $1,000, max leverage 50, per-trade estimated risk $10, daily stop on summed realized losses $30.

| Lookback | Signals | Closed trades | Wins | Realized PnL | Max closed-equity DD |
|---|---:|---:|---:|---:|---:|
| 7 days | 1 | 1 | 0 | -$1.01 | 0.10% |
| 30 days | 9 | 9 | 0 | -$71.90 | 7.19% |

Caveats: preliminary results only. Funding, liquidation mechanics, exchange filters, partial fills, mark-price stops, intra-candle sequencing, floating PnL and exact order execution are not modeled. The independent simulator differs from the current repo runner; reproduce with tests and a corrected runner before trusting results. No live trading. Strategy did not show profitability in this sample.

Next: fix repo backtest execution/timing, write unit tests for gap detection and next-bar fills, validate trade journal against historical candles, and compare V6 on same data.
