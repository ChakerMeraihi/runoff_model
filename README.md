# runoff_model — Behavioral deposit run-off for IRRBB (Algeria)

Pure **standard-library** Python (no numpy / pandas / sklearn) model of behavioral
run-off `S(t)` / `B(t)` for non-maturing deposits (comptes à vue DINARS = DAV,
épargne, découverts, …), built to run on a locked-down bank workstation.

**Status:** engine complete and **synthetic-validated** (`run_tests.py` green).
It has **not** yet been run on the real EFM client panel — that is the one binding
gap and happens on the work PC. See **[TRANSFER.md](TRANSFER.md)**.

## Hard rule: code only, never data
No client / EFM / DAV data and no pipeline outputs are ever committed. The
`.gitignore` enforces this (`_out/`, `_artifacts/`, `_synth/`, `panel/_out/`, and
all `*.csv` / `*.xlsx` / …). The **one** tracked exception is
`src/data/regime_events.csv` — a hand-authored calendar of *public, documented*
Algerian macro episodes that the code reads as an input.

## Layout
| Path | What |
|------|------|
| `src/data/`   | Macro data layer (IMF / World Bank / oil / parallel-FX scrape). **Needs internet.** |
| `src/model/`  | Numerical + statistical kit: hazard, erosion, HMM/regimes, breaks, IRRBB (ΔEVE/ΔNII), MC stress, pure-stdlib `.xlsx` writer. `run_tests.py` = full self-test suite. |
| `src/panel/`  | EFM/DAV ingestion: `efm_convert_xls.ps1` (Excel `.xls`→`.xlsx`), `efm_collect.py` (stdlib xlsx reader → client panel), `panel_builder.py`, `profile_dav.py`. |
| `src/runoff_*.py`, `src/run_pipeline.py` | Orchestration (download → panel → eval/fit → daily → stress → report/book). |
| `doc/` | Theory companion (markdown + built PDF). |

## Requirements
- **Python 3.10**, standard library only.
- **Excel on the work PC** — the legacy EFM workbooks are old `.xls` (OLE2/BIFF);
  pure stdlib reads `.xlsx` only, so Excel converts them once via `efm_convert_xls.ps1`.

## Quick check / demo
```
python src/model/run_tests.py        # expect all self-tests green
python src/run_pipeline.py --demo    # full end-to-end on synthetic data
```

## Real run (work PC) — 2 commands
See **[TRANSFER.md](TRANSFER.md)**. In short:
```
powershell src/panel/efm_convert_xls.ps1 -Root "<...\Controle_de_gestion>" -OutDir "<...\EFM_converted>"
python src/run_pipeline.py --data-dir "<...\EFM_converted>"
```
`run_pipeline.py` auto-detects whether `--data-dir` holds `DAV_*.txt` dumps or a converted
EFM tree (`06-EFM/*.xlsx`), builds the survival panel, fits, and writes the reports. It adapts
to shorter EFM history automatically. If the real EFM headers differ from the assumed ones,
`python src/panel/efm_collect.py profile "<EFM_converted>"` prints them so the column map can be tuned.
