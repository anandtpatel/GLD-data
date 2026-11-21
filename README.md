# GLD data

Updated by a GitHub Action every weekday (after the close and again ~7:37 AM ET). Public data only.

| File | What | Source |
|---|---|---|
| `gld_10y.csv` | GLD daily OHLCV, ~10 years | Yahoo (yfinance), Stooq fallback |
| `data/gld_holdings.csv` | GLD tonnes in trust, daily since 2004 | SPDR Gold Shares archive |
| `data/cot_gold.csv` | COMEX gold COT, weekly: managed money long/short/net, non-commercial net | CFTC public reporting API |
| `data/fed_odds.csv` | Fed decision market odds, one snapshot per day | Kalshi public API |
| `data/macro.csv` | 10y real yield (DFII10), broad dollar (DTWEXBGS), fed funds (DFF) | FRED |
| `data/status.json` | Which sources worked on the last run | — |

## VGT (Vanguard Information Technology ETF) — `vgt/`

| File | What | Source |
|---|---|---|
| `vgt/vgt_10y.csv` | VGT daily OHLCV + Adj Close, ~10 years | Yahoo (yfinance), Stooq fallback |
| `vgt/peers.csv` | Daily Close/Adj Close: SPY, QQQ, XLK, SOXX, AAPL, MSFT, NVDA, AVGO, ORCL, CRM, AMD, CSCO | Yahoo |
| `vgt/fund_snapshot.csv` | Daily snapshot: total assets, NAV, shares outstanding (flows proxy; history builds from 2026-11) | Yahoo |
| `vgt/top_holdings.csv` | Top-10 holdings and weights, daily snapshot | Yahoo |
| `vgt/cot_nasdaq.csv` | E-mini Nasdaq-100 COT, weekly: leveraged funds, asset managers, non-commercial net | CFTC public reporting API |
| `vgt/macro.csv` | VIX (VIXCLS), VXN (VXNCLS), 10y real (DFII10), 10y nominal (DGS10), fed funds (DFF) | FRED |
| `vgt/status.json` | Which VGT sources worked on the last run | — |

Fed odds are shared: `data/fed_odds.csv`.
