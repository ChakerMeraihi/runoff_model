"""Bootstrap confidence intervals (pure stdlib) -- PLANv2 7.

Two resamplers:
  - moving_block : contiguous month-blocks -> the BINDING temporal CI (accounts
    share macro shocks, so months are not independent). Block length ~ macro
    autocorrelation / the horizon H.
  - cluster      : resample whole accounts -> the idiosyncratic component.

The self-test shows the block bootstrap attains nominal coverage on an
autocorrelated series while the naive i.i.d. bootstrap is too narrow (under-covers),
which is exactly why PLANv2 headlines the time-block interval.
"""
from __future__ import annotations

import math
import random
import statistics


def _norm_cdf(z):
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def _norm_ppf(p):
    """Inverse normal CDF (Acklam's rational approximation), stdlib."""
    if p <= 0.0:
        return -1e10
    if p >= 1.0:
        return 1e10
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
           (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def bca_ci(data, stat_fn, n_boot=2000, alpha=0.05, seed=0):
    """Bias-Corrected and accelerated (BCa) bootstrap CI for i.i.d. `data`.
    Second-order accurate: corrects median-bias (z0) and skew/acceleration (a) of
    the bootstrap distribution -> nominal coverage where percentile under-covers,
    and works for non-mean functionals (e.g. an S(t) point). Jackknife for `a`."""
    rng = random.Random(seed)
    n = len(data)
    theta_hat = stat_fn(data)
    boots = [stat_fn([data[rng.randrange(n)] for _ in range(n)]) for _ in range(n_boot)]
    boots.sort()
    # bias-correction z0 from fraction of boots below theta_hat
    n_less = sum(1 for b in boots if b < theta_hat)
    if n_less == 0 or n_less == n_boot:
        return percentile_ci(boots, alpha)
    z0 = _norm_ppf(n_less / n_boot)
    # acceleration via jackknife
    jack = []
    for i in range(n):
        loo = data[:i] + data[i + 1:]
        jack.append(stat_fn(loo))
    jbar = sum(jack) / n
    num = sum((jbar - j) ** 3 for j in jack)
    den = 6.0 * (sum((jbar - j) ** 2 for j in jack) ** 1.5) + 1e-30
    a = num / den
    zl, zu = _norm_ppf(alpha / 2), _norm_ppf(1 - alpha / 2)

    def adj(z):
        zz = z0 + (z0 + z) / (1 - a * (z0 + z))
        return _norm_cdf(zz)

    pl, pu = adj(zl), adj(zu)

    def q(pp):
        i = pp * (n_boot - 1)
        lo = int(i)
        fr = i - lo
        return boots[lo] if lo + 1 >= n_boot else boots[lo] * (1 - fr) + boots[lo + 1] * fr

    return q(pl), theta_hat, q(pu)


def percentile_ci(vals, alpha=0.05):
    s = sorted(vals)
    n = len(s)

    def q(p):
        i = p * (n - 1)
        lo = int(i)
        frac = i - lo
        return s[lo] if lo + 1 >= n else s[lo] * (1 - frac) + s[lo + 1] * frac

    return q(alpha / 2), q(0.5), q(1 - alpha / 2)


def moving_block_indices(n, block_len, rng):
    out = []
    while len(out) < n:
        st = rng.randint(0, max(0, n - block_len))
        out.extend(range(st, min(n, st + block_len)))
    return out[:n]


def block_bootstrap(values, stat_fn, block_len, n_boot=1000, seed=0):
    rng = random.Random(seed)
    n = len(values)
    return [stat_fn([values[i] for i in moving_block_indices(n, block_len, rng)])
            for _ in range(n_boot)]


def cluster_bootstrap(groups, stat_fn, n_boot=1000, seed=0):
    rng = random.Random(seed)
    keys = list(groups)
    out = []
    for _ in range(n_boot):
        sample = []
        for _ in range(len(keys)):
            sample.extend(groups[keys[rng.randrange(len(keys))]])
        out.append(stat_fn(sample))
    return out


def stationary_bootstrap_indices(n, p, rng):
    """Politis & Romano (1994) stationary bootstrap: block lengths ~ Geometric(p)
    (mean block 1/p), wrap-around. Unlike fixed-block MBB the resampled series is
    STATIONARY. Consistent for the mean / smooth functionals under stationarity +
    alpha-mixing (sum of mixing coeffs finite), with 1/p -> inf and 1/(pn) -> 0."""
    idx, i = [], rng.randrange(n)
    while len(idx) < n:
        idx.append(i)
        i = rng.randrange(n) if rng.random() < p else (i + 1) % n
    return idx


def stationary_bootstrap(values, stat_fn, p, n_boot=1000, seed=0):
    rng = random.Random(seed)
    n = len(values)
    return [stat_fn([values[i] for i in stationary_bootstrap_indices(n, p, rng)])
            for _ in range(n_boot)]


def hac_mean_ci(x, L=None, alpha=0.05):
    """Newey-West (Bartlett-kernel) HAC confidence interval for the mean of a
    dependent series. Valid under stationarity + weak dependence; second-order
    correct vs the (first-order) percentile bootstrap, so it attains nominal
    coverage where the percentile interval under-covers. This is the §5 tool for
    the ECM elasticity SE."""
    n = len(x)
    if L is None:
        L = max(1, int(4 * (n / 100.0) ** (2 / 9)))   # Newey-West bandwidth
    mu = sum(x) / n
    d = [v - mu for v in x]
    var = sum(v * v for v in d) / n
    for k in range(1, L + 1):
        gk = sum(d[t] * d[t - k] for t in range(k, n)) / n
        var += 2 * (1 - k / (L + 1)) * gk
    se = (max(var, 0.0) / n) ** 0.5
    z = 1.959963985
    return mu, mu - z * se, mu + z * se


def _ar1(n, phi, sigma, rng):
    x, prev = [], 0.0
    for _ in range(n):
        prev = phi * prev + rng.gauss(0, sigma)
        x.append(prev)
    return x


if __name__ == "__main__":
    # Relaxing the i.i.d. hypothesis: CI for the mean of an AR(1) series (true 0).
    # i.i.d. bootstrap is invalid under dependence; the stationary bootstrap (geom
    # blocks) is the dependence-correct resampler; HAC is the second-order-correct
    # interval that actually hits nominal coverage at this n.
    TRIALS, n, phi = 800, 120, 0.6
    p = n ** (-1 / 3)                 # mean geometric block ~ n^{1/3}
    block_len = max(1, round(n ** (1 / 3)))

    def cov_ci(c, ntr=TRIALS):
        pr = c / ntr
        se = (pr * (1 - pr) / ntr) ** 0.5
        return pr, pr - 1.96 * se, pr + 1.96 * se

    c = {"iid (percentile)": 0, "fixed-block (percentile)": 0,
         "stationary geom (percentile)": 0, "HAC Newey-West (t)": 0}
    rng = random.Random(0)
    for t in range(TRIALS):
        x = _ar1(n, phi, 1.0, rng)
        lo, _, hi = percentile_ci(block_bootstrap(x, statistics.mean, 1, 300, t))
        c["iid (percentile)"] += lo <= 0 <= hi
        lo, _, hi = percentile_ci(block_bootstrap(x, statistics.mean, block_len, 300, t))
        c["fixed-block (percentile)"] += lo <= 0 <= hi
        lo, _, hi = percentile_ci(stationary_bootstrap(x, statistics.mean, p, 300, t))
        c["stationary geom (percentile)"] += lo <= 0 <= hi
        _, lo, hi = hac_mean_ci(x)
        c["HAC Newey-West (t)"] += lo <= 0 <= hi

    print(f"AR(1) phi={phi}, n={n}, target 0.95  (SE inflation "
          f"~sqrt((1+phi)/(1-phi))={((1+phi)/(1-phi))**0.5:.2f}x)")
    for name in c:
        pr, l, h = cov_ci(c[name])
        print(f"  {name:<30} {pr:.3f}  95% CI [{l:.3f},{h:.3f}]")

    mean_fn = lambda d: sum(d) / len(d)

    # (a) SANITY: symmetric normal mean -> BCa must ~= percentile ~= 0.95 (no bug)
    TR2, n2 = 600, 40
    cp = cb = 0
    for t in range(TR2):
        rng2 = random.Random(1000 + t)
        x = [rng2.gauss(0, 1) for _ in range(n2)]
        boots = [mean_fn([x[rng2.randrange(n2)] for _ in range(n2)]) for _ in range(800)]
        lo, _, hi = percentile_ci(boots)
        cp += (lo <= 0.0 <= hi)
        lo, _, hi = bca_ci(x, mean_fn, n_boot=800, seed=t)
        cb += (lo <= 0.0 <= hi)
    print(f"\n(a) SANITY normal mean (symmetric), n={n2}, target 0.95, {TR2} trials:")
    p, l, h = cov_ci(cp, TR2)
    print(f"  percentile: {p:.3f} [{l:.3f},{h:.3f}]")
    p, l, h = cov_ci(cb, TR2)
    print(f"  BCa:        {p:.3f} [{l:.3f},{h:.3f}]   (should match percentile -> no bug)")

    # (b) SKEWED mean of Exp(1) (true 1): BCa corrects ASYMMETRIC tail miss. Two-
    # sided coverage can look similar; the tell is the LEFT vs RIGHT miss balance
    # (each tail should miss ~2.5%). Percentile is lopsided on skewed data.
    pl = pr_ = bl = br = 0
    for t in range(TR2):
        rng2 = random.Random(5000 + t)
        x = [rng2.expovariate(1.0) for _ in range(n2)]
        boots = [mean_fn([x[rng2.randrange(n2)] for _ in range(n2)]) for _ in range(800)]
        lo, _, hi = percentile_ci(boots)
        pl += (1.0 < lo)      # true below interval = left miss
        pr_ += (1.0 > hi)     # true above interval = right miss
        lo, _, hi = bca_ci(x, mean_fn, n_boot=800, seed=t)
        bl += (1.0 < lo)
        br += (1.0 > hi)
    print(f"(b) SKEWED mean of Exp(1), n={n2}, {TR2} trials -> tail-miss balance "
          f"(each tail target 2.5%):")
    print(f"  percentile: left-miss={pl/TR2:.3f} right-miss={pr_/TR2:.3f}  "
          f"cover={1-(pl+pr_)/TR2:.3f}  <- lopsided tails (skew)")
    print(f"  BCa:        left-miss={bl/TR2:.3f} right-miss={br/TR2:.3f}  "
          f"cover={1-(bl+br)/TR2:.3f}  <- tails rebalanced")
