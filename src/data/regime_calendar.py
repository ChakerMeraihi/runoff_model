"""Build the monthly exogenous regime calendar for PLANv2 6.7 Role-2.

Combines:
  - a hand-authored calendar of DOCUMENTED Algerian macro episodes (regime_events.csv),
  - data-driven oil tertiles (from the World Bank Brent series we fetched),
  - an optional hand-authored parallel-FX premium (parallel_fx_premium.csv; empty
    until filled -- no public series exists).

Output: monthly (regime_state, severity, oil_brent, oil_tertile, parallel_premium_pct).

LOOK-AHEAD NOTE: `regime_state`/`severity` are HAND-AUTHORED WITH HINDSIGHT, so they
are for **Role-2 stress scenarios only** (exogenous), NOT a Role-1 causal feature.
The causal, autonomous regime feature for Role-1 lives in model/regime_features.py
(online HMM filtered posterior + CUSUM + ICSS). `oil_tertile` is now CAUSAL
(expanding-window, no look-ahead).
"""
from __future__ import annotations

import bisect
import csv
import os

from fetch_util import write_series_csv

HERE = os.path.dirname(os.path.abspath(__file__))
HEADER = ("ref_month", "regime_state", "severity", "oil_brent", "oil_tertile",
          "parallel_premium_pct")


def month_iter(a, b):
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    y, m = ya, ma
    while (y, m) <= (yb, mb):
        yield f"{y}-{m:02d}"
        m += 1
        if m > 12:
            m, y = 1, y + 1


def _read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def load_events(path=None):
    return _read_csv(path or os.path.join(HERE, "regime_events.csv"))


def load_oil(path=None):
    path = path or os.path.join(HERE, "_out", "oil_brent_monthly.csv")
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            out[row[0]] = float(row[1])
    return out


def load_parallel(path=None):
    path = path or os.path.join(HERE, "parallel_fx_premium.csv")
    out = {}
    for r in _read_csv(path):
        if r.get("premium_pct"):
            out[r["ref_month"]] = float(r["premium_pct"])
    return out


def build(start="2005-01", end="2026-12"):
    state, sev = {}, {}
    for e in load_events():
        for mth in month_iter(e["start_month"], e["end_month"]):
            state[mth], sev[mth] = e["regime_state"], int(e["severity"])
    oil = load_oil()
    par = load_parallel()

    # CAUSAL (expanding-window) oil tertile: at month tau the threshold uses only
    # oil history with ref_month <= tau (no look-ahead). Early months are warmup.
    warmup = 24
    hist = []
    rows = []
    for mth in month_iter(start, end):
        ob = oil.get(mth)
        ter = ""
        if ob is not None:
            bisect.insort(hist, ob)
            if len(hist) >= warmup:
                lo = hist[len(hist) // 3]
                hi = hist[2 * len(hist) // 3]
                ter = "low" if ob < lo else ("high" if ob >= hi else "mid")
        rows.append((mth, state.get(mth, ""), sev.get(mth, ""),
                     ob if ob is not None else "", ter, par.get(mth, "")))
    return rows


if __name__ == "__main__":
    rows = build()
    out = write_series_csv(rows, os.path.join(HERE, "_out", "regime_calendar.csv"), header=HEADER)
    n_par = sum(1 for r in rows if r[5] != "")
    print(f"wrote {len(rows)} months -> {out}  (parallel-premium filled: {n_par})")
    by_m = {r[0]: r for r in rows}
    print("\nvalidation (state / severity / oil / tertile):")
    for m in ["2008-07", "2016-01", "2018-06", "2020-04", "2022-06", "2024-12"]:
        print("  ", by_m.get(m))
