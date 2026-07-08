# Balance Erosion and Combined Run-off

Hazard gives $A_i(t)$. For DAV, surviving accounts also draw down (erosion $r_i(t)$); ignoring it
understates run-off.

## Two channels

$B_i(t)=B_i(0)A_i(t)r_i(t)$:

- $A_i(t)\in[0,1]$ — survival probability (discrete event), the hazard;
- $r_i(t)=B_i(t)/B_i(0)\mid$ alive — continuous shrinkage, $r_i(0)=1$.

Modeled separately (different statistics, different drivers, desk wants them apart).

## Retention model

Log scale (positivity, multiplicative $\to$ additive). Target $\Delta\log B_{i,t}=\log B_{i,t}-
\log B_{i,t-1}$, **linear elastic net** on the causal features; reconstruct
$$
r_i(t)=\exp\!\Big(\sum_{k=1}^{t}\widehat{\Delta\log B}_{i,k}\Big).
\tag{6.1}
$$
Same PIT / walk-forward / balance-weighting.

**Remark 6.1 (Huber here).** $\Delta\log B$ has genuine outliers (one-off transfers) $\Rightarrow$ **Huber loss**
($\sim 8\times$ more robust at 5% contamination). The binary hazard cannot have outliers, so Huber
is for this continuous regression, not Ch. 3.

## Combined curve

$$
B(t)=\frac{\sum_i B_i(0)A_i(t)r_i(t)}{\sum_i B_i(0)},\quad
A(t)=\frac{\sum_i B_i(0)A_i(t)}{\sum_i B_i(0)},\quad
r(t)=\frac{\sum_i B_i(0)r_i(t)}{\sum_i B_i(0)}.
\tag{6.2}
$$
Gap $A(t)-B(t)$ = erosion contribution. Synthetic (declining gen): $B(12)\approx0.89$ vs
$A(12)\approx0.97$ $\Rightarrow$ most year-1 run-off is drawdown, not closure.

**Pitfall.** Upward balance drift $\Rightarrow$ $r(t)>1$ $\Rightarrow$ accretive "run-off" $>1$ (nonsense). Synthetic gen
flipped to $-0.4\%$/mo to fix. On real data the sign is empirical (high-inflation nominal growth can
give $r>1$ legitimately — separate IRRBB decision).

Deployed quantity = $B(t)$; $S(t)=A(t)$ is the attrition sub-component. Daily scorer dispatches on
the selected run-off model (Ch. 7).
