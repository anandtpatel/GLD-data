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
def _holdings_from_table(df):
    """df: raw sheet/CSV with no header. Find the header row with 'Date' and 'Tonnes'."""
    hdr = None
    for i in range(min(len(df), 60)):
        vals = [str(v).strip().lower() for v in df.iloc[i].tolist()]
        if any(v.startswith("date") for v in vals) and any("tonnes" in v for v in vals):
            hdr = i
            break
    if hdr is None:
        return None
    t = df.iloc[hdr + 1:].copy()
    t.columns = [str(c).strip() for c in df.iloc[hdr].tolist()]
    dcol = next(c for c in t.columns if c.lower().startswith("date"))
    tcol = next(c for c in t.columns if "tonnes" in c.lower())
    ocol = next((c for c in t.columns if "ounces" in c.lower()), None)
    out = pd.DataFrame({"date": pd.to_datetime(t[dcol], errors="coerce", dayfirst=True)})
    num = lambda c: pd.to_numeric(t[c].astype(str).str.replace(",", "").str.strip(), errors="coerce")
    out["tonnes"] = num(tcol)
    if ocol:
        out["ounces"] = num(ocol)
    out = out.dropna(subset=["date", "tonnes"]).sort_values("date")
    out["date"] = out["date"].dt.strftime("%Y-%m-%d")
    return out.drop_duplicates("date", keep="last")


def gld_holdings():
    import re
    cands = []
    for page in ("https://www.spdrgoldshares.com/usa/historical-data/",
                 "https://www.spdrgoldshares.com/usa/gld/"):
        try:
            html = get(page).decode("utf-8", "ignore")
            for href in re.findall(r'href="([^"]+\.(?:xlsx|csv)[^"]*)"', html, re.I):
                if "gld" in href.lower() and "gldm" not in href.lower():
                    cands.append(urllib.parse.urljoin(page, href))
        except Exception:  # noqa: BLE001
            pass
    cands += ["https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.xlsx",
              "https://www.spdrgoldshares.com/assets/dynamic/GLD/GLD_US_archive_EN.csv"]
    tried = []
    for url in dict.fromkeys(cands):
        try:
            raw = get(url)
            if raw[:2] == b"PK":
                sheets = pd.read_excel(io.BytesIO(raw), sheet_name=None, header=None)
            elif raw[:4] == b"%PDF" or raw[:20].lstrip().startswith(b"<"):
                tried.append(f"{url} -> not data ({raw[:8]!r})")
                continue
            else:
                txt = raw.decode("utf-8", "ignore").lstrip("\ufeff")
                sheets = {"csv": pd.read_csv(io.StringIO(txt), header=None, dtype=str,
                                             on_bad_lines="skip", engine="python")}
            for sh in sheets.values():
                out = _holdings_from_table(sh)
                if out is not None and len(out):
                    return save(out, "gld_holdings.csv") + f" src={url}"
            tried.append(f"{url} -> no Date/Tonnes header")
        except Exception as e:  # noqa: BLE001
            tried.append(f"{url} -> {type(e).__name__}: {e}")
    raise RuntimeError(" ; ".join(tried))


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
                **{k: v for k, v in m.items()
                   if any(t in k for t in ("price", "bid", "ask", "volume", "open_interest"))
                   and not isinstance(v, (dict, list))},
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
