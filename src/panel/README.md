# DAV panel layer — Layer 0 (internal, work PC)

Turns the anonymized monthly DAV dumps into the account-month **survival panel**
that feeds `../model/` (splitter → hazard → survival). Pure stdlib. The raw dumps
never leave the bank; only the panel + model outputs do.

## Workflow on the WORK PC

1. **Profile first (don't guess the scope/grain):**
   ```
   python profile_dav.py  "C:\...\DAV anonymized"
   ```
   Share `profile_report.txt`. It reports distinct `Rubriques` / `CODE TYPE COMPTE`,
   rows-per-client, clients holding multiple products, balance distribution, header
   drift, date coverage — everything needed to finalize the two decisions below.

2. **Finalize config from the profile**, then build the panel:
   ```
   python panel_builder.py
   ```

## The modules

| File | Role |
|---|---|
| `dav_reader.py` | robust read: encoding/delimiter/`Titre du rapport`/header-by-name/US-date/comma-number. Period from the **filename** (`DAV_MMYYYY`), not the PERIODE column. |
| `profile_dav.py` | **run on work PC** → profiling report (scope + grain + quality evidence) |
| `panel_builder.py` | stack files → account-month survival panel + PIT macro join |
| `synth_dav_files.py` | generates files in the real format (for local validation) |

## The two decisions `panel_builder` is parameterized on

- **Grain** (`key_extra`): default account key = `(client_id, type_compte)`. The
  profile shows how many clients hold >1 Rubrique/type — if material (likely, given
  `compte à vue` vs `dépôt de garantie`), keep the type in the key.
- **Scope / segment** (`scope_keywords`, default `None` = **keep everything**):
  nothing is dropped. Every deposit product is kept and tagged with a `segment`
  (`vue_dinars`, `vue_bu`, `garantie`, …). `dépôt de garantie` is a pledged-collateral
  deposit that runs off on its underlying **contract** lifecycle, not on depositor
  behaviour, so it must be a **separate hazard segment** — kept, not pooled, not
  deleted. The modeling layer chooses per-segment vs pooled-with-dummy (reversible);
  set `scope_keywords` only if the desk explicitly restricts perimeter.
- **Event** (`floor`): `disappearance OR balance < floor` (user decision). Set
  `floor` in KDA from the profile's balance quantiles + desk input on "near-empty".

## Survival bookkeeping (validated on synthetic ground truth)

- event month = earliest of {first month balance < floor, closure month}; closure =
  last observed month if before panel end, else **right-censored**.
- **left-truncation**: only observed person-months enter; `seasoning` (months since
  `DATE OUVERTURE`) carries pre-observation tenure. Accounts opened before the panel
  are counted (`left_truncated_accounts`).
- data quality: opening-date-after-first-seen is impossible → clamped + counted
  (`bad_open_dates_clamped`); internal gaps carried forward as alive (configurable).

Validation (`python panel_builder.py` on synthetic): 57/57 disappearance + 11/11
floor events matched, 0 rows after event, scope excludes `dépôt de garantie`,
seasoning ≥ 0, macro PIT join 100%.

## Output

`_out/panel.csv` — columns: `account_id, month_int, year, month, event, seasoning,
log_balance, balance_kda, cal_month, rubrique, type_compte` + PIT macro columns
(`cpi_yoy, money_market, oil_brent, regime_state, ramadan_frac, …`). Ready for the
model layer.
