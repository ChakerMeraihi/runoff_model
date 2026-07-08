# DAV run-off — external data layer (Layer 1 & 2)

Pure-stdlib fetch + assembly of the external macro series for the DAV run-off model
(PLANv2 §1b). No `requests` / `pandas` / `openpyxl` — only `urllib`, `json`,
`xml.etree`, `html.parser`, `zipfile`, `csv`, `ssl`, `math`, `statistics`.

**Run it:** `python run_all.py` (from this dir). Safe to re-run — HTTP payloads are
cached under `_cache/`. Outputs land in `_out/`.

The **DAV account panel (Layer 0)** is bank-internal and is *not* fetched here; it is
joined on the work PC (PLANv2 Phase 1).

## Modules

| File | Series | Source (verified 2026-06) |
|---|---|---|
| `fetch_util.py` | HTTP GET + gzip cache, `ssl`-relax, `Accept` header, CSV writer | — |
| `worldbank.py` | annual CPI cross-check (`FP.CPI.TOTL`) | World Bank API (JSON) |
| `imf.py` | **monthly CPI** (modeling inflation) | IMF SDMX 3.0 `IMF.STA/CPI/5.0.0`, key `DZA.CPI._T.IX.M` |
| `imf_rates.py` | monthly policy / money-market / t-bill rates | IMF `IMF.STA/MFS_IR/9.0.0`, keys `DZA.{DISR,MMRT,GSTBILY}_RT_PT_A_PT.M` |
| `oil_worldbank.py` | monthly Brent oil (regime proxy) | World Bank Pink Sheet xlsx (IMF has **no** oil dataflow) |
| `fx.py` | monthly official DZD/USD, DZD/EUR | IMF `IMF.STA/ER/4.0.1`, keys `DZA.XDC_{USD,EUR}.PA_RT.M` |
| `xlsx_reader.py` | minimal stdlib `.xlsx` reader (zip+XML) | — (used by oil; reusable for ONS) |
| `seasonal_calendar.py` | Ramadan / Eid al-Fitr / Eid al-Adha / summer vacation | computed (tabular Hijri calendar); PIT-safe, forward-known |
| `regime_events.csv` | documented Algerian macro episodes (hand-authored) | — |
| `regime_calendar.py` | monthly regime state + severity + oil tertile | events × oil data |
| `parallel_fx_premium.csv` | parallel-FX premium template (empty — no public series) | hand-fill |
| `macro_panel.py` | wide + **PIT** monthly join of everything | — |
| `gate_a.py` | rate / inflation identifiability diagnostic | — |
| `run_all.py` | end-to-end driver | — |
| `_dflist.py`, `_probe_data.py` | IMF SDMX discovery helpers (dev tools) | — |

## Key outputs (`_out/`)

- `macro_panel_wide.csv` — raw contemporaneous monthly alignment, 2005–2026.
- `macro_panel_pit.csv` — PIT view (feature at τ = latest value available given a
  per-series publication lag). This is the as-of logic the Phase-1 DAV join reuses.
- per-series CSVs (`imf_cpi_monthly_DZA.csv`, `imf_rate_*`, `oil_brent_monthly.csv`,
  `fx_*`, `seasonal_calendar.csv`, `regime_calendar.csv`).

Coverage 2015-01…2024-12: all core series **120/120** (t-bill 118/120; parallel-FX
premium 0/120 by design).

## Gate A result (from real data)

- **Policy/discount rate is administered** — 3 distinct values, 92-month flat run,
  constant through the 2023–2024 OOS. *Not* usable to identify rate-sensibilité.
- **Money-market rate is the rate driver** — 118 distinct, std 0.91, varies in the
  OOS too (std 0.54) → elasticity is OOS-checkable.
- **Inflation** (0.1%–10.8%, std 2.7%) is a solid primary driver.
- **OOS split:** dev (2015–2022) holds all three regimes incl. the 2020–21
  currency-stress; the frozen OOS (2023–2024) is **stagnant-only** → regime-stress
  run-off can be fit but **not OOS-validated** (scenario band, per §6.7).

## Validation done

Each series checked against known anchors: oil (2008-07≈134, 2020-04≈23, 2022-06≈120),
FX (DZD/USD 93→120→134 across 2015/2020/2024), seasonal calendar (Ramadan/Eid dates
2015/2020/2024 within ±1 day), PIT lag (pit[2020-04].cpi == wide[2020-03].cpi).
