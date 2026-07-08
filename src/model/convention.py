"""Convention baseline: core/volatile decomposition + run-off (pure stdlib) -- PLANv2 5a.

The regulator-recognised reference for non-maturing deposits (the floor the behavioural
model must beat). Split each account's (or the book's) balance into:
  core     = the stable floor held through time  -> long behavioural maturity
  volatile = the rest                            -> short run-off
and amortise each sleeve. The book run-off is then

  B(h) = [ core * core_decay(h) + volatile * vol_decay(h) ] / (core + volatile)

This is a *stock decomposition*, not a cohort survival model -- which is exactly why we
benchmark it against the account-level hazard (Gate B): if the simple core/volatile
curve matches realised run-off out-of-time, the behavioural model has to earn its extra
complexity.

Core estimation = trailing minimum (a robust floor) by default, or a low quantile.
Amortisation = linear: volatile over `vol_life` months, core over `core_life` months
(core_life >> horizon -> core is ~flat over a 12m view, the classic treatment).
"""
from __future__ import annotations


def _quantile(xs, q):
    s = sorted(xs)
    if not s:
        return 0.0
    pos = q * (len(s) - 1)
    lo = int(pos)
    frac = pos - lo
    return s[lo] * (1 - frac) + s[lo + 1] * frac if lo + 1 < len(s) else s[lo]


def split_core_volatile(series, window=12, method="min", q=0.10):
    """One balance series -> (core_level, volatile_level) at its last point.
    core = trailing min (method='min') or trailing q-quantile (method='quantile')
    over the last `window` points; volatile = max(0, last - core)."""
    if not series:
        return 0.0, 0.0
    w = series[-window:] if len(series) > window else series
    core = min(w) if method == "min" else _quantile(w, q)
    core = max(0.0, core)
    last = max(0.0, series[-1])
    core = min(core, last)
    return core, last - core


def book_core_volatile(account_series, window=12, method="min", q=0.10):
    """account_series = list of per-account balance series (each oldest->newest).
    Returns (book_core, book_volatile) summed across accounts."""
    bc = bv = 0.0
    for s in account_series:
        c, v = split_core_volatile(s, window=window, method=method, q=q)
        bc += c
        bv += v
    return bc, bv


def _decay(h, life):
    """Linear amortisation to zero over `life` months (clamped to [0,1])."""
    if life <= 0:
        return 0.0 if h > 0 else 1.0
    return max(0.0, 1.0 - h / float(life))


def convention_runoff(core, volatile, H, vol_life=3, core_life=60):
    """Book run-off B(0..H) from a core/volatile split (B[0]=1)."""
    tot = core + volatile
    if tot <= 0:
        return [1.0] + [0.0] * H
    return [(core * _decay(h, core_life) + volatile * _decay(h, vol_life)) / tot
            for h in range(H + 1)]


def book_convention_runoff(account_series, H, window=12, vol_life=3, core_life=60,
                           method="min", q=0.10):
    """End-to-end: per-account trailing balances -> book core/volatile -> run-off B(0..H).
    Returns (B, core_share)."""
    core, vol = book_core_volatile(account_series, window=window, method=method, q=q)
    B = convention_runoff(core, vol, H, vol_life=vol_life, core_life=core_life)
    share = core / (core + vol) if (core + vol) > 0 else 0.0
    return B, share


if __name__ == "__main__":
    import random
    rng = random.Random(0)

    # synthetic book: each account has a stable core + a transient volatile top-up.
    # ground-truth core share ~ 0.7; volatile should run off fast, core slow.
    accounts = []
    true_core_total = true_vol_total = 0.0
    for _ in range(400):
        core = rng.lognormvariate(7.0, 0.8)
        vol = core * rng.uniform(0.1, 0.6)
        true_core_total += core
        true_vol_total += vol
        series = [core + vol * rng.uniform(0.8, 1.2) + rng.gauss(0, core * 0.02)
                  for _ in range(24)]
        accounts.append(series)

    bc, bv = book_core_volatile(accounts, window=12, method="min")
    true_share = true_core_total / (true_core_total + true_vol_total)
    est_share = bc / (bc + bv)
    print("core/volatile decomposition:")
    print(f"  true core share  ~ {true_share:.3f}")
    print(f"  est  core share    {est_share:.3f}  (trailing-min over 12m)")

    B, share = book_convention_runoff(accounts, H=12, vol_life=3, core_life=60)
    print(f"\nconvention run-off B(t) (core_life=60m, vol_life=3m):")
    print("  " + "  ".join(f"B({h})={B[h]:.3f}" for h in (0, 1, 3, 6, 12)))
    mono = all(B[i] >= B[i + 1] - 1e-12 for i in range(len(B) - 1))
    print(f"  monotone non-increasing: {mono}")
    print(f"  volatile gone by month 3: B(3)~core_share={B[3]:.3f} vs core_share {share:.3f} "
          f"-> {abs(B[3]-share) < 0.02}")
    print(f"  core barely moves at 12m (12/60 amortised): B(12)={B[12]:.3f} "
          f"(expect ~{share*(1-12/60):.3f})")
