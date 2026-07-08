# Modèle d'écoulement des Dépôts à Vue (DAV) --- Plan v2 (pure-stdlib / no-transformer)

**Deliverable (unchanged):** a behavioral run-off curve `S(t)` for non-maturing demand deposits (DAV), plus its rate/inflation **sensibilité**, packaged for IRRBB (and optionally FTP). **Data:** client-level (account-level) monthly panel, **2015-01 → 2024-12 (120 months)**, DZD. **External joins:** market rate(s) + inflation (monthly), plus regime-defining macro proxies for §6.7 (full data requirements + stdlib download recipe in §1b).

**What changed vs PLAN.md.** The deployment environment is a locked-down bank PC: **Python standard library only** --- no `pip`, no `numpy`/`pandas`/`scikit-learn`, no `R`/`Rscript`. Therefore the optional deep/GenAI challenger (Phase 4 transformer with continuous/two-clock positional encoding, attention, DeepHit head) is **removed**, and its role --- supplying nonlinearity --- is taken over by **truncated path signatures + a penalized-linear/logistic readout (Elastic Net, optionally group-Lasso)**, all hand-rolled in stdlib. The rigor protocol is **unchanged**; only the function approximator changes. This document is self-contained: where it repeats PLAN.md it is for completeness, where it diverges the divergence is justified.

------------------------------------------------------------------------

## 0. Two facts that frame everything (carried from PLAN.md, plus one new)

1.  **Forward extrapolation on 120 months.** A run-off model predicts the decay of *today's* stock. So **out-of-time (walk-forward) is the headline protocol; CPCV is secondary** (variance/robustness only).
2.  **Binding uncertainty is temporal, not cross-sectional.** Many accounts but \~10 years and few rate/inflation cycles. The honest headline interval comes from a **time-block / common-factor bootstrap**, never an account-only bootstrap (accounts share macro shocks).
3.  **(New) Capacity is bounded by data, not by the model.** 120 months and few macro cycles cannot fund a learned high-capacity representation; a transformer would overfit out-of-time here. A **fixed signature basis + regularized readout is the correctly-sized model**, not a downgrade. The bottleneck is data and signal --- the same lesson as the trading engine's overparam rounds. This is *why* dropping the transformer costs little real predictive value (see §11 for the honest accounting).

------------------------------------------------------------------------

## 1. The environment contract (new, binding)

-   **Stdlib only.** Allowed: `csv`, `math`, `json`, `glob`, `os`, `re`, `random`, `itertools`, `statistics`, `bisect`, `array`, plus `urllib.request`, `ssl`, `gzip`, `zipfile`, `sqlite3`, `datetime`, `decimal`, `html.parser`, `xml.etree.ElementTree` for **fetching and parsing external series** (§1b --- download + parse are all stdlib, no `requests`/`pandas`/`beautifulsoup`). Not available: `numpy`, `pandas`, `scikit-learn`, `iisignature`, `esig`, `matplotlib`, `R`/`glmnet`/`survival`.
-   **Data residency.** Even SHA-256-anonymized data is unlikely to be cleared to leave the bank. **Resolution: move the code to the data, not the data to the code.** Raw panel stays inside the bank; all modeling runs on the bank PC; only **model outputs** leave (coefficients, `S(t)` curves, elasticities, calibration tables) --- these are aggregates, not client records, and clear governance. The anonymization script is retained only as an off-site fallback.
-   **No plotting.** Diagnostics (reliability/PIT, fan charts, sig path) are **written to CSV** and plotted later off-PC, or rendered as ASCII. Nothing in the pipeline requires a plotting library.
-   **Performance.** Pure-Python linear algebra and bootstraps are slow but tractable at this scale (the ECM is \~96 points; the hazard table is large but the per-coordinate updates are cheap). Use incremental signature updates (Chen) and cache fold-local fits to keep bootstraps affordable.

------------------------------------------------------------------------

## 1b. Data requirements --- what must exist, and how to get it (stdlib download + preproc)

Three data layers. Layer 0 is bank-internal and given; Layers 1--2 are external and **fetchable with stdlib alone** (`urllib.request` to download, `csv`/`json`/`xml.etree`/`html.parser`/`zipfile` to parse). The **work PC has outbound internet to World Bank, Bank of Algeria, ONS, and IMF**, so the fetch runs directly on it --- no carry-in step; write each series to plain CSV. No `requests`/`pandas`. **No EOD feed is available on the work PC** --- anything previously sourced from EOD is re-sourced below. Every external row carries an **`available_date`** (publication date) so the as-of join in §4b is honest.

**Layer 0 --- the DAV panel (internal, mandatory, never leaves the bank).** Account-month dumps, 2015-01 → 2024-12, one row per `(account, month)`. Required fields: - account id, stable across yearly dumps (the robust reader locates it by header name, §4); - balance `CTRVL KDA` (currency-consistent), `CODE TYPE COMPTE`, currency; - `DATE OUVERTURE` (seasoning + left-truncation); - enough contiguous monthly snapshots to reconstruct the closure/dormancy/floor **event** (§3, §5.1). Minimum to function: ≥ \~5 contiguous years of monthly snapshots for one homogeneous DZD segment; ideally the full 120. **Without Layer 0 there is no project** --- it is the only source of behavioral signal. Integrity gate: summed `CTRVL KDA` per month must reconcile to a known book total (§4 QA).

**Layer 1 --- macro covariates for the hazard & ECM (external, mandatory).** Monthly, each row stamped with `available_date` for the PIT join. Best source per series (verified 2026-06):

| Series                                                   | Role                                     | Primary source (stdlib fetch + parse)                                                                                                                                                                                                                                                                       | Caveat                                                                                                                                                                                      |
|----------------------------------------------------------|------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| **CPI / inflation --- MONTHLY (the series we model on)** | the likely *binding* driver (Gate A)     | **ONS Algeria** monthly IPC is the authoritative origin (Excel/PDF bulletins → `xlsx` is a zip of XML, parse via `zipfile`+`xml.etree`; PDF → manual); **IMF IFS `PCPI_IX`** (monthly) is the machine-readable mirror via the new SDMX 3.0 API `https://api.imf.org/external/sdmx/3.0` → `json`/`xml.etree` | use ONS for `available_date`/revision truth, IMF for scripting; legacy `dataservices.imf.org/REST/SDMX_JSON.svc` is **retired**; confirm the SDMX 3.0 dataflow id on the structure endpoint |
| **CPI --- annual (cross-check only)**                    | sanity anchor, never the modeling series | **World Bank** `api.worldbank.org/v2/country/DZA/indicator/FP.CPI.TOTL?format=json` → `json`                                                                                                                                                                                                                | annual only (✅ verified live, clean JSON)                                                                                                                                                  |
| **Policy / rediscount / interbank rate, monthly**        | the rate-*sensibilité* driver            | **Bank of Algeria** statistics pages (rediscount, interbank, govt-securities) → HTML via `html.parser`; IMF IFS money-market rate as machine-readable alternative                                                                                                                                           | BoA TLS chain is broken → pass a relaxed `ssl` context to `urllib` (`check_hostname=False`); some history is PDF-only → manual rows (cheap: the rate rarely moves --- *that is* Gate A)     |
| **Official DZD FX rate, monthly**                        | pass-through / FX context                | **Bank of Algeria** (daily/annual, since 1994, HTML) or World Bank `PA.NUS.FCRF` (annual)                                                                                                                                                                                                                   | BoA cert workaround as above                                                                                                                                                                |
| reference deposit rate (optional)                        | deposit-beta                             | BoA / IMF if published                                                                                                                                                                                                                                                                                      | often unavailable → fold into the policy-rate pass-through                                                                                                                                  |

Fetch mechanics (stdlib): `urllib.request.urlopen` (+ `ssl` context for `.dz`), parse with `json` / `xml.etree` / `html.parser`, write one tidy CSV per series --- `ref_month, value, available_date`. Cache raw payloads to disk (`gzip`) so a flaky endpoint doesn't block a re-run. Manual CSV export (ONS bulletins, BoA PDFs) is the always-available fallback; the rest of the pipeline is identical.

**Layer 2 --- regime-defining series (external, needed only for the §6.7 regime layer).** Monthly proxies that label the structural state; monthly (or quarterly forward-filled) is enough to *date* regimes.

| Series                                | Regime signal                                       | Source (stdlib)                                                                                                                                                                                              | Caveat                                                                                                 |
|---------------------------------------|-----------------------------------------------------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| **oil / hydrocarbon price (monthly)** | hydrocarbon-liquidity vs stagnant                   | **IMF Primary Commodity Prices** (avg crude spot `POILAPSP`, monthly) via the SDMX 3.0 API → `json`; or **World Bank Pink Sheet** monthly crude (`CMO-Historical-Data-Monthly.xlsx`) → `zipfile`+`xml.etree` | no EOD on the work PC --- both are stdlib-reachable; confirm IMF dataflow id on the structure endpoint |
| **parallel-vs-official FX premium**   | currency-stress                                     | **no official endpoint** --- curated from market-watch sites + dated press on devaluations/controls                                                                                                          | the one genuinely hand-built series; document provenance and `available_date` honestly                 |
| **discrete event flag**               | devaluation / capital-control / policy-reset months | hand-coded calendar from BoA/press                                                                                                                                                                           | this *is* the exogenous regime calendar Role-2 needs (§6.7)                                            |

The event flag and the FX-premium series are **authored, not downloaded** --- that is acceptable (and unavoidable) here, but it makes the §6.7 Role-2 generator `Q` assumption-driven by construction; disclose it as such.

### → Status: external data layer BUILT & VALIDATED (2026-06, `src/data/`)

The exploratory caveats above are superseded by verified endpoints/keys (pure stdlib, `python run_all.py`):

| Series                           | Verified source + key                                                                                                                                |
|----------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| monthly CPI (modeling inflation) | IMF SDMX 3.0 `IMF.STA/CPI/5.0.0`, key `DZA.CPI._T.IX.M` (legacy `dataservices.imf.org` is dead; needs SDMX-JSON `Accept` header). 120/120 in window. |
| monthly rates                    | IMF `IMF.STA/MFS_IR/9.0.0`, keys `DZA.{DISR,MMRT,GSTBILY}_RT_PT_A_PT.M`                                                                              |
| monthly oil (Brent)              | World Bank Pink Sheet xlsx --- **IMF has no commodity-price dataflow** (`POILAPSP` not available); parsed with a stdlib `zipfile`+`xml.etree` reader |
| monthly official FX              | IMF `IMF.STA/ER/4.0.1`, keys `DZA.XDC_{USD,EUR}.PA_RT.M`                                                                                             |
| annual CPI cross-check           | World Bank `FP.CPI.TOTL` (JSON)                                                                                                                      |
| seasonal                         | computed from a tabular Hijri calendar (Ramadan/Eid/vacation), PIT-safe                                                                              |
| parallel-FX premium              | no public series (WPCPER does not cover Algeria) → hand-authored template                                                                            |

**Gate A is RESOLVED on real data:** the **policy/discount rate is administered** (3 distinct values, a 92-month flat run, constant through 2023--2024) → rate-sensibilité is **not** identifiable from it; use the **money-market rate** (118 distinct, std 0.91, varies in the OOS). Inflation (0.1--10.8%) is a solid driver. The frozen OOS (2023--2024) is **stagnant-regime only** (dev holds the 2020--21 currency-stress), so regime-stress run-off is fit-only / **not OOS-validated** --- confirming the §6.7 Role-2 "scenario, not coverage-certified" caveat from data. Output: `src/data/_out/macro_panel_{wide,pit}.csv` + `gate_a.py`.

**What "enough data" honestly means.** The binding scarcity is **macro cycles, not accounts** (§0.2): 120 months buys few rate/inflation episodes and \~2--3 regime episodes. That is *why* Gate A may downgrade the rate claim, and *why* the regime generator `Q` is fixed exogenously rather than freely estimated (§6.7). More accounts do not fix this; only more *time* / more *cycles* would.

------------------------------------------------------------------------

## 2. Validation budget --- how to split 120 months (unchanged from PLAN.md)

### 2.1 Frozen out-of-time (OOS) test

Reserve the **last 18--24 months as a frozen OOT test; default = 24 months (2023-01 → 2024-12)**, touched **once**, at Phase 5. Development = **2015-01 → 2022-12 (96 months)**. - Data-contingent caveat: inventory *when* rate/inflation actually varied (**Gate A**) **before** fixing the split. Prefer at least one macro-varying episode in *both* dev and test; if only one exists, keep it in dev and **declare the rate sensibilité OOS-unvalidated**. Do not silently choose. - Horizon-vs-history limit: if regulatory run-off horizons exceed \~24 months, 10 years cannot validate them OOS --- validate the hazard and ≤24-month aggregate empirically; beyond that label the curve a **structural extrapolation** with sensitivity to the extrapolation assumption.

### 2.2 Development window (96 months): two nested protocols

-   **Primary --- anchored walk-forward (expanding origin).** Train `[2015-01, o]`, score `(o, o+K]`, step `o` forward. Selects depth `N`, penalties `(λ, α)`, and the model class. Report **mean and dispersion across origins**, never a single fold.
-   **Secondary --- CPCV, adapted.** 96 dev months → **8 super-blocks ≈12 months → N=8, k=2 → 28 paths**, with **purge + embargo** between train/test super-blocks. Grouping unit = calendar month. Report fold dispersion as robustness only.

### 2.3 Purge & embargo sizing

-   Single-month hazard label overlaps 1 month; the aggregated `S(t)` backtest over horizon `H` overlaps `H`. **Embargo/purge = H** on both sides of every test block for `S(t)` calibration; 1 month suffices for pure hazard discrimination. **Embargo is a time-distance predicate on τ, not a row count** (§4b): drop rows whose label window `[τ, τ+H]` overlaps a test block, measured in real time.

### 2.4 Optional unseen-account split

Time split lets an account appear in train (early) and test (late) --- desired. To also claim generalization to **newly opened accounts**, additionally hold out a random sample of accounts entirely and report a "new-client" score. Secondary.

------------------------------------------------------------------------

## 3. Phase 0 --- Scope & data contract (½ week)

-   Pin with the desk: segment/currency in scope, downstream consumer (IRRBB sensitivity / FTP / both), the *référentiel* meaning of `CODE TYPE COMPTE` (B35M/T cut). Confirm grain = **account-month**.
-   **Define the event precisely** (closure / balance below floor / dormant N months) and decide **single event vs competing risks** here (§5.1).
-   **Define the run-off quantity precisely --- the "entire client interaction" decision.** Run-off has two components: `Bᵢ(t) = Bᵢ(0)·Aᵢ(t)·rᵢ(t)` = (alive indicator) × (balance kept \| alive).
    -   `Aᵢ(t)` = attrition → the **hazard model**.
    -   `rᵢ(t)` = balance erosion on surviving accounts → a **second pooled model**, or freeze balance at `t₀` and absorb erosion into the hazard. **For DAV, erosion is usually material** (clients keep the account but draw down the balance), so the default is **model both** unless the desk scopes it out. Decide now --- it sets the panel columns.
-   Fix success metric now (§6): OOT realized-vs-predicted aggregate `S(t)` + calibration + interval coverage, benchmarked vs convention and ECM.
-   **Output:** one-page scope note (event definition, attrition-only vs attrition×erosion, frozen-OOS pending Gate A).

------------------------------------------------------------------------

## 4. Phase 1 --- Data engineering: build the panel (1--1.5 weeks, pure stdlib)

Goal: a clean account-month panel with **survival bookkeeping** and **PIT-correct** macro joins.

-   **Parse the dumps with `csv` + the robust reader already built** (auto-detects UTF-16/UTF-8/cp1252, skips the `Titre du rapport` title row, locates the ID column by header name so year-to-year column drift is harmless). One row per `(account, month)` with balance (`CTRVL KDA` for currency consistency), type, `DATE OUVERTURE`, currency.
-   Filter to a homogeneous population (DZD, target segment).
-   **Survival bookkeeping:** event flag; **right-censoring** for accounts alive at 2024-12; **left-truncation** for accounts opened before 2015 (enter at 2015-01 with seasoning \> 0 --- condition the likelihood on survival-to-entry; do **not** treat entry as origin).
-   **Features (all causal, ≤ t):** seasoning (months since `DATE OUVERTURE`), age cohort, lagged balances, balance buckets, calendar month/year, intra-account balance trend/volatility. These feed both the raw feature set and the **path channels** used for signatures (§6.2).
-   **Event-time / point-in-time indexing --- the data model (see §4b).** Each row is one `(account, decision-time τ)` observation; every feature is the value **known at τ** (latest external obs with `τ_available ≤ τ`). Indexing is by **event time, not a global clock**. This is a data-engineering property --- the model is time-agnostic --- so it must be baked into the panel builder here, not bolted on later.
-   **Macro join --- PIT as-of, on `τ_available`.** Rates/inflation are published with a lag; join **as-of** the information set at decision time τ (latest value with `τ_available ≤ τ`), never the revised vintage aligned to its reference month. Stdlib mechanics: sort each series by `τ_available`, `bisect` for the latest ≤ τ (no `merge_asof` needed). Document the assumed publication lag.
-   **Elapsed time as explicit feature columns.** Once flattened to a table, the model sees rows independently --- it has no idea two rows are 1 vs 6 months apart. So the time structure must enter as columns: `Δτ since last event`, seasoning (tenure), calendar month/year. (This is the un-glamorous truth behind the signature "time channel": in a plain table you add the columns yourself.)
-   **No-look-ahead on fitted statistics.** Balance-bucket thresholds, standardization scalers, ECM coefficients, signature standardizers, any quantile cut --- **fit on the training window only**, applied forward. Make `fit_end` a **required argument**, not a default (this is the exact engine Step-3 bug).
-   **QA:** missingness, gaps, outliers, currency consistency; **reconcile summed `CTRVL KDA` to a known aggregate total per month** (the single most important integrity check).
-   **Output:** modeling-ready panel (account-month table) + data-quality memo.

### → Gate A (rate identifiability) --- run before any rate-conditional model

Check the rate series actually **varied** 2015--2024 (levels, distinct regimes, std, count of moves). If largely administered/flat, rate sensitivity is **weakly identified** --- flag now, lean the stress story on **inflation + scenario assumptions** rather than a fitted rate elasticity, and let this gate decide both the frozen-OOS placement (§2.1) and how strong the *sensibilité* claim may honestly be.

------------------------------------------------------------------------

## 4b. Event-time / point-in-time indexing (cross-cutting; the actual design)

**The model is time-agnostic.** Ridge/Lasso/logistic see a flat table --- rows × feature columns + a label. "Event-time vs global clock" is a **data-engineering decision about how rows are built and features aligned**, not a model capability. The pipeline handles event-time iff the panel builder and the splitter handle it. Three obligations + one design fork.

**Three obligations (all stdlib):** 1. **PIT as-of feature joins.** Row keyed `(account, τ)`; each feature = latest external obs with `τ_available ≤ τ`. Backward join on *availability time*. `sort + bisect.bisect_right`. 2. **Elapsed time as explicit columns.** `Δτ since last event`, seasoning, calendar terms --- because a flattened table has no implicit clock. Irregular spacing enters as data, not as model structure. 3. **Folds & embargo as time-distance predicates on τ, not integer slices.** Walk-forward = "train on `τ ≤ cutoff`"; embargo = "drop rows whose label window `[τ, τ+H]` overlaps the test window" measured in **real time**, not row count. This is the one place event-time changes the splitter code.

**The design fork --- irregular inputs, but what grid for the output?** - **(A) Decouple (recommended, default).** Features are event-time/PIT (irregular, as-of τ), but the **hazard decision grid is regular monthly**, so `S(t) = Π(1−ĥ)` stays coherent and the regulatory horizons (1m/3m/12m) are legible. Inputs irregular, output regular. - **(B) Fully continuous-time hazard** (Cox-style / interval-length exposure weighting). More correct if events are genuinely irregular *and* irregular-horizon output is required, but it needs exposure normalization / partial likelihood --- more machinery and harder to defend in pure stdlib. Not the default.

**Net:** the model layer needs nothing special; event-time handling lives entirely in (i) the panel builder's as-of join + elapsed-time columns and (ii) the splitter's time-based predicates. Bake both in from row one.

------------------------------------------------------------------------

## 5. Phase 2 --- Baselines: convention + penalized ECM (1 week, pure stdlib)

The floor to clear and the regulator-recognized reference.

-   **(a) Convention baseline.** Core/volatile split via minimum-balance or moving-average floor, assign amortization to each sleeve. The model must **beat it on the OOT backtest** to justify itself.
-   **(b) Penalized ECM (replaces plain OLS).** Collapse to aggregate (or per-segment) monthly balance; fit an error-correction regression on rate(s) + inflation + seasonality → equilibrium level, reversion speed, interpretable rate **elasticity**. Use the stdlib **Ridge/Elastic-Net linear** estimator (`ridge_lasso.py`) for stability on the short series; pure OLS is the λ=0 special case.
    -   **Rigor add:** \~96 serially-dependent points → textbook OLS CIs are wrong. Use **HAC (Newey--West)** standard errors (closed-form, stdlib) and a **moving-block bootstrap over months** for the elasticity CI.
-   **Output:** baseline `S(t)` curves + elasticity with honest (block-bootstrap) intervals.

------------------------------------------------------------------------

## 6. Phase 3 --- Core model: pooled penalized-logistic hazard on signatures (3--4 weeks, pure stdlib)

The behavioral run-off and the project backbone. **This phase absorbs the nonlinearity role that the deleted transformer would have played**, via signatures.

### 6.1 Unit of analysis --- pooled, not per-client, not raw-aggregate

-   **One pooled model** over **person-month rows** `(account i, month t, event?, features, balance_weight)`, respecting left-truncation and right-censoring. Shared parameters; per-account hazards arise because each account feeds its own features.
-   **Not per-client** (rare events, short histories → overfit) and **not a raw weighted average of DAV** (that is the convention/ECM floor; it discards heterogeneity, segment stress, seasoning/cohort).
-   **Aggregate to the book by balance:** `S(t) = Σᵢ Sᵢ(t)·Bᵢ(0) / Σᵢ Bᵢ(0)`, with the balance-erosion term `rᵢ(t)` if modeled (Phase 0). This is the "weighted average" --- applied to **modeled survival**, not raw balances.

### 6.2 Nonlinearity via truncated signatures --- what they are, honestly

**A signature is a feature map, nothing more:** a fixed, deterministic transform from a path to a vector of iterated integrals. It is *not* learned, *not* adaptive, *not* a model. We compute it once and feed it to the linear readout (§6.3). It is the strongest nonlinearity buildable with zero dependencies on thin data --- chosen for its *rigidity* (un-overfittable, regularizable), not because it beats a transformer (§11).

-   **Channels per account path:** `log(balance)`, rate, inflation (+ optionally seasoning).
-   **Path transforms before the signature (these are mandatory, not optional polish).** A raw signature is reparametrization-invariant --- it *throws away* level, absolute time, and quadratic variation, which is exactly what we need. We deliberately break those invariances: **time-augmentation** (append `τ` --- this *is* the continuous-PE equivalent, same information as the transformer's positional encoding, just in a feature column; append seasoning for the tenure clock), **basepoint**, and **lead-lag** (so depth-2 terms capture quadratic variation / volatility).
-   **Causal/PIT (event-time, per §4b):** at decision time τ, compute the signature of the path on `[entry, τ]` over the **event-time** points (irregular spacing handled by the appended `τ` channel) → predicts hazard on the next monthly interval. Strictly uses obs with `τ_available ≤ τ`. Update **incrementally via Chen's identity** (signatures concatenate) to avoid recomputing per step.
-   **Truncation depth `N` = the nonlinearity knob, with a hard cutoff** --- everything above depth N is discarded permanently; no tuning recovers it (default sweep N∈{2,3}). For d channels the signature has `(d^(N+1)−1)/(d−1)` terms; with augmentation d≈5--6, depth 3 → \~150--250 features. The dimensionality blow-up is *why* the regularized readout (§6.3) is mandatory, not optional. Hand-rolled via iterated sums (\~50 lines, no `iisignature`).
-   **Standardize** signature features fold-local (`fit_end`).

### 6.3 The readout --- Elastic Net logistic (not plain Lasso)

-   The discrete-time hazard is a **pooled logistic regression**: `P(event in (t,t+1] | alive)`.
-   Readout on signature features = **Elastic Net (L1 + L2)**, because signature terms are **collinear** (shuffle-product redundancy): L2 stabilizes the collinearity, L1 gives sparsity/selection. Pure Lasso is unstable on collinear groups; pure Ridge keeps everything. CV the mix `α` and strength `λ`.
-   **Group-Lasso refinement (interpretability upgrade, ship second):** group signature terms by **channel** (all terms involving `rate`) or by **depth level**; block coordinate descent drops/keeps whole channels/levels → built-in channel ablation and a clean governance story ("rate channel contributes nothing → sensibilité is inflation-driven", serving Gate A).
-   **Competing risks:** if closure/dormancy/large-withdrawal differ in balance impact (Phase 0), use a **multinomial (softmax) penalized logistic** (cause-specific hazards), aggregated into stock decay.
-   **Monotonicity:** where theory demands (hazard non-increasing in tenure), impose sign/shape on the depth-1 seasoning term, or post-hoc isotonic adjust; document.

### 6.4 Weighting, imbalance, calibration

-   **Balance-weight the loss** (`CTRVL KDA`): the aggregate is whale-dominated, so weight each person-month by balance (run-off analog of uniqueness weights). Optionally combine with inverse-censoring weights (IPCW).
-   Handle rare-event imbalance via the weighting and threshold-free metrics; do **not** resample in a way that breaks the time structure.
-   **Calibrate the hazards** --- reliability table + **PIT** check, written to CSV. A run-off model lives or dies on **calibration, not discrimination**: a well-calibrated mediocre-AUC hazard gives a correct `S(t)`; a sharp-but-miscalibrated one does not.

### 6.5 Balance erosion `rᵢ(t)` (if scoped in Phase 0)

-   Second pooled model: **Elastic-Net (linear) on signature features** predicting the balance-ratio-given- alive. Same PIT/walk-forward/weighting discipline. Combine with `Aᵢ(t)` into `Bᵢ(t)`.

### 6.6 Validation (strict, per §2)

-   **Primary:** anchored walk-forward; **secondary:** 28-path CPCV with embargo = `H`. No leakage: signature standardizers, buckets, λ/α selection all fold-local; macro PIT-aligned.
-   Aggregate per-account survival × balance → `S(t)`; feed rate/inflation scenarios → **stressed `S(t)`**.
-   **Report intervals, not point curves** --- and the right intervals (§7).
-   **Output:** base + stressed run-off with honest intervals + explainability (§8).

### 6.7 Regime-switching layer --- the adaptive overlay (new)

Smooth models (signatures, the §7 latent factor) **smear** discrete structural breaks; DZD attrition plausibly *steps* at devaluations / liquidity-regime changes. Add a regime layer in **two roles**, both pure stdlib, both scored on the §6.6 OOT protocol. This is the project's only continuous-time stochastic-process model --- a **pure-jump Markov chain, deliberately not a diffusion SDE** (a diffusion's `σ` is unidentifiable on 120 months of an administered-rate market; see §0.2 / §1b).

**Role 1 --- regime posterior as a hazard feature (the forecaster, data-efficient).** - Fit a small **HMM, 2--3 states**, on the Layer-2 macro series (fiscal-liquidity proxy, FX premium, inflation) via Baum--Welch / forward--backward (\~80 lines stdlib). - At decision time τ, the **filtered** posterior `P(regime=k | info ≤ τ)` (PIT --- filtered, never smoothed across the future) enters the existing Elastic-Net logistic hazard as covariates. This generalizes the §7 latent monthly factor from one scalar to a regime simplex, and is what makes the **base** `S(t)` regime-aware.

**Role 2 --- continuous-time Markov chain for stressed `S(t)` (the regulator-legible stress object).** - Define states **exogenously** --- you *know* the episodes: `{hydrocarbon-liquid, stagnant,   currency-stress}`. With \~2--3 episodes in 120 months the generator `Q` is **not** freely identifiable, so do not pretend to learn it: set the regime calendar from Layer-2, estimate only the **within-regime hazard level `α_k`** (a regime-conditional intercept in the pooled logistic), and treat `Q`'s sojourn times as a **scenario knob** driven by the stress narrative. - Survival under the chain: `S(t) = 1ᵀ exp((Q − diag(α)) t) p₀`, the matrix exponential of the sub-generator via **scaling-and-squaring + truncated series** (stdlib; reuse the Gaussian-elimination / standardizer kit). `α_k` from Role-1 / the pooled hazard; `Q`, `p₀` from the scenario. - Use it to generate **stressed run-off**: shift `p₀` to the stress state, or raise the entry intensity into `currency-stress`, and read off the degraded `S(t)`. Clean complement to the §9 ±200bp / inflation shocks.

**Validation & honesty.** Both roles compete against the no-regime hazard on identical OOT origins (Gate B); keep the layer only if it improves OOT `S(t)` calibration/coverage, or is needed to express a stress the smooth model cannot. Disclose that Role-2's `Q` is **assumption-driven, not estimated** (a Gate-A-style caveat) --- the stressed curve is a *scenario* band, not a coverage-certified one.

### → Gate B (does extra complexity pay?)

Compare depth-3 vs depth-2, signatures vs raw engineered features, group-Lasso vs Elastic Net **on the same OOT protocol**. Keep the simplest model that wins out-of-time; **deflate** for the depth/penalty search. (There is no deep Phase 4 to gate anymore --- this is the internal complexity gate.)

------------------------------------------------------------------------

## 7. Uncertainty quantification --- the honest-CI core (applies Phases 2--6, pure stdlib)

Two independent error sources; **report both, headline the binding one.**

-   **(a) Cross-sectional (account) → account-cluster bootstrap.** Resample *accounts* with replacement, carrying all of each account's month-rows (block = account), recompute `S(t)`. **Narrow** with many accounts.
-   **(b) Temporal / common-shock → stationary/moving-block bootstrap over months.** Block length ≈ macro autocorrelation length (or = `H`); resample contiguous month-blocks and refit. With \~10 years this is **wide** and is the **binding constraint**.

**Honesty rule:** accounts share macro shocks → not independent. The **headline fan-chart and any p-value come from the time-block bootstrap** (or a hierarchical bootstrap: months → then accounts within), never the account bootstrap. Show the account bootstrap only as the small idiosyncratic component, labelled.

**Common-factor approximation (stdlib substitute for frailty).** A full random-effects/frailty model needs EM/numerical integration --- out of stdlib reach. Approximate honestly: (a) **segment fixed effects** as dummies, and/or (b) a **latent monthly factor** via a 2-step --- fit the hazard, extract the mean monthly residual as a common-shock series, refit with it as a covariate. Plot the factor vs macro (off-PC); if it tracks the rate/inflation episode that *is* the explainable common-shock channel; if not, it absorbs unexplained correlated attrition --- disclose. Label this an approximation in the validation note.

### 7.1 Conformal prediction for distribution-free stress intervals

Block bootstrap gives parameter/sampling uncertainty; conformal gives distribution-free finite-sample coverage on the prediction. All arithmetic --- pure stdlib. - **Score:** per `(segment, horizon)` realized-vs-predicted run-off residual `|S_real(t) − Ŝ(t)|` (or the per-month hazard residual). Calibrate on the most recent dev block; apply forward. - **Split conformal** assumes exchangeability (time series violates it) → use **weighted conformal** (reweight calibration toward the test regime) and/or **adaptive conformal (ACI)** that updates the quantile online as coverage drifts. **CQR** sits naturally on quantile hazards. - **For stress:** wrap each stressed `S(t)` in a conformal band, then **widen by the time-block bootstrap** for macro-parameter uncertainty; report the **combined** band. - **Caveat:** under a genuine OOD rate shock (which Algeria may never have realized --- Gate A), conformal coverage is **not guaranteed**; the band is then a **scenario band, not a coverage-certified one**. Say which it is.

------------------------------------------------------------------------

## 8. Explainability --- model *and* data (governance-grade, stdlib)

With a linear-on-signatures readout, interpretability is *structurally easier* than the transformer --- no SHAP library needed; coefficients and ablations carry it.

**Tier 1 --- interpretable anchors (always present).** - **ECM elasticity, equilibrium level, reversion speed** --- readable economic numbers. - **Pooled-logistic on raw features** (no signatures) as a transparent reference: signed coefficients on seasoning, balance bucket, calendar. The signature model is benchmarked against it. - **Monotonic constraints** on seasoning make behavior explainable by construction.

**Tier 2 --- data & feature explanation (stdlib, model-agnostic).** - **Group-Lasso channel selection** *is* the explanation: which path channels survive (rate vs inflation vs balance) and at which depth. Report **balance-weighted** importance too (whale-dominated aggregate differs from a random account --- state both). - **Channel ablation** (the stdlib substitute for SHAP on the deep model): drop the rate channel from the path, refit, measure the `S(t)` shift. Quantifies each channel's contribution honestly. - **Partial-dependence / ALE** for seasoning, balance bucket, rate, inflation --- compute by sweeping one input and averaging predictions; write to CSV. These *are* the behavioral story ("hazard drops sharply after the first 6 months, then flattens"). - Compute importances **out-of-time / out-of-bag** to avoid in-sample optimism.

**Tier 3 --- signature introspection (replaces attention maps).** - **Depth-1 signature terms = channel increments** (interpretable); **depth-2 = areas/covariations** between channels (e.g. balance--rate co-movement); higher depths less readable. Report which **depth levels** carry weight (via group-Lasso) rather than reading individual high-order terms. - **Honest caveat:** signature coefficients are a **functional-level**, not coefficient-level, explanation. Corroborate any mechanism claim with **ablation** and **PDP**, not the raw coefficient.

**Deliverable:** interpretability appendix --- Tier 1 numbers, Tier 2 group-selection + ablation + PDP/ALE CSVs, Tier 3 depth-level attribution, each labelled by evidential weight.

------------------------------------------------------------------------

## 9. Phase 4 --- Sensitivity, stress & validation packaging (1--1.5 weeks)

-   **Sensibilité.** Re-run `S(t)` under rate shocks (±200bp parallel, steepener/flattener) and inflation scenarios → effective **duration / key-rate sensitivity / WAL** of the deposit. Carry the Gate-A caveat: if rate variation was weak, present rate sensitivity as **scenario-driven, not empirically identified**, and lead with inflation.
-   **Regime stress (§6.7 Role 2).** Generate stressed `S(t)` from the continuous-time Markov chain: shift `p₀` to `currency-stress` and/or raise the entry intensity, read the degraded curve via the matrix-exponential survival `1ᵀexp((Q−diag(α))t)p₀`. Report beside the rate/inflation shocks; label `Q` assumption-driven (scenario band, not coverage-certified).
-   **Robustness battery:**
    -   stability of `S(t)` across time windows and segments;
    -   sensitivity to the **event definition** (re-estimate under alternative closure/dormancy/floor definitions --- a known degree of freedom, so quantify it);
    -   **realized-vs-predicted** aggregate `S(t)` on the frozen OOT window, with **interval coverage** (does the 90% time-block fan cover realized?) as the headline calibration metric;
    -   convention-vs-ECM-vs-signature-model on identical OOT origins.
-   **Metrics summary.** Discrimination (time-dependent AUC / Uno's C, IPCW) is secondary; **calibration (reliability + PIT) + aggregate-`S(t)` error + coverage are primary**, plus predicted-vs-realized WAL/duration.
-   **Model-validation file.** Assumptions, data lineage, PIT/no-look-ahead evidence, convention-vs-ECM-vs-model table, calibration evidence, Tier 1--3 explainability, and **honest limitations** --- especially Gate-A rate identifiability, the horizon-vs-history extrapolation limit, and the signature-vs-transformer trade (§11).
-   **Output:** final run-off curves (base + stressed), the run-off **rule**, the sensitivity table, the validation note.

------------------------------------------------------------------------

## 10. Pure-stdlib implementation inventory (what to build, \~order)

| Component                                                                        | Status / approach              | Stdlib feasibility              |
|----------------------------------------------------------------------------------|--------------------------------|---------------------------------|
| Robust dump parser (UTF-16, title row, header-by-name)                           | **built** (`anonymize`/reader) | ✅                              |
| Account-month panel builder + survival bookkeeping                               | Phase 1                        | ✅ `csv`/`re`/`math`            |
| **Event-time PIT as-of join** (`τ_available`) + elapsed-time columns             | Phase 1 / §4b                  | ✅ `sort`+`bisect`              |
| **Time-based fold/embargo splitter** (predicates on τ)                           | §4b / §6.6                     | ✅ index logic                  |
| Ridge / Lasso / **Elastic Net** linear (CV λ,α)                                  | **core built**; add EN mix     | ✅ (`ridge_lasso.py`)           |
| Penalized **logistic** hazard (IRLS / grad descent + soft-threshold)             | Phase 3 next                   | ✅ \~60 lines                   |
| Multinomial (competing-risks) logistic                                           | Phase 3 option                 | ✅ softmax + grad               |
| **Group-Lasso** (block coordinate descent)                                       | Phase 3 refinement             | ✅ more work                    |
| **Truncated signatures** (time-aug, basepoint, lead-lag, Chen)                   | Phase 3                        | ✅ \~50 lines, no `iisignature` |
| Gaussian-elimination solver, standardizer                                        | **built**                      | ✅                              |
| Walk-forward + CPCV folds, embargo=H                                             | Phase 3 validation             | ✅ index logic                  |
| Time-block + account-cluster bootstrap                                           | §7                             | ✅ (slow but fine)              |
| Conformal (split / weighted / adaptive / CQR)                                    | §7.1                           | ✅ arithmetic                   |
| HAC / Newey--West SE                                                             | Phase 2                        | ✅ formula                      |
| PIT / reliability / PDP / ALE → CSV                                              | §6.4/§8                        | ✅ binning                      |
| Latent monthly factor (2-step)                                                   | §7                             | ✅ approximation                |
| **Stdlib data fetcher** (`urllib`/`json`/`csv` + `available_date`)               | §1b                            | ✅ `urllib.request`             |
| **HMM (2--3 state) Baum--Welch / forward--backward**                             | §6.7 Role 1                    | ✅ \~80 lines                   |
| **Continuous-time MC survival** (sub-generator matrix-exp, scaling-and-squaring) | §6.7 Role 2                    | ✅ reuse linalg                 |
| GBM, SHAP, transformer, full frailty                                             | **dropped / approximated**     | ❌ not stdlib                   |

Build order: **panel → ECM/Elastic-Net floor → signatures → penalized-logistic hazard → aggregation to `S(t)` → walk-forward/embargo → bootstrap+conformal+PIT → group-Lasso & competing risks → stress & packaging.**

------------------------------------------------------------------------

## 11. Honest accounting --- what we actually trade (no oversell)

**First, drop the framing that signatures "replace" the transformer.** A signature is a **fixed feature map** (deterministic iterated integrals); a transformer is a **learnable model**. They are not the same kind of object. What we are really doing: **transformer (learned features + learned weighting) → signatures (fixed hand-chosen features) + linear readout (linear weighting).** That is *more rigid*, not better. We choose it because rigidity is an asset here --- un-overfittable on thin data, regularizable, zero-dependency --- not because it is secretly superior.

**The honest trade:** - **Lost:** (a) a *learned, data-adaptive* representation, and (b) *selective attention* to specific past events. Signatures *summarize* the whole path; they cannot spotlight one event and ignore the rest. - **What carries over (not "recovered for free"):** nonlinear cross-channel interactions (depth-2 area terms), the two-clock / continuous-PE information (just `τ` and seasoning as channels --- same info, different container), interaction-order control (truncation depth), variable-length→fixed-vector handling (intrinsic to the signature).

**Signatures' own downsides --- stated, not buried:** - **Hard truncation:** everything above depth N is discarded permanently. - **Dimensionality blow-up** (exponential in depth × channels) --- the *reason* elastic-net/group-lasso is mandatory. - **Reparametrization invariance is a liability here**, not elegance: the raw signature drops level, absolute time, and quadratic variation, so you must hand-engineer basepoint + time-augmentation + lead-lag to put them back. Fiddly. - **Interpretability is genuinely worse** than raw coefficients --- iterated-integral terms don't read as "seasoning effect = X." Ablation/PDP mitigate but don't fully fix this.

**Why the loss is nonetheless small *here*:** 120 months and few macro cycles cannot fund a learned representation without overfitting out-of-time --- the transformer's extra capacity is capacity the data can't pay for (the engine's overparam lesson). The binding constraint is **data and signal, not architecture**. The only genuine residual sacrifice is a sharp nonlinear pattern above depth-N and below the noise floor --- which cannot be verified on 120 months without self-deception, so claiming the transformer would capture it violates the project's own honesty rules.

**Net for IRRBB:** the signature + Elastic-Net route is **correctly sized**, **zero-dependency**, and **more defensible to model validation** --- and we accept worse single-event interpretability and the loss of learned/attention capacity as the price. A fair trade in this governance + constraint context, not a free win.

------------------------------------------------------------------------

## 12. The spine, in one breath

Build the **event-time, PIT-correct** account-month panel (stdlib parser + as-of join on `τ_available` + elapsed-time columns, §4b) → **check rates actually moved (Gate A)** → set convention + HAC/block-bootstrapped **penalized ECM** as the floor → pooled, **balance-weighted, calibrated, competing-risk penalized-logistic hazard on truncated path signatures** (Elastic Net → group-Lasso), with a 2-step latent common factor, validated **out-of-time first** and CPCV-second with embargo = `H`, aggregated (balance-weighted) to `S(t)` with **time-block (not account-only) intervals + conformal bands**, with a **2--3 state regime overlay** (HMM posterior as a hazard feature; exogenous-state continuous-time Markov chain for stressed `S(t)`) → stress + validate with **coverage as the headline**. **Phases 0--3 already constitute a complete, defensible IRRBB deliverable; there is no deep upside phase --- the signature basis is the nonlinearity, in stdlib.**

------------------------------------------------------------------------

## 13. Open decisions to confirm before coding

1.  **Run-off quantity:** attrition-only, or attrition × balance-erosion `rᵢ(t)`? (default: both for DAV)
2.  **Event definition:** single event vs competing risks (closure/dormancy/floor)?
3.  **Frozen-OOS length & placement:** default 24 months (2023--2024) --- confirm after Gate-A inventory.
4.  **Max evaluated horizon `H`:** sets embargo and the empirical-vs-extrapolated validation limit.
5.  **Signature config:** channels (`log-balance`, rate, inflation, seasoning?), transforms (time-aug + lead-lag + basepoint?), depth sweep (N∈{2,3}?), readout (Elastic Net first, group-Lasso second?).
6.  **Regime layer (§6.7):** number of states (2 vs 3), which Layer-2 series define them, and whether Role-2's `Q` sojourn times come from a fixed stress narrative or a coarse empirical episode count.
