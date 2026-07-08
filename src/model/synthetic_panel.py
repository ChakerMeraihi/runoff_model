"""Synthetic DAV survival panel with a KNOWN discrete-time hazard (pure stdlib).

Ground-truth harness to validate the hazard estimator and the S(t) aggregation.
Each account is observed monthly from its entry seasoning until it attrites
(event=1) or is right-censored at the horizon. The per-month hazard is

    logit h = b0 + b_seasoning*z_seasoning + b_logbal*z_logbal
                 + b_macro*z_macro + b_ramadan*ramadan_frac

with features standardized as z_*. Balances persist (random walk) and give the
balance weight. Left-truncation: accounts enter at seasoning > 0.
"""
from __future__ import annotations

import math
import random

FEATURES = ("z_seasoning", "z_logbal", "z_macro", "ramadan_frac")
TRUE_COEF = {
    "intercept": -3.0,     # base monthly hazard ~ 4.7%
    "z_seasoning": -0.8,   # hazard falls with tenure
    "z_logbal": -0.5,      # bigger balances stickier
    "z_macro": 0.4,        # higher money-market rate -> more attrition
    "ramadan_frac": 1.0,   # Ramadan spending -> more attrition
}


def _sigmoid(x):
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def generate(n_accounts=4000, horizon=120, seed=0):
    """Return (rows, true_coef). rows = list of dicts (one per person-month at risk)."""
    rng = random.Random(seed)
    # a macro series (standardized) and a ramadan_frac series over the horizon
    macro = [rng.gauss(0, 1) for _ in range(horizon)]
    ramadan = [0.0] * horizon
    for t in range(horizon):
        # crude recurring Ramadan: ~1 month per 12, drifting
        if (t - (t // 12)) % 12 in (0,):
            ramadan[t] = rng.uniform(0.5, 1.0)

    rows = []
    for acc in range(n_accounts):
        entry = rng.randint(0, horizon - 12)          # left-truncation
        seasoning0 = rng.randint(0, 60)               # months already open at entry
        log_bal = rng.gauss(7.0, 1.5)                 # log balance (KDA)
        for t in range(entry, horizon):
            seasoning = seasoning0 + (t - entry)
            log_bal += rng.gauss(0, 0.05)             # balance random walk
            z_season = (seasoning - 60) / 40.0
            z_logbal = (log_bal - 7.0) / 1.5
            z_macro = macro[t]
            ram = ramadan[t]
            logit = (TRUE_COEF["intercept"]
                     + TRUE_COEF["z_seasoning"] * z_season
                     + TRUE_COEF["z_logbal"] * z_logbal
                     + TRUE_COEF["z_macro"] * z_macro
                     + TRUE_COEF["ramadan_frac"] * ram)
            h = _sigmoid(logit)
            event = 1 if rng.random() < h else 0
            rows.append({
                "account": acc, "month": t, "event": event,
                "z_seasoning": z_season, "z_logbal": z_logbal,
                "z_macro": z_macro, "ramadan_frac": ram,
                "weight": math.exp(log_bal),
            })
            if event:
                break                                  # attrited -> leaves the panel
    return rows, dict(TRUE_COEF)


def to_xy(rows, features=FEATURES):
    X = [[r[f] for f in features] for r in rows]
    y = [r["event"] for r in rows]
    w = [r["weight"] for r in rows]
    return X, y, w


if __name__ == "__main__":
    rows, true = generate()
    n_ev = sum(r["event"] for r in rows)
    print(f"rows={len(rows)} accounts-events={n_ev} "
          f"event_rate={n_ev/len(rows):.4f} true_coef={true}")
