"""IMF MFS_IR interest rates for Algeria (monthly) — the rate-sensibilite drivers.

Indicator codes discovered via the DZA.*.* data probe (key order COUNTRY.INDICATOR.FREQUENCY):
  DISR_RT_PT_A_PT     discount rate (central bank policy / rediscount) -- headline driver
  MMRT_RT_PT_A_PT     money market (interbank) rate
  GSTBILY_RT_PT_A_PT  government Treasury-bill yield
  MFS135_RT_PT_A_PT / MFS162_RT_PT_A_PT  other reported rates (deposit / lending)
"""
from __future__ import annotations

from imf import fetch_imf_data
from fetch_util import write_series_csv

DATAFLOW = "IMF.STA/MFS_IR/9.0.0"
RATES = {
    "policy_discount": "DZA.DISR_RT_PT_A_PT.M",
    "money_market":    "DZA.MMRT_RT_PT_A_PT.M",
    "tbill_yield":     "DZA.GSTBILY_RT_PT_A_PT.M",
}


def fetch_rate(name, **kw):
    return fetch_imf_data(key=RATES[name], dataflow=DATAFLOW, **kw)


if __name__ == "__main__":
    for name in RATES:
        rows = fetch_rate(name)
        out = write_series_csv(rows, f"_out/imf_rate_{name}_DZA.csv")
        win = [r for r in rows if "2015-01" <= r[0] <= "2024-12"]
        vals = [r[1] for r in win]
        print(f"{name:<16} rows={len(rows)} window={len(win)} "
              f"min={min(vals)} max={max(vals)} distinct={len(set(vals))} "
              f"last={rows[-1]} -> {out}")
