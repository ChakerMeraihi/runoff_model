"""SADF / GSADF / BSADF explosiveness detector (Phillips-Shi-Yu), pure stdlib.

Standard ADF is LEFT-tailed (null: unit root vs stationary alternative). To detect
BUBBLES / EXPLOSIVE behaviour you flip to a RIGHT-tailed recursive ADF:

  ADF_stat(window)   : right-tailed ADF on a sub-sample
  sadf               : sup over expanding windows with a fixed start (PSY 2011)
  gsadf              : sup over expanding windows AND a moving start (PSY 2015) ->
                       detects multiple/periodically-collapsing bubbles
  bsadf_series       : backward-SADF at each t -> date-stamps regime onset/collapse

For DAV this flags EXPLOSIVE deposit flight or a parallel-FX-premium blow-up
(currency-stress onset) -- the discrete break CUSUM (mean) and ICSS (variance) miss.
BSADF is causal at each t (uses data <= t), so date-stamping is look-ahead-safe;
critical values come from a Monte-Carlo null (random walk) computed once.
"""
from __future__ import annotations

import random

from linalg import ols


def adf_right(x, trend="c"):
    """Right-tailed ADF t-stat on the persistence coef (lag 0, no augmentation)."""
    if len(x) < 8:
        return float("-inf")
    dy = [x[t] - x[t - 1] for t in range(1, len(x))]
    rows, target = [], []
    for t in range(1, len(x)):
        r = [x[t - 1]]
        if trend in ("c", "ct"):
            r.append(1.0)
        if trend == "ct":
            r.append(float(t))
        rows.append(r)
        target.append(dy[t - 1])
    try:
        fit = ols(rows, target)
    except Exception:  # noqa
        return float("-inf")
    gamma = fit["beta"][0]
    var = fit["sigma2"] * fit["xtx_inv"][0][0]
    if var <= 0:
        return float("-inf")
    return gamma / var ** 0.5


def sadf(x, r0=None, trend="c"):
    n = len(x)
    r0 = r0 or max(12, int(0.2 * n))
    return max(adf_right(x[:e], trend) for e in range(r0, n + 1))


def gsadf(x, r0=None, trend="c"):
    n = len(x)
    r0 = r0 or max(12, int(0.2 * n))
    best = float("-inf")
    for e in range(r0, n + 1):
        for s in range(0, e - r0 + 1):
            best = max(best, adf_right(x[s:e], trend))
    return best


def bsadf_series(x, r0=None, trend="c"):
    """Backward SADF at each end-point e: sup over start s of ADF on x[s:e].
    Returns array (None for e<r0). Causal: bsadf[e] uses only x[:e]."""
    n = len(x)
    r0 = r0 or max(12, int(0.2 * n))
    out = [None] * n
    for e in range(r0, n + 1):
        out[e - 1] = max(adf_right(x[s:e], trend) for s in range(0, e - r0 + 1))
    return out


def mc_critical(n, r0=None, trend="c", n_sim=200, q=0.95, seed=0, stat="gsadf"):
    """Monte-Carlo critical value of the statistic under the unit-root null."""
    rng = random.Random(seed)
    fn = gsadf if stat == "gsadf" else sadf
    vals = []
    for _ in range(n_sim):
        rw, v = [0.0], 0.0
        for _ in range(n - 1):
            v += rng.gauss(0, 1)
            rw.append(v)
        vals.append(fn(rw, r0, trend))
    vals.sort()
    return vals[int(q * (len(vals) - 1))]


if __name__ == "__main__":
    rng = random.Random(3)
    n = 120

    # (1) pure random walk -> NOT explosive
    rw, v = [0.0], 0.0
    for _ in range(n - 1):
        v += rng.gauss(0, 1)
        rw.append(v)

    # (2) random walk that goes EXPLOSIVE in the middle then collapses (a bubble)
    bub, v = [0.0], 0.0
    for t in range(1, n):
        if 60 <= t < 85:
            v = 1.06 * v + rng.gauss(0, 1)      # explosive root > 1
        elif t == 85:
            v *= 0.5                              # collapse
        else:
            v = v + rng.gauss(0, 1)              # unit root
        bub.append(v)

    crit = mc_critical(n, n_sim=150, stat="gsadf", seed=7)
    print(f"GSADF 95% MC critical value (n={n}): {crit:+.3f}\n")
    g_rw, g_bub = gsadf(rw), gsadf(bub)
    print(f"random walk  GSADF={g_rw:+.3f} -> "
          f"{'EXPLOSIVE' if g_rw > crit else 'no bubble (correct)'}")
    print(f"bubble path  GSADF={g_bub:+.3f} -> "
          f"{'EXPLOSIVE (correct)' if g_bub > crit else 'missed'}")

    # date-stamping via BSADF
    bs = bsadf_series(bub)
    flagged = [t for t, val in enumerate(bs) if val is not None and val > crit]
    print(f"\nBSADF date-stamped explosive months (true window 60-85): "
          f"{flagged[:1]}..{flagged[-1:]} (n={len(flagged)})")
