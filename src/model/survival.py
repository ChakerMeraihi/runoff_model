"""Aggregate hazards -> book run-off S(t), with calibration + PIT -- PLANv2 6.4/6.6.

Given a fitted discrete-time hazard, for each alive account at decision time o we
roll the survival forward S_i(t) = prod_{h<=t}(1 - h_i(o+h)), then aggregate to the
book by balance weight:  S(t) = sum_i B_i(o) S_i(t) / sum_i B_i(o).

A run-off model lives or dies on CALIBRATION, not discrimination (PLANv2 6.4):
  reliability_table : predicted-vs-realized hazard by probability bin
  pit_values        : randomized PIT for discrete outcomes -> should be ~Uniform(0,1)
                      if the hazard is calibrated (KS-style check, stdlib)
  brier             : mean squared (pred - outcome), a proper scoring rule
"""
from __future__ import annotations

import math
import random


def survival_path(hazards):
    """hazards = [h_1,...,h_H] -> S = [1, (1-h1), (1-h1)(1-h2), ...] length H+1."""
    S, cur = [1.0], 1.0
    for h in hazards:
        cur *= (1.0 - h)
        S.append(cur)
    return S


def book_survival(per_account):
    """per_account = list of (balance_weight, [hazard_1..H]). Returns book S(t).
    Attrition-only run-off (account survival), ignores balance erosion."""
    H = len(per_account[0][1])
    paths = [(w, survival_path(hz)) for w, hz in per_account]
    wsum = sum(w for w, _ in paths) or 1e-12
    return [sum(w * sp[t] for w, sp in paths) / wsum for t in range(H + 1)]


def combined_runoff(per_account):
    """FULL run-off B(t) = balance-weighted A(t) x r(t)  (PLANv2 6.5).
    per_account = list of (balance0, [hazard_1..H], [retention_0..H]) where retention
    is r(t)=B(t)/B(0)|alive (retention[0]=1). Returns (B_t, A_t, r_t_avg):
      A_t   = balance-weighted account-survival (attrition only)
      r_t   = balance-weighted retention among survivors
      B_t   = balance-weighted A_t * r_t  -- the deployed run-off curve."""
    H = len(per_account[0][1])
    wsum = sum(b for b, _, _ in per_account) or 1e-12
    A_t, r_t, B_t = [], [], []
    for t in range(H + 1):
        a_num = r_num = b_num = 0.0
        for b0, hz, ret in per_account:
            S = survival_path(hz)[t]
            rr = ret[t] if t < len(ret) else ret[-1]
            a_num += b0 * S
            r_num += b0 * rr
            b_num += b0 * S * rr
        A_t.append(a_num / wsum)
        r_t.append(r_num / wsum)
        B_t.append(b_num / wsum)
    return B_t, A_t, r_t


def reliability_table(pred, outcome, n_bins=10, w=None):
    """Equal-count bins of predicted hazard vs realized event rate. With w (e.g. the
    balance weight), the per-bin means are balance-weighted -- the book-level
    calibration that matters for a whale-dominated aggregate (PLANv2 6.4)."""
    order = sorted(range(len(pred)), key=lambda i: pred[i])
    rows = []
    for b in range(n_bins):
        idx = order[b * len(order) // n_bins:(b + 1) * len(order) // n_bins]
        if not idx:
            continue
        if w is None:
            mp = sum(pred[i] for i in idx) / len(idx)
            mo = sum(outcome[i] for i in idx) / len(idx)
        else:
            sw = sum(w[i] for i in idx) or 1e-12
            mp = sum(w[i] * pred[i] for i in idx) / sw
            mo = sum(w[i] * outcome[i] for i in idx) / sw
        rows.append((b, len(idx), mp, mo))
    return rows


def calibration_error(pred, outcome, n_bins=10):
    """Expected Calibration Error: weighted |mean_pred - mean_outcome| over bins."""
    rows = reliability_table(pred, outcome, n_bins)
    n = len(pred)
    return sum(cnt * abs(mp - mo) for _, cnt, mp, mo in rows) / n


def brier(pred, outcome):
    return sum((p - y) ** 2 for p, y in zip(pred, outcome)) / len(pred)


def pit_values(pred, outcome, seed=0):
    """Randomized PIT for Bernoulli outcomes. For a calibrated model these are
    Uniform(0,1). For outcome y with P(event)=p:
       y=0 -> U(0, 1-p);  y=1 -> U(1-p, 1)."""
    rng = random.Random(seed)
    out = []
    for p, y in zip(pred, outcome):
        p = min(max(p, 1e-9), 1 - 1e-9)
        if y == 0:
            out.append(rng.uniform(0.0, 1.0 - p))
        else:
            out.append(rng.uniform(1.0 - p, 1.0))
    return out


def ks_uniform(values):
    """Kolmogorov-Smirnov distance of `values` to Uniform(0,1)."""
    s = sorted(values)
    n = len(s)
    d = 0.0
    for i, v in enumerate(s):
        d = max(d, abs((i + 1) / n - v), abs(v - i / n))
    return d


if __name__ == "__main__":
    from synthetic_panel import generate, to_xy, FEATURES
    from hazard import LogisticElasticNet

    # fit hazard on a synthetic panel, check calibration + PIT on held-out months
    rows, _ = generate(n_accounts=3000, horizon=120, seed=2)
    cut = 90
    tr = [r for r in rows if r["month"] < cut]
    te = [r for r in rows if r["month"] >= cut]
    Xtr, ytr, wtr = to_xy(tr)
    Xte, yte, wte = to_xy(te)
    model = LogisticElasticNet(l1=0.0, l2=1e-6, lr=12.0, epochs=400).fit(Xtr, ytr, w=wtr)
    p = model.predict_proba(Xte)

    print(f"out-of-time calibration (train<{cut}, test>={cut}):")
    print(f"  ECE={calibration_error(p, yte):.4f}  Brier={brier(p, yte):.4f}  "
          f"mean_pred={sum(p)/len(p):.4f} actual={sum(yte)/len(yte):.4f}")
    print("  reliability (bin, n, pred, actual):")
    for b, cnt, mp, mo in reliability_table(p, yte):
        print(f"    {b:<3} n={cnt:<5} pred={mp:.4f} actual={mo:.4f}")

    pit = pit_values(p, yte)
    ks = ks_uniform(pit)
    ks_crit = 1.358 / math.sqrt(len(pit))     # KS 95%
    print(f"\nPIT uniformity KS={ks:.4f} (95% crit {ks_crit:.4f}) -> "
          f"{'calibrated' if ks < ks_crit else 'miscalibrated'}")

    # book S(t): roll forward 12-month hazards for accounts alive at month `cut`
    alive = {}
    for r in rows:
        if r["month"] == cut and r["event"] == 0:
            alive[r["account"]] = r["weight"]
    # toy roll-forward: hold features at month cut, predict constant-feature hazard
    feats_at_cut = {r["account"]: [r[f] for f in FEATURES] for r in rows if r["month"] == cut}
    per = []
    for acc, w in list(alive.items())[:1500]:
        h = model.predict_proba([feats_at_cut[acc]])[0]
        per.append((w, [h] * 12))
    St = book_survival(per)
    print(f"\nbook S(t) (balance-weighted), 12-month roll-forward from month {cut}:")
    print("  " + "  ".join(f"S({t})={St[t]:.3f}" for t in (0, 3, 6, 9, 12)))
    print(f"  monotone non-increasing: {all(St[i] >= St[i+1] for i in range(len(St)-1))}")
