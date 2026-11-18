"""Download / update GLD daily prices into gld_10y.csv.

First run: pulls ~10 years. Later runs: appends only new days.
Source: Yahoo via yfinance, falling back to Stooq.
"""
import io
import os
import sys
import urllib.request

import pandas as pd

CSV = "gld_10y.csv"
COLS = ["Date", "Open", "High", "Low", "Close", "Volume"]


def from_yahoo(period):
    import yfinance as yf
    df = yf.download("GLD", period=period, interval="1d",
                     auto_adjust=False, progress=False)
    if df is None or df.empty:
        raise RuntimeError("yfinance returned no rows")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index()
    df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    return df[COLS]


def from_stooq():
    url = "https://stooq.com/q/d/l/?s=gld.us&i=d"
    raw = urllib.request.urlopen(url, timeout=60).read().decode()
    df = pd.read_csv(io.StringIO(raw))
    if df.empty or "Close" not in df.columns:
        raise RuntimeError("stooq returned no rows")
    return df[COLS]


def main():
    have = pd.read_csv(CSV) if os.path.exists(CSV) else None
    period = "10y" if have is None or have.empty else "1mo"

    new = None
    for name, fn in (("yahoo", lambda: from_yahoo(period)), ("stooq", from_stooq)):
        try:
            new = fn()
            print(f"source={name} rows={len(new)}")
            break
        except Exception as e:  # noqa: BLE001
            print(f"{name} failed: {e}", file=sys.stderr)
    if new is None:
        sys.exit("all sources failed")

    df = new if have is None else pd.concat([have, new])
    df = (df.drop_duplicates("Date", keep="last")
            .sort_values("Date")
            .reset_index(drop=True))
    cutoff = (pd.Timestamp.today() - pd.DateOffset(years=10, days=7)).strftime("%Y-%m-%d")
    df = df[df["Date"] >= cutoff]
    for c in ["Open", "High", "Low", "Close"]:
        df[c] = df[c].astype(float).round(4)
    df.to_csv(CSV, index=False)
    print(f"saved {len(df)} rows, {df['Date'].iloc[0]} -> {df['Date'].iloc[-1]}")


if __name__ == "__main__":
    main()
