# Uncertainty: Bootstrap, Conformal, MC Stress

Two error sources; headline the binding one.

- **(a) Cross-sectional (accounts).** Account-cluster bootstrap (block = account, carry all its
  rows). **Narrow** ($N_{\text{acct}}$ large).
- **(b) Temporal (common shock).** Accounts share each month's macro $\Rightarrow$ not independent. Time-block
  bootstrap. **Wide, binding.**

**Honesty rule.** Headline = time-block; account bootstrap = the small idiosyncratic component,
labelled. The account band drastically understates uncertainty.

## Block bootstrap

**Definition 10.1 (moving-block).** Resample contiguous month-blocks of length $\ell\approx$ macro
ACF length (or $=H$); concatenate to length $T$; refit; recompute $S(t)$.

**Definition 10.2 (stationary, Politis–Romano).** Geometric random block lengths $\Rightarrow$ exactly stationary
resample (removes fixed-$\ell$ periodicity).

**Limit.** At $T\approx120$, high persistence $\Rightarrow$ coverage caps $\sim 0.88$ (too few independent
blocks). Fix = fixed-$b$ HAC / bootstrap-$t$. Disclosed.

## BCa

Naive percentile is biased under skew/variance-dependence (bounded survival). Adjust endpoints by
bias-correction $\hat z_0$ and acceleration $\hat a$ (jackknife):
$$
\alpha_{1,2}=\Phi\!\Big(\hat z_0+\frac{\hat z_0\pm z_{1-\alpha/2}}{1-\hat a(\hat z_0\pm z_{1-\alpha/2})}\Big).
\tag{10.1}
$$
Second-order accurate, transformation-respecting. Default for $S(t)$.

## Conformal

**Definition 10.3 (split conformal).** Nonconformity score $|S_{\text{real}}(t)-\hat S(t)|$ on a
calibration block; band = empirical $(1-\alpha)$ quantile. Marginal coverage guaranteed under
**exchangeability** (assumption-free distributionally).

Time series breaks exchangeability $\Rightarrow$ **weighted conformal** (reweight calibration toward the test
regime) and/or **ACI** (update the quantile online as coverage drifts); **CQR** on quantile hazards.
Synthetic split-conformal coverage = $0.90$ exact at all noise levels.

**Combined band:** conformal (prediction) $\oplus$ time-block bootstrap (parameter). Under a genuine
OOD rate shock (Gate A) coverage is **not** guaranteed $\Rightarrow$ scenario band, stated.

## Monte-Carlo stress

HMM is generative $\Rightarrow$ simulate regime+macro paths through frozen hazard+erosion:
$$
\text{fan}=\{p_{05},p_{50},p_{95}\}\text{ of }S(t),\qquad \text{WAL tail}=\{p_{01},p_{99}\}.
\tag{10.2}
$$
For baseline, $\pm200$bp, adverse-regime. Synthetic: $+200$bp WAL $11.23\to11.13$ mo, monotone bands.

**Scope.** MC = macro-path uncertainty only (bands tight $\sim\pm2\%$ at 238 accounts). Parameter
uncertainty (binding on 120 mo) = time-block bootstrap. Full band = coef-bootstrap $\times$ MC
(documented next layer); current ships the macro-path fan.
