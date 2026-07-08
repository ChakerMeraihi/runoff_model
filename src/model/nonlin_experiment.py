"""nonlin_experiment.py -- a HARD, non-Markovian synthetic where nonlinearity genuinely
matters, so we can TRULY test whether path/interaction features + a booster help. Pure stdlib.

The earlier demo synthetic was Markovian (current log-balance absorbs the path) -> signatures
CANNOT help by construction, which rigs the test. Here the attrition hazard depends on:
  (P) PATH: the account's balance DRAWDOWN (trailing-max vs now) and DECLINE STREAK
      -> NOT recoverable from the current level alone (two accounts at the same balance but
         different histories have different risk).
  (I) STATIC INTERACTION: a threshold product of CURRENT features (oil x low-seasoning)
      -> a tree can build it from raw levels; a linear-in-logit model cannot.

Then we fit four models OUT-OF-SAMPLE:
  1. logistic on RAW LEVELS            -> sees neither P nor I
  2. logistic on levels + PATH/CHANGE  -> sees P (and I if we add the interaction feature); EXPLAINABLE
  3. GBM (LightGBM-style) on levels     -> builds I, misses P (no history in current x_t)
  4. GBM on levels + path/change        -> sees both

Expected ranking (and the honest lesson): #1 worst; #3 helps for the interaction but a booster
on CURRENT levels still MISSES path -> the win comes from FEATURE ENGINEERING (path/change),
not just model class; and the explainable GLM+features rivals the black box. That is the
governance-correct answer to "just use XGBoost": name the nonlinearity as features.
"""
from __future__ import annotations

import math
from random import Random

from hazard import LogisticElasticNet
from gbm import GBMHazard


# --------------------------------------------------------------------------- #
# hard DGP: an account-month panel with path + interaction driven attrition
# --------------------------------------------------------------------------- #
def generate_hard_panel(n_accounts=500, T=72, seed=0):
    rng = Random(seed)
    # shared macro: an oil path with a drawdown episode (months 30-45)
    oil = []
    lvl = 100.0
    for t in range(T):
        shock = -3.0 if 30 <= t < 45 else 0.3
        lvl = max(20.0, lvl + shock + rng.gauss(0, 2.0))
        oil.append(lvl)
    oil_max = [max(oil[:t + 1]) for t in range(T)]
    oil_dd = [(oil_max[t] - oil[t]) / oil_max[t] for t in range(T)]   # oil drawdown (path)
    mm = [3.0 + 0.5 * math.sin(t / 9.0) + rng.gauss(0, 0.1) for t in range(T)]

    rows = []
    for a in range(n_accounts):
        open_t = rng.randint(0, T // 3)
        bal = rng.lognormvariate(8.0, 1.0)
        peak = bal
        streak = 0
        prev = bal
        seasoning = rng.randint(0, 40)
        alive = True
        for t in range(open_t, T):
            if not alive:
                break
            # balance path: mild drift + occasional decline runs (path structure)
            drift = -0.05 if (rng.random() < 0.25) else 0.01
            bal = max(1.0, bal * (1.0 + drift + rng.gauss(0, 0.05)))
            peak = max(peak, bal)
            streak = streak + 1 if bal < prev else 0
            prev = bal
            seasoning += 1
            bal_dd = (peak - bal) / peak                              # balance drawdown (path)
            low_season = 1.0 if seasoning < 18 else 0.0
            # HARD hazard: path terms + a threshold INTERACTION of current-ish features
            z = (-4.2
                 + 3.0 * bal_dd                                       # (P) drawdown
                 + 0.9 * (1.0 if streak >= 3 else 0.0)                # (P) decline streak
                 + 2.5 * (oil_dd[t] * low_season))                    # (I) interaction/threshold
            h = 1.0 / (1.0 + math.exp(-z))
            event = 1 if rng.random() < h else 0
            rows.append({
                "account_id": f"A{a}", "month_int": t, "event": event,
                # RAW LEVELS the models get by default:
                "log_balance": math.log(bal), "seasoning": float(seasoning),
                "oil": oil[t], "money_market": mm[t],
                # ground-truth path/interaction drivers (used to BUILD augmented feats):
                "_bal": bal, "_peak": peak,
            })
            if event:
                alive = False
    return rows, {"oil": oil, "oil_dd": oil_dd}


LEVEL_FEATS = ["log_balance", "seasoning", "oil", "money_market"]


def augment(rows, macro, month_key="month_int"):
    """Add EXPLAINABLE path/change/interaction features ('change w.r.t. something'):
    balance drawdown, decline streak, oil drawdown, and the oil x low-seasoning interaction.
    Computed causally from each account's own history + the shared macro path."""
    oil_dd = macro["oil_dd"]
    by_acc = {}
    for r in rows:
        by_acc.setdefault(r["account_id"], []).append(r)
    for _, rs in by_acc.items():
        rs.sort(key=lambda r: r[month_key])
        peak = -1e18
        prev = None
        streak = 0
        for r in rs:
            b = r["_bal"]
            peak = max(peak, b)
            streak = streak + 1 if (prev is not None and b < prev) else 0
            prev = b
            t = r[month_key]
            odd = oil_dd[t] if t < len(oil_dd) else 0.0
            low_season = 1.0 if r["seasoning"] < 18 else 0.0
            r["bal_drawdown"] = (peak - b) / peak if peak > 0 else 0.0
            r["decline_streak"] = float(streak)
            r["oil_drawdown"] = odd
            r["oil_x_lowseason"] = odd * low_season
    return ["bal_drawdown", "decline_streak", "oil_drawdown", "oil_x_lowseason"]


# --------------------------------------------------------------------------- #
def _nll(p, y):
    return -sum(y[i] * math.log(max(p[i], 1e-12)) + (1 - y[i]) * math.log(max(1 - p[i], 1e-12))
               for i in range(len(y))) / max(1, len(y))


def _auc(p, y):
    pos = [p[i] for i in range(len(y)) if y[i] == 1]
    neg = [p[i] for i in range(len(y)) if y[i] == 0]
    if not pos or not neg:
        return 0.5
    wins = ties = 0
    # rank-based (Mann-Whitney); subsample for speed on big sets
    for a in pos:
        for b in neg:
            if a > b:
                wins += 1
            elif a == b:
                ties += 1
    return (wins + 0.5 * ties) / (len(pos) * len(neg))


def _split(rows, month_key="month_int", test_frac=0.25):
    months = sorted({r[month_key] for r in rows})
    cut = months[int(len(months) * (1 - test_frac))]
    tr = [r for r in rows if r[month_key] < cut]
    te = [r for r in rows if r[month_key] >= cut]
    return tr, te


def _fit_eval(train, test, feats, kind):
    Xtr = [[r[f] for f in feats] for r in train]
    ytr = [r["event"] for r in train]
    Xte = [[r[f] for f in feats] for r in test]
    yte = [r["event"] for r in test]
    if kind == "logit":
        m = LogisticElasticNet(l1=1e-4, l2=1e-3, solver="cd", epochs=300).fit(Xtr, ytr)
    else:
        m = GBMHazard(n_estimators=80, max_depth=3, learning_rate=0.1,
                      min_child_weight=5.0).fit(Xtr, ytr)
    p = m.predict_proba(Xte)
    return {"nll": _nll(p, yte), "auc": _auc(p, yte)}


def run(seed=0, verbose=True):
    rows, macro = generate_hard_panel(seed=seed)
    aug = augment(rows, macro)
    train, test = _split(rows)
    res = {
        "logit_levels": _fit_eval(train, test, LEVEL_FEATS, "logit"),
        "logit_levels+path": _fit_eval(train, test, LEVEL_FEATS + aug, "logit"),
        "gbm_levels": _fit_eval(train, test, LEVEL_FEATS, "gbm"),
        "gbm_levels+path": _fit_eval(train, test, LEVEL_FEATS + aug, "gbm"),
    }
    if verbose:
        er = sum(r["event"] for r in rows) / len(rows)
        print(f"hard non-Markovian panel: {len(rows)} rows, event rate {er:.3f}")
        print(f"  {'model':<20} {'OOS NLL':>9} {'OOS AUC':>9}")
        for k, v in res.items():
            print(f"  {k:<20} {v['nll']:>9.4f} {v['auc']:>9.3f}")
    return res


def _self_test():
    res = run(seed=1, verbose=True)
    lin = res["logit_levels"]["auc"]
    lin_aug = res["logit_levels+path"]["auc"]
    gbm_aug = res["gbm_levels+path"]["auc"]
    # engineering the path/interaction features must BEAT the linear-on-levels model
    assert lin_aug > lin + 0.03, f"path features didn't help: {lin_aug} vs {lin}"
    # and the best augmented model clearly beats raw-levels linear
    assert max(lin_aug, gbm_aug) > lin + 0.05, "nonlinearity not recovered"
    # raw-levels linear should be the weakest (or tied worst) on AUC
    assert lin <= min(lin_aug, gbm_aug) + 1e-9
    print("nonlin_experiment self-test PASSED")
    print("  -> on non-Markovian data, engineered path/change features (explainable) + the")
    print("     booster BEAT logistic-on-raw-levels; the win is FEATURES, not just model class.")


if __name__ == "__main__":
    _self_test()
