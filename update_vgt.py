"""Download data for the daily VGT (Vanguard Information Technology ETF) report into vgt/.

Each source is independent: one failing doesn't stop the others.
  vgt/vgt_10y.csv          VGT daily OHLCV + Adj Close, ~10 years (Yahoo, Stooq fallback)
  vgt/peers.csv            Daily Close / Adj Close for SPY, QQQ, XLK, SOXX and VGT's top holdings
  vgt/fund_snapshot.csv    Daily snapshot: VGT total assets, NAV, implied shares outstanding (appended)
  vgt/top_holdings.csv     Latest VGT top-10 holdings and weights (snapshot, appended daily)
  vgt/cot_nasdaq.csv       CFTC TFF, E-mini Nasdaq-100 (209742): leveraged funds / asset managers + legacy non-comm
  vgt/macro.csv            FRED: VIXCLS, VXNCLS, DFII10, DGS10, DFF; dates FRED hasn't posted yet are
                           filled from Cboe / Treasury / NY Fed (Yahoo last resort), flagged in status.json
  vgt/status.json          what succeeded / failed on the last run
Fed odds are shared with GLD: data/fed_odds.csv.
"""
import io
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pandas as pd

OUT = "vgt"
UA = {"User-Agent": "Mozilla/5.0 (gld-data bot; github.com/anandtpatel/gld-data)"}
PEERS = ["SPY", "QQQ", "XLK", "SOXX", "AAPL", "NVDA", "MSFT", "AVGO", "MU", "AMD", "CSCO", "PLTR",
         "AMAT", "PANW", "ORCL", "CRM"]
COLS = ["Date", "Open", "High", "Low", "Close", "Adj Close", "Volume"]
status = {"run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
TODAY = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read()


def save(df, name):
    df.to_csv(os.path.join(OUT, name), index=False)
    return f"ok rows={len(df)}"


def append_snapshot(df, name, key="snapshot_date"):
    path = os.path.join(OUT, name)
    if os.path.exists(path):
        old = pd.read_csv(path)
        old = old[old[key] != TODAY]
        df = pd.concat([old, df])
    return save(df, name)


def _yf(tickers, period):
    import yfinance as yf
    df = yf.download(tickers, period=period, interval="1d", auto_adjust=False,
                     progress=False, group_by="ticker", threads=True)
    if df is None or df.empty:
        raise RuntimeError("yfinance returned no rows")
    return df


# ---------------- VGT prices ----------------
def vgt_prices():
    # Full 10y refresh every run: Yahoo split-adjusts history, so appending would mix bases.
    try:
        df = _yf(["VGT"], "10y")
        if isinstance(df.columns, pd.MultiIndex):
            df = df["VGT"]
        df = df.reset_index()
        df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
        new, src = df[COLS], "yahoo"
    except Exception as e:  # noqa: BLE001
        raw = get("https://stooq.com/q/d/l/?s=vgt.us&i=d").decode()
        new = pd.read_csv(io.StringIO(raw))
        if new.empty or "Close" not in new.columns:
            raise RuntimeError(f"yahoo failed ({e}); stooq returned no rows")
        new["Adj Close"] = float("nan")  # stooq: no adjusted column
        new, src = new[COLS], "stooq"
    df = new.dropna(subset=["Close"])  # drop unfinished / blank bars
    df = df.drop_duplicates("Date", keep="last").sort_values("Date").reset_index(drop=True)
    cutoff = (pd.Timestamp.today() - pd.DateOffset(years=10, days=7)).strftime("%Y-%m-%d")
    df = df[df["Date"] >= cutoff]
    for c in ["Open", "High", "Low", "Close", "Adj Close"]:
        df[c] = df[c].astype(float).round(4)
    return save(df, "vgt_10y.csv") + f" src={src} last={df['Date'].iloc[-1]}"


# ---------------- Peers / top holdings prices ----------------
def peers():
    df = _yf(PEERS, "10y")  # full refresh: split-safe
    rows = []
    for t in PEERS:
        if t not in df.columns.get_level_values(0):
            continue
        s = df[t][["Close", "Adj Close"]].dropna(subset=["Close"]).reset_index()
        s["Date"] = pd.to_datetime(s["Date"]).dt.strftime("%Y-%m-%d")
        s.insert(1, "Ticker", t)
        rows.append(s)
    out = pd.concat(rows)
    out = (out.drop_duplicates(["Date", "Ticker"], keep="last")
              .sort_values(["Ticker", "Date"]).reset_index(drop=True))
    for c in ["Close", "Adj Close"]:
        out[c] = out[c].astype(float).round(4)
    return save(out, "peers.csv") + f" tickers={out['Ticker'].nunique()}"


# ---------------- Fund snapshot (flows proxy) ----------------
def fund_snapshot():
    import yfinance as yf
    t = yf.Ticker("VGT")
    info = t.info or {}
    assets = info.get("totalAssets")
    nav = info.get("navPrice") or info.get("previousClose")
    shares = info.get("sharesOutstanding") or (assets / nav if assets and nav else None)
    if not assets and not shares:
        raise RuntimeError("no totalAssets / sharesOutstanding in yfinance info")
    row = pd.DataFrame([{
        "snapshot_date": TODAY, "total_assets": assets, "nav": nav,
        "shares_outstanding": shares,
        "shares_source": "reported" if info.get("sharesOutstanding") else "implied(assets/nav)",
    }])
    return append_snapshot(row, "fund_snapshot.csv")


def top_holdings():
    import yfinance as yf
    th = yf.Ticker("VGT").funds_data.top_holdings
    if th is None or th.empty:
        raise RuntimeError("no top holdings")
    th = th.reset_index()
    th.columns = ["symbol", "name", "weight"][:len(th.columns)]
    th.insert(0, "snapshot_date", TODAY)
    return append_snapshot(th, "top_holdings.csv")


# ---------------- CFTC: E-mini Nasdaq-100 ----------------
def _pick(df, *needles):
    for c in df.columns:
        if all(n in c for n in needles):
            return c
    raise KeyError(f"no column with {needles}")


def cot_nasdaq():
    base = "https://publicreporting.cftc.gov/resource/"
    q = urllib.parse.urlencode({
        "cftc_contract_market_code": "209742",
        "$order": "report_date_as_yyyy_mm_dd ASC",
        "$limit": "5000",
    })
    tff = pd.DataFrame(json.loads(get(f"{base}gpe5-46if.json?{q}")))  # TFF, futures only
    leg = pd.DataFrame(json.loads(get(f"{base}6dca-aqww.json?{q}")))  # legacy, futures only
    num = lambda df, c: pd.to_numeric(df[c], errors="coerce")
    d = pd.DataFrame({"date": tff["report_date_as_yyyy_mm_dd"].str[:10],
                      "open_interest": num(tff, _pick(tff, "open_interest"))})
    d["lev_long"] = num(tff, _pick(tff, "lev_money", "long"))
    d["lev_short"] = num(tff, _pick(tff, "lev_money", "short"))
    d["am_long"] = num(tff, _pick(tff, "asset_mgr", "long"))
    d["am_short"] = num(tff, _pick(tff, "asset_mgr", "short"))
    d["lev_net"] = d.lev_long - d.lev_short
    d["am_net"] = d.am_long - d.am_short
    l = pd.DataFrame({
        "date": leg["report_date_as_yyyy_mm_dd"].str[:10],
        "noncomm_net": num(leg, "noncomm_positions_long_all") - num(leg, "noncomm_positions_short_all"),
    })
    d = d.merge(l, on="date", how="outer").sort_values("date")
    return save(d, "cot_nasdaq.csv") + f" last={d['date'].iloc[-1]}"


# ---------------- FRED ----------------
def macro():
    frames = []
    for sid in ("VIXCLS", "VXNCLS", "DFII10", "DGS10", "DFF"):
        csv = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd=2016-01-01").decode()
        f = pd.read_csv(io.StringIO(csv))
        f.columns = ["date", sid]
        f[sid] = pd.to_numeric(f[sid], errors="coerce")
        frames.append(f.set_index("date"))
    df = pd.concat(frames, axis=1).sort_index()
    # FRED lags ~1 business day. Fill newer dates from the primary publishers
    # (FRED's own upstream sources), with Yahoo as a last resort. FRED values are
    # never overwritten; filled values are listed in status.json as provisional.
    fills, errs = [], []
    for sid, fns in MACRO_FALLBACKS.items():
        fred_last = df[sid].dropna().index.max()
        for src, fn in fns:
            try:
                s = fn().dropna()
                s = s[s.index > fred_last]
                if s.empty:
                    continue
                for d, v in s.items():  # all dates are newer than FRED's last value
                    df.loc[d, sid] = round(float(v), 4)
                df = df.sort_index()
                fills.append(f"{sid}->{s.index.max()}({src})")
                break
            except Exception as e:  # noqa: BLE001
                errs.append(f"{sid}/{src}: {type(e).__name__}")
    df = df.reset_index().rename(columns={"index": "date"}).sort_values("date")
    last = ", ".join(f"{c}={df.loc[df[c].notna(), 'date'].max()}" for c in MACRO_IDS)
    msg = save(df, "macro.csv") + f" last: {last}"
    if fills:
        msg += " | provisional (not yet on FRED): " + "; ".join(fills)
    if errs:
        msg += " | fallback errors: " + "; ".join(errs)
    return msg


# ---------- Fallbacks for the FRED lag (each returns a date-indexed Series) ----------
MACRO_IDS = ("VIXCLS", "VXNCLS", "DFII10", "DGS10", "DFF")


def _iso(idx):
    return pd.DatetimeIndex(pd.to_datetime(idx, errors="coerce")).strftime("%Y-%m-%d")


def _cboe(index):
    # https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv  -> DATE,OPEN,HIGH,LOW,CLOSE
    raw = get(f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{index}_History.csv").decode()
    f = pd.read_csv(io.StringIO(raw))
    f.columns = [c.strip().upper() for c in f.columns]
    s = pd.to_numeric(f["CLOSE"], errors="coerce")
    s.index = _iso(f["DATE"])
    return s[s.index.notna()].tail(30)


def _yahoo_close(ticker):
    df = _yf([ticker], "1mo")
    if isinstance(df.columns, pd.MultiIndex):
        df = df[ticker]
    s = df["Close"].astype(float)
    if ticker == "^TNX" and s.median() > 20:  # older Yahoo convention: yield x10
        s = s / 10
    s.index = _iso(s.index)
    return s


def _treasury_10y(kind):
    # Treasury daily par yield CSVs (MM/DD/YYYY, newest first). kind: real | nominal
    typ = "daily_treasury_real_yield_curve" if kind == "real" else "daily_treasury_yield_curve"
    out = []
    year = datetime.now(timezone.utc).year
    for y in (year - 1, year):  # covers January
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
               f"daily-treasury-rates.csv/{y}/all?type={typ}&field_tdr_date_value={y}&page&_format=csv")
        try:
            f = pd.read_csv(io.StringIO(get(url).decode()))
        except Exception:  # noqa: BLE001
            if y == year:
                raise
            continue
        col = next(c for c in f.columns if c.strip().upper().replace(" ", "") in ("10YR", "10YEAR"))
        s = pd.to_numeric(f[col], errors="coerce")
        s.index = _iso(f["Date"])
        out.append(s)
    return pd.concat(out).sort_index()


def _nyfed_effr():
    j = json.loads(get("https://markets.newyorkfed.org/api/rates/unsecured/effr/last/10.json"))
    recs = j.get("refRates", [])
    s = pd.Series({r["effectiveDate"][:10]: float(r["percentRate"]) for r in recs if r.get("percentRate") is not None})
    return s.sort_index()


MACRO_FALLBACKS = {
    "VIXCLS": [("cboe", lambda: _cboe("VIX")), ("yahoo", lambda: _yahoo_close("^VIX"))],
    "VXNCLS": [("cboe", lambda: _cboe("VXN")), ("yahoo", lambda: _yahoo_close("^VXN"))],
    "DFII10": [("treasury", lambda: _treasury_10y("real"))],
    "DGS10": [("treasury", lambda: _treasury_10y("nominal")), ("yahoo", lambda: _yahoo_close("^TNX"))],
    "DFF": [("nyfed", _nyfed_effr)],
}


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, fn in (("vgt_prices", vgt_prices), ("peers", peers),
                     ("fund_snapshot", fund_snapshot), ("top_holdings", top_holdings),
                     ("cot_nasdaq", cot_nasdaq), ("macro", macro)):
        try:
            status[name] = fn()
        except Exception as e:  # noqa: BLE001
            status[name] = f"FAILED: {type(e).__name__}: {e}"[:300]
        print(name, status[name])
    with open(os.path.join(OUT, "status.json"), "w") as fh:
        json.dump(status, fh, indent=2)


if __name__ == "__main__":
    main()
    sys.exit(0)
