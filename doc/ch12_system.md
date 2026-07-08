# System, Governance, Limits

## Frozen-artifact contract

> Only recalibration writes coefficients; the daily scorer only reads.

$$
\texttt{eval}\to\texttt{hp\_selected.json}\ \to\ \texttt{fit}\to\texttt{model.json}\ \to\ \texttt{daily}\to S(t).
$$
Daily can run unattended: a noisy month cannot re-train. CUSUM may *recommend* a refit; refit is
governed.

## Two cadences

- **Routine (monthly):** re-fit *coefficients* on **all** data (no held-out tail — run-off must be
  current), frozen HPs. Fast, low-governance.
- **Governed (quarterly):** re-*select* HPs + family + model class (nested, Ch. 11); `model.json`
  flagged "pending MR sign-off". Heavier.

`eval` (reporting, held-out, **not** deployed) and `fit` (deployed, all-data, **not** the reported
number) are separate entry points — conflating them = test-on-train.

## Operational surface

One button:
```
python run_pipeline.py --dav-dir <dumps>               # monthly
python run_pipeline.py --dav-dir <dumps> --recalibrate # quarterly
```
Steps: `download` (macro fetch + panel rebuild), `eval`, `fit`, `daily` ($B(t)$), `stress` (MC fan +
WAL tail), `report` (self-contained HTML). Plots = hand-written **SVG** (no matplotlib). Only
aggregates leave the bank (coefs, curves, elasticities, calibration) — never client rows.

## Explainability (governance)

Linear-on-features $\Rightarrow$ no SHAP needed. **T1** anchors (ECM elasticity/reversion; raw-feature logistic;
monotone seasoning). **T2** group-Lasso channel selection + channel ablation (SHAP substitute) +
PDP/ALE. **T3** signature depth attribution (functional-level only $\Rightarrow$ corroborate by ablation).

## Spine (one line)

PIT event-time panel $\to$ Gate A $\to$ convention + HAC/block-bootstrap ECM floor $\to$ pooled balance-weighted
calibrated penalized-logistic hazard on raw+signatures, with regime posterior + erosion $\to$ walk-forward
(CPCV second) + purge/embargo + nested selection $\to$ balance-weighted $S(t),B(t)$ + time-block +
conformal bands $\to$ regime overlay (HMM feature; CTMC stressed $S(t)$) $\to$ stress with **coverage** as
headline.

## Honest limits (not deployment-certified)

1. All numbers = methodology on synthetic real-format data; real panel on bank PC; first run needs
   `profile_dav.py` to confirm grain/scope/floor.
2. Frozen OOS 2023–24 single-regime $\Rightarrow$ regime-stress $S(t)$ fit-only.
3. Rate sensitivity weakly identified (Gate A) $\Rightarrow$ scenario-driven.
4. Bootstrap coverage caps $\sim 0.88$ at $T{=}120$ $\Rightarrow$ fixed-$b$ HAC / bootstrap-$t$.
5. Coef-bootstrap $\times$ MC combine = named next layer; current fan is macro-path only.

Cadence = **monthly**, two jobs (frozen scoring vs governed recalibration); never daily retraining.
The deliverable is the measured-vs-assumed boundary, not a single accuracy number.
