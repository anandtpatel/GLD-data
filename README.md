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
