"""Penalized Error-Correction Model baseline for aggregate DAV balance -- PLANv2 5.

Two-step Engle-Granger ECM:
  (1) long-run (cointegrating) level:  log_bal_t = a + b1*rate_t + b2*infl_t + e_t
      -> b1 is the long-run rate ELASTICITY (semi-elasticity on log balance).
  (2) short-run with error correction:
      d log_bal_t = c + g1*d rate_t + g2*d infl_t + phi*e_{t-1} + seasonal + u_t
      -> phi (the speed of adjustment) must be negative (mean reversion); |phi| is
         the monthly reversion speed.

Rigor (PLANv2 5): ~96 serially-dependent points -> textbook OLS CIs are wrong. We
report NEWEY-WEST HAC standard errors AND a moving-block bootstrap CI for the
long-run elasticity. Per Gate A, the rate driver is the MONEY-MARKET rate (the
policy rate is administered/flat). Ridge option for short samples (lambda=0 -> OLS).
"""
from __future__ import annotations

import math

from linalg import ols
from bootstrap import moving_block_indices, percentile_ci
import random


def _hac_se(X, resid, xtx_inv, L=None):
    """Newey-West HAC covariance -> SEs for OLS coefficients with dependent errors."""
    n, k = len(X), len(X[0])
    if L is None:
        L = max(1, int(4 * (n / 100.0) ** (2 / 9)))
    # meat = sum_l w_l (S_l + S_l^T), S_l = sum_t x_t x_{t-l}' e_t e_{t-l}
    meat = [[0.0] * k for _ in range(k)]
    for lag in range(0, L + 1):
        w = 1.0 if lag == 0 else 1.0 - lag / (L + 1)
        S = [[0.0] * k for _ in range(k)]
        for t in range(lag, n):
            et, etl = resid[t], resid[t - lag]
            xt, xtl = X[t], X[t - lag]
            f = et * etl
            for a in range(k):
                fa = f * xt[a]
                for b in range(k):
                    S[a][b] += fa * xtl[b]
        if lag == 0:
            for a in range(k):
                for b in range(k):
                    meat[a][b] += S[a][b]
        else:
            for a in range(k):
                for b in range(k):
                    meat[a][b] += w * (S[a][b] + S[b][a])
    # sandwich: (X'X)^-1 meat (X'X)^-1
    tmp = [[sum(xtx_inv[a][c] * meat[c][b] for c in range(k)) for b in range(k)] for a in range(k)]
    cov = [[sum(tmp[a][c] * xtx_inv[c][b] for c in range(k)) for b in range(k)] for a in range(k)]
    return [math.sqrt(max(cov[j][j], 0.0)) for j in range(k)]


def fit_ecm(log_bal, rate, infl, season=None, ridge=0.0, block_len=6, n_boot=600, seed=0):
    n = len(log_bal)
    # ---- step 1: long-run level regression ----
    Xl = [[1.0, rate[t], infl[t]] for t in range(n)]
    lr = ols(Xl, log_bal, ridge=ridge)
    a, b1, b2 = lr["beta"]
    e = lr["resid"]
    se_lr = _hac_se(Xl, e, lr["xtx_inv"])

    # ---- step 2: short-run ECM ----
    drows, dtarget = [], []
    for t in range(1, n):
        r = [1.0, rate[t] - rate[t - 1], infl[t] - infl[t - 1], e[t - 1]]
        if season is not None:
            r.append(season[t])
        drows.append(r)
        dtarget.append(log_bal[t] - log_bal[t - 1])
    sr = ols(drows, dtarget, ridge=ridge)
    se_sr = _hac_se(drows, sr["resid"], sr["xtx_inv"])
    phi = sr["beta"][3]

    # ---- moving-block bootstrap CI for the long-run elasticity b1 ----
    rng = random.Random(seed)
    boot = []
    for _ in range(n_boot):
        idx = moving_block_indices(n, block_len, rng)
        Xb = [Xl[i] for i in idx]
        yb = [log_bal[i] for i in idx]
        try:
            boot.append(ols(Xb, yb, ridge=ridge)["beta"][1])
        except Exception:  # noqa
            pass
    bb_lo, bb_med, bb_hi = percentile_ci(boot)

    return {
        "long_run": {"const": a, "rate_elasticity": b1, "infl_elasticity": b2,
                     "hac_se": se_lr},
        "short_run": {"beta": sr["beta"], "hac_se": se_sr, "reversion_speed_phi": phi},
        "elasticity_block_ci": (bb_lo, bb_med, bb_hi),
    }


if __name__ == "__main__":
    rng = random.Random(0)
    n = 120
    # ground truth: cointegrated system. log_bal long-run = 10 - 0.40*rate + 0.8*infl
    TRUE_B1, TRUE_B2, TRUE_PHI = -0.40, 0.80, -0.25
    rate = []
    r = 2.0
    for _ in range(n):
        r += rng.gauss(0, 0.3)            # persistent money-market-like rate
        rate.append(r)
    infl = []
    f = 0.04
    for _ in range(n):
        f = 0.9 * f + 0.1 * 0.04 + rng.gauss(0, 0.004)
        infl.append(f)
    eq = [10 + TRUE_B1 * rate[t] + TRUE_B2 * infl[t] for t in range(n)]
    log_bal = [eq[0]]
    for t in range(1, n):
        # error-correction data-generating process
        prev_gap = log_bal[t - 1] - eq[t - 1]
        dbal = TRUE_PHI * prev_gap + (eq[t] - eq[t - 1]) + rng.gauss(0, 0.01)
        log_bal.append(log_bal[t - 1] + dbal)

    res = fit_ecm(log_bal, rate, infl)
    lr = res["long_run"]
    print("LONG-RUN (cointegrating) level:")
    print(f"  rate elasticity  = {lr['rate_elasticity']:+.4f}  HAC se={lr['hac_se'][1]:.4f}  (true {TRUE_B1})")
    print(f"  infl elasticity  = {lr['infl_elasticity']:+.4f}  HAC se={lr['hac_se'][2]:.4f}  (true {TRUE_B2})")
    lo, med, hi = res["elasticity_block_ci"]
    print(f"  rate elasticity 95% block-bootstrap CI: [{lo:+.4f}, {hi:+.4f}]  (median {med:+.4f})")
    print(f"  -> CI covers truth {TRUE_B1}: {lo <= TRUE_B1 <= hi}")
    sr = res["short_run"]
    print(f"\nSHORT-RUN error correction:")
    print(f"  reversion speed phi = {sr['reversion_speed_phi']:+.4f}  HAC se={sr['hac_se'][3]:.4f}  "
          f"(true {TRUE_PHI}; must be <0)")
    print(f"  -> mean-reverting: {sr['reversion_speed_phi'] < 0}")
