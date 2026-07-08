"""Range-based volatility estimators (pure stdlib) -- low-frequency-data tools.

Monthly close-to-close volatility on ~120 points is extremely noisy (each monthly
sigma estimate uses 1 return). If the DAV panel exposes intra-month balance extremes
(open/high/low/close of the aggregate or per-account balance), range-based estimators
are far more EFFICIENT (more information per period):

  parkinson      : uses high-low range only; ~5x efficiency vs close-to-close,
                   but assumes zero drift and no jumps -> biased low if either.
  garman_klass   : uses OHLC; ~7x efficiency; still assumes zero drift, no jumps.
  rogers_satchell: uses OHLC; DRIFT-robust (unbiased under nonzero drift).
  yang_zhang     : combines overnight + open-close + RS; drift- AND jump-robust,
                   minimum variance -> the best general choice.

CAUTION for deposits: balances JUMP (salary inflows, large withdrawals), which
violates the no-jump assumption of Parkinson/GK. Prefer Rogers-Satchell or
Yang-Zhang, or realized variance aggregated from daily balances when available.
All estimators return a VARIANCE per period (take sqrt for sigma).
"""
from __future__ import annotations

import math


def parkinson(high, low):
    return (math.log(high / low) ** 2) / (4.0 * math.log(2.0))


def garman_klass(o, h, l, c):
    return 0.5 * math.log(h / l) ** 2 - (2 * math.log(2) - 1) * math.log(c / o) ** 2


def rogers_satchell(o, h, l, c):
    return (math.log(h / c) * math.log(h / o) + math.log(l / c) * math.log(l / o))


def yang_zhang(bars, k=None):
    """bars = list of (open, high, low, close) with the PREVIOUS close available.
    Combines overnight (close_{t-1}->open_t), open->close, and Rogers-Satchell."""
    n = len(bars)
    if n < 2:
        return float("nan")
    oc, co, rs = [], [], []      # open-close, overnight (prevclose->open), RS
    for i in range(1, n):
        po = bars[i - 1][3]
        o, h, l, c = bars[i]
        co.append(math.log(o / po))
        oc.append(math.log(c / o))
        rs.append(rogers_satchell(o, h, l, c))
    m = len(oc)
    mu_co = sum(co) / m
    mu_oc = sum(oc) / m
    v_co = sum((x - mu_co) ** 2 for x in co) / (m - 1) if m > 1 else 0.0
    v_oc = sum((x - mu_oc) ** 2 for x in oc) / (m - 1) if m > 1 else 0.0
    v_rs = sum(rs) / m
    if k is None:
        k = 0.34 / (1.34 + (m + 1) / (m - 1)) if m > 1 else 0.34
    return v_co + k * v_oc + (1 - k) * v_rs


def realized_variance(returns):
    """Sum of squared sub-period returns within the period (e.g. daily within month)."""
    return sum(r * r for r in returns)


if __name__ == "__main__":
    import random
    rng = random.Random(4)

    # simulate a month as 21 daily log-steps with KNOWN daily vol -> monthly var truth
    def make_month(daily_sig, drift=0.0, jump=0.0):
        price = 100.0
        o = price
        hi = lo = price
        rets = []
        for d in range(21):
            step = drift + rng.gauss(0, daily_sig) + (jump if d == 10 else 0.0)
            price *= math.exp(step)
            rets.append(step)
            hi = max(hi, price)
            lo = min(lo, price)
        return (o, hi, lo, price), rets

    # ---- efficiency test: zero drift, no jump. estimate monthly variance ----
    daily_sig = 0.01
    true_month_var = 21 * daily_sig ** 2
    N = 4000
    est = {"close-close": [], "parkinson": [], "garman_klass": [], "rogers_satchell": []}
    prev_c = 100.0
    for _ in range(N):
        (o, h, l, c), rets = make_month(daily_sig)
        est["close-close"].append(math.log(c / prev_c) ** 2)   # 1-return estimator
        est["parkinson"].append(parkinson(h, l))
        est["garman_klass"].append(garman_klass(o, h, l, c))
        est["rogers_satchell"].append(rogers_satchell(o, h, l, c))
        prev_c = c

    def stats(v):
        m = sum(v) / len(v)
        sd = (sum((x - m) ** 2 for x in v) / len(v)) ** 0.5
        return m, sd

    print(f"true monthly variance = {true_month_var:.6f}  (zero drift, no jump)")
    print(f"{'estimator':<16}{'mean':>12}{'std(noise)':>14}{'efficiency':>12}")
    base_sd = stats(est['close-close'])[1]
    baseline = {}
    for name, v in est.items():
        m, sd = stats(v)
        baseline[name] = m
        eff = (base_sd / sd) ** 2 if sd > 0 else float('inf')
        print(f"  {name:<14}{m:>12.6f}{sd:>14.6f}{eff:>11.1f}x")
    print("  NOTE: range estimators read ~30-40% LOW here -- discrete-sampling bias")
    print("        (21 daily obs miss the true continuous high/low). Real low-freq caveat.")

    # ---- drift robustness: measure CHANGE vs each estimator's own no-drift baseline
    # (isolates drift sensitivity from the discretization bias above). RS robust.
    print("\ndrift sensitivity (+0.004/day): % change vs each estimator's no-drift mean")
    drift = 0.004
    for name, fn in [("parkinson", lambda o, h, l, c: parkinson(h, l)),
                     ("garman_klass", garman_klass),
                     ("rogers_satchell", rogers_satchell)]:
        vals, prev_c = [], 100.0
        for _ in range(N):
            (o, h, l, c), _ = make_month(daily_sig, drift=drift)
            vals.append(fn(o, h, l, c))
            prev_c = c
        m = sum(vals) / len(vals)
        chg = (m - baseline[name]) / baseline[name] * 100
        tag = "  <- drift-contaminated" if abs(chg) > 40 else ("  <- drift-robust" if abs(chg) < 25 else "")
        print(f"  {name:<16} {chg:+.1f}%{tag}")
