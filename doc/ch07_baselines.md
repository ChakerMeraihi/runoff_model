# Baselines: Convention and ECM

The hazard must beat these out-of-time, else ship the simpler one (governed selection, Ch. 11).

## Convention (core/volatile)

**Definition 7.1.** Over a trailing window: core = trailing min (or low quantile) of book balance;
volatile = excess. Amortize each linearly:
$$
B_{\text{conv}}(t)=\text{core}\,\Big(1-\tfrac{t}{L_{\text{core}}}\Big)_+
+\text{vol}\,\Big(1-\tfrac{t}{L_{\text{vol}}}\Big)_+,\quad L_{\text{core}}\!\approx\!60,\ L_{\text{vol}}\!\approx\!3.
\tag{7.1}
$$
Transparent, no estimation; the floor. Static $\Rightarrow$ no conditioning, no sensitivity.

## ECM (error-correction)

Cointegration: $\log D_t$ and macro share a stationary long-run relation
$\log D_t^\ast=\gamma_0+\gamma_i i_t+\gamma_\pi\pi_t$ ($i$=money-market rate, $\pi$=inflation).

**Definition 7.2.**
$$
\Delta\log D_t=\underbrace{\phi\big(\log D_{t-1}-\log D_{t-1}^\ast\big)}_{\text{error correction}}
+\sum_k\theta_k\,\Delta x_{t-k}+\varepsilon_t,\qquad \phi<0.
\tag{7.2}
$$
Readables: **elasticity** $\gamma_i$ (rate sensibilité); **reversion speed** $\phi$;
**half-life** $\ln(0.5)/\ln(1+\phi)$. Fit with ridge/EN linear (pure OLS $=\lambda{=}0$).

**Honest SEs.** $T\approx96$ serially-dependent points $\Rightarrow$ OLS SEs wrong. Use **HAC (Newey–West)**
for $t$-stats + **moving-block bootstrap over months** for the elasticity CI (Ch. 10).

**Artifact.** Synthetic $\hat\phi>0$ $\Rightarrow$ no cointegration, undefined half-life. Reported as such; real
data decides.

## Role

convention = legibility floor; ECM = economics; hazard = challenger. All three scored on **realized
cohort book run-off** MAE on a held-out window; winner = governed frozen choice (Ch. 11). Deploy the
hazard only when it beats both OOT.
