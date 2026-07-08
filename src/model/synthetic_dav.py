"""Realistic synthetic DAV panel with KNOWN attrition, erosion, and path-dependence.

Richer than synthetic_panel.py -- generates the structure needed to validate the
full engine before the work-PC move:
  - ATTRITION hazard A(t): logit depends on seasoning, log-balance, macro, ramadan,
    PLUS a PATH term (recent balance downtrend raises hazard) -> lets signatures beat
    raw features.
  - EROSION r(t): surviving accounts draw down balance at a rate driven by macro +
    seasoning (known drift) -> validates erosion.py.
  - SEGMENTS: vue (behavioural) vs garantie (contract-driven, stickier) -> validates
    per-segment handling.

Returns per-account records with the full monthly balance path so feature builders
(raw + signature) and both models can be validated against ground truth.
"""
from __future__ import annotations

import math
import random

TRUE_HAZARD = {"intercept": -3.2, "seasoning": -0.8, "logbal": -0.4,
               "macro": 0.35, "ramadan": 0.9, "path_downtrend": 1.5}
TRUE_EROSION = {"intercept": -0.004, "macro": -0.010, "seasoning": 0.002}  # monthly log-drift


def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x)) if x > -700 else 0.0


def generate(n_accounts=600, T=120, seed=0):
    rng = random.Random(seed)
    macro, m = [], 0.0
    for _ in range(T):
        m = 0.85 * m + rng.gauss(0, 0.6)
        macro.append(m)
    ramadan = [rng.uniform(0.5, 1.0) if (t + t // 12) % 12 == 0 else 0.0 for t in range(T)]

    accts = []
    for a in range(n_accounts):
        seg = "garantie" if rng.random() < 0.3 else "vue"
        entry = rng.randint(0, T - 18)
        season0 = rng.randint(0, 60)
        lb = rng.gauss(7.0, 1.4)
        path = {}              # month -> log-balance
        event = None
        hz_scale = 0.4 if seg == "garantie" else 1.0       # garantie stickier
        prev_lb = lb
        for t in range(entry, T):
            season = season0 + (t - entry)
            # erosion drift on the (surviving) balance
            drift = (TRUE_EROSION["intercept"] + TRUE_EROSION["macro"] * macro[t]
                     + TRUE_EROSION["seasoning"] * (season / 100.0))
            lb = lb + drift + rng.gauss(0, 0.03)
            path[t] = lb
            downtrend = max(0.0, prev_lb - lb)             # path feature: recent drop
            prev_lb = lb
            logit = (TRUE_HAZARD["intercept"]
                     + TRUE_HAZARD["seasoning"] * ((season - 60) / 40)
                     + TRUE_HAZARD["logbal"] * ((lb - 7) / 1.4)
                     + TRUE_HAZARD["macro"] * macro[t]
                     + TRUE_HAZARD["ramadan"] * ramadan[t]
                     + TRUE_HAZARD["path_downtrend"] * downtrend)
            if rng.random() < hz_scale * _sigmoid(logit):
                event = t
                break
        accts.append({"id": a, "seg": seg, "entry": entry, "season0": season0,
                      "path": path, "event": event})
    return {"accts": accts, "macro": macro, "ramadan": ramadan, "T": T}


def to_hazard_rows(data, with_path=False):
    """Person-month rows for the attrition hazard. with_path adds the recent
    log-balance downtrend (the path feature raw level can't see)."""
    macro, ram = data["macro"], data["ramadan"]
    rows = []
    for ac in data["accts"]:
        months = sorted(ac["path"])
        for k, t in enumerate(months):
            season = ac["season0"] + (t - ac["entry"])
            lb = ac["path"][t]
            r = {"account": ac["id"], "month_int": t, "seg": ac["seg"],
                 "event": 1 if ac["event"] == t else 0,
                 "seasoning": (season - 60) / 40.0, "log_balance": (lb - 7) / 1.4,
                 "macro": macro[t], "ramadan": ram[t],
                 "weight": math.exp(lb)}
            if with_path:
                prev_lb = ac["path"][months[k - 1]] if k > 0 else lb
                r["downtrend"] = max(0.0, prev_lb - lb)
            rows.append(r)
    return rows


def to_erosion_rows(data):
    """Monthly log-balance INCREMENTS on surviving accounts (the erosion target)."""
    macro = data["macro"]
    rows = []
    for ac in data["accts"]:
        months = sorted(ac["path"])
        for k in range(1, len(months)):
            t, tp = months[k], months[k - 1]
            if t != tp + 1:
                continue
            season = ac["season0"] + (t - ac["entry"])
            rows.append({"account": ac["id"], "month_int": t, "seg": ac["seg"],
                         "d_logbal": ac["path"][t] - ac["path"][tp],
                         "macro": macro[t], "seasoning": season / 100.0,
                         "weight": math.exp(ac["path"][tp])})
    return rows


if __name__ == "__main__":
    d = generate(seed=1)
    hr = to_hazard_rows(d, with_path=True)
    er = to_erosion_rows(d)
    n_ev = sum(r["event"] for r in hr)
    segs = {}
    for ac in d["accts"]:
        segs[ac["seg"]] = segs.get(ac["seg"], 0) + 1
    print(f"accounts={len(d['accts'])} segments={segs}")
    print(f"hazard rows={len(hr)} events={n_ev} rate={n_ev/len(hr):.4f}")
    print(f"erosion rows={len(er)} mean d_logbal={sum(r['d_logbal'] for r in er)/len(er):+.5f} "
          f"(true intercept {TRUE_EROSION['intercept']})")
    print(f"TRUE hazard coefs: {TRUE_HAZARD}")
    print(f"TRUE erosion coefs: {TRUE_EROSION}")
