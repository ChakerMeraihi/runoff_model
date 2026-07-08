# Regimes: HMM and Stressed Survival

Smooth models smear discrete change; DZD attrition *steps* at devaluations / liquidity shifts. Add a
regime layer in two roles.

## Gaussian HMM

**Definition 8.1.** Latent $z_t\in\{1,\dots,K\}$, transition $A_{jk}=\mathbb P(z_t{=}k\mid z_{t-1}{=}j)$,
emission $x_t\mid z_t{=}k\sim\mathcal N(\mu_k,\Sigma_k)$ (diagonal $\Sigma$). $x_t$ = macro vector.

Three inferences ($\sim 80$ lines stdlib):

- **filter** $\mathbb P(z_t{=}k\mid x_{1:t})$ — causal (forward recursion);
- **smooth** $\mathbb P(z_t{=}k\mid x_{1:T})$ — non-causal (forward–backward);
- **learn** $(\mu,\Sigma,A,\pi)$ — Baum–Welch (EM).

**Causality:** as a feature, use **filtered** only (smoothed leaks the future); params fit on dev,
applied forward. Two build fixes: (i) **standardize emissions** (else oil $\sim 65$ swamps cpi
$\sim 0.05$ $\to$ single-state collapse, degenerate $A\sim 10^{-229}$); (ii) **BIC-select** $K\in\{2,3\}$.

## Role 1: posterior as hazard feature

At $\tau$, the filtered posterior $\mathbb P(z_\tau{=}k\mid x_{1:\tau})$ (a simplex point) enters
(3.3) as `regime_p*`. Makes the *base* $S(t)$ regime-aware. Deployed iff **Gate B** (Ch. 11) lowers
frozen-OOS NLL; on synthetic it was dropped (no incremental signal). Real data decides.

## Online (dynamic) HMM

Static Baum–Welch = one $A$ for all time — wrong when *dynamics* change. Track discounted sufficient
statistics with forgetting $\rho$, from **filtered** responsibilities:
$$
W_k\!\leftarrow\!\rho W_k+\gamma_t(k),\quad
S^{(1)}_k\!\leftarrow\!\rho S^{(1)}_k+\gamma_t(k)x_t,\quad
T_{jk}\!\leftarrow\!\rho T_{jk}+\xi_t(j,k),
\tag{8.1}
$$
re-derive $(\mu_k,\Sigma_k,A_t)$; effective memory $\approx 1/(1-\rho)$ ($\rho{=}0.97\Rightarrow\sim 33$ mo).
Tracks a $0.95\to0.60$ persistence shift a static fit blurs to $\sim 0.76$. Warm-start from batch BW;
filtered-only $\Rightarrow$ look-ahead-safe. Daily advances the *filter* (frozen params); re-segmentation = refit.

## Role 2: CTMC stressed survival

Pure-jump Markov chain (not a diffusion: $\sigma$ unidentifiable on 120 mo administered-rate data).
Exogenous states $\{$liquid, stagnant, currency-stress$\}$, generator $Q$, regime-conditional hazard
intercepts $\alpha_k$, init $p_0$:
$$
S(t)=\mathbf 1^\top\exp\!\big((Q-\operatorname{diag}\alpha)\,t\big)\,p_0
\tag{8.2}
$$
(matrix-exp by scaling-and-squaring + truncated series). $Q$ is **set, not learned** ($\sim 2$–3
episodes $\Rightarrow$ unidentifiable): date regimes exogenously, estimate only $\alpha_k$, treat $Q$ sojourns as
a scenario knob. Stress = shift $p_0\to$ currency-stress / raise entry intensity $\Rightarrow$ degraded $S(t)$.
Result = **scenario band, not coverage-certified** (Gate-A caveat).

**Ablation.** Data-driven hazard wins (book-$S(t)$ MAE $\approx0.04$); CTMC beats the constant floor
($0.12<0.17$) but is dominated. Stochastic toolbox = the stress generator, not the forecaster.
