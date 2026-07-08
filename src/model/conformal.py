"""Split & weighted conformal prediction (pure stdlib) -- PLANv2 7.1.

Distribution-free, finite-sample coverage on the PREDICTION error (not the
parameter). The self-test shows conformal holds nominal coverage under non-Gaussian
noise where a Gaussian +/- z*sigma band fails -- i.e. it is robust to the error
distribution, which is the point for a deployment band on S(t).
"""
from __future__ import annotations

import math


def conformal_q(abs_residuals, alpha):
    """Finite-sample conformal quantile of absolute residuals (symmetric band)."""
    n = len(abs_residuals)
    if n == 0:
        return float("inf")
    k = math.ceil((n + 1) * (1 - alpha))
    if k > n:
        return float("inf")
    return sorted(abs_residuals)[k - 1]


def weighted_conformal_q(abs_residuals, weights, alpha):
    """Weighted conformal quantile (covariate-shift / regime reweighting)."""
    pairs = sorted(zip(abs_residuals, weights))
    tot = sum(weights) + 1.0          # +1 for the (unit-weight) test point
    cum = 0.0
    for r, w in pairs:
        cum += w
        if cum / tot >= (1 - alpha):
            return r
    return float("inf")


if __name__ == "__main__":
    import random
    import statistics

    def trial(noise, seed):
        rng = random.Random(seed)
        # y = 2x + noise; fit OLS on train, conformal on cal, test coverage
        def gen(n):
            xs = [rng.uniform(-2, 2) for _ in range(n)]
            ys = [2 * x + noise(rng) for x in xs]
            return xs, ys
        xtr, ytr = gen(200)
        xc, yc = gen(200)
        xte, yte = gen(400)
        # OLS slope/intercept
        mx = statistics.mean(xtr)
        my = statistics.mean(ytr)
        b = sum((x - mx) * (y - my) for x, y in zip(xtr, ytr)) / sum((x - mx) ** 2 for x in xtr)
        a = my - b * mx
        res = [abs(y - (a + b * x)) for x, y in zip(xc, yc)]
        q = conformal_q(res, alpha=0.10)
        cov_conf = sum(1 for x, y in zip(xte, yte) if abs(y - (a + b * x)) <= q) / len(xte)
        # naive Gaussian band +/- 1.645*sigma (90%)
        sig = statistics.pstdev([y - (a + b * x) for x, y in zip(xc, yc)])
        cov_gauss = sum(1 for x, y in zip(xte, yte) if abs(y - (a + b * x)) <= 1.645 * sig) / len(xte)
        return cov_conf, cov_gauss

    noises = {
        "gaussian": lambda r: r.gauss(0, 1),
        "exponential": lambda r: r.expovariate(1.0) - 1.0,
        "heavy-tail(t3-ish)": lambda r: r.gauss(0, 1) / max(0.1, abs(r.gauss(0, 1))),
    }
    print("split-conformal coverage (target 0.90), 200 trials each:")
    for name, noise in noises.items():
        cc, cg = [], []
        for s in range(200):
            a_, b_ = trial(noise, s)
            cc.append(a_)
            cg.append(b_)
        def ci(v):
            p = statistics.mean(v)
            se = statistics.pstdev(v) / len(v) ** 0.5
            return p, p - 1.96 * se, p + 1.96 * se
        pc, lc, hc = ci(cc)
        pg, lg, hg = ci(cg)
        print(f"  {name:<20} conformal={pc:.3f} [{lc:.3f},{hc:.3f}]   "
              f"gaussian-band={pg:.3f} [{lg:.3f},{hg:.3f}]")
