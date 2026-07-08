# Modèle d'écoulement des Dépôts à Vue (DAV) — Refined Project Plan

**Deliverable:** a behavioral run-off curve `S(t)` for non-maturing demand deposits (DAV),
plus its rate/inflation **sensibilité**, packaged for IRRBB (and optionally FTP).
**Data:** client-level (account-level) monthly panel, **2015-01 → 2024-12 (120 months)**, DZD.
**External joins available:** market rate(s) + inflation (monthly).

This plan ports the rigor of the multi-horizon forecasting engine (CPCV, purge/embargo,
sequential/block bootstrap, PIT calibration, no-look-ahead discipline, honesty-over-pretty-numbers)
to a **survival / discrete-time-hazard** problem on a **short monthly panel**. Where the methods
transfer directly they are reused; where the problem differs they are deliberately adapted, and the
adaptation is justified rather than assumed.

---

## 0. The one thing that changes everything: this is forward extrapolation on 120 months

The trading engine estimated a *cross-sectional* signal and could afford CPCV as the **primary**
protocol (combinatorial folds, 28 paths) because the quantity of interest (IC) is roughly
exchangeable across folds. A run-off model is different on two axes:

1. **It extrapolates forward in time.** Its job is to predict the decay of *today's* stock over the
   coming months/years. A protocol that trains on the future to score the past (the core of CPCV)
   does **not** establish forward predictive validity — which is precisely what model validation and
   the regulator will demand. So **out-of-time (walk-forward) is the headline protocol; CPCV is a
   secondary variance/robustness tool**, not the deliverable's evidence base. This inversion is
   intentional.

2. **The binding uncertainty is temporal, not cross-sectional.** There may be 10⁴–10⁶ accounts but
   only **~10 years and very few rate/inflation cycles**. Cross-sectional sampling error is tiny;
   regime/macro uncertainty dominates. This is the same lesson as `feedback_report_ci_not_pvalues`
   (cross-asset-honest block bootstrap, block = horizon): **accounts are not independent — they share
   macro shocks** — so an account-only bootstrap understates total uncertainty exactly the way the
   per-asset-independent bootstrap did. The honest headline interval must come from a **time-block /
   common-factor bootstrap**, not an account bootstrap. See §6.

Everything below follows from these two facts.

---

## 1. Validation budget — how to split 120 months

### 1.1 Frozen out-of-time (OOS) test
**Reserve the last 18–24 months as a frozen out-of-time test; default = 24 months (2023-01 → 2024-12).**
Touch it **once**, at Phase 5, for the realized-vs-predicted backtest. Development uses **2015-01 →
2022-12 (96 months)**.

Rationale:
- 24 months ≈ 20% of the timeline, covers ≥2 full seasonal cycles, and lets you backtest run-off
  curves out to a 12–24-month horizon from rolling origins inside the window.
- It is long enough that the OOT backtest is a genuine multi-month run-off test, not a one-shot.

**Data-contingent caveat (decide with Gate A first).** With Algerian rates historically administered,
the *only* episode where rates/inflation actually moved may be narrow (e.g. the post-2022 inflation
spike). There is then a real tension:
- Put that episode in the **test** window → you test the rate channel out-of-sample (ideal) but
  cannot *fit* the elasticity well.
- Put it in **development** → you can fit the elasticity but it is then OOS-untested.

**Action:** inventory *when* rate and inflation actually varied (Gate A) **before** fixing the split.
Prefer a split that leaves at least one macro-varying episode in *both* dev and test. If there is only
one such episode, keep it in dev (so the elasticity is estimable) and **declare the rate sensibilité
as OOS-unvalidated** in the validation note (honesty rule). Do not silently choose.

**Horizon-vs-history limit (state it openly).** If the run-off horizons of regulatory interest exceed
~24 months, 10 years of data **cannot** fully validate them out-of-sample. Validate the hazard and the
≤24-month aggregate empirically; beyond that the curve is a **structural extrapolation** and must be
labelled as such, with sensitivity to the extrapolation assumption reported.

### 1.2 Development window (96 months): two nested protocols
- **Primary — anchored walk-forward (expanding origin).** Train on `[2015-01, o]`, score `(o, o+K]`,
  step the origin `o` forward. This is the protocol that mirrors deployment and selects
  hyper-parameters and the model class. Report the **mean and dispersion across origins**, never a
  single fold.
- **Secondary — Combinatorial Purged CV (CPCV), adapted.** Used only to get a *distribution* of
  performance and a stability read, analogous to the 28-path engine. Group the 96 dev months into
  **8 super-blocks of ~12 months → N=8, k=2 → C(8,2)=28 paths**, with **purge + embargo** between
  train and test super-blocks (§1.3). Grouping unit = calendar month, so an account's rows are split
  by time and the *same month* never appears in both train and test. Report fold dispersion as a
  robustness check, **not** as the headline number.

### 1.3 Purge & embargo sizing
The single-month hazard label at `(account i, month t)` = event in `(t, t+1]`, so raw label overlap
is **1 month**. But the deliverable aggregates hazards into a survival curve over a horizon `H`
(`S(t) = Π(1 − ĥ)` out to `H`), and the **backtest** of `S` over `H` months has overlap `H`. Therefore:

- **Embargo / purge = H**, the maximum run-off horizon you evaluate (e.g. 12 or 24 months), applied
  on **both** sides of every test block. Any train person-month whose `[t, t+H]` label window overlaps
  a test block is purged. This is the direct analog of the engine's embargo `E = 60` business days =
  `max horizon`.
- For pure hazard discrimination (1-month), a 1-month embargo suffices; for `S(t)` calibration use `H`.

### 1.4 Optional: unseen-account generalization split
The time split lets an account appear in train (early months) and test (late months) — desired, it is
the same entity over time (like an asset). If you also need to claim generalization to **newly opened
accounts**, additionally hold out a random sample of accounts entirely and report a "new-client"
score. Optional, secondary.

---

## 2. Phase 0 — Scope & data contract (½ week)

Unchanged in intent; tightened on the points that bind the statistics.

- Pin with the manager: GenAI interpretation, segment/currency in scope, downstream consumer (IRRBB
  sensitivity / FTP / both), and the *référentiel* definition of the `CODE TYPE COMPTE` values
  (B35M/T cut). Confirm grain = **account-month**.
- **Define the event precisely** — closure / balance below a floor / dormant N months — and decide
  **competing risks vs single event** here (see §3.1). Everything downstream keys off this.
- **Define the run-off quantity precisely.** Is `S(t)` the decay of the *current outstanding stock*
  (standard écoulement) driven by (a) account attrition **and** (b) balance erosion on surviving
  accounts? If yes, the model is not pure survival — it is
  `B_i(t) = B_i(0)·A_i(t)·r_i(t)` (alive-indicator × balance-ratio-given-alive). Decide whether to
  model `r_i(t)` explicitly or freeze balance at `t0` and absorb erosion into the hazard. **This
  choice must be made now**, not discovered in Phase 3.
- Fix success metric now (see §5): OOT realized-vs-predicted aggregate `S(t)` + calibration +
  interval coverage, benchmarked against the convention and the ECM.
- **Output:** one-page scope note (incl. event definition, run-off quantity, frozen-OOS decision
  pending Gate A).

---

## 3. Phase 1 — Data engineering: build the panel (1–1.5 weeks)

Goal: a clean account-month panel with **survival bookkeeping** and **PIT-correct** macro joins.

- Parse fixed-width dumps (`read_fwf` / regex) → tidy DataFrame; one row per `(account, month)` with
  balance (`CTRVL KDA` for currency consistency), type, `DATE OUVERTURE`, currency.
- Filter to a homogeneous population (DZD, target segment).
- **Survival bookkeeping:** event flag; **right-censoring** for accounts alive at 2024-12;
  **left-truncation** for accounts opened before 2015 (they enter at 2015-01 with seasoning > 0 — the
  hazard likelihood must condition on survival-to-entry, do **not** treat entry as origin).
- **Features (all causal, ≤ t):** seasoning (months since `DATE OUVERTURE`), age cohort, lagged
  balances, balance buckets, calendar month/year, intra-account balance trend/volatility.
- **Macro join — PIT discipline (direct port of the Step-3 look-ahead lesson).** Rates/inflation are
  published with a lag; align **as-of** the information set at decision month `t`, never the
  final/revised vintage aligned to its reference month. Document the assumed publication lag.
- **No-look-ahead on fitted statistics.** Balance-bucket thresholds, standardization scalers, ECM
  coefficients, frailty estimates, any quantile cut — **fit on the training window only**, applied
  forward. This is the exact failure mode of the engine's global-fit bug (`feedback_step3_lookahead_bug`):
  a threshold fit on 2015–2024 and used at 2016 leaks the future. Make `fit_end` a **required**
  argument, not a default.
- **QA:** missingness, gaps, outliers, currency consistency; **reconcile summed `CTRVL KDA` to a known
  aggregate total** per month (the single most important data-integrity check).
- **Output:** modeling-ready panel + data-quality memo.

### → Gate A (rate identifiability) — run before any rate-conditional model
Check the market-rate series actually **varied** over 2015–2024 (plot levels, count distinct regimes,
measure its standard deviation and number of moves). If rates were largely administered/flat, the
rate sensitivity is **weakly identified** — flag it now, lean the stress story on **inflation** and on
**scenario assumptions** rather than a fitted rate elasticity, and let this gate decide both the
frozen-OOS placement (§1.1) and how strong the *sensibilité* claim may honestly be.

---

## 4. Phase 2 — Baselines: convention + aggregate ECM (1 week)

The floor to clear and the regulator-recognized reference.

- **(a) Convention baseline.** Core/volatile split via minimum-balance or moving-average floor, then
  assign amortization to each sleeve. This is the recognized reference; the model must **beat it on
  the OOT backtest** to justify itself.
- **(b) Aggregate ECM.** Collapse to aggregate (or per-segment) monthly balance; fit an
  error-correction regression on rate(s) + inflation + seasonality → equilibrium level, reversion
  speed, interpretable rate **elasticity**. This is the "classic OLS" floor and yields an aggregate
  run-off + sensitivity benchmark.
  - **Rigor add:** the ECM is a single time series of ~96 points with serial dependence — its
    coefficient CIs from textbook OLS are **wrong**. Use **HAC (Newey–West)** standard errors and a
    **moving-block bootstrap over months** for the elasticity CI. This is the small-sample,
    serially-dependent analog of the engine's HAC/`n_eff` treatment.
- **Output:** baseline `S(t)` curves + elasticity number with honest (block-bootstrap) intervals.

---

## 5. Phase 3 — Core model: account-level discrete-time hazard (2–3 weeks)

The behavioral run-off and the project backbone.

### 5.1 Structure
- Reshape to the **person-month** survival table: `(account, month, event_flag, features,
  balance_weight)`, respecting left-truncation (rows only from entry month onward) and right-censoring.
- Fit a **discrete-time hazard**: start with **pooled logistic** (interpretable, the documented
  baseline), then **gradient boosting** as the challenger that typically wins on tabular-temporal
  panels.
- **Better-method (v2) inline — competing risks.** If closure / dormancy / large-withdrawal differ in
  balance impact (decided in Phase 0), fit a **multinomial / cause-specific discrete hazard** rather
  than a single binary event; aggregate cause-specific survival into the stock decay. Generic survival
  regression that lumps causes will mis-state run-off if the causes have different balance signatures.
- **Better-method (v2) inline — monotonic constraints.** Constrain the GBM to be monotone in seasoning
  where theory demands it (e.g. hazard non-increasing in tenure for sticky deposits), both for
  governance defensibility and to reduce variance on the short panel.

### 5.2 Weighting, imbalance, calibration
- **Balance-weight the loss** (`CTRVL KDA`): the aggregate is whale-dominated, so the economically
  relevant loss weights each person-month by its balance. This is the run-off analog of the engine's
  uniqueness weights — same machinery (sample weights into the loss), different (economic) motivation.
  Optionally combine with inverse-censoring weights (IPCW) for unbiased survival estimation.
- Handle rare-event imbalance (low monthly hazard) via the weighting and threshold-free metrics; do
  **not** resample in a way that breaks the time structure.
- **Calibrate the hazards** — reliability diagram + **PIT** check (the buy-side calibration instinct
  ports directly; cf. `project_pit_calibration_2026_06_11`). A run-off model lives or dies on
  calibration, not discrimination: a well-calibrated mediocre-AUC hazard gives a correct `S(t)`; a
  sharp-but-miscalibrated one does not.

### 5.3 Validation (strict, per §1)
- **Primary:** anchored walk-forward; **secondary:** 28-path CPCV with embargo = `H`. No leakage:
  fitted statistics fold-local, macro PIT-aligned.
- Aggregate per-account survival × balance → portfolio `S(t) = Σᵢ Sᵢ(t)·Bᵢ(t) / Σᵢ Bᵢ(0)` (with the
  balance-erosion term `rᵢ(t)` if modeled per Phase 0).
- Feed rate/inflation scenarios through each `Sᵢ` → **stressed `S(t)`**.
- **Report intervals, not point curves** — and the *right* intervals (§6).
- **SHAP** for interpretability and the model-validation file.
- **Output:** base + stressed run-off with honest intervals + SHAP attributions.

### → Gate B (is deep learning worth it?)
If Phase 3 already beats the convention and validates cleanly out-of-time, the deep/GenAI layer is a
genuine **option**, not a requirement. Decide here whether Phase 4 buys enough lift to justify its
governance cost.

---

## 6. Uncertainty quantification — the honest-CI core (applies across Phases 2–5)

Two independent error sources; **report both, headline the binding one.**

- **(a) Cross-sectional (account) uncertainty → account-cluster bootstrap.** Resample *accounts* with
  replacement, carrying **all** of each account's month-rows (block = the account), recompute `S(t)`.
  With many accounts this interval is **narrow**.
- **(b) Temporal / common-shock uncertainty → stationary/moving block bootstrap over months.** Block
  length ≈ macro autocorrelation length (or = `H`); resample contiguous month-blocks and refit. With
  ~10 years and few cycles this interval is **wide** and is the **true binding constraint**.

**The honesty rule (direct port of `feedback_report_ci_not_pvalues`):** accounts share macro shocks,
so they are **not** independent. An account-only bootstrap understates total uncertainty exactly as
the per-asset-independent bootstrap did (it turned a true `p≈0.34` into a spurious `p<0.001`). The
**headline fan-chart and any p-value must come from the time-block bootstrap** (or a hierarchical
bootstrap: resample months → then accounts within), not the account bootstrap. Show the account
bootstrap only as the (small) idiosyncratic component, clearly labelled.

**Model-side reinforcement — frailty / latent common factor.** Add a shared random effect (account
frailty) or a **latent monthly factor** so the model itself attributes correlated attrition to a
common shock. Without it, scenario fan-charts are too tight (idiosyncratic-only) — the same under-
dispersion failure seen in PIT calibration. The latent factor also makes the stressed-`S(t)` fan
honest.

### 6.1 Conformal prediction for distribution-free stress intervals
The block bootstrap gives **parameter/sampling** uncertainty; conformal gives **distribution-free
finite-sample coverage** on the prediction itself — and the two are complementary, not substitutes.
Port the engine's conformal layer (Part VI / Ch37, `project_pit_calibration`):

- **Object to conformalize.** Define a nonconformity score per evaluated unit — per
  `(segment, horizon)` realized-vs-predicted run-off residual `|S_real(t) − Ŝ(t)|` (or the
  per-month hazard residual). Calibrate on the most recent dev block; apply forward.
- **Split conformal** gives marginal coverage **if exchangeable** — which time series violate. So use
  **weighted conformal** (Tibshirani et al., reweight calibration points toward the test regime) and/or
  **adaptive conformal (ACI / Gibbs–Candès)** that updates the quantile online as coverage drifts.
  This is the run-off analog of "weighted conformal under TS violation + per-CPCV-path quantile
  averaging."
- **CQR** (conformalized quantile regression) sits naturally on a quantile/survival head: fit
  quantile hazards, then conformalize the residual → bands with coverage guarantee that *adapt* to
  local difficulty (wider in stressed months).
- **For stress specifically:** wrap each stressed `S(t)` scenario in a conformal band so the IRRBB
  deliverable carries a **coverage-guaranteed** interval, then **widen by the time-block bootstrap**
  for macro-parameter uncertainty. Report the *combined* band. The headline stress claim is then:
  "under a +200bp shock, run-off at 12m is `Ŝ ± [conformal ⊕ block-bootstrap]` with ~90% coverage."
- **Honest caveat to state:** conformal coverage is only as exchangeable-valid as the calibration
  regime resembles the stress regime. Under a genuine out-of-distribution rate shock (which Algeria
  may never have realized — Gate A), conformal coverage is **not guaranteed**; the band is then a
  *scenario* band, not a coverage-certified one. Say which it is.

---

## 7. Phase 4 — GenAI / deep challenger (optional, 2–4 weeks)

Kept only if it beats Phase 3 on the **same** OOT protocol. Two routes, **not both**:

- **(A) Per-account temporal model.** One **global** shared TCN or temporal transformer with account
  as the batch dimension (the engine's "global model, asset = batch" pattern). **Two-clock encoding**:
  calendar positional encoding + age/seasoning positional encoding (the engine's calendar+temporal
  embeddings, adapted). NaN masking (masks-not-drop). A proper **discrete-time survival head**
  (DeepHit-style cause-specific or a hazard logit per month), **balance-weighted**. Optionally
  **depth-2/3 signature features** of each account's `(balance, rate, inflation)` path as inputs
  (ports Part II/Ch22 directly), and the **latent monthly factor** from §6 for residual common shocks.
  - v2 note: prefer a *proper survival head + monotone/competing-risk structure* over a generic
    regression head — a behavioral run-off needs calibrated hazards, not just point forecasts.
- **(B) Time-series foundation model, zero-shot** on the aggregate/segment balance series as the
  literal "GenAI" garnish, benchmarked vs the ECM. Honest framing: this is a benchmark/garnish, not
  the engine.

Benchmark honestly against Phases 2–3 on identical OOT splits, same embargo, same balance-weighted
metric. **Deflate** for the number of architectures/event-definitions tried (the engine's
`eval_path_0` cherry-pick and deflated-Sharpe lessons: don't report the best of N as if it were the
only one). **Output:** challenger results + keep/drop recommendation.

---

## 8. Phase 5 — Sensitivity, stress & validation packaging (1–1.5 weeks)

- **Sensibilité.** Re-run `S(t)` under rate shocks (±200bp parallel, steepener/flattener) and inflation
  scenarios → effective **duration / key-rate sensitivity / WAL** of the deposit. Carry the Gate-A
  caveat through: if rate variation was weak, present rate sensitivity as **scenario-driven, not
  empirically identified**, and lead with inflation.
- **Robustness battery (ports the engine's model-free safeguards):**
  - stability of `S(t)` across time windows and across segments;
  - sensitivity to the **event definition** (re-estimate under the alternative closure/dormancy/floor
    definitions — this is a known degree of freedom, so quantify it);
  - **realized-vs-predicted** aggregate `S(t)` on the frozen OOT window, with **interval coverage**
    (does the 90% time-block fan cover realized?) as the headline calibration metric;
  - convention-vs-ECM-vs-model on identical OOT origins.
- **Metrics summary.** Discrimination (time-dependent AUC / Uno's C, IPCW) is secondary; **calibration
  (reliability + PIT) and aggregate-`S(t)` error + coverage are primary**, plus predicted-vs-realized
  WAL/duration.
- **Model-validation file.** Assumptions, data lineage, PIT/no-look-ahead evidence, baseline-vs-
  challenger table, calibration evidence, SHAP interpretability, and **honest limitations** —
  especially the Gate-A rate identifiability and the horizon-vs-history extrapolation limit.
- **Output:** final run-off curves (base + stressed), the run-off **rule**, the sensitivity table, and
  the validation note.

---

## 8b. Explainability — model *and* data (governance-grade)

A model-validation / IRRBB context demands you can explain **both what the data says** and **why the
model decides**. Build explainability in three tiers, anchored on interpretable baselines so the
"AI" never has to be taken on faith.

**Tier 1 — interpretable anchors (always present).**
- The **ECM elasticity, equilibrium level, reversion speed** are inherently readable economic
  numbers — they are the explanation the manager already named.
- The **pooled-logistic hazard** gives signed, magnitudable coefficients (seasoning, balance bucket,
  calendar) as a transparent reference the challenger is benchmarked against.
- **Monotonic constraints** on the GBM (hazard monotone in seasoning, etc.) make the model's
  behavior explainable *by construction* and defensible to validation.

**Tier 2 — data & feature explanation (model-agnostic).**
- **SHAP / TreeSHAP** global (mean |φ|) for what drives attrition, and **local** SHAP for "why this
  cohort/segment runs off faster." Compute **out-of-bag/out-of-time** to avoid the in-bag optimism
  bias (Ch41 lesson).
- **Partial-dependence / ALE curves** for seasoning, balance bucket, rate, inflation — these *are* the
  behavioral story (e.g. "hazard drops sharply after the first 6 months, then flattens").
- **Balance-weighted importance:** report importance weighted by `CTRVL KDA`, because what explains the
  *aggregate* run-off (whale-dominated) differs from what explains a random account. State both.

**Tier 3 — deep-model introspection (only if Phase 4 is kept).**
- For the **temporal transformer**, **attention maps** show *which months and which clock* the model
  attends to — the **two-clock** design lets you separate **calendar attention (seasonality)** from
  **age/seasoning attention (tenure effects)**, which is a genuinely useful behavioral read.
- **Caveat (state it in the validation note):** attention weights are **not** a faithful explanation
  on their own — high attention ≠ high causal contribution. Corroborate any attention story with
  **SHAP on the deep model**, **ablation** (remove the rate channel, measure `S(t)` shift), and
  **integrated gradients / occlusion** before claiming a mechanism. Attention is a hypothesis
  generator, not the evidence.
- For the **latent monthly factor** (§6), plot its trajectory against macro — if it tracks the rate/
  inflation episode, that *is* the explainable common-shock channel; if it doesn't, it's absorbing
  unexplained correlated attrition, which you must disclose.

**Deliverable:** a short interpretability appendix in the validation note — Tier 1 numbers, Tier 2
SHAP/PDP plots, and (if applicable) Tier 3 attention + ablation, each labelled by how much evidential
weight it carries.

---

## 9. The spine, in one breath

Build the PIT-correct account-month panel → **check rates actually moved (Gate A)** → set convention +
HAC/block-bootstrapped ECM as the floor → account-level **balance-weighted, calibrated, competing-risk
discrete hazard** with a latent common factor, validated **out-of-time first** and CPCV-second with
embargo = `H`, aggregated to `S(t)` with **time-block (not account-only) intervals** → deep/GenAI only
if it earns its keep on the same protocol → stress + validate with coverage as the headline.
**Phases 0–3 already constitute a complete, defensible IRRBB deliverable; Phase 4 is the upside.**

---

## 10. Rigor cheat-sheet — what ported, what changed

| Engine method | Run-off adaptation |
|---|---|
| CPCV primary (28 paths) | **Walk-forward primary; CPCV secondary** (forward extrapolation must be proven out-of-time). 8 super-blocks → 28 paths for variance only. |
| Embargo `E = max horizon` | Embargo/purge `= H` (run-off horizon backtested); 1-month for pure hazard. |
| Uniqueness sample weights | **Balance weights** (`CTRVL KDA`) + optional IPCW. |
| Cross-asset-honest block bootstrap (block = h) | **Time-block bootstrap over months** as the *headline* CI; account bootstrap is the small idiosyncratic part only. |
| Conformal (weighted/adaptive/CQR, Ch37) | **Distribution-free coverage band** on `S(t)`/hazard residuals for stress; combined with block bootstrap; flagged as scenario-band (not certified) under OOD shocks. |
| SHAP / interpretability | **3-tier explainability**: ECM/logistic anchors → OOB SHAP + PDP/ALE (balance-weighted) → transformer attention + ablation (attention = hypothesis, not proof). |
| PIT calibration | Hazard reliability + PIT; **calibration is the primary metric**, not discrimination. |
| Step-3 global-fit look-ahead bug | `fit_end` required; buckets/scalers/ECM coeffs/frailty fold-local; macro PIT-aligned with publication lag. |
| IC-of-mean not mean-of-\|IC\| | Report aggregate `S(t)` error + **coverage**, not the best-of-N fold; deflate HP/event-definition search. |
| Frozen test sanctity | Last 18–24 months frozen OOT, touched once; placement decided by Gate A macro inventory. |
| Latent factors / frailty | Latent monthly factor / account frailty so fan-charts aren't under-dispersed. |

---

## 11. Open decisions to confirm before coding
1. **Run-off quantity:** attrition-only, or attrition × balance-erosion `rᵢ(t)`? (§0, Phase 0)
2. **Event definition:** single event vs competing risks (closure/dormancy/floor)? (§3.1)
3. **Frozen-OOS length & placement:** default 24 months (2023–2024) — confirm after the Gate-A macro
   inventory (§1.1).
4. **Max evaluated horizon `H`:** sets the embargo and the limit of empirical (vs extrapolated)
   validation (§1.3, §1.1).
5. **Deep layer route if Gate B passes:** (A) per-account temporal model vs (B) foundation-model
   garnish (§7).
