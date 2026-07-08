"""param_uncertainty.py -- PARAMETER uncertainty of the run-off via a time-block bootstrap
of the panel (pure stdlib). On ~120 monthly observations the binding uncertainty on B(t) is
not the macro path (montecarlo.py) but the COEFFICIENTS: refit the hazard+erosion on resampled
histories and see how much B(t) moves.

Design: this module owns only the GENERIC machinery -- moving-block resampling of the month
index and percentile aggregation -- and takes a `draw_fn(resampled_rows) -> B_curve` callback
that does the fit+roll of the CURRENT (fixed) cohort. The orchestration (runoff_book) injects
the real fit_hazard + fit_erosion + roll_book_fast; the self-test injects a light analytic
draw. That keeps this module dependency-free and unit-testable, and avoids an upward import.

Moving-block (not iid) because monthly balances are serially dependent -- resampling single
months would destroy the autocorrelation and understate the bands. Block joins break a few
per-account erosion increments (the consecutive-month guard skips them) -- acceptable.
"""
from __future__ import annotations

from random import Random


def moving_block_months(months, block, rng):
    """Resample a same-length month timeline from contiguous blocks (with replacement)."""
    n = len(months)
    if n == 0:
        return []
    block = max(1, min(block, n))
    out = []
    while len(out) < n:
        start = rng.randint(0, n - block)
        out.extend(months[start:start + block])
    return out[:n]


def _percentile(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = k - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def bootstrap_runoff_fan(rows, month_key, draw_fn, n_boot=40, block=6, seed=0,
                         levels=(5, 50, 95)):
    """Time-block bootstrap of the panel -> distribution of B(t). draw_fn(resampled_rows)
    must return a run-off curve B[0..H] (or None to skip a failed draw). Returns per-horizon
    percentile bands + a WAL confidence interval."""
    rng = Random(seed)
    months = sorted({r[month_key] for r in rows})
    by_m = {}
    for r in rows:
        by_m.setdefault(r[month_key], []).append(r)
    draws = []
    for _ in range(n_boot):
        sel = moving_block_months(months, block, rng)
        resampled = [r for m in sel for r in by_m[m]]
        bc = draw_fn(resampled)
        if bc:
            draws.append(bc)
    if not draws:
        return None
    H = min(len(d) for d in draws) - 1
    band = {lv: [] for lv in levels}
    for t in range(H + 1):
        col = sorted(d[t] for d in draws)
        for lv in levels:
            band[lv].append(_percentile(col, lv))
    wal = sorted(sum(d[1:H + 1]) for d in draws)
    return {"n_boot": len(draws), "levels": list(levels), "H": H,
            "B_pct": band, "wal_pct": {lv: _percentile(wal, lv) for lv in levels},
            "wal_draws": wal}


def combine_with_macro(param_fan, macro_fan, levels=(5, 50, 95)):
    """Rough total-uncertainty combine of the PARAMETER fan (this module) and a MACRO-path
    fan (montecarlo.py), assuming approximate independence: widen the parameter band by the
    macro band's half-width at each horizon (added in quadrature around the shared median).
    Honest approximation -- documented as such; the joint bootstrap-through-MC is the exact
    but far more expensive object."""
    if not param_fan or not macro_fan:
        return param_fan
    lo, mid, hi = levels
    pB, mB = param_fan["B_pct"], macro_fan.get("B_pct") or macro_fan
    H = min(param_fan["H"], len(mB[mid]) - 1)
    out = {lv: [] for lv in levels}
    for t in range(H + 1):
        m = pB[mid][t]
        hw_p_hi, hw_p_lo = pB[hi][t] - m, m - pB[lo][t]
        hw_m_hi = (mB[hi][t] - mB[mid][t]) if mid in mB else 0.0
        hw_m_lo = (mB[mid][t] - mB[lo][t]) if mid in mB else 0.0
        out[mid].append(m)
        out[hi].append(m + (hw_p_hi ** 2 + hw_m_hi ** 2) ** 0.5)
        out[lo].append(m - (hw_p_lo ** 2 + hw_m_lo ** 2) ** 0.5)
    return {"n_boot": param_fan["n_boot"], "levels": list(levels), "H": H, "B_pct": out,
            "note": "parameter (+) macro half-widths in quadrature (approx independent)"}


# --------------------------------------------------------------------------- #
def _self_test():
    # synthetic panel: 120 months, ~200 accounts, a small monthly attrition hazard.
    rng = Random(0)
    rows = []
    for m in range(120):
        for a in range(200):
            rows.append({"month_int": m, "event": 1 if rng.random() < 0.01 else 0})

    # light analytic draw: hazard = resampled mean event rate; B(t) = (1-h)^t over 24m
    def draw_fn(resampled):
        n = len(resampled)
        if not n:
            return None
        h = sum(r["event"] for r in resampled) / n
        return [(1.0 - h) ** t for t in range(25)]

    fan = bootstrap_runoff_fan(rows, "month_int", draw_fn, n_boot=60, block=6, seed=1)
    assert fan and fan["n_boot"] == 60
    lo, mid, hi = fan["B_pct"][5], fan["B_pct"][50], fan["B_pct"][95]
    # bands ordered at every horizon, and non-degenerate (real sampling spread)
    for t in range(fan["H"] + 1):
        assert lo[t] <= mid[t] <= hi[t], (t, lo[t], mid[t], hi[t])
    assert hi[fan["H"]] - lo[fan["H"]] > 1e-4, "band collapsed -> bootstrap not varying"
    # full-sample point estimate lies inside the band
    h_full = sum(r["event"] for r in rows) / len(rows)
    B24 = (1 - h_full) ** 24
    assert lo[24] <= B24 <= hi[24], (lo[24], B24, hi[24])
    # WAL CI ordered
    w = fan["wal_pct"]
    assert w[5] <= w[50] <= w[95]

    # moving-block basic properties
    mb = moving_block_months(list(range(120)), 6, Random(2))
    assert len(mb) == 120 and min(mb) >= 0 and max(mb) <= 119

    # combine widens the band
    macro = {"B_pct": {5: [x * 0.99 for x in mid], 50: mid, 95: [x * 1.01 for x in mid]}}
    comb = combine_with_macro(fan, macro)
    assert comb["B_pct"][95][12] >= hi[12] - 1e-9

    print("param_uncertainty self-test PASSED")
    print(f"  {fan['n_boot']} block-bootstrap draws; WAL 90% CI = "
          f"[{w[5]:.2f}, {w[95]:.2f}] mo (median {w[50]:.2f})")
    print(f"  B(24) band = [{lo[24]:.3f}, {hi[24]:.3f}], point {B24:.3f} inside")


if __name__ == "__main__":
    _self_test()
