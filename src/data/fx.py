"""Official DZD exchange rates from IMF ER (monthly, period average), stdlib.

ER dataflow key order: COUNTRY . INDICATOR(currency pair) . TYPE . FREQUENCY.
  XDC_USD = units of domestic currency (DZD) per USD
  XDC_EUR = DZD per EUR
  PA_RT   = period-average rate (EOP_RT = end of period)
These are the OFFICIAL rates; the parallel-market premium is hand-authored (no
public endpoint; WPCPER does not cover Algeria).
"""
from __future__ import annotations

from imf import fetch_imf_data
from fetch_util import write_series_csv

DATAFLOW = "IMF.STA/ER/4.0.1"
FX = {
    "usd_dzd": "DZA.XDC_USD.PA_RT.M",
    "eur_dzd": "DZA.XDC_EUR.PA_RT.M",
}


def fetch_fx(name, **kw):
    return fetch_imf_data(key=FX[name], dataflow=DATAFLOW, **kw)


if __name__ == "__main__":
    for name in FX:
        rows = fetch_fx(name)
        out = write_series_csv(rows, f"_out/fx_{name}_official.csv")
        win = [r for r in rows if "2015-01" <= r[0] <= "2024-12"]
        by_m = {r[0]: r[1] for r in rows}
        print(f"{name:<8} rows={len(rows)} window={len(win)} "
              f"2015-01={by_m.get('2015-01')} 2020-01={by_m.get('2020-01')} "
              f"2024-12={by_m.get('2024-12')} -> {out}")
