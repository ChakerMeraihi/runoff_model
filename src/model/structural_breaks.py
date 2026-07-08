"""Structural-break detectors for autonomous, look-ahead-safe regime work (stdlib).

Two complementary tools:
  - cusum_detect : online two-sided CUSUM for MEAN/level shifts. Causal by
    construction (accumulates only past deviations) -> safe as a real-time feature.
  - icss         : Inclan-Tiao Iterated Cumulative Sums of Squares for VARIANCE
    shifts (the core-vs-volatile deposit signal). RETROSPECTIVE (the D_k statistic
    uses the whole segment), so it is look-ahead-safe ONLY when run train-window-
    only. icss_causal() is an expanding-window wrapper that re-runs ICSS on data
    <= tau to emit a causal variance-regime feature.

Both are autonomous (no hand labels) and pure stdlib.
"""
from __future__ import annotations

import math
import statistics

# ---------------------------------------------------------------- CUSUM (mean)
def cusum_detect(x, warmup=24, k_sigma=0.5, h_sigma=5.0, min_seg=6):
    """Online two-sided CUSUM. Returns dict with causal per-t arrays:
    changepoint (0/1), segment_id, seg_mean (causal), level vs expanding mean."""
    n = len(x)
    warmup = min(warmup, max(2, n // 4))
    mu = statistics.mean(x[:warmup])
    sd = statistics.pstdev(x[:warmup]) or 1.0
    k, h = k_sigma * sd, h_sigma * sd
    gp = gn = 0.0
    cps, seg = [], [0] * n
    cur, last_cp = 0, 0
    for t in range(n):
        gp = max(0.0, gp + (x[t] - mu - k))
        gn = min(0.0, gn + (x[t] - mu + k))
        if (gp > h or gn < -h) and (t - last_cp) >= min_seg:
            cps.append(t)
            cur += 1
            last_cp = t
            lo = max(0, t - min_seg + 1)
            mu = statistics.mean(x[lo:t + 1])
            sd = statistics.pstdev(x[lo:t + 1]) or sd
            k, h = k_sigma * sd, h_sigma * sd
            gp = gn = 0.0
        seg[t] = cur
    # causal segment mean + level vs expanding global mean/std
    seg_mean = [0.0] * n
    level = [""] * n
    run_sum = run_cnt = 0.0
    seg_sum = seg_cnt = 0.0
    cur_seg = 0
    g_sum = g_sqsum = 0.0
    for t in range(n):
        if seg[t] != cur_seg:
            cur_seg, seg_sum, seg_cnt = seg[t], 0.0, 0.0
        seg_sum += x[t]
        seg_cnt += 1
        seg_mean[t] = seg_sum / seg_cnt
        g_sum += x[t]
        g_sqsum += x[t] * x[t]
        gmean = g_sum / (t + 1)
        gstd = math.sqrt(max(g_sqsum / (t + 1) - gmean * gmean, 1e-12))
        d = seg_mean[t] - gmean
        level[t] = "high" if d > 0.5 * gstd else ("low" if d < -0.5 * gstd else "mid")
    return {"changepoint": [1 if t in set(cps) else 0 for t in range(n)],
            "segment_id": seg, "seg_mean": seg_mean, "level": level, "cps": cps}


# ------------------------------------------------------------ ICSS (variance)
# 95% critical value of sup_k sqrt(T/2)|D_k| (Brownian bridge) = 1.358.
# IT (Inclan-Tiao) uses this directly but ASSUMES i.i.d. -> over-rejects under
# autocorrelation / conditional heteroskedasticity. Sanso-Arago-Carrion (2004)
# kappa2 replaces the variance scaling with a HAC long-run 4th-moment, restoring
# size under dependence; its sup statistic shares the SAME 1.358 critical value.
ICSS_CRIT = 1.358


def _bartlett_lrv4(a, m):
    """HAC (Bartlett) long-run variance of the squared-deviation process, for
    Sanso kappa2: lrv = gamma0 + 2 sum_l (1 - l/(m+1)) gamma_l, gamma on (a^2 - sig2)."""
    T = len(a)
    sig2 = sum(v * v for v in a) / T
    e = [v * v - sig2 for v in a]
    g0 = sum(ei * ei for ei in e) / T
    lrv = g0
    for l in range(1, m + 1):
        gl = sum(e[t] * e[t - l] for t in range(l, T)) / T
        lrv += 2 * (1 - l / (m + 1)) * gl
    return max(lrv, 1e-12)


def _dk_argmax(a, robust=False, m=None, bw_scale=1.0):
    """Return (argmax, sup-statistic). robust=False -> IT (i.i.d.); True -> Sanso kappa2."""
    T = len(a)
    if T < 3:
        return None, 0.0
    C, s = [], 0.0
    for v in a:
        s += v * v
        C.append(s)
    CT = C[-1]
    if CT <= 0:
        return None, 0.0
    if not robust:
        best_j, best = None, 0.0
        for j in range(T - 1):
            Dk = C[j] / CT - (j + 1) / T
            if abs(Dk) > best:
                best, best_j = abs(Dk), j
        return best_j, math.sqrt(T / 2.0) * best
    # Sanso kappa2: B_k = (C_k - (k/T) C_T) / sqrt(T * lrv4)
    if m is None:
        m = max(1, int(bw_scale * 4 * (T / 100.0) ** (2 / 9)))
    lrv = _bartlett_lrv4(a, m)
    denom = math.sqrt(T * lrv)
    best_j, best = None, 0.0
    for j in range(T - 1):
        Bk = (C[j] - (j + 1) / T * CT) / denom
        if abs(Bk) > best:
            best, best_j = abs(Bk), j
    return best_j, best


def _find_break(a, lo, hi, robust=False, bw_scale=1.0):
    j, M = _dk_argmax(a[lo:hi + 1], robust=robust, bw_scale=bw_scale)
    if j is not None and M > ICSS_CRIT and 1 <= j <= (hi - lo) - 1:
        return lo + j
    return None


def icss(a, robust=False, bw_scale=3.0):
    """Inclan-Tiao variance change points via binary segmentation + significance
    refinement (retrospective). `a` should be ~zero-mean (e.g. a growth/diff series).
    robust=True uses the Sanso kappa2 (dependence-robust) statistic; bw_scale tunes
    the HAC bandwidth (larger -> more robust to persistent conditional heterosked.)."""
    T = len(a)
    cps = []

    def rec(lo, hi):
        if hi - lo < 2:
            return
        c = _find_break(a, lo, hi, robust=robust, bw_scale=bw_scale)
        if c is None:
            return
        cps.append(c)
        rec(lo, c)
        rec(c + 1, hi)

    rec(0, T - 1)
    cps = sorted(set(cps))
    # refinement: each cp must stay significant bracketed by its neighbors
    for _ in range(10):
        pts = [-1] + cps + [T - 1]
        new = []
        for i in range(1, len(pts) - 1):
            c = _find_break(a, pts[i - 1] + 1, pts[i + 1], robust=robust, bw_scale=bw_scale)
            if c is not None:
                new.append(c)
        new = sorted(set(new))
        if new == cps:
            break
        cps = new
    return cps


def icss_causal(a, min_history=36):
    """Expanding-window ICSS -> causal features. At each t>=min_history, re-run ICSS
    on a[:t+1] and emit months-since-last-variance-break + causal segment std."""
    n = len(a)
    since = [None] * n
    seg_std = [None] * n
    for t in range(min_history - 1, n):
        cps = icss(a[:t + 1])
        start = (cps[-1] + 1) if cps else 0
        since[t] = t - start
        seg = a[start:t + 1]
        seg_std[t] = statistics.pstdev(seg) if len(seg) > 1 else 0.0
    return {"months_since_break": since, "seg_std": seg_std}


if __name__ == "__main__":
    import random
    rng = random.Random(7)

    # CUSUM: mean shifts 0 -> +3 (t=80) -> -2 (t=160)
    x = []
    for t in range(240):
        m = 0.0 if t < 80 else (3.0 if t < 160 else -2.0)
        x.append(m + rng.gauss(0, 0.5))
    cu = cusum_detect(x)
    print(f"CUSUM mean-shift breaks (true ~80,160): {cu['cps']}")
    print(f"  levels at t=40/120/200: {cu['level'][40]}/{cu['level'][120]}/{cu['level'][200]}")

    # ICSS: variance shifts std 0.5 -> 2.0 (t=100) -> 0.7 (t=200), zero mean
    a = []
    for t in range(300):
        sd = 0.5 if t < 100 else (2.0 if t < 200 else 0.7)
        a.append(rng.gauss(0, sd))
    bp = icss(a)
    print(f"\nICSS variance breaks (true ~100,200): {bp}")
    for lo, hi in zip([0] + bp, bp + [len(a)]):
        print(f"  segment [{lo:3d},{hi:3d}) std={statistics.pstdev(a[lo:hi]):.2f}")
    cz = icss_causal(a)
    print(f"causal seg_std at t=80/150/280: "
          f"{cz['seg_std'][80]:.2f}/{cz['seg_std'][150]:.2f}/{cz['seg_std'][280]:.2f}")

    # Sanso vs Inclan-Tiao: false positives under GARCH(1,1) dependence, NO true break.
    def garch(n, rng, w=0.05, al=0.10, be=0.85):
        s2, e = 1.0, []
        for _ in range(n):
            s2 = w + al * (e[-1] ** 2 if e else 0.0) + be * s2
            e.append(rng.gauss(0, 1) * s2 ** 0.5)
        return e
    TR = 300
    print(f"\nGARCH(1,1) a=0.10 b=0.85, NO true break -> false-positive rate over "
          f"{TR} sims (target ~0.05):")
    rg = random.Random(11)
    sims = [garch(150, rg) for _ in range(TR)]
    fp_it = sum(1 for g in sims if icss(g, robust=False)) / TR
    print(f"  Inclan-Tiao (i.i.d. crit):        {fp_it:.3f}  <- over-rejects under dependence")
    for bw in (1.0, 3.0, 6.0):
        fp = sum(1 for g in sims if icss(g, robust=True, bw_scale=bw)) / TR
        print(f"  Sanso kappa2 (HAC, bw_scale={bw}):  {fp:.3f}")
