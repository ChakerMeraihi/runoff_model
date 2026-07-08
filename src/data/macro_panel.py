"""Assemble all external monthly series into one macro panel (PLANv2 1b -> 4b).

Produces two tables in _out/:
  macro_panel_wide.csv : contemporaneous alignment of every series by ref_month
                         (raw, for inspection / feature engineering).
  macro_panel_pit.csv  : PIT-correct view -- each feature at decision month tau is
                         the latest value available given a per-series publication
                         LAG, i.e. value of month (tau - lag). This is the as-of
                         logic the Phase-1 panel builder will reuse for the DAV join.

Pure stdlib. Inflation (cpi_yoy) is derived since the CPI index level alone is not
the economic variable. Decision convention: end-of-month tau (month tau's value is
usable once published, i.e. after `lag` months).
"""
from __future__ import annotations

import csv
import os

from fetch_util import write_series_csv

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_out")

# 3-column series files: name -> filename
SIMPLE = {
    "cpi": "imf_cpi_monthly_DZA.csv",
    "money_market": "imf_rate_money_market_DZA.csv",
    "policy_discount": "imf_rate_policy_discount_DZA.csv",
    "tbill_yield": "imf_rate_tbill_yield_DZA.csv",
    "oil_brent": "oil_brent_monthly.csv",
    "usd_dzd": "fx_usd_dzd_official.csv",
    "eur_dzd": "fx_eur_dzd_official.csv",
}

# publication lag in months (0 = known within the reference month; market/admin
# rates, FX, oil are contemporaneous; CPI is published with a lag).
LAGS = {
    "cpi": 1, "cpi_yoy": 1,
    "money_market": 0, "policy_discount": 0, "tbill_yield": 0,
    "oil_brent": 0, "usd_dzd": 0, "eur_dzd": 0,
    "ramadan_frac": 0, "ramadan_days": 0, "eid_fitr": 0, "eid_adha": 0,
    "is_summer_vacation": 0, "severity": 0, "parallel_premium_pct": 0,
}

SEASONAL_COLS = ("ramadan_frac", "ramadan_days", "eid_fitr", "eid_adha", "is_summer_vacation")
REGIME_COLS = ("regime_state", "severity", "oil_tertile", "parallel_premium_pct")

NUMERIC = set(SIMPLE) | {"cpi_yoy"} | set(SEASONAL_COLS) | {"severity", "parallel_premium_pct"}

COLUMNS = (["cpi", "cpi_yoy", "money_market", "policy_discount", "tbill_yield",
            "oil_brent", "usd_dzd", "eur_dzd"]
           + list(SEASONAL_COLS) + list(REGIME_COLS))


def month_add(m, k):
    y, mo = map(int, m.split("-"))
    idx = y * 12 + (mo - 1) + k
    return f"{idx // 12}-{idx % 12 + 1:02d}"


def month_iter(a, b):
    y, m = a, None
    cur = a
    while cur <= b:
        yield cur
        cur = month_add(cur, 1)


def _read_simple(fn):
    out = {}
    with open(os.path.join(OUT, fn), newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        next(rd)
        for row in rd:
            if row and row[1] != "":
                out[row[0]] = float(row[1])
    return out


def _read_multi(fn, key="ref_month"):
    out = {}
    with open(os.path.join(OUT, fn), newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out[r[key]] = r
    return out


def load_all():
    series = {name: _read_simple(fn) for name, fn in SIMPLE.items()}
    # derived: inflation YoY from CPI index
    cpi = series["cpi"]
    series["cpi_yoy"] = {m: round(cpi[m] / cpi[month_add(m, -12)] - 1, 6)
                         for m in cpi if month_add(m, -12) in cpi}
    seasonal = _read_multi("seasonal_calendar.csv")
    regime = _read_multi("regime_calendar.csv")
    return series, seasonal, regime


def _cell(series, seasonal, regime, name, m):
    if name in series:
        return series[name].get(m, "")
    if name in SEASONAL_COLS:
        r = seasonal.get(m)
        return float(r[name]) if r else ""
    if name in REGIME_COLS:
        r = regime.get(m)
        if not r:
            return ""
        v = r[name]
        return float(v) if (name in NUMERIC and v != "") else v
    return ""


def build_wide(start="2005-01", end="2026-02"):
    series, seasonal, regime = load_all()
    rows = []
    for m in month_iter(start, end):
        rows.append([m] + [_cell(series, seasonal, regime, c, m) for c in COLUMNS])
    return rows


def build_pit(start="2005-01", end="2026-02"):
    """As-of view: feature at tau = value of month (tau - lag)."""
    series, seasonal, regime = load_all()
    rows = []
    for m in month_iter(start, end):
        row = [m]
        for c in COLUMNS:
            src = month_add(m, -LAGS.get(c, 0))
            row.append(_cell(series, seasonal, regime, c, src))
        rows.append(row)
    return rows


if __name__ == "__main__":
    header = ["ref_month"] + COLUMNS
    wide = build_wide()
    pit = build_pit()
    w_out = write_series_csv(wide, os.path.join(OUT, "macro_panel_wide.csv"), header=header)
    p_out = write_series_csv(pit, os.path.join(OUT, "macro_panel_pit.csv"), header=header)
    print(f"wide -> {w_out} ({len(wide)} months)")
    print(f"pit  -> {p_out} ({len(pit)} months)")

    # validation: coverage over 2015-2024
    win = [r for r in wide if "2015-01" <= r[0] <= "2024-12"]
    print(f"\ncoverage in 2015-01..2024-12 ({len(win)} months):")
    for j, c in enumerate(COLUMNS, start=1):
        n = sum(1 for r in win if r[j] != "")
        flag = "" if n == len(win) else "  <-- GAPS"
        print(f"  {c:<20} {n}/{len(win)}{flag}")
    print("\nsample row 2020-04 (wide):")
    s = next(r for r in wide if r[0] == "2020-04")
    print("  ", dict(zip(["ref_month"] + COLUMNS, s)))
    print("PIT check: cpi at 2020-04 should equal wide cpi at 2020-03 (lag 1):")
    wmap = {r[0]: r for r in wide}
    pmap = {r[0]: r for r in pit}
    ci = 1 + COLUMNS.index("cpi")
    print(f"  pit[2020-04].cpi={pmap['2020-04'][ci]}  wide[2020-03].cpi={wmap['2020-03'][ci]}")
