# Validation

$S(t)$ forecasts the future decay of today's stock $\Rightarrow$ out-of-time only.

## Protocols

- **Anchored walk-forward (headline).** Train $[\text{start},o]$, score $(o,o+K]$, step $o$; report
  mean $\pm$ dispersion across origins, never one fold. Mimics deployment.
- **CPCV (secondary).** Dev $\to N{=}8$ super-blocks ($\sim 12$ mo), $k{=}2$ test $\Rightarrow$ $\binom 82{=}28$
  paths; report dispersion only (less deployment-faithful than walk-forward).

## Purge / embargo

Hazard label overlaps 1 mo; aggregated $S(t)$ overlaps $H$.

**Definition 11.1.** *Purge*: drop train rows whose label window $[\tau,\tau+H]$ overlaps a test
block. *Embargo*: drop a buffer after it. Both are **time predicates on $\tau$ (real months), not row
counts**; width $=H$ for $S(t)$, $1$ mo for hazard discrimination. The one place event-time changes
the splitter.

## Nested selection (headline never selects)

Using the held-out tail to *select* and *report* inflates the headline. Fix: train $\to$ validation
$\to$ test:

- **train** (walk-forward): HP search $(\lambda,\alpha)$;
- **validation:** feature family (base / +regime / +signatures) by NLL **and** run-off model
  (convention/ECM/hazard) by realized-cohort MAE;
- **test:** touched once = honest headline; never selects.

Two-level: within family L1 selects per-coef; across families validation NLL decides. **Parsimony
tie-break** (1-SE spirit, family tol $\sim0.01$, model tol $\sim0.05$): deploy the simplest within
tol. Demo: base+regime beat base by 0.3% (noise) $\Rightarrow$ parsimony drops regime, deploys base; signatures
rejected (val 0.043 vs 0.026); model = hazard (test MAE 0.012 vs ECM 0.044 vs conv 0.055); honest
test NLL $\approx0.033$, ECE $\approx0.0036$.

## Gate B

Same OOT protocol decides each option (depth 3 vs 2, sig vs raw, regime on/off, hazard vs
conv/ECM); keep the simplest OOT winner, deflate for the search. Every option re-tested each
recalibration, never permanently parked.

## Calibration scorecard

$S(t)=\prod(1-h)$ $\Rightarrow$ level-sensitive. Discrimination (time-dependent AUC, Uno C, IPCW) secondary.
Primary:

- **reliability** (predicted vs realized $h$ by bin; also balance-weighted);
- **PIT** $\approx$ Uniform$(0,1)$, KS;
- **aggregate-$S(t)$ error** on the frozen tail;
- **coverage** — does the 90% time-block fan cover realized? (headline: a band that misses is worse
  than none).

Censoring administrative & non-informative $\Rightarrow$ IPCW justified-omitted (stated).

## Validation file

Stability across windows/segments; **event-definition** sensitivity (re-fit under alt floor/dormancy
rules); conv-vs-ECM-vs-hazard on identical origins; predicted-vs-realized WAL/duration; Tier 1–3
explainability (ECM elasticity/reversion; group-Lasso channel selection + ablation; PDP/ALE; signature
depth attribution); honest limits (Gate A, horizon-vs-history, bootstrap cap, signature trade). That
file — not one number — is the sign-off artifact.
