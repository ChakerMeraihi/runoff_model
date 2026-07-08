# Preface {.unnumbered}

Theory companion to the DAV run-off model (`BNP_Work/runoff_model/`). Goal: derive the run-off
curve $S(t)$ for non-maturing demand deposits (DAV, Algeria) for IRRBB, in pure Python stdlib.
Dense by design: definitions, equations, results. The code is the *how*; this is the *why*.

**Two binding constraints.** (i) **Stdlib only** — no `numpy`/`pandas`/`sklearn`/`matplotlib`;
every estimator is hand-rolled. (ii) **Time-scarce** — $N_{\text{acct}}\gg T$, with $T\approx120$
months and few macro cycles, so honest uncertainty is *temporal* and a high-capacity model
overfits out-of-time. Both recur throughout.

## Notation {.unnumbered}

| Symbol | Meaning |
|---|---|
| $i,\ t$ | account index; monthly (event-)time index |
| $\tau,\ \mathcal F_\tau$ | decision time; information knowable at $\tau$ |
| $T_i$ | event month for $i$ (closure / dormancy / balance $<$ floor) |
| $h_i(t)$ | hazard $=\mathbb P(T_i=t{+}1\mid T_i>t,\ \mathcal F_t)$ |
| $S_i(t)=A_i(t)$ | account survival $=\mathbb P(T_i>t)$; $A$ when contrasting with erosion |
| $r_i(t)$ | retention given alive $=B_i(t)/B_i(0)\mid$ survival |
| $B_i(t)=B_i(0)A_i(t)r_i(t)$ | account balance run-off |
| $S(t),\ B(t)$ | book curves, balance-weighted aggregates |
| $B_i(0)$ | as-of balance (run-off weight), `CTRVL KDA`, KDA |
| $\mathbf z_{i,t}$ | causal feature vector, $\mathcal F_t$-measurable |
| $\boldsymbol\beta,\ b_0,\ \sigma$ | hazard coefs, intercept, sigmoid $\sigma(x)=(1+e^{-x})^{-1}$ |
| $\mathrm{Sig}^N(X)$ | depth-$N$ truncated signature |
| $Q,\ p_0$ | CTMC generator, initial regime law |
| $H$ | max evaluated horizon (months); sets purge/embargo |
| $\text{WAL}$ | $=\sum_{t\ge0}S(t)\,\Delta t=\sum_{t\ge1}(S(t{-}1)-S(t))\,t$ |

## Conventions {.unnumbered}

- **Causality.** Anything feeding a prediction at $\tau$ uses data with availability $\le\tau$;
  fitted statistics use a required `fit_end`, applied forward.
- **Calibration $\gg$ discrimination.** $S(t)=\prod(1-h)$ amplifies hazard-*level* bias, not
  ranking error (Ch. 3, 11).
- **Honesty.** All current numbers are methodology results on synthetic real-format data; the
  real panel is on the bank PC. Fit-only / scenario-only / not-OOS-validatable claims are flagged.
- Balances in KDA; aggregates are balance-weighted (whale-dominated book).
