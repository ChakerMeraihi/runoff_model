"""IMF SDMX 3.0 data fetcher (stdlib JSON) — the monthly inflation series.

Monthly Algeria all-items CPI index = the modeling inflation series (PLANv2 1b).
Key layout for the CPI dataflow (IMF.STA/CPI/5.0.0), discovered via imf_probe.py:
  COUNTRY . INDEX_TYPE . COICOP_1999 . TYPE_OF_TRANSFORMATION . FREQUENCY
  DZA     . CPI        . _T          . IX                     . M

The legacy dataservices.imf.org/REST/SDMX_JSON.svc endpoint is retired; this uses
the new api.imf.org SDMX 3.0 service and requires an SDMX-JSON Accept header.
"""
from __future__ import annotations

import json

from fetch_util import http_get, write_series_csv

BASE = "https://api.imf.org/external/sdmx/3.0"
SDMX_JSON_DATA = "application/vnd.sdmx.data+json;version=2.0.0"

DATAFLOW = "IMF.STA/CPI/5.0.0"
DZA_CPI_MONTHLY = "DZA.CPI._T.IX.M"   # all-items monthly CPI index level, Algeria


def fetch_imf_data(key=DZA_CPI_MONTHLY, dataflow=DATAFLOW, **kw):
    """Return [(period, value, available_date), ...] oldest-first for an SDMX key."""
    url = f"{BASE}/data/dataflow/{dataflow}/{key}?dimensionAtObservation=TIME_PERIOD"
    payload = json.loads(http_get(url, accept=SDMX_JSON_DATA, **kw))
    return _parse_sdmx_json(payload)


def _parse_sdmx_json(payload):
    data = payload["data"]
    struct = data.get("structures", data.get("structure"))
    if isinstance(struct, list):
        struct = struct[0]
    def vkey(v):
        return v.get("id") or v.get("value") or v.get("start") or v.get("name")

    def norm_month(p):
        # SDMX monthly notation "2015-M01" -> ISO "2015-01"
        return p.replace("-M", "-") if p and "-M" in p else p

    def num(x):
        try:
            return float(x)
        except (TypeError, ValueError):
            return None

    time_vals = []
    for d in struct["dimensions"]["observation"]:
        if d["id"] == "TIME_PERIOD":
            time_vals = [norm_month(vkey(v)) for v in d["values"]]
    ds = data["dataSets"][0]
    rows = []
    if "series" in ds:
        for sval in ds["series"].values():
            for tidx, ov in sval["observations"].items():
                rows.append((time_vals[int(tidx)], num(ov[0]), ""))
    else:
        for okey, ov in ds.get("observations", {}).items():
            rows.append((time_vals[int(okey.split(":")[-1])], num(ov[0]), ""))
    rows = [r for r in rows if r[1] is not None]
    rows.sort(key=lambda r: r[0])
    return rows


if __name__ == "__main__":
    rows = fetch_imf_data()
    out = write_series_csv(rows, "_out/imf_cpi_monthly_DZA.csv")
    print(f"wrote {len(rows)} rows -> {out}")
    if rows:
        print("first:", rows[0], " last:", rows[-1])
        win = [r for r in rows if "2015-01" <= r[0] <= "2024-12"]
        print(f"rows in 2015-01..2024-12: {len(win)}")
