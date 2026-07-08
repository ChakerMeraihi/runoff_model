# DAV run-off — operational orchestration

## For the ALM team — one command

Drop the new monthly `DAV_MMYYYY.txt` into the dumps folder, then:

```
# MONTHLY (routine): refresh data, re-fit coefficients on all data, score, stress, report
python run_pipeline.py --dav-dir "D:\dav_dumps"

# QUARTERLY (governed): also re-select the model's hyper-parameters (needs MR sign-off)
python run_pipeline.py --dav-dir "D:\dav_dumps" --recalibrate

# DEMO (no bank data): synthetic real-format panel end-to-end
python run_pipeline.py --demo
```

> A bare `python run_pipeline.py` (no `--dav-dir`, no `--demo`) **refuses to run** — it
> would train on synthetic data and overwrite the deployed `model.json`. You must choose.

`run_pipeline.py` is the master runner. It preflight-checks the inputs, runs the steps
in the governed order, writes an **audit trail** (`_out/run_manifest.json` + appends
`_out/run_log.txt`: timestamp, host, mode, panel-end month, model version, per-step
status/timing), and returns a Task-Scheduler-friendly exit code (0 only if every
required step passed). `--dry-run` prints the plan without executing.

**Why two cadences (model governance).** Re-fitting *coefficients* on fresh data is a
routine recalibration → monthly. Re-selecting *hyper-parameters* is a model change →
quarterly, and the resulting `model.json` is flagged "pending model-risk sign-off" in
the manifest. The **first run auto-recalibrates** (no frozen HPs exist yet).

The same steps run individually (each is its own script):

| step | command | job | cadence |
|---|---|---|---|
| 1 | `runoff_download.py --dav-dir <dir>` | update data: fetch macro + rebuild/concat panel | on new dump |
| 2 (gov.) | `runoff_eval.py panel/_out/panel.csv` | re-select HPs + frozen-OOS report + Gate B | quarterly |
| 3 | `runoff_fit.py panel/_out/panel.csv` | deploy: fit ALL data with frozen HPs → `model.json` | monthly |
| 4 | `runoff_daily.py panel/_out/panel.csv` | score book run-off `S(t)` | daily / on demand |
| 5 | `runoff_stress.py panel/_out/panel.csv` | Monte-Carlo fan + WAL tail (±200bp, adverse) | quarterly |
| 6 | `runoff_report.py` | self-contained `report.html` (10 panels) + SVGs | after the above |

(`runoff_refit.py` = eval+fit in one call, for a manual governed recalibration.)

You only have the DAV dumps on the bank PC; step 1 fetches the macro (Bank of Algeria
rate, IMF CPI, World Bank oil, FX) over the work PC's internet. Deliverables (aggregates
only, never client rows): `_artifacts/model.json`, `_artifacts/validation_report.txt`,
`_out/calibration.csv`, `_out/stress/mc_stress.json`, `_out/report.html`, plus the audit
manifest/log. Intra-month re-score without recalibrating = `runoff_daily.py` + `runoff_report.py`.

## Scheduling (Windows Task Scheduler)

```
# monthly routine, 1st at 02:00
schtasks /create /tn dav_runoff_monthly  /sc monthly /d 1 /st 02:00 ^
  /tr "python C:\...\src\run_pipeline.py --dav-dir D:\dav_dumps"
# quarterly governed recalibration, 1st of Jan/Apr/Jul/Oct
schtasks /create /tn dav_runoff_quarterly /sc monthly /m JAN,APR,JUL,OCT /d 1 /st 03:00 ^
  /tr "python C:\...\src\run_pipeline.py --dav-dir D:\dav_dumps --recalibrate"
```

---

**Selection is nested (no selection bias on the headline).** `runoff_eval` splits the
panel **train → validation → test**: HP search on **train** (walk-forward), the
**feature family** (base / +regime / +signatures) *and* the **run-off model**
(convention/ECM/hazard) are chosen on **validation**, and the **test** tail is touched
**once** for the honest frozen-OOS headline — never used to select anything. Within a
fixed family, L1 (elastic net) does the per-coefficient selection; *across* families,
validation NLL decides — so a feature that only matters under special conditions is
picked up automatically when those conditions appear, without us hand-including it.

**Three cadences, one rule:** only the recalibration jobs write model artifacts;
`runoff_daily.py` only reads them (the "frozen-artifact contract" — what makes daily
safe to run unattended).

```
runoff_eval.py  --writes-->  _artifacts/hp_selected.json   (HP choice + OOS report)
                                      |
runoff_fit.py   --reads HP, writes--> _artifacts/model.json (deployed, ALL data)
                                      |
runoff_daily.py --reads model.json--> _out/daily/runoff_<month>.json  (S(t))
```

| entrypoint | cadence | trains on | writes |
|---|---|---|---|
| `run_pipeline.py` | monthly (one button) | runs all 5 in order | every artifact below |
| `runoff_download.py` | on new data | — (no model) | `panel/_out/panel.csv` |
| `runoff_eval.py` | quarterly / on-demand | DEV, last ~18mo **held out** | `hp_selected.json` + `validation_report.txt` |
| `runoff_fit.py` | monthly | **ALL data** (no held-out tail) | `model.json` |
| `runoff_daily.py` | daily | — (reads frozen) | `runoff_<month>.json` |
| `runoff_stress.py` | on-demand / quarterly | — (reads frozen) | `_out/stress/mc_stress.json` |
| `runoff_refit.py` | — | convenience wrapper = `eval` then `fit` | both |

**`runoff_stress.py` — Monte-Carlo stress distribution.** The deterministic +200bp curve
and Role-2 Markov generator give *scenarios*; for IRRBB you also want the *distribution*
and its tail. This forward-simulates regime+macro paths (the HMM is generative) through
the frozen hazard → S(t) **fan (5/50/95)** + **WAL tail (p01/p99)** for baseline, ±200bp,
and an adverse-regime start. It is **macro-path** uncertainty; combine with the time-block
bootstrap for **parameter** uncertainty (the binding constraint on 120 months) to get the
full band. Reads `model.json`, writes `_out/stress/mc_stress.json` (plotted by the report).

**Why eval and fit are separate (important):** `runoff_eval.py` produces the **honest
held-out OOS number** for your report — it is NOT deployed. `runoff_fit.py` produces
the **deployed** model trained on ALL data up to today (the live run-off must be
current), reusing the HPs eval selected. You almost never re-tune HPs monthly; you
re-fit coefficients monthly. Run `eval` only when you want to re-select HPs / re-report.

## `runoff_daily.py` — the heartbeat (run every day, one command)

Idempotent. On days with no new monthly close it re-emits the current S(t) and exits;
the day the close lands it ingests and scores. **Never** changes coefficients.

Each run with new data:
1. load frozen `model.json` (coefficients, scaler, HMM params, conformal q)
2. advance the **online regime FILTER** one step (frozen HMM params) → regime posterior
3. **CUSUM break ALARM** on the macro series → if it trips, flag `recommend_early_refit`
   (alarm only — it never changes the model on its own)
4. roll the frozen hazard forward → book **S(t) base + stressed (+200bp) + bands + WAL**
5. write `_out/daily/runoff_<month>.json` + update `state.json`

What is NOT in daily: HP search, re-fitting, structural-break *re-segmentation*, Gate A.
Those change what the model *is* → they live in refit (governed).

## `runoff_eval.py` — HP selection + frozen-OOS + ablation (reporting)

HP search (parallel grid, walk-forward) → frozen-OOS backtest on the held-out tail →
stochastic ablation → `hp_selected.json` + `validation_report.txt`. This is the honest
performance number and the governance artifact; **not deployed**.

## `runoff_fit.py` — deployed fit on ALL data (operational)

Reuses `hp_selected.json`, re-fits the hazard on **all** rows (no held-out tail), fits
the regime HMM params for daily's filter → `model.json`. This is what daily scores with.
Run monthly. A governance sign-off gates promoting the new `model.json`.

## Regime layer — two distinct objects (do not conflate)

1. **Learned HMM posterior (Role-1, the hazard feature).** Gaussian HMM fit on the
   macro series with **standardized emissions** (without standardizing, oil ~65 swamps
   cpi ~0.05 → degenerate transitions / single-state collapse) and a **BIC-selected K**
   (2–3 states; BIC penalizes the third unless it earns it). The causal *filtered*
   posterior `P(state_t | macro_≤t)` is offered to the EN-logistic hazard as `regime_p*`
   columns — but only **deployed if it earns its place** (Gate B below).
2. **Exogenous regime calendar (Role-2, the stress scenario).** Hand-dated
   `{hydrocarbon-liquid, stagnant, currency-stress}` from `data/regime_calendar.py`; it
   shades the timeline plot and drives the stressed-`S(t)` scenario. It is *not* learned
   and is stagnant-dominated by construction (Gate A: ~2–3 episodes in 120 months).

**Gate B (PLANv2 6.7).** `runoff_eval.py` fits the HMM on **DEV only**, attaches the
filtered posterior to all rows (dev params applied forward → frozen-OOS stays honest),
and runs the full HP search + frozen-OOS **twice** (`base` vs `base+regime`). The regime
feature is deployed **iff it lowers frozen-OOS NLL**; otherwise it is reported but
dropped. The decision + per-candidate NLL/ECE land in `validation_report.txt` and
`model.json["regime_gate"]`. Retrain trigger is separate: **CUSUM** break alarm.

| tool | daily | refit |
|---|---|---|
| HMM **filter** (causal posterior, standardized) | ✅ frozen params | re-fits params + BIC-K |
| regime feature in the hazard | ✅ if Gate B kept it (`use_regime`) | Gate-B decision |
| CUSUM **break alarm** | ✅ monitoring only | — |
| ICSS / batch-HMM **re-segmentation** | — | ✅ |

## Which run-off model gets deployed (convention vs ECM vs hazard)

Three models can produce the book run-off `B(t)` (= today's *balance* decaying):
- **convention** — core/volatile split (`model/convention.py`): the regulator reference.
- **ECM** — Engle-Granger on the book aggregate (`model/ecm.py`): the economics (rate
  **elasticity**, reversion **φ**, **half-life**), always reported even when not deployed.
- **hazard** — the account-level behavioural model `A(t)·r(t)`: the challenger.

**Selection is a governed, frozen decision (like HP selection).** At the `--recalibrate`
step, `runoff_eval` scores all three against the **realised cohort book run-off** on the
**validation** window (MAE), records the winner, and reports the chosen model again on the
untouched **test** tail; it lands in `model.json["runoff_model"]` with the comparison table.
Routine monthly fits **reuse that choice** — they never silently switch. Override with
`--model {auto,hazard,ecm,convention}` (`auto` = validation winner, default).

Separately, the **feature family** (raw `base` vs `+regime` posterior vs `+signatures`
path-terms) is auto-selected on validation NLL each recalibration — so signatures and the
regime layer are *re-tested every time*, not parked, and deployed only when they help.
(`--no-signatures` skips the heavier signature family.)
`runoff_daily` dispatches on `runoff_model` to produce `B(t)`; the report's *Run-off model
selection* panel plots realised vs all three so the pick is auditable. This is how
"is the complex model worth it?" gets answered in print: deploy the behavioural model only
when it beats the convention/ECM out-of-time, otherwise ship the simple one.

## Multi-product book run-off (`runoff_book.py`)

The entrypoints above model **one pooled panel**. `runoff_book.py` runs that same
validated machinery **once per behavioral book** and aggregates to a whole-book curve:

```
python runoff_book.py                     # multi-segment synthetic demo
python runoff_book.py panel/_out/panel.csv   # real panel (all segments in one CSV)
```

The behavioral catalogue lives in `model/products.py` — **comptes à vue DINARS**
(primary DAV), **comptes à vue DEVISES**, **épargne**, **découverts**, **HB engagement de
financement**. Each gets its **own** hazard `A_s(t)` + erosion `r_s(t)` → `B_s(t)`; the
book run-off is the **balance-weighted** sum `B_book(t) = Σ_s W_s·B_s(t) / Σ_s W_s`
(`W_s` = the segment's current book balance). Per segment the driver reuses `evaluate`
(nested train/val/test, HP + model selection) and `fit_deployed` unchanged, then rolls
the frozen `book_survival` — so each per-segment model is bit-for-bit the single-product
one. Signatures/ablation are off in the book run (5× segments; both are single-product
diagnostics); the frozen-OOS protocol is preserved.

> **Scope: behavioral only.** The EFM "Encours" is a **stock snapshot** — it has the
> balance but **no date d'échéance**, so a contractual *échéancier* (DAT, BDC, crédits)
> can only come from a separate deal/Matisse extract, which we don't have. Contractual
> books (`garantie`, …) are **tagged in the panel and excluded** from the engine
> (`products.py` `behavioral=False`), never behaviorally modeled.

Artifacts (`_out/book/`): `book_runoff.json` (per-segment + aggregate curves & WAL),
`model_<segment>.json` (each deployed per-segment model), and **`report.xlsx`** — a real
`.xlsx` written with **pure stdlib** (`model/xlsx_writer.py`: zip of hand-emitted XML, the
write-half of the reader in `panel/efm_collect.py`, round-trips through it). Sheets: *Book
Summary* (per-book balance / weight / WAL base+200bp / test-NLL / run-off winner / version
+ a BOOK total), *Book Runoff* (whole-book `A_t/r_t/B_t/B_t+200bp`), one `Curve_<segment>`
per book, and *Skipped* (books with too little history). Native in-cell charts are phase 2.

## HP search & overfit control

Glmnet-style **λ-path × α-grid**, selected by walk-forward OOT **NLL/Brier** (proper
scoring, not AUC). Deterministic & auditable — the right tool for 2–3 HPs and for
model-validation governance, **not** Optuna/TPE. Parallel across CPU cores via stdlib
`multiprocessing` (measured **~6–7× on 20 cores**, result identical to serial).

**1-SE rule (default, overfit-safe):** deploy the most-regularized HP whose OOT score
is within 1 SE of the best. Leans toward regularization, and stays stable when the HP
surface is flat (where the raw argmin just chases noise into a corner).

**No early stopping** — and intentionally so. For a *convex* elastic-net, coordinate
descent converges *to* the penalized optimum; stopping early would be a worse solve of
the same problem, not a different regularizer. The overfit controls are the penalty λ
(selected on walk-forward OOT via the 1-SE rule), the purged/embargoed CPCV, the frozen
OOS tail, and conformal coverage. `lr`/`epochs`/`max_irls` are convergence settings
(fixed), not tuned; HP *ranking* uses lighter CD than the final deployed fit.

## Solver

Hazard = **coordinate descent** (glmnet IRLS + cyclic CD, `solver='cd'`, default):
per-coordinate soft-threshold over a ridge-shrunk denominator (elastic net), no step
tuning, fast on the collinear signature features. **Proximal gradient** (`solver='pgd'`,
ISTA) is kept and gives identical coefficients. (Projected GD is *not* used — that's for
the constrained ‖β‖₁≤t form, not our penalized form.) **Huber** is not used on the
hazard (a Bernoulli outcome has no outliers); it belongs on the continuous ECM /
balance-erosion regressions, where it is the right robust loss if outliers appear.

## Stochastic ablation (rigor)

`model/stochastic_ablation.py` compares the data-driven EN-logistic hazard against the
**continuous-time Markov-generator survival** `S(t)=1ᵀexp((Q−diag α)t)p₀`, a
tenure-parametric hazard, and a constant-hazard floor, on identical OOT book-S(t).
Result: the data-driven model wins; the stochastic Markov model beats the floor but is
dominated — documenting that the stochastic toolbox is not competitive here.

## Scheduling — daily score

The monthly/quarterly `run_pipeline.py` schedules are at the top of this file. The only
*daily* job is the score (reads the frozen model; never recalibrates):
```
schtasks /create /tn dav_runoff_daily /sc daily /st 06:00 ^
  /tr "python C:\...\src\runoff_daily.py D:\dav_dumps\panel.csv"
```

## Plots (pure stdlib — no matplotlib)

`model/viz.py` hand-writes **SVG** (just XML text) → opens in any browser on the
locked-down PC, zero dependencies. Plots are aggregates (clearing-safe). Chart types:
line + fan band, bars + error, reliability diagram, PIT histogram, heatmap, regime
timeline; plus ASCII sparklines for the terminal.

```
python runoff_report.py    # -> _out/report.html (one self-contained file, 8 panels)
                           #    + _out/svg/*.svg (standalone, for Beamer/LaTeX)
```
Panels: S(t) fan chart · reliability diagram · PIT histogram · HP-surface heatmap ·
stochastic-ablation bars · oil+regime timeline · rate & inflation · regime posterior.
`runoff_eval.py` persists the diagnostics (reliability table, PIT counts, HP grid) that
the report plots; `runoff_daily.py` prints an S(t) ASCII sparkline in its log.

## Layout

```
src/
  runoff_daily.py     heartbeat (reads model.json)
  runoff_refit.py     monthly recalibration (writes model.json)
  _artifacts/         model.json + validation_report.txt   (the frozen contract)
  _out/daily/         runoff_<month>.json + state.json
  data/               external macro fetch -> macro_panel_pit.csv (run_all.py)
  panel/              DAV dumps -> account-month survival panel (panel_builder.py)
  model/              estimators + regime + rigor kit (run_tests.py: 14/14)
```

## Demo (synthetic, no bank data)

```
python run_pipeline.py     # full chain (recommended) -> all artifacts + report.html
```
or step-by-step (each step builds the real-format synthetic panel on first call, so the
demo exercises the SAME path as production — money-market stress, balance erosion,
regime gate — not a degenerate toy):
```
python runoff_refit.py     # = eval then fit -> _artifacts/model.json + validation_report.txt
python runoff_daily.py     # -> scores S(t)
python runoff_daily.py     # -> [no-op] idempotent
```
On the work PC, build the real panel once with `python panel/panel_builder.py --dav-dir
<dumps> --out panel/_out/panel.csv` (after `panel/profile_dav.py` confirms grain/scope),
then pass that `panel.csv` to eval/fit/daily — or just `run_pipeline.py --dav-dir <dumps>`.
