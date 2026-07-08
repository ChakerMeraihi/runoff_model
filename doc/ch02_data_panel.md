# The Data: Survival Panel

The model fits a flat table; survival structure and no-look-ahead live in the **data layer**.

## Panel construction

Input: monthly dumps `DAV_MMYYYY.txt` (filename = authoritative period). Hazards: title preamble,
UTF-16/cp1252, US dates, comma-thousands, **column drift** $\to$ resolve columns *by header name*.
Output: account-month rows $(i,t)$ with `CTRVL KDA`, type, `DATE OUVERTURE`, `segment`.

- Keep all deposit products; tag `segment` $\in\{$vue_dinars, vue_bu, garantie$\}$. Guarantee deposits
  run off on contract lifecycle $\to$ **separate hazard segment**, never pooled, never dropped.
- **Integrity gate:** $\sum_i$ `CTRVL KDA` per month must reconcile to the known book total.
  Non-reconciling panel $\Rightarrow$ wrong population; nothing downstream fixes it.

## Survival bookkeeping

**Definition 2.1 (event).** $T_i=$ first month $i$ disappears (closure/dormancy) **or** balance
$<$ floor (economic withdrawal). Floor is a degree of freedom $\to$ stress-tested (Ch. 11).

Two finite-window effects:

- **Right-censoring.** Alive at window end $\Rightarrow$ $T_i>T_{\text{end}}$ only. Keep the account — it is the
  stickiest balance; it contributes a run of $y=0$ rows then stops (handled natively by the
  discrete likelihood, Ch. 3).
- **Definition 2.2 (left-truncation).** Opened before the window $\Rightarrow$ enter the risk set at
  $T_{\text{start}}$ with seasoning $>0$, likelihood **conditioned on survival to entry**; tenure
  measured from `DATE OUVERTURE`, not from $T_{\text{start}}$.

Output = a **risk set**: per month, accounts under observation, each tagged alive/event, correct
tenure clock.

## Event-time / point-in-time (PIT)

A flat table has no clock; inject it as data. Three obligations (panel builder):

1. **Elapsed time as columns:** seasoning, calendar month/year, $\Delta\tau$ since last event.
2. **PIT as-of macro join.** Each macro row carries `available_date`. Attach to $(i,\tau)$ the
   latest macro obs with $\text{available\_date}\le\tau$ (backward as-of: sort + `bisect`). Never
   the reference-month or revised vintage.
3. **No fitted statistic peeks.** Scalers, bucket cuts, $\lambda$, regime params, signature
   standardizers all fit on train, applied forward. `fit_end` is required, not default (the Step-3
   leak).

**Remark 2.1.** The model is time-agnostic; event-time handling = (i) panel as-of join +
elapsed-time columns, (ii) splitter time-predicates (Ch. 11). Nothing in the estimator.

**Design fork.** (A, default) features irregular/PIT, **decision grid regular monthly** $\to$ $S(t)=
\prod(1-h)$ coherent, regulatory horizons legible. (B) fully continuous-time hazard (Cox + exposure)
— more correct only if irregular-horizon output is required; rejected (data are monthly, harder in
stdlib).

## Gate A (real-data result)

Policy rate administered (3 values, 92-mo flat) $\to$ use **money-market rate** (118 distinct,
$\text{std}\approx0.91$). Inflation 0.1–10.8% = solid driver. Frozen OOS 2023–24 = single stagnant
regime $\to$ regime-stress $S(t)$ is **fit-only, not OOS-validatable**. Resolved here, before fitting.
