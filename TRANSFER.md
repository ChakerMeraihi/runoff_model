# Transfer to the work PC (bank workstation)

Get the code onto the work PC either way:
- **Download ZIP** (repo is public): `< > Code` → *Download ZIP*, or
  `https://github.com/ChakerMeraihi/runoff_model/archive/refs/heads/main.zip`, then extract.
- **Or copy by hand** if downloads are blocked: open each file on github.com and paste its
  contents into a file of the same name/location.

## Rules
- **Code only, never data.** Nothing under `_out/`, `_artifacts/`, `_synth/`,
  `panel/_out/`; no `.csv` / `.xlsx` — **except** `src/data/regime_events.csv`.
- Recreate the **exact directory tree** below; paste each file verbatim.
- Easy-to-miss non-`.py` files: **`src/panel/efm_convert_xls.ps1`**,
  **`src/data/regime_events.csv`**, `doc/build.ps1`, `doc/meta.yaml`, `doc/build_order.txt`.
- Binary docs (`*.pdf`, `*.html`) can't be pasted — rebuild from `doc/` if needed.

## After pasting: sanity check
```
python src/model/run_tests.py      # expect the full self-test suite green
```
If it's green, the paste is intact and imports resolve.

## Work-PC run order — 2 commands
1. **Convert/organize the EFM folders** (Excel; originals untouched, a mirror `.xlsx` tree is written under `-OutDir`):
   ```
   powershell src/panel/efm_convert_xls.ps1 -Root "<...\Controle_de_gestion>" -OutDir "<...\EFM_converted>"
   ```
2. **Run the pipeline** — it auto-detects the EFM tree, builds the panel, fits, and writes the reports:
   ```
   python src/run_pipeline.py --data-dir "<...\EFM_converted>"
   ```
   Deliverables land in `src/_out/` (`report.html`, `book/report.xlsx`, `stress/`, `calibration.csv`)
   and `src/_artifacts/model.json`.

Notes:
- First run auto-recalibrates (selects hyper-parameters); later runs reuse them. `--recalibrate` forces it.
- Works on **shorter EFM history** too — the multi-product book automatically shrinks its walk-forward
  validation window when there are few months (validation is just weaker with less history).
- The `src/data/` macro layer needs **internet**. Offline bank PC: fetch macro on an internet machine,
  carry the **public** macro CSVs into `src/data/_out/`, and add `--skip-macro`.

**Only if command 2 errors on columns** (real EFM headers / sheet names differ from the assumed ones),
profile one workbook and send me the output so I can tune the column map (`efm_collect.WANT`):
```
python src/panel/efm_collect.py profile "<...\EFM_converted>"
```

---

## File checklist (required to run)

**`src/` (orchestration)**
- [ ] run_pipeline.py
- [ ] runoff_book.py
- [ ] runoff_common.py
- [ ] runoff_daily.py
- [ ] runoff_download.py
- [ ] runoff_eval.py
- [ ] runoff_fit.py
- [ ] runoff_refit.py
- [ ] runoff_report.py
- [ ] runoff_stress.py

**`src/data/`**
- [ ] fetch_util.py
- [ ] fx.py
- [ ] gate_a.py
- [ ] imf.py
- [ ] imf_rates.py
- [ ] macro_panel.py
- [ ] oil_worldbank.py
- [ ] parallel_fx.py
- [ ] regime_calendar.py
- [ ] run_all.py
- [ ] seasonal_calendar.py
- [ ] worldbank.py
- [ ] xlsx_reader.py
- [ ] **regime_events.csv**  ← curated input, not regenerable

**`src/model/`**
- [ ] bootstrap.py
- [ ] conformal.py
- [ ] convention.py
- [ ] ecm.py
- [ ] erosion.py
- [ ] explosive.py
- [ ] fracdiff.py
- [ ] gbm.py
- [ ] hazard.py
- [ ] hmm_regime.py
- [ ] hp_search.py
- [ ] irrbb.py
- [ ] linalg.py
- [ ] linmodel.py
- [ ] macro_sim.py
- [ ] montecarlo.py
- [ ] nonlin_experiment.py
- [ ] online_hmm.py
- [ ] operational_validation.py
- [ ] param_uncertainty.py
- [ ] products.py
- [ ] range_vol.py
- [ ] regime_var_garch.py
- [ ] report_text.py
- [ ] run_tests.py
- [ ] sig_features.py
- [ ] signatures.py
- [ ] splitter.py
- [ ] stochastic_ablation.py
- [ ] structural_breaks.py
- [ ] survival.py
- [ ] synthetic_dav.py
- [ ] synthetic_panel.py
- [ ] viz.py
- [ ] xlsx_validate.py
- [ ] xlsx_writer.py

**`src/panel/`**
- [ ] dav_reader.py
- [ ] efm_collect.py
- [ ] panel_builder.py
- [ ] profile_dav.py
- [ ] synth_dav_files.py
- [ ] synth_efm_files.py
- [ ] **efm_convert_xls.ps1**  ← the EFM folder-organizer (PowerShell + Excel)

**Optional (reference docs):** `README.md`, `PLAN*.md`, `doc/*.md`, `doc/build.ps1`, `doc/meta.yaml`, `doc/build_order.txt`.
