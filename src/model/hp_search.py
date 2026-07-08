"""Hyper-parameter search for the EN-logistic hazard (pure stdlib) -- PLANv2 6.3 / Gate B.

glmnet-style lambda-path x alpha-grid, selected by mean WALK-FORWARD out-of-time
score (Brier / NLL -- proper scoring rules, NOT AUC: a run-off model is judged on
calibration). Deterministic and auditable -- the right tool for 2-3 HPs and for
model-validation governance, not a stochastic Optuna/TPE.

PARALLEL: the grid is embarrassingly parallel (each cell independent). We use the
stdlib `multiprocessing` Pool -> true multi-core CPU speedup (separate processes,
no GIL), allowed on the locked-down PC. Set n_jobs=1 to force serial.

Reported OOT score at the selected HP is OPTIMISTIC (selection bias); the honest
number is the frozen-OOS backtest in runoff_refit. lr/epochs are optimization
settings (fixed for convergence), not tuned.
"""
from __future__ import annotations

import math
import os
from multiprocessing import Pool

from hazard import LogisticElasticNet
from splitter import walk_forward


def brier(p, y):
    return sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / len(p)


def nll(p, y):
    s = 0.0
    for pi, yi in zip(p, y):
        pi = min(max(pi, 1e-12), 1 - 1e-12)
        s += -(yi * math.log(pi) + (1 - yi) * math.log(1 - pi))
    return s / len(p)


def _lambda_path(n_lam=8, lo=1e-5, hi=1e-1):
    r = (hi / lo) ** (1.0 / (n_lam - 1))
    return [lo * r ** i for i in range(n_lam)]


# globals populated per worker process (avoid re-pickling the data per cell)
_G = {}


def _init_worker(folds_tr_te, features, event_key, weight_key, lr, epochs, score, max_irls):
    _G.update(folds=folds_tr_te, features=features, event_key=event_key,
              weight_key=weight_key, lr=lr, epochs=epochs, max_irls=max_irls,
              scorer=(nll if score == "nll" else brier), score=score)


def _eval_cell(cell):
    """Evaluate one (lambda, alpha) cell -> mean walk-forward OOT score. Top-level
    (picklable) so multiprocessing on Windows (spawn) can dispatch it. Uses light CD
    (few IRLS steps) -- ranking cells needs ordering, not full convergence."""
    lam, alpha = cell
    g = _G
    feats, ek, wk = g["features"], g["event_key"], g["weight_key"]
    l1, l2 = lam * alpha, lam * (1 - alpha)
    fold_scores = []
    for tr, te in g["folds"]:
        if not tr or not te:
            continue
        Xtr = [[r[f] for f in feats] for r in tr]
        ytr = [r[ek] for r in tr]
        wtr = [r[wk] for r in tr] if wk else None
        m = LogisticElasticNet(l1=l1, l2=l2, solver="cd", max_irls=g["max_irls"],
                               epochs=g["epochs"]).fit(Xtr, ytr, w=wtr)
        Xte = [[r[f] for f in feats] for r in te]
        yte = [r[ek] for r in te]
        fold_scores.append(g["scorer"](m.predict_proba(Xte), yte))
    if not fold_scores:
        return None
    mean = sum(fold_scores) / len(fold_scores)
    if len(fold_scores) > 1:
        var = sum((s - mean) ** 2 for s in fold_scores) / (len(fold_scores) - 1)
        se = (var / len(fold_scores)) ** 0.5            # SE of the mean across folds
    else:
        se = 0.0
    return {"lambda": lam, "alpha": alpha, g["score"]: mean, "se": se,
            "n_folds": len(fold_scores)}


def select_1se(table, score):
    """glmnet 1-SE rule: among cells whose mean OOT score is within 1 SE of the best
    cell's score, pick the MOST REGULARIZED (largest lambda; tie-break larger alpha =
    sparser). Overfit-safe, and stable when the HP surface is flat (raw argmin would
    chase noise). Returns (best_min, best_1se)."""
    best_min = min(table, key=lambda c: c[score])
    thresh = best_min[score] + best_min.get("se", 0.0)
    within = [c for c in table if c[score] <= thresh]
    best_1se = max(within, key=lambda c: (c["lambda"], c["alpha"]))
    fmt = lambda c: {"lambda": c["lambda"], "alpha": c["alpha"], "score": c[score]}
    return fmt(best_min), fmt(best_1se)


def hp_search(rows, features, month_key="month_int", event_key="event",
              weight_key=None, H=1, min_train=60, step=12, val_len=12,
              alphas=(0.0, 0.25, 0.5, 0.75, 1.0), lambdas=None,
              lr=12.0, epochs=60, max_irls=10, score="nll", rule="1se",
              n_jobs=None, verbose=False):
    """Grid-search (lambda, alpha) by mean walk-forward OOT score, parallel across
    CPU cores (stdlib multiprocessing). n_jobs=None -> all cores; 1 -> serial.
    rule='1se' (overfit-safe, default) or 'min' (raw argmin). Returns (best, table)
    where best carries both selections."""
    lambdas = lambdas or _lambda_path()
    months = sorted({r[month_key] for r in rows})
    # Adaptive windowing: on a SHORT panel (fewer than min_train+val_len+1 distinct
    # months) shrink the walk-forward window so at least one fold still fits, instead of
    # failing outright. This ONLY triggers when the panel is too short for the defaults --
    # long panels keep the validated 60/12 behaviour byte-for-byte. Lets the multi-product
    # book and a first-run recalibration work on the shorter real EFM history.
    n_months = len(months)
    if n_months < min_train + val_len + 1:
        val_len = max(3, min(val_len, n_months // 4))
        min_train = max(6, min(min_train, n_months - val_len - 1))
        step = max(1, min(step, val_len))
    fold_months = walk_forward(months, H, min_train=min_train, step=step, val_len=val_len)
    by_month = {}
    for r in rows:
        by_month.setdefault(r[month_key], []).append(r)
    # materialize folds as (train_rows, test_rows) once, so workers don't re-subset
    folds = []
    for tr_m, te_m in fold_months:
        tr = [r for m in tr_m for r in by_month.get(m, [])]
        te = [r for m in te_m for r in by_month.get(m, [])]
        folds.append((tr, te))

    cells = [(lam, alpha) for alpha in alphas for lam in lambdas]
    init_args = (folds, features, event_key, weight_key, lr, epochs, score, max_irls)
    n_jobs = (os.cpu_count() or 1) if n_jobs is None else n_jobs

    if n_jobs == 1:
        _init_worker(*init_args)
        results = [_eval_cell(c) for c in cells]
    else:
        with Pool(processes=n_jobs, initializer=_init_worker, initargs=init_args) as pool:
            results = pool.map(_eval_cell, cells)

    table = [r for r in results if r is not None]
    if not table:
        raise ValueError(
            f"hp_search produced no scored folds: {len(months)} months, "
            f"min_train={min_train}, val_len={val_len}. Panel too short for walk-forward "
            f"(need > min_train+val_len months). Lower min_train or use a longer panel.")
    if verbose:
        for c in sorted(table, key=lambda d: d[score]):
            print(f"  alpha={c['alpha']:.2f} lambda={c['lambda']:.1e} "
                  f"{score}={c[score]:.5f} +/- {c.get('se', 0):.5f}")
    best_min, best_1se = select_1se(table, score)
    best = dict(best_1se if rule == "1se" else best_min)   # copy: avoid self-reference
    best["rule"] = rule
    best["best_min"] = best_min
    best["best_1se"] = best_1se
    return best, table


if __name__ == "__main__":
    import time
    from synthetic_panel import generate, FEATURES

    rows, _ = generate(n_accounts=1000, horizon=120, seed=3)
    for r in rows:
        r["month_int"] = r["month"]          # splitter convention

    n_cpu = os.cpu_count() or 1
    print(f"CPU cores available: {n_cpu}")

    t0 = time.time()
    best1, _ = hp_search(rows, list(FEATURES), weight_key="weight", score="nll",
                         epochs=60, n_jobs=1)
    t_serial = time.time() - t0

    t0 = time.time()
    best, table = hp_search(rows, list(FEATURES), weight_key="weight", score="nll",
                            epochs=60, n_jobs=None)
    t_par = time.time() - t0

    print(f"serial:   {t_serial:5.1f}s")
    print(f"parallel: {t_par:5.1f}s  ({n_cpu} procs)  speedup x{t_serial/max(t_par,1e-9):.1f}")
    print(f"\nraw argmin : {best['best_min']}")
    print(f"1-SE rule  : {best['best_1se']}   <- deployed (overfit-safe)")
    tbl = sorted(table, key=lambda d: d["nll"])
    flat = tbl[-1]["nll"] - tbl[0]["nll"]
    print(f"grid NLL spread (max-min): {flat:.5f}  -> "
          f"{'FLAT surface; 1-SE picks the parsimonious model' if flat < 0.01 else 'real structure'}")
    print("\nNOTE: this OOT NLL is optimistic (grid selection); the honest number is "
          "the frozen-OOS backtest in runoff_eval.")
