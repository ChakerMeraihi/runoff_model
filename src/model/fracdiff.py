"""Fractional differentiation + ADF stationarity test (pure stdlib).

DAV balances and macro levels are near-unit-root (I(1)). Integer differencing
(d=1) makes them stationary but ERASES memory. Fractional differencing
(1-L)^d with the MINIMAL d in (0,1] that achieves stationarity preserves maximum
long memory while passing ADF — AFML Ch.5 (Hosking 1981).

  frac_weights : binomial expansion weights of (1-L)^d
  ffd          : fixed-width-window fractional diff (weights truncated at tol)
  adf          : Augmented Dickey-Fuller t-stat (OLS), with MacKinnon 5% crit
  min_ffd_d    : smallest d on a grid s.t. the FFD series is ADF-stationary

No look-ahead: FFD at time t uses only past values (causal one-sided filter); the
ADF search picks d on the training window and applies the same weights forward.
"""
from __future__ import annotations

import math

from linalg import ols

# MacKinnon (2010) asymptotic 5% critical values for ADF
ADF_CRIT_5 = {"nc": -1.941, "c": -2.861, "ct": -3.411}


def frac_weights(d, size):
    """Weights w_k of (1-L)^d, k=0..size-1 (w_0=1)."""
    w = [1.0]
    for k in range(1, size):
        w.append(-w[-1] * (d - k + 1) / k)
    return w


def ffd_weights(d, tol=1e-4, max_size=400):
    """Fixed-width-window weights: keep until |w_k| < tol (AFML FFD)."""
    w = [1.0]
    k = 1
    while k < max_size:
        wk = -w[-1] * (d - k + 1) / k
        if abs(wk) < tol:
            break
        w.append(wk)
        k += 1
    return w


def ffd(x, d, tol=1e-4):
    """Causal fractionally-differenced series; first (len(w)-1) entries are None
    (insufficient history). Returns (series_with_None, weights)."""
    w = ffd_weights(d, tol)
    L = len(w)
    out = [None] * len(x)
    for t in range(L - 1, len(x)):
        out[t] = sum(w[j] * x[t - j] for j in range(L))
    return out, w


def adf(x, lags=1, trend="c"):
    """Augmented Dickey-Fuller. Regress dY_t on Y_{t-1}, [trend], and `lags` lagged
    differences. Returns (t_stat, gamma, crit_5). Null: unit root (non-stationary)."""
    dy = [x[t] - x[t - 1] for t in range(1, len(x))]
    rows, target = [], []
    start = lags + 1
    for t in range(start, len(x)):
        r = [x[t - 1]]                                   # level (the gamma term)
        if trend in ("c", "ct"):
            r.append(1.0)
        if trend == "ct":
            r.append(float(t))
        for L in range(1, lags + 1):
            r.append(dy[t - 1 - L])                      # lagged differences
        rows.append(r)
        target.append(dy[t - 1])
    fit = ols(rows, target)
    n, k = fit["n"], fit["k"]
    gamma = fit["beta"][0]
    s2 = fit["sigma2"]
    se_gamma = math.sqrt(max(s2 * fit["xtx_inv"][0][0], 1e-30))
    return gamma / se_gamma, gamma, ADF_CRIT_5.get(trend, -2.861)


def min_ffd_d(x, grid=None, tol=1e-4, trend="c"):
    """Smallest d s.t. the FFD series rejects the ADF unit-root null at 5%."""
    if grid is None:
        grid = [i / 20 for i in range(0, 21)]            # 0.0 .. 1.0 step 0.05
    for d in grid:
        s, _ = ffd(x, d, tol)
        s = [v for v in s if v is not None]
        if len(s) < 20:
            continue
        t, _, crit = adf(s, lags=1, trend=trend)
        if t < crit:                                     # reject unit root
            return d, t, crit
    return 1.0, *adf([v for v in ffd(x, 1.0, tol)[0] if v is not None], 1, trend)[0::2]


if __name__ == "__main__":
    import random
    rng = random.Random(1)

    # I(1) random walk: ADF should NOT reject (non-stationary); min d ~ near 1?
    rw = [0.0]
    for _ in range(400):
        rw.append(rw[-1] + rng.gauss(0, 1))
    t_rw, _, c = adf(rw, trend="c")
    print(f"random walk  ADF t={t_rw:+.3f} (crit {c}) -> "
          f"{'stationary' if t_rw < c else 'UNIT ROOT (correct)'}")

    # stationary AR(1) phi=0.3: ADF SHOULD reject
    ar = [0.0]
    for _ in range(400):
        ar.append(0.3 * ar[-1] + rng.gauss(0, 1))
    t_ar, _, c = adf(ar, trend="c")
    print(f"AR(1) 0.3    ADF t={t_ar:+.3f} (crit {c}) -> "
          f"{'STATIONARY (correct)' if t_ar < c else 'unit root'}")

    # min fractional d to make the random walk stationary
    d, t, c = min_ffd_d(rw, trend="c")
    s, w = ffd(rw, d)
    # memory preserved: correlation of FFD series with the original level
    sv = [(i, v) for i, v in enumerate(s) if v is not None]
    lv = [rw[i] for i, _ in sv]
    fv = [v for _, v in sv]
    mlv, mfv = sum(lv) / len(lv), sum(fv) / len(fv)
    cov = sum((a - mlv) * (b - mfv) for a, b in zip(lv, fv))
    corr = cov / (math.sqrt(sum((a - mlv) ** 2 for a in lv) * sum((b - mfv) ** 2 for b in fv)) + 1e-12)
    print(f"\nmin FFD d for random walk: d={d:.2f}  ADF t={t:+.3f}<{c}  "
          f"window={len(w)} weights")
    print(f"  memory retained: corr(FFD, level)={corr:+.3f}  "
          f"(d=1 full-diff would destroy this)")
