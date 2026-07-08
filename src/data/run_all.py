"""Run the full external data layer end-to-end: fetch -> assemble -> Gate A.

Pure stdlib. Safe to re-run (HTTP payloads are cached on disk). Run from src/data/:
    python run_all.py
Outputs land in _out/. The DAV account panel (Layer 0) is bank-internal and is NOT
fetched here; it is joined on the work PC (PLANv2 Phase 1).
"""
from __future__ import annotations

import os

import worldbank
import imf
import imf_rates
import oil_worldbank
import fx
import seasonal_calendar
import regime_calendar
import macro_panel
import gate_a
from fetch_util import write_series_csv

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_out")


def w(rows, name, header=("ref_period", "value", "available_date")):
    return write_series_csv(rows, os.path.join(OUT, name), header=header)


def main():
    print("[1/9] World Bank annual CPI (cross-check)")
    w(worldbank.fetch_worldbank(worldbank.CPI_ANNUAL), "wb_cpi_annual_DZA.csv")

    print("[2/9] IMF monthly CPI (modeling inflation series)")
    w(imf.fetch_imf_data(), "imf_cpi_monthly_DZA.csv")

    print("[3/9] IMF monthly rates (policy / money-market / t-bill)")
    for n in imf_rates.RATES:
        w(imf_rates.fetch_rate(n), f"imf_rate_{n}_DZA.csv")

    print("[4/9] World Bank Brent oil (regime proxy)")
    w(oil_worldbank.fetch_oil_brent(), "oil_brent_monthly.csv")

    print("[5/9] IMF official FX (USD/EUR per DZD)")
    for n in fx.FX:
        w(fx.fetch_fx(n), f"fx_{n}_official.csv")

    print("[6/9] seasonal calendar (Ramadan/Eid/vacation)")
    w(seasonal_calendar.month_rows(), "seasonal_calendar.csv", header=seasonal_calendar.HEADER)

    print("[6b] parallel-market FX premium (eurodz.com scrape -> parallel_fx_premium.csv)")
    try:
        import parallel_fx
        parallel_fx.scrape()                              # REAL square premium, 2016->today
    except Exception as e:
        print(f"  parallel-FX scrape skipped ({type(e).__name__}); premium stays as-is")

    print("[7/9] regime calendar (events + oil tertiles + parallel premium)")
    w(regime_calendar.build(), "regime_calendar.csv", header=regime_calendar.HEADER)

    print("[8/9] macro panel (wide + PIT)")
    hdr = ["ref_month"] + macro_panel.COLUMNS
    w(macro_panel.build_wide(), "macro_panel_wide.csv", header=hdr)
    w(macro_panel.build_pit(), "macro_panel_pit.csv", header=hdr)

    print("[9/9] Gate A inventory\n")
    gate_a.main()


if __name__ == "__main__":
    main()
