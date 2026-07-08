# Transfer to the work PC (bank workstation)

The bank PC cannot clone or download a zip — you open this repo on github.com and
**copy each file's contents by hand** into a file of the same name/location.

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

## Work-PC run order
1. **Organize/convert the EFM folders** (Excel; originals untouched, mirror tree written under `-OutDir`):
   ```
   powershell src/panel/efm_convert_xls.ps1 -Root "<...\Controle_de_gestion>" -OutDir "<...\EFM_converted>"
   ```
2. **Profile one real EFM** to confirm the schema, then share `profile_report.txt`
   (tune `panel_builder.segment_of()` / `model/products.py` and grain/scope/floor to the real Rubrique labels):
   ```
   python src/panel/profile_dav.py "<...\EFM_converted>"
   ```
3. **Run the autonomous pipeline** (download macro → panel → eval/fit → daily → stress → report/book):
   ```
   python src/run_pipeline.py --data-dir "<...\EFM_converted>"
   ```
   - First run auto-recalibrates; `--recalibrate` forces governed HP re-selection.
   - The `src/data/` macro layer needs internet. If the bank PC is offline, fetch macro
     on an internet machine and carry the **public** macro CSVs over (still no client data).

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
- [ ] **efm_convert_xls.ps1**  ← the EFM folder-organizer (PowerShell + Excel)

**Optional (reference docs):** `README.md`, `PLAN*.md`, `doc/*.md`, `doc/build.ps1`, `doc/meta.yaml`, `doc/build_order.txt`.
