# GUIDE — running the deposit run-off model

A practical, step-by-step guide for the **ALM / Treasury team** who run the model each
month. For *what the model is*, see [README.md](README.md); for *getting the code onto the
bank PC*, see [TRANSFER.md](TRANSFER.md).

Everything runs from the `src/` folder with plain `python` — no install, standard library only.

---

## TL;DR — the monthly run (one command)
```
python src/run_pipeline.py --data-dir "<...\EFM_converted>"
```
This does the whole chain: refresh data → build the panel → fit → score → stress → reports.
Everything lands in `src/_out/` and `src/_artifacts/`.

---

## First time on the bank PC — 2 commands
**1. Convert the EFM workbooks** (Excel converts the old `.xls` to `.xlsx`; your originals are
never touched — a mirror copy is written under `-OutDir`):
```
powershell src/panel/efm_convert_xls.ps1 -Root "<...\Controle_de_gestion>" -OutDir "<...\EFM_converted>"
```
**2. Run the pipeline** on the converted folder:
```
python src/run_pipeline.py --data-dir "<...\EFM_converted>"
```
That's it. `run_pipeline.py` auto-detects the EFM folder, builds the panel, fits, and writes
the reports.

---

## Try it without any bank data (demo)
```
python src/run_pipeline.py --demo     # full pipeline on synthetic data
python src/model/run_tests.py          # run every self-test (expect all green)
```

---

## What you get (the deliverables)
After a run, look in `src/_out/` and `src/_artifacts/`:

| File | What it is |
|------|-----------|
| `_out/report.html` | Visual report — **open in any web browser** |
| `_out/book/report.xlsx` | The **Excel workbook**: every deposit book + IRRBB (ΔEVE/ΔNII) + crisis + uncertainty. Includes **Glossaire** and **Guide** sheets (in French) explaining each tab. |
| `_artifacts/model.json` | The fitted model (coefficients) — the "signed-off" model |
| `_out/stress/mc_stress.json` | Monte-Carlo stress results |
| `_out/calibration.csv` | Calibration / reliability of the model |
| `_out/run_manifest.json`, `_out/run_log.txt` | Audit trail (who/when/what, per-step status) |

---

## Two cadences: routine vs governed
| When | Command | What it does |
|------|---------|--------------|
| **Monthly (routine)** | `run_pipeline.py --data-dir X` | Reuses the signed-off hyper-parameters, just refits on the new month. Fast. |
| **Quarterly / on change (governed)** | `run_pipeline.py --data-dir X --recalibrate` | Re-selects the hyper-parameters (flagged "pending model-risk sign-off"). |

The **first ever run** auto-recalibrates (there are no frozen settings yet). The pipeline also
**auto-escalates to recalibrate** if it detects a regime break in the rates — no manual trigger
needed. Add `--no-auto-recalibrate` to turn that off.

---

## The individual scripts (optional — run steps separately)
`run_pipeline.py` runs these in order. You can also run any one on its own:

| Script | Role |
|--------|------|
| `runoff_download.py` | Refresh macro data + (re)build the panel from the EFM/DAV folder |
| `runoff_eval.py` | *(governed)* re-select hyper-parameters + frozen-OOS validation |
| `runoff_fit.py` | Fit the deployed model on all data (frozen settings) |
| `runoff_daily.py` | Score the run-off curve S(t) / B(t) |
| `runoff_stress.py` | Monte-Carlo stress fan + WAL tail |
| `runoff_report.py` | Build `report.html` |
| `runoff_book.py` | Multi-product book + IRRBB → `report.xlsx` |

---

## Options (flags) reference
All of these go on `run_pipeline.py`:

| Flag | What it does |
|------|--------------|
| `--data-dir "<dir>"` | The real-data folder — an EFM tree (`06-EFM/*.xlsx`) or `DAV_*.txt` dumps. (`--dav-dir` is the same thing.) |
| `--demo` | Run on synthetic data, no bank data needed |
| `--recalibrate` | Governed re-selection of hyper-parameters |
| `--no-auto-recalibrate` | Don't auto-escalate to recalibrate on a detected regime break |
| `--skip-macro` | Don't fetch macro — use what's already downloaded (**use this offline**) |
| `--refresh-macro` | Force re-fetching macro from the internet |
| `--model {auto,hazard,ecm,convention}` | Which run-off model to deploy (`auto` = best on frozen-OOS) |
| `--floor <KDA>` | Balance below this = "account closed" (default 50) |
| `--scope "<kw,kw>"` | Keep only Rubriques containing these keywords (default: keep everything) |
| `--paths <N>` | Monte-Carlo paths for the stress fan (default 1200) |
| `--no-augment` | Turn off the engineered path features in the book |
| `--crisis-elasticity <x>` | **ALM assumption:** deposit-flight elasticity in the imposed crisis (default 0.8) |
| `--crisis-oil-drop <x>` | **ALM assumption:** oil-price drop fraction in the crisis, e.g. 0.55 = −55% (default 0.40) |
| `--crisis-months <n>` | **ALM assumption:** duration of the imposed oil crash, in months (default 6) |
| `--dry-run` | Show the plan + preflight checks, run nothing |

---

## "What if…?" analysis
The Excel report is a **static snapshot of one governed run** — you don't type new numbers into
cells to explore a scenario. The numbers come from a *fitted model + simulations*, not Excel
formulas, so editing a cell wouldn't recompute anything downstream.

Instead, **re-run with a changed setting** and you get a fully consistent new report in ~30s:
```
python src/run_pipeline.py --data-dir X --model ecm        # a different run-off model
python src/run_pipeline.py --data-dir X --floor 100        # stricter "closed" threshold
python src/run_pipeline.py --data-dir X --scope "DINARS"   # one product only
python src/run_pipeline.py --data-dir X --paths 2000       # a denser stress fan
python src/run_pipeline.py --data-dir X --recalibrate      # re-tune on the latest data
```

**Change the crisis (reverse-stress) assumptions** — the ALM assumptions you see on the
`Crise_Stress` sheet (that `0,8` flight elasticity, the −40% oil crash, 6 months) *are* flags:
```
python src/run_pipeline.py --data-dir X --crisis-elasticity 0.9    # harsher deposit flight
python src/run_pipeline.py --data-dir X --crisis-oil-drop 0.55     # deeper oil crash (-55%)
python src/run_pipeline.py --data-dir X --crisis-months 9          # longer crash
python src/run_pipeline.py --data-dir X --crisis-elasticity 0.5 --crisis-months 4   # combine
```
Defaults are `0.8` / `0.40` / `6`. The `Crise_Stress` sheet (WAL under crisis, shortening,
dEVE under crisis) recomputes to match — consistently, because the whole model re-runs.

> **Note:** the **regulatory** rate scenarios (±200 bp and the six EBA scenarios behind
> ΔEVE/ΔNII) are *defined by the regulator* and intentionally **not** adjustable — changing them
> would make those numbers non-regulatory. Only the *reverse-stress crisis* (an internal ALM
> what-if) is a knob.

---

## Offline bank PC
The `src/data/` macro layer downloads from the internet (IMF / World Bank / oil / FX). If the
bank PC has no internet, fetch the **public** macro CSVs on a machine that does, copy them into
`src/data/_out/`, and add `--skip-macro`.

---

## Troubleshooting
| Symptom | Fix |
|---------|-----|
| `no DAV dumps and no EFM workbooks` | Point `--data-dir` at the folder that contains the `06-EFM` tree (or the `DAV_*.txt` files). |
| `report.xlsx` doesn't update / permission error | Close it in Excel first — Excel locks a file while it's open. |
| Columns not recognized | `python src/panel/efm_collect.py profile "<EFM_converted>"` prints the real headers; send them so the column map can be tuned. |
| Macro fetch fails | Use `--skip-macro` (with the public CSVs in place), or check the machine's internet. |
| Short history / few months | Handled automatically — the model shrinks its validation window (validation is just weaker with less data). |

---

## Reading `report.xlsx`
It opens in Excel. If it was downloaded from email/web, Excel may show a yellow **"Enable
Editing"** bar — that's Excel's download safety, not the file being locked; click it and it's
fine. Key sheets: **Synthèse** (summary), **Écoulement** (run-off curves), **IRRBB** (ΔEVE/ΔNII),
**Crise / Stress**, **Incertitude**, plus **Glossaire** and **Guide** sheets that explain every
tab in plain French. It's a *display* of the model's output — to change anything, re-run the
pipeline (see "What if…?" above).
