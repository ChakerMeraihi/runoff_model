# Penalized Logistic Readout

Hundreds of collinear features (signatures, Ch. 5) on $T\approx120$ $\Rightarrow$ unpenalized fit overfits and
is unstable. Elastic net makes (3.4) well-posed.

## Penalty

$$
\Omega_\alpha(\boldsymbol\beta)=\alpha\|\boldsymbol\beta\|_1+\tfrac12(1-\alpha)\|\boldsymbol\beta\|_2^2,
\qquad (\ell_1,\ell_2)=(\lambda\alpha,\ \lambda(1-\alpha)),\quad b_0\ \text{unpenalized}.
\tag{4.1}
$$

- **L1** $\to$ exact zeros (selection); needed since most signature terms are noise.
- **L2** $\to$ shares weight across collinear groups (signatures are shuffle-product collinear);
  stabilizes what pure Lasso ($\alpha{=}1$) makes unstable.
- Pure Ridge ($\alpha{=}0$) never selects. Tune $(\lambda,\alpha)$ out-of-time (Ch. 11). Standardize
  features internally; map coefs back to raw space.

## Solver: coordinate descent (glmnet)

IRLS quadratic approx of the logistic loss, then cyclic CD. Per coordinate, closed form via the
soft-threshold $\mathcal S_\kappa(z)=\operatorname{sign}(z)\max(|z|-\kappa,0)$:

**Proposition 4.1 (CD update).**
$$
\beta_j\leftarrow
\frac{\mathcal S_{\ell_1}\!\big(\sum_i\tilde w_i z_{ij} r_i^{(-j)}\big)}{\sum_i\tilde w_i z_{ij}^2+\ell_2},
\tag{4.2}
$$
$r_i^{(-j)}$ = partial residual excl. $j$, $\tilde w_i$ = IRLS weights. Soft-threshold = L1
selection; $+\ell_2$ = ridge shrinkage. Closed-form, no step tuning, stable on collinear features.
`solver='pgd'` (ISTA: grad step + prox) gives identical coefs (cross-check).

**Remark 4.1.** Not used: *projected* GD (that solves the constrained $\|\beta\|_1\le t$ form);
**Huber** (Bernoulli has no outliers) — Huber belongs on the continuous ECM/erosion regressions
(Ch. 6–7).

## Overfit control

**1-SE rule** (default). Let $\mathrm{CV}(\lambda)$ = mean OOT score over walk-forward origins,
$\mathrm{SE}$ = its s.e. across origins. Deploy
$$
\lambda^\star=\max\{\lambda:\ \mathrm{CV}(\lambda)\le\mathrm{CV}(\lambda_{\min})+\mathrm{SE}(\lambda_{\min})\}.
\tag{4.3}
$$
Among statistically-indistinguishable models, pick the sparsest. On the (flat) synthetic HP surface
the raw argmin chases noise; 1-SE picks the defensible model.

**No early stopping.** The penalized logistic is *convex*; CD converges *to* the optimum of (4.1).
Stopping early = a worse solve of the same problem, not a milder regularizer. So `lr`, `epochs`,
`max_irls` are convergence settings, fixed. Real overfit controls: $\lambda$ (1-SE), purged CPCV,
frozen OOS tail, conformal coverage (Ch. 10–11).

## HP search

Only $(\lambda,\alpha)$. **$\lambda$-path $\times$ $\alpha$-grid**, scored by walk-forward OOT
**NLL/Brier** (proper scoring, *not* AUC). Grid (not Optuna/TPE) = deterministic, auditable for
governance. Parallel via stdlib `multiprocessing` ($\sim 6$–$7\times$ on 20 cores, identical to
serial). The grid sets L1 strength $\Rightarrow$ feature inclusion is never hand-tuned; we only count surviving
terms.
