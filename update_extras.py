"""Download supporting data for the daily GLD report into data/.

Each source is independent: one failing doesn't stop the others.
  data/cot_gold.csv        CFTC COT, COMEX gold (088691): managed money + non-commercial
  data/gld_holdings.csv    SPDR GLD daily tonnes / ounces in the trust
  data/fed_odds.csv        Kalshi Fed decision markets (latest snapshot, appended)
  data/macro.csv           FRED: 10y real yield (DFII10), broad dollar (DTWEXBGS), fed funds (DFF)
  data/status.json         what succeeded / failed on the last run
"""
import io
import json
import os
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import pandas as pd

OUT = "data"
UA = {"User-Agent": "Mozilla/5.0 (gld-data bot; github.com/anandtpatel/gld-data)"}
status = {"run_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def get(url, timeout=60):
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout).read()


def save(df, name):
    df.to_csv(os.path.join(OUT, name), index=False)
    return f"ok rows={len(df)}"


# ---------------- CFTC Commitments of Traders ----------------
def cot():
    base = "https://publicreporting.cftc.gov/resource/"
    q = urllib.parse.urlencode({
        "cftc_contract_market_code": "088691",
        "$order": "report_date_as_yyyy_mm_dd ASC",
        "$limit": "5000",
    })
    dis = pd.DataFrame(json.loads(get(f"{base}72hh-3qpy.json?{q}")))   # disaggregated, futures only
    leg = pd.DataFrame(json.loads(get(f"{base}6dca-aqww.json?{q}")))   # legacy, futures only
    d = pd.DataFrame({
        "date": dis["report_date_as_yyyy_mm_dd"].str[:10],
        "open_interest": dis["open_interest_all"].astype(int),
        "mm_long": dis["m_money_positions_long_all"].astype(int),
        "mm_short": dis["m_money_positions_short_all"].astype(int),
    })
    d["mm_net"] = d.mm_long - d.mm_short
    l = pd.DataFrame({
        "date": leg["report_date_as_yyyy_mm_dd"].str[:10],
        "noncomm_net": leg["noncomm_positions_long_all"].astype(int)
                       - leg["noncomm_positions_short_all"].astype(int),
    })
    d = d.merge(l, on="date", how="outer").sort_values("date")
    return save(d, "cot_gold.csv")


# ---------------- SPDR GLD holdings ----------------
def gld_holdings():
    raw = get("https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.csv").decode("utf-8", "ignore")
    lines = raw.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip().lower().startswith("date"))
    df = pd.read_csv(io.StringIO("\n".join(lines[start:])), skipinitialspace=True)
    df.columns = [c.strip() for c in df.columns]
    tcol = next(c for c in df.columns if "tonnes" in c.lower())
    ocol = next((c for c in df.columns if "ounces" in c.lower()), None)
    out = pd.DataFrame({"date": pd.to_datetime(df["Date"], errors="coerce", dayfirst=True)})
    out["tonnes"] = pd.to_numeric(df[tcol].astype(str).str.replace(",", ""), errors="coerce")
    if ocol:
        out["ounces"] = pd.to_numeric(df[ocol].astype(str).str.replace(",", ""), errors="coerce")
    out = out.dropna(subset=["date", "tonnes"]).sort_values("date")
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    out = out.drop_duplicates("date", keep="last")
    return save(out, "gld_holdings.csv")


# ---------------- Kalshi Fed odds ----------------
def fed_odds():
    rows = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    for series in ("KXFEDDECISION", "KXFED"):
        q = urllib.parse.urlencode({"series_ticker": series, "status": "open", "limit": "200"})
        data = json.loads(get(f"https://api.elections.kalshi.com/trade-api/v2/markets?{q}"))
        for m in data.get("markets", []):
            rows.append({
                "snapshot_utc": now, "series": series,
                "event": m.get("event_ticker"), "ticker": m.get("ticker"),
                "title": m.get("title"), "subtitle": m.get("yes_sub_title") or m.get("subtitle"),
                "close_time": m.get("close_time"),
                "last_price": m.get("last_price"), "yes_bid": m.get("yes_bid"), "yes_ask": m.get("yes_ask"),
            })
    if not rows:
        raise RuntimeError("no open Kalshi Fed markets returned")
    new = pd.DataFrame(rows)
    path = os.path.join(OUT, "fed_odds.csv")
    if os.path.exists(path):
        old = pd.read_csv(path)
        old = old[old.snapshot_utc.str[:10] != now[:10]]  # one snapshot per day
        new = pd.concat([old, new])
    new = new[new.snapshot_utc >= (pd.Timestamp.utcnow() - pd.Timedelta(days=400)).strftime("%Y-%m-%d")]
    return save(new, "fed_odds.csv")


# ---------------- FRED macro ----------------
def macro():
    frames = []
    for sid in ("DFII10", "DTWEXBGS", "DFF"):
        csv = get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd=2016-01-01").decode()
        f = pd.read_csv(io.StringIO(csv))
        f.columns = ["date", sid]
        f[sid] = pd.to_numeric(f[sid], errors="coerce")
        frames.append(f.set_index("date"))
    df = pd.concat(frames, axis=1).reset_index().sort_values("date")
    return save(df, "macro.csv")


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, fn in (("cot", cot), ("gld_holdings", gld_holdings),
                     ("fed_odds", fed_odds), ("macro", macro)):
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
