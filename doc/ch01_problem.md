# The Problem

## Setup

A demand deposit (DAV) is contractually repayable on demand ($T_{\text{contract}}=0$) yet the
*book* balance is sticky. IRRBB needs a behavioral maturity replacing the missing contractual one.

**Definition 1.1 (run-off curve).** Normalize today's stock to $1$. $S(t)=$ expected fraction
still present at horizon $t$; $S(0)=1$, $S$ weakly decreasing. $1-S(t)=$ cumulative run-off.

Summaries used downstream:
$$
\text{WAL}=\sum_{t\ge0}S(t)\,\Delta t,
\qquad
D_{\text{eff}}=-\frac1S\frac{\partial S}{\partial y}.
\tag{1.1}
$$
Uses: IRRBB ($\Delta$EVE/$\Delta$NII bucketing under rate shocks), FTP (tenor crediting).

## Decomposition

Three behaviors drive the curve: **attrition** (discrete event, whole balance leaves),
**erosion** (continuous drawdown of a surviving balance), **core** (inert sticky float $\to$ long
tail). Make it explicit per account:
$$
B_i(t)=B_i(0)\,\underbrace{A_i(t)}_{\text{alive?}}\,\underbrace{r_i(t)}_{\text{kept}\mid\text{alive}},
\qquad A_i(t)\in[0,1],\ r_i(0)=1.
\tag{1.2}
$$
Book aggregates (balance-weighted, because the book is whale-dominated):
$$
B(t)=\frac{\sum_i B_i(0)A_i(t)r_i(t)}{\sum_i B_i(0)},
\qquad
S(t)=\frac{\sum_i B_i(0)A_i(t)}{\sum_i B_i(0)}.
\tag{1.3}
$$
$S(t)$ = attrition-only sub-curve (Ch. 3–5); $B(t)$ = deployed curve, adds erosion $r$ (Ch. 6).

**Remark 1.1.** A raw weighted-average of historical balances = the convention/ECM floor (Ch. 7);
it discards seasoning, segment, regime, balance-bucket heterogeneity. The behavioral model (1.2)
keeps it and aggregates *modeled* survival — a bet that must win on an out-of-time backtest (Ch. 11),
not be asserted.

## Three structural difficulties

1. **Forward extrapolation, short data.** $S(t)$ predicts the future decay of today's stock $\to$
   out-of-time is the only honest test. With $T\approx120$ and few cycles, the curve beyond
   $\sim 24$ months is *structural extrapolation*, not validated.
2. **Uncertainty is temporal.** Accounts are many but share each month's macro shock $\to$ not
   independent. Honest bands resample *time*, not accounts (Ch. 10).
3. **Gate A (rate identifiability).** A rate elasticity needs rate variation. The Algerian policy
   rate is administered (3 distinct values, 92-month flat run) $\to$ not identifiable; substitute the
   money-market rate, lean stress on inflation + scenarios, disclose. Run *before* any
   rate-conditional fit.

These three dictate the design: small, regularized, time-block-validated, explicit about
measured-vs-assumed.
