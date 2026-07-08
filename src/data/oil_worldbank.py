"""Monthly crude oil (Brent) from the World Bank Pink Sheet (stdlib xlsx).

Layer-2 regime proxy for hydrocarbon liquidity (PLANv2 6.7), replacing the
unavailable EOD feed. Brent tracks Algeria's Sahara Blend closely. Sheet
"Monthly Prices", header row has "Crude oil, Brent", dates like 1960M01.
"""
from __future__ import annotations

import re

from fetch_util import http_get, write_series_csv
from xlsx_reader import read_sheet

URL = ("https://thedocs.worldbank.org/en/doc/"
       "18675f1d1639c7a34d463f59263ba0a2-0050012025/related/"
       "CMO-Historical-Data-Monthly.xlsx")

DATE_RE = re.compile(r"^(\d{4})M(\d{2})$")


def fetch_oil_brent(url=URL, column="Crude oil, Brent", **kw):
    rows = read_sheet(http_get(url, timeout=120, **kw), "Monthly Prices")
    col = hdr = None
    for i, r in enumerate(rows):
        for j, c in enumerate(r):
            if isinstance(c, str) and c.strip().lower() == column.lower():
                hdr, col = i, j
    if col is None:
        raise RuntimeError(f"column {column!r} not found in Pink Sheet")
    out = []
    for r in rows[hdr + 1:]:
        d = r[0] if r else None
        m = DATE_RE.match(d) if isinstance(d, str) else None
        if not m:
            continue
        v = r[col] if col < len(r) else None
        if isinstance(v, (int, float)):
            out.append((f"{m.group(1)}-{m.group(2)}", float(v), ""))
    out.sort(key=lambda x: x[0])
    return out


if __name__ == "__main__":
    rows = fetch_oil_brent()
    out = write_series_csv(rows, "_out/oil_brent_monthly.csv")
    win = [r for r in rows if "2015-01" <= r[0] <= "2024-12"]
    print(f"wrote {len(rows)} rows ({len(win)} in 2015-2024) -> {out}")
    by_m = {r[0]: r[1] for r in rows}
    print("validation vs known Brent levels ($/bbl):")
    for m, expect in [("2008-07", "~133 peak"), ("2020-04", "~18-26 COVID crash"),
                      ("2022-06", "~118 Ukraine spike"), ("2016-01", "~31 trough")]:
        print(f"  {m}: {by_m.get(m)}   (expected {expect})")
