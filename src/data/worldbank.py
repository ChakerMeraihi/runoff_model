"""World Bank indicator API fetcher (stdlib JSON).

Verified live 2026-06 for Algeria CPI. NOTE: most WB series are ANNUAL, so per
PLANv2 Section 1b this is the annual CROSS-CHECK only, never the monthly modeling
series (monthly inflation -> ONS / IMF IFS).
"""
from __future__ import annotations

import json

from fetch_util import http_get, write_series_csv

BASE = "https://api.worldbank.org/v2"

# Handy indicator ids (Algeria = DZA)
CPI_ANNUAL = "FP.CPI.TOTL"          # consumer price index, 2010 = 100
FX_OFFICIAL = "PA.NUS.FCRF"          # official exchange rate, LCU per US$, period avg


def fetch_worldbank(indicator, country="DZA", start=2015, end=2024, per_page=2000, **kw):
    """Return [(year, value, available_date), ...] oldest-first for a WB indicator.

    available_date is left blank: WB does not expose a vintage date on this
    endpoint, and annual series are low-stakes for the PIT join.
    """
    url = (f"{BASE}/country/{country}/indicator/{indicator}"
           f"?format=json&date={start}:{end}&per_page={per_page}")
    payload = json.loads(http_get(url, **kw))
    if not isinstance(payload, list) or len(payload) < 2 or payload[1] is None:
        msg = payload[0] if isinstance(payload, list) and payload else payload
        raise RuntimeError(f"unexpected WB payload for {indicator}: {msg}")
    rows = [(rec["date"], rec["value"], "")
            for rec in payload[1] if rec.get("value") is not None]
    rows.sort(key=lambda r: r[0])
    return rows


if __name__ == "__main__":
    rows = fetch_worldbank(CPI_ANNUAL)
    out = write_series_csv(rows, "_out/wb_cpi_annual_DZA.csv")
    print(f"wrote {len(rows)} rows -> {out}")
    for r in rows:
        print(r)
