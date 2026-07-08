# Structural Breaks and Monitoring

Detect discrete change: retrospectively (where a single fit is illegitimate) and online (alarm on
staleness). All causal — normalization by future data is the leak to avoid.

## CUSUM (mean break)

Recursive residuals $w_t$ (expanding-window 1-step errors), i.i.d. mean-0 under stability.
$$
S_t=\frac1{\hat\sigma}\sum_{j=k+1}^t w_j,\qquad n^{-1/2}S_{\lfloor rn\rfloor}\xrightarrow{d}B(r).
\tag{9.1}
$$
A mean break drifts the residuals $\Rightarrow$ $S_t$ crosses $\pm c\sqrt n(1+2r)$ ($c$ = Brownian-crossing
quantile). Production: macro-stream **break alarm** $\Rightarrow$ flags `recommend_early_refit` (alarm only).
$\hat\sigma$ must be expanding-window (full-sample $\hat\sigma$ = the Step-3 leak).

## ICSS + Sansó (variance breaks)

$C_t=\sum_{j\le t}a_j^2$, centered statistic $D_t=C_t/C_n-t/n$ ($D_0{=}D_n{=}0$):
$$
\sqrt{\tfrac n2}\,\max_t|D_t|\xrightarrow{d}\sup_{r}|B^0(r)|,\quad B^0(r)=B(r)-rB(1).
\tag{9.2}
$$
Candidate at $\arg\max_t|D_t|$; iterate (find, split, recurse, refine). Classical ICSS i.i.d.-Gaussian
$\Rightarrow$ **over-detects under GARCH**. **Sansó $\kappa_2$** uses a dependence-robust (HAC) scaling $\Rightarrow$ FP rate
$0.41\to0.06$ (bw scale $\approx3$).

## SADF (explosive episode)

Backward-expanding/rolling ADF, supremum:
$$
\mathrm{SADF}=\sup_{r_2}\mathrm{ADF}_{0,r_2},\qquad
\mathrm{GSADF}=\sup_{r_1,r_2}\mathrm{ADF}_{r_1,r_2}.
\tag{9.3}
$$
Right-tailed (bubble = root $>1$): reject when ADF large. Backward-expanding sequence date-stamps
(cross / un-cross a simulated CV) $\Rightarrow$ causal "explosive" flag (deposit flight).

## Use

- **Diagnostic:** a flag $\Rightarrow$ a single fit over that span averages regimes $\Rightarrow$ expanding/rolling refit +
  embargo so a label window does not straddle a break (Ch. 11).
- **Monitoring:** daily CUSUM alarm, monitoring-only; re-segmentation/refit is governed (frozen-artifact
  contract).
- **Pitfall:** detection lag is intrinsic (need post-break data to confirm); never peek forward. The
  alarm means "a break is *by now* apparent."
