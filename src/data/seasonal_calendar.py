"""Deterministic seasonal calendar for DAV run-off (pure stdlib).

DAV balances swing on culturally-fixed calendar events, not the Gregorian month:
  - Ramadan: heavy cash drawdown / spending (month 9 of the Hijri calendar)
  - Eid al-Fitr (1 Shawwal) and Eid al-Adha (10 Dhu al-Hijja): spending spikes
  - Summer vacation (Jul-Aug in Algeria): travel / withdrawal season

Ramadan/Eid drift ~11 days earlier each Gregorian year, so a fixed month dummy
cannot capture them; we convert each day to the Hijri calendar and aggregate per
Gregorian month. These features are KNOWN INFINITELY IN ADVANCE (computable), so
they are PIT-safe by construction and carry no available_date / publication lag.

Hijri conversion: tabular ("Kuwaiti") civil Islamic calendar via Julian Day
Number. Accurate to +/- 1-2 days vs Algeria's moon-sighting observance, which is
immaterial for monthly aggregation. Document the approximation in the model file.
"""
from __future__ import annotations

import calendar as _cal
from datetime import date

from fetch_util import write_series_csv

HEADER = ("ref_month", "ramadan_frac", "ramadan_days",
          "eid_fitr", "eid_adha", "is_summer_vacation")


def jdn_of(dt: date) -> int:
    # proleptic Gregorian ordinal -> Julian Day Number (date(2000,1,1)->2451545)
    return dt.toordinal() + 1721425


def to_islamic(dt: date):
    """Return (year, month, day) in the tabular civil Islamic calendar."""
    l = jdn_of(dt) - 1948440 + 10632
    n = (l - 1) // 10631
    l = l - 10631 * n + 354
    j = ((10985 - l) // 5316) * ((50 * l) // 17719) + (l // 5670) * ((43 * l) // 15238)
    l = l - ((30 - j) // 15) * ((17719 * j) // 50) - (j // 16) * ((15238 * j) // 43) + 29
    m = (24 * l) // 709
    d = l - (709 * m) // 24
    y = 30 * n + j - 30
    return y, m, d


def month_rows(start_year=2005, end_year=2026):
    rows = []
    for yr in range(start_year, end_year + 1):
        for mo in range(1, 13):
            ndays = _cal.monthrange(yr, mo)[1]
            ramadan = eid_fitr = eid_adha = 0
            for dd in range(1, ndays + 1):
                _, im, idd = to_islamic(date(yr, mo, dd))
                if im == 9:                      # Ramadan
                    ramadan += 1
                if im == 10 and idd == 1:        # 1 Shawwal = Eid al-Fitr
                    eid_fitr = 1
                if im == 12 and idd == 10:       # 10 Dhu al-Hijja = Eid al-Adha
                    eid_adha = 1
            rows.append((f"{yr}-{mo:02d}", round(ramadan / ndays, 4),
                         ramadan, eid_fitr, eid_adha, 1 if mo in (7, 8) else 0))
    return rows


if __name__ == "__main__":
    rows = month_rows()
    out = write_series_csv(rows, "_out/seasonal_calendar.csv", header=HEADER)
    print(f"wrote {len(rows)} months -> {out}")
    checks = {"2015-06", "2015-07", "2020-04", "2020-05", "2024-03", "2024-04", "2024-06"}
    by_m = {r[0]: r for r in rows}
    print("\nvalidation vs known Ramadan/Eid dates:")
    print("  ", HEADER)
    for m in sorted(checks):
        print("  ", by_m[m])
