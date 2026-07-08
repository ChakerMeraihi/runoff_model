"""parallel_fx.py -- automate the Algeria PARALLEL-MARKET ("square" / marche noir) FX rate.

There is no official/IMF endpoint for Algeria's black-market EUR/USD rate. But eurodz.com
publishes a FULL DAILY history (2016->today) of BOTH the parallel EUR/DZD and the official
rate, embedded as JSON in the page. We fetch it, compute the parallel PREMIUM
(parallel/official - 1) per day, aggregate to monthly, and write parallel_fx_premium.csv --
which regime_calendar.py already consumes into `parallel_premium_pct`. Pure stdlib (urllib +
regex); runs on the internet-connected machine at data-refresh time, like the IMF/WB fetch.

Sources (primary first; the others are current-rate fallbacks / cross-checks):
  eurodz.com/taux       -- daily history JSON {date,buyRate,sellRate,officialBuy/Sell}  <- history
  exchangedz.com/fr/taux, forexalgerie.com  -- current parallel EUR/USD (spot only)

Honesty: this is the informal-market rate as published by a tracker, not an official series;
it is the best available proxy for the square and is labelled `source` in the CSV.
"""
from __future__ import annotations

import csv
import os
import re
import ssl
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_CSV = os.path.join(HERE, "parallel_fx_premium.csv")

EURODZ = "https://eurodz.com/taux/"
EXCHANGEDZ = "https://www.exchangedz.com/fr/taux"
FOREXALG = "https://www.forexalgerie.com/"
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# daily history rows embedded in eurodz.com (EUR/DZD parallel + official)
_HIST = re.compile(
    r'\{"date":"([0-9]{4}-[0-9]{2}-[0-9]{2})",'
    r'"buyRate":([0-9.]+),"sellRate":([0-9.]+),'
    r'"officialBuyRate":([0-9.]+),"officialSellRate":([0-9.]+)\}')
# recent EXTENDED rows also carry USD (+GBP) parallel: eurodz publishes USD only for the last
# ~month, not the full 2016 history, so USD parallel is recent-only (EUR is the deep signal).
_HIST_USD = re.compile(
    r'"date":"([0-9]{4}-[0-9]{2}-[0-9]{2})"[^}]*?'
    r'"usdBuyRate":([0-9.]+),"usdSellRate":([0-9.]+)')
# current spot (fallback)
_SPOT_EUR = re.compile(r'"EUR":\{"buy":([0-9.]+),"sell":([0-9.]+)')
_SPOT_USD = re.compile(r'"USD":\{"buy":([0-9.]+),"sell":([0-9.]+)')


def _fetch(url, timeout=20):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE                       # some trackers have loose certs
    req = urllib.request.Request(url, headers=UA)
    return urllib.request.urlopen(req, timeout=timeout, context=ctx).read().decode("utf-8", "ignore")


def parse_history(html):
    """-> list of (date 'YYYY-MM-DD', parallel_eur_dzd, official_eur_dzd) from eurodz JSON."""
    out = []
    for d, b, s, ob, os_ in _HIST.findall(html):
        par = (float(b) + float(s)) / 2.0
        off = (float(ob) + float(os_)) / 2.0
        if par > 0 and off > 0:
            out.append((d, par, off))
    return out


def parse_usd(html):
    """-> list of (date, usd_parallel_dzd) from the recent extended rows (USD is not in the
    deep EUR history)."""
    out = []
    for d, b, s in _HIST_USD.findall(html):
        mid = (float(b) + float(s)) / 2.0
        if mid > 0:
            out.append((d, mid))
    return out


def _monthly_mean(pairs):
    """[(date, value)] -> {YYYY-MM: mean value}."""
    agg = {}
    for d, v in pairs:
        a = agg.setdefault(d[:7], [0.0, 0])
        a[0] += v
        a[1] += 1
    return {ym: a[0] / a[1] for ym, a in agg.items()}


def to_monthly(daily, usd_daily=None):
    """Daily (date, parallel, official) -> monthly rows (ref_month, parallel_eur, official_eur,
    premium_pct, parallel_usd) using the monthly MEAN. Premium = parallel/official - 1.
    USD parallel is merged in where available (recent months only)."""
    agg = {}
    for d, par, off in daily:
        ym = d[:7]                                        # YYYY-MM
        a = agg.setdefault(ym, {"par": 0.0, "off": 0.0, "n": 0})
        a["par"] += par
        a["off"] += off
        a["n"] += 1
    usd_m = _monthly_mean(usd_daily) if usd_daily else {}
    rows = []
    for ym in sorted(agg):
        a = agg[ym]
        par = a["par"] / a["n"]
        off = a["off"] / a["n"]
        prem = (par / off - 1.0) * 100.0 if off else 0.0
        usd = round(usd_m[ym], 2) if ym in usd_m else ""
        rows.append((ym, round(par, 2), round(off, 2), round(prem, 2), usd))
    return rows


def scrape(out_csv=OUT_CSV, verbose=True):
    """Fetch the parallel-FX history, write parallel_fx_premium.csv. Returns n months, or 0."""
    try:
        html = _fetch(EURODZ)
    except Exception as e:
        if verbose:
            print(f"  parallel-FX: eurodz fetch failed ({type(e).__name__}); "
                  f"trying current-spot fallback")
        return _fallback_spot(out_csv, verbose)
    daily = parse_history(html)
    if not daily:
        if verbose:
            print("  parallel-FX: no history parsed from eurodz; trying current-spot fallback")
        return _fallback_spot(out_csv, verbose)
    usd_daily = parse_usd(html)
    monthly = to_monthly(daily, usd_daily)
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ref_month", "parallel_eur_dzd", "official_eur_dzd", "premium_pct",
                    "parallel_usd_dzd", "source"])
        for ym, par, off, prem, usd in monthly:
            w.writerow([ym, par, off, prem, usd, "eurodz.com"])
    if verbose:
        lo, hi = monthly[0], monthly[-1]
        n_usd = sum(1 for r in monthly if r[4] != "")
        print(f"  parallel-FX: {len(monthly)} months {lo[0]}..{hi[0]}  "
              f"(EUR premium {lo[3]:.0f}% -> {hi[3]:.0f}%; USD parallel on {n_usd} recent months)"
              f"  -> {os.path.basename(out_csv)}")
    return len(monthly)


def _fallback_spot(out_csv, verbose):
    """If the history is unavailable, at least capture the CURRENT parallel EUR premium from a
    tracker (one point, this month). Better than nothing for live monitoring."""
    for url in (EURODZ, EXCHANGEDZ, FOREXALG):
        try:
            html = _fetch(url)
        except Exception:
            continue
        m = _SPOT_EUR.search(html)
        if m:
            par = (float(m.group(1)) + float(m.group(2))) / 2.0
            if verbose:
                print(f"  parallel-FX: current spot parallel EUR/DZD={par:.1f} from {url} "
                      f"(no history; official premium needs the official rate)")
            return 0
    if verbose:
        print("  parallel-FX: all sources failed; parallel_premium stays empty")
    return 0


def current_spot():
    """Current parallel EUR & USD (buy/sell) for the daily monitor. Best-effort dict."""
    for url in (EURODZ, EXCHANGEDZ):
        try:
            html = _fetch(url)
        except Exception:
            continue
        out = {}
        me, mu = _SPOT_EUR.search(html), _SPOT_USD.search(html)
        if me:
            out["eur_buy"], out["eur_sell"] = float(me.group(1)), float(me.group(2))
        if mu:
            out["usd_buy"], out["usd_sell"] = float(mu.group(1)), float(mu.group(2))
        if out:
            out["source"] = url
            return out
    return {}


# --------------------------------------------------------------------------- #
def _self_test():
    # offline: parse a synthetic sample identical in shape to eurodz's embedded JSON
    sample = ('...prefix...'
              # deep EUR history (short format, 2016->)
              '{"date":"2016-01-01","buyRate":174.5,"sellRate":174.5,'
              '"officialBuyRate":117.42,"officialSellRate":119.77}'
              '{"date":"2016-01-15","buyRate":176.5,"sellRate":177.5,'
              '"officialBuyRate":117.50,"officialSellRate":119.90}'
              '{"date":"2024-06-10","buyRate":240,"sellRate":242,'
              '"officialBuyRate":145.0,"officialSellRate":146.0}'
              # a recent EXTENDED row (EUR + USD + GBP), coexists with the deep history
              '{"date":"2024-06-10","buyRate":240,"sellRate":242,'
              '"officialBuyRate":145.0,"officialSellRate":146.0,'
              '"usdBuyRate":222,"usdSellRate":224,"gbpBuyRate":300,"gbpSellRate":304}'
              '...suffix...')
    daily = parse_history(sample)
    assert len(daily) == 3, daily
    usd = parse_usd(sample)
    assert len(usd) == 1 and usd[0][0] == "2024-06-10" and abs(usd[0][1] - 223) < 1, usd
    monthly = to_monthly(daily, usd)
    assert monthly[0][0] == "2016-01" and monthly[-1][0] == "2024-06", monthly
    assert 40 < monthly[0][3] < 55, monthly[0]                 # 2016-01 EUR premium ~48%
    assert 60 < monthly[-1][3] < 70, monthly[-1]               # 2024-06 EUR premium ~65%
    assert monthly[0][4] == "" and abs(monthly[-1][4] - 223) < 1, monthly   # USD only recent
    assert _SPOT_EUR.search('x"EUR":{"buy":276,"sell":278,').group(1) == "276"
    print("parallel_fx self-test PASSED (offline parse + monthly EUR premium + recent USD)")
    print(f"  sample: 2016-01 EUR prem={monthly[0][3]}%  2024-06 EUR prem={monthly[-1][3]}% "
          f"USD parallel={monthly[-1][4]}")


if __name__ == "__main__":
    import sys
    if "--scrape" in sys.argv:
        n = scrape()
        print(f"wrote {n} months to {OUT_CSV}")
    else:
        _self_test()
