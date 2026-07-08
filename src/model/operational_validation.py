"""Operational (deployment-grade) validation of the run-off pipeline -- stdlib.

This is NOT a unit test. It runs the system the way it will be DEPLOYED:

  - anchored WALK-FORWARD out-of-time: at each origin o, fit the hazard ONLY on
    person-months with month <= o, then forecast survival forward;
  - PIT / no leakage: the forward S(t|o) uses only info available at o -- seasoning
    increments deterministically, the Ramadan calendar is forward-known, but the
    macro rate is FROZEN at its o-value (a real scenario assumption) and balance is
    frozen at o; NO realized future is used to predict;
  - calibration on the FUTURE: predicted book S(t) vs realized run-off, out-of-time;
  - CONFORMAL band coverage on a later origin, calibrated on earlier origins;
  - ROBUSTNESS: the whole walk-forward repeated across seeds -> CIs, not points;
  - DETERMINISM: same seed -> byte-identical output.

Honest limit: real deployment sign-off needs the bank DAV panel (Layer 0). This
proves the METHODOLOGY is deployment-ready on a realistic synthetic panel.
"""
from __future__ import annotations

import math
import random
import statistics

from hazard import LogisticElasticNet
from conformal import conformal_q

TRUE = {"b0": -3.2, "season": -0.8, "logbal": -0.5, "macro": 0.4, "ram": 1.0}


def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x)) if x > -700 else 0.0


def gen_panel(seed, T=120, n_accounts=500):
    rng = random.Random(seed)
    macro, m = [], 0.0
    for _ in range(T):
        m = 0.85 * m + rng.gauss(0, 0.6)
        macro.append(m)
    ramadan = [rng.uniform(0.5, 1.0) if (t + t // 12) % 12 == 0 else 0.0 for t in range(T)]
    accts = []
    for _ in range(n_accounts):
        entry = rng.randint(0, T - 18)
        season0 = rng.randint(0, 60)
        lb = rng.gauss(7.0, 1.5)
        lbpath, event = {}, None
        for t in range(entry, T):
            lb += rng.gauss(0, 0.05)
            lbpath[t] = lb
            season = season0 + (t - entry)
            logit = (TRUE["b0"] + TRUE["season"] * ((season - 60) / 40)
                     + TRUE["logbal"] * ((lb - 7) / 1.5)
                     + TRUE["macro"] * macro[t] + TRUE["ram"] * ramadan[t])
            if rng.random() < _sigmoid(logit):
                event = t
                break
        accts.append({"entry": entry, "season0": season0, "lbpath": lbpath, "event": event})
    return accts, macro, ramadan, T


def _feat(season, lb, macro_t, ram_t):
    return [(season - 60) / 40.0, (lb - 7) / 1.5, macro_t, ram_t]


def _train_rows(accts, macro, ramadan, o):
    X, y, w = [], [], []
    for ac in accts:
        if ac["entry"] > o:
            continue
        last = ac["event"] if (ac["event"] is not None and ac["event"] <= o) else o
        for t in range(ac["entry"], last + 1):
            season = ac["season0"] + (t - ac["entry"])
            X.append(_feat(season, ac["lbpath"][t], macro[t], ramadan[t]))
            y.append(1 if ac["event"] == t else 0)
            w.append(math.exp(ac["lbpath"][t]))
    return X, y, w


def _st_curves(accts, macro, ramadan, model, o, H):
    alive = [ac for ac in accts if ac["entry"] <= o and (ac["event"] is None or ac["event"] > o)]
    per = []
    for ac in alive:
        bo = math.exp(ac["lbpath"][o])
        S, spath = 1.0, [1.0]
        for h in range(1, H + 1):
            t = o + h
            season = ac["season0"] + (t - ac["entry"])
            ram = ramadan[t] if t < len(ramadan) else 0.0
            hz = model.predict_proba([_feat(season, ac["lbpath"][o], macro[o], ram)])[0]
            S *= (1 - hz)
            spath.append(S)
        rpath = [1.0] + [1.0 if (ac["event"] is None or ac["event"] > o + h) else 0.0
                         for h in range(1, H + 1)]
        per.append((bo, spath, rpath))
    wsum = sum(p[0] for p in per) or 1e-12
    pred = [sum(bo * sp[h] for bo, sp, _ in per) / wsum for h in range(H + 1)]
    real = [sum(bo * rp[h] for bo, _, rp in per) / wsum for h in range(H + 1)]
    return pred, real


def walk_forward(seed, origins=(78, 90, 102), H=12, epochs=180):
    accts, macro, ramadan, T = gen_panel(seed)
    out = []
    for o in origins:
        X, y, w = _train_rows(accts, macro, ramadan, o)
        model = LogisticElasticNet(l1=0.0, l2=1e-6, lr=12.0, epochs=epochs).fit(X, y, w=w)
        pred, real = _st_curves(accts, macro, ramadan, model, o, H)
        out.append((o, pred, real))
    return out


def _ci(vals):
    p = statistics.mean(vals)
    se = statistics.pstdev(vals) / len(vals) ** 0.5 if len(vals) > 1 else 0.0
    return p, p - 1.96 * se, p + 1.96 * se


if __name__ == "__main__":
    R = 6
    H = 12
    origins = (78, 90, 102)
    alpha = 0.10

    oot_mae, conf_cov, h12_err = [], [], []
    for seed in range(R):
        res = walk_forward(seed, origins, H)
        errs = [abs(p - r) for _, pred, real in res for p, r in zip(pred, real)]
        oot_mae.append(statistics.mean(errs))
        h12_err.append(statistics.mean(abs(pred[H] - real[H]) for _, pred, real in res))
        # conformal: calibrate |resid| on first 2 origins, test coverage on last
        cal = [abs(p - r) for (_, pred, real) in res[:2] for p, r in zip(pred, real)]
        q = conformal_q(cal, alpha)
        _, predL, realL = res[-1]
        cov = sum(1 for p, r in zip(predL, realL) if abs(p - r) <= q) / len(predL)
        conf_cov.append(cov)

    # determinism
    d1 = walk_forward(0, origins, H)
    d2 = walk_forward(0, origins, H)
    deterministic = all(p1 == p2 for (_, a1, b1), (_, a2, b2) in zip(d1, d2)
                        for p1, p2 in zip(a1 + b1, a2 + b2))

    print("=" * 74)
    print(f"OPERATIONAL WALK-FORWARD VALIDATION  (R={R} seeds, origins={origins}, H={H})")
    print("=" * 74)
    m, lo, hi = _ci(oot_mae)
    print(f"out-of-time book S(t) MAE     : {m:.4f}  95% CI [{lo:.4f},{hi:.4f}]")
    m, lo, hi = _ci(h12_err)
    print(f"out-of-time |err| at H={H}      : {m:.4f}  95% CI [{lo:.4f},{hi:.4f}]")
    m, lo, hi = _ci(conf_cov)
    print(f"conformal band coverage (t={1-alpha:.2f}): {m:.3f}  95% CI [{lo:.3f},{hi:.3f}]")
    print(f"determinism (same seed identical): {deterministic}")
    print("\nexample walk-forward curve (seed 0, last origin):")
    _, pred, real = walk_forward(0, origins, H)[-1]
    print("  h   pred_S   real_S   |err|")
    for h in range(0, H + 1, 3):
        print(f"  {h:<3} {pred[h]:.3f}    {real[h]:.3f}    {abs(pred[h]-real[h]):.3f}")
