# Discrete-Time Survival and the Hazard

Cast attrition as discrete-time survival $\Rightarrow$ it *becomes* penalized logistic regression on
person-month rows.

## Hazard and survival

**Definition 3.1.** $h_i(t)=\mathbb P(T_i=t{+}1\mid T_i>t,\ \mathcal F_t)$,
$\ S_i(t)=\mathbb P(T_i>t\mid\mathcal F_0)$.

**Proposition 3.1.** $\displaystyle S_i(t)=\prod_{k=1}^{t}\big(1-h_i(k)\big).$ (3.1)

*Proof.* $\mathbb P(T_i>t)=\prod_{k=1}^t\mathbb P(T_i>k\mid T_i>k{-}1)$, each factor $=1-h_i(k)$.
$\square$

(3.1) is the engine: model the 1-month hazard, chain to the whole curve (`survival_path`:
`cur*=(1-h)`).

## Likelihood

Per at-risk month, $y_{i,t}=\mathbf 1\{T_i=t{+}1\}$. Risk set $\mathcal R_i$ = observed at-risk
months.
$$
\mathcal L=\prod_i\prod_{t\in\mathcal R_i} h_i(t)^{y_{i,t}}\big(1-h_i(t)\big)^{1-y_{i,t}}.
\tag{3.2}
$$
This is exactly a **pooled Bernoulli (logistic) regression** on person-month rows. Censoring and
truncation need no extra machinery:

- right-censored $\Rightarrow$ a string of $y=0$ rows, future absent from (3.2);
- left-truncated $\Rightarrow$ enters $\mathcal R_i$ from observation start $\Rightarrow$ auto-conditioned on survival to
  entry.

## Parameterization

$$
h_i(t)=\sigma\big(b_0+\boldsymbol\beta^\top\mathbf z_{i,t}\big),\qquad \mathbf z_{i,t}\ \mathcal F_t\text{-meas.}
\tag{3.3}
$$
Design:

- **Pooled** (one $\boldsymbol\beta$, heterogeneity via $\mathbf z_{i,t}$): per-account overfits
  (rare events, short histories); raw-aggregate discards cross-section (= ECM, Ch. 7).
- **Balance-weighted loss** $w_{i,t}\propto B_i(0)$:
$$
\min_{b_0,\boldsymbol\beta}\ -\!\sum_{i,t} w_{i,t}\!\big[y_{i,t}\log h_i+(1{-}y_{i,t})\log(1{-}h_i)\big]
+\lambda\,\Omega_\alpha(\boldsymbol\beta).
\tag{3.4}
$$
Book is whale-dominated $\Rightarrow$ weight by balance, else the fit optimizes the typical *small* account.
$\Omega_\alpha$ = elastic net (Ch. 4).

- **Competing risks (option):** multinomial/softmax cause-specific $h_i^{(c)}$, aggregated to stock
  decay. Default: single event.

## Aggregation

For accounts alive at the as-of date, roll (3.1) to $S_i(t)=A_i(t)$, then
$$
S(t)=\frac{\sum_i B_i(0)A_i(t)}{\sum_i B_i(0)},\quad t=0,\dots,H.
\tag{3.5}
$$
$\times r_i(t)$ $\Rightarrow$ $B(t)$ (Ch. 6). Stressed curves = re-score with shocked macro (Ch. 9).

## Metric: calibration, not discrimination

$S(t)=\prod(1-h)$ $\Rightarrow$ a product is sensitive to systematic bias in each $h$, insensitive to the
ranking of the $h$'s. So judge *levels*:

- **reliability** (predicted $h$ vs realized frequency by bin, also balance-weighted);
- **PIT** (randomized PIT residuals $\approx$ Uniform$(0,1)$, KS test);
- **Brier** (proper scoring rule; AUC is *not*, it rewards ranking only).

Synthetic deployed hazard: OOT ECE $\approx0.003$, PIT-KS $\approx0.017$. Headline of the
validation file (Ch. 11).
