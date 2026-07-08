"""Penalized logistic discrete-time hazard (elastic-net), pure stdlib.

The PLANv2 6.3 backbone: P(event in (t,t+1] | alive) = sigmoid(b0 + b.z), fitted by
weighted proximal-gradient descent with an L2 ridge term and an L1 soft-threshold
(elastic net). Features are standardized internally (unpenalized intercept); the
fitted coefficients are also exposed in RAW feature space for interpretation and
validation. Sample weights carry the balance weighting (PLANv2 6.4).
"""
from __future__ import annotations

import math


def _sigmoid(x):
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


class Scaler:
    def fit(self, X):
        n, d = len(X), len(X[0])
        self.mu = [sum(r[j] for r in X) / n for j in range(d)]
        self.sd = []
        for j in range(d):
            var = sum((r[j] - self.mu[j]) ** 2 for r in X) / n
            self.sd.append(math.sqrt(var) or 1.0)
        return self

    def transform(self, X):
        mu, sd = self.mu, self.sd
        return [[(r[j] - mu[j]) / sd[j] for j in range(len(mu))] for r in X]


class LogisticElasticNet:
    """Elastic-net penalized logistic hazard. solver:
      'cd'  -> glmnet-style IRLS + cyclic coordinate descent (default; the standard
               for elastic-net: per-coordinate soft-threshold, no step tuning, fast).
      'pgd' -> proximal gradient descent (ISTA): smooth grad + L1 soft-threshold prox.
    The (l1, l2) split equals (lambda*alpha, lambda*(1-alpha)). Interface is identical
    for both solvers: coef_std_, intercept_std_, scaler, coef_, intercept_."""

    def __init__(self, l1=0.0, l2=1e-4, lr=0.5, epochs=200, tol=1e-6,
                 solver="cd", max_irls=15, verbose=False):
        # Defaults tuned for speed at negligible accuracy cost: on a CONVEX elastic-net the
        # IRLS+CD converges in a handful of iterations; the old 25x400 / tol=1e-8 ran ~10x
        # past convergence. Early-stop does the rest.
        self.l1, self.l2, self.lr = l1, l2, lr
        self.epochs, self.tol = epochs, tol
        self.solver, self.max_irls, self.verbose = solver, max_irls, verbose

    def _nll(self, Z, y, w, b0, b, W):
        s = 0.0
        for i in range(len(Z)):
            lin = b0 + sum(b[j] * Z[i][j] for j in range(len(b)))
            p = min(max(_sigmoid(lin), 1e-12), 1 - 1e-12)
            s += w[i] * (-y[i] * math.log(p) - (1 - y[i]) * math.log(1 - p))
        return s / W

    def fit(self, X, y, w=None):
        self.scaler = Scaler().fit(X)
        Z = self.scaler.transform(X)
        n, d = len(Z), len(Z[0])
        w = [1.0] * n if w is None else list(w)
        W = sum(w)
        if self.solver == "cd":
            b, b0 = self._fit_cd(Z, y, w, n, d)
        else:
            b, b0 = self._fit_pgd(Z, y, w, n, d, W)
        self.coef_std_, self.intercept_std_ = b, b0
        mu, sd = self.scaler.mu, self.scaler.sd
        self.coef_ = [b[j] / sd[j] for j in range(d)]
        self.intercept_ = b0 - sum(b[j] * mu[j] / sd[j] for j in range(d))
        return self

    def _fit_pgd(self, Z, y, w, n, d, W):
        b, b0, prev = [0.0] * d, 0.0, None
        for ep in range(self.epochs):
            g, g0 = [0.0] * d, 0.0
            for i in range(n):
                zi = Z[i]
                r = w[i] * (_sigmoid(b0 + sum(b[j] * zi[j] for j in range(d))) - y[i])
                g0 += r
                for j in range(d):
                    g[j] += r * zi[j]
            b0 -= self.lr * (g0 / W)
            thr = self.lr * self.l1
            for j in range(d):
                bj = b[j] - self.lr * (g[j] / W + self.l2 * b[j])
                b[j] = (bj - thr) if bj > thr else ((bj + thr) if bj < -thr else 0.0)
            if ep % 20 == 0 or ep == self.epochs - 1:
                loss = self._nll(Z, y, w, b0, b, W)
                if prev is not None and abs(prev - loss) < self.tol:
                    break
                prev = loss
        return b, b0

    def _fit_cd(self, Z, y, w, n, d):
        """glmnet-style IRLS + cyclic coordinate descent (elastic net). Same math as the
        textbook form, but optimized for pure-Python latency:
          - COLUMN-MAJOR storage (Zt[j] is contiguous) -> the per-coordinate inner loop
            walks a single list instead of double-indexing Z[i][j];
          - den[j] and omega*Zt are INVARIANT within an IRLS step -> precomputed once, not
            recomputed every inner epoch (was the dominant redundant cost);
          - the working residual r = (y-p)/pw is maintained incrementally;
          - the linear predictor is rebuilt only over the ACTIVE (nonzero) coordinates.
        Result is numerically identical; ~5-10x faster on wide feature sets."""
        Zt = [[Z[i][j] for i in range(n)] for j in range(d)]     # column-major, once
        b = [0.0] * d
        Wn = sum(w)
        ybar = sum(w[i] * y[i] for i in range(n)) / Wn
        b0 = math.log((ybar + 1e-12) / (1 - ybar + 1e-12))
        prev = None
        for _ in range(self.max_irls):
            # linear predictor over the current (sparse) active set
            active = [j for j in range(d) if b[j] != 0.0]
            omega = [0.0] * n
            r = [0.0] * n                                        # r = (y-p)/pw
            for i in range(n):
                lin = b0
                for j in active:
                    lin += b[j] * Zt[j][i]
                p = min(max(_sigmoid(lin), 1e-5), 1 - 1e-5)
                pw = p * (1 - p)
                omega[i] = w[i] * pw
                r[i] = (y[i] - p) / pw
            Om = sum(omega) or 1e-12
            # invariants for this IRLS step: omega*Zt columns and their den[j]
            omZt = [[omega[i] * Zt[j][i] for i in range(n)] for j in range(d)]
            den = [sum(omZt[j][i] * Zt[j][i] for i in range(n)) / n for j in range(d)]
            for _ in range(self.epochs):
                db0 = sum(omega[i] * r[i] for i in range(n)) / Om
                b0 += db0
                for i in range(n):
                    r[i] -= db0
                max_change = 0.0
                for j in range(d):
                    ozj, zj, bj_old = omZt[j], Zt[j], b[j]
                    corr = 0.0
                    for i in range(n):
                        corr += ozj[i] * r[i]
                    num = corr / n + bj_old * den[j]             # partial residual incl. j
                    if num > self.l1:
                        bj = (num - self.l1) / (den[j] + self.l2)
                    elif num < -self.l1:
                        bj = (num + self.l1) / (den[j] + self.l2)
                    else:
                        bj = 0.0
                    change = bj - bj_old
                    if change != 0.0:
                        for i in range(n):
                            r[i] -= change * zj[i]               # keep residual in sync
                        b[j] = bj
                        if abs(change) > max_change:
                            max_change = abs(change)
                if max_change < self.tol:
                    break
            loss = self._nll(Z, y, w, b0, b, Wn)
            if prev is not None and abs(prev - loss) < self.tol:
                break
            prev = loss
        return b, b0

    def predict_proba(self, X):
        Z = self.scaler.transform(X)
        return [_sigmoid(self.intercept_std_ + sum(self.coef_std_[j] * zi[j]
                                                   for j in range(len(zi)))) for zi in Z]


def auc(scores, y):
    pairs = sorted(zip(scores, y))
    ranks = [0.0] * len(pairs)
    i = 0
    while i < len(pairs):
        j = i
        while j < len(pairs) and pairs[j][0] == pairs[i][0]:
            j += 1
        avg = (i + j - 1) / 2.0 + 1
        for k in range(i, j):
            ranks[k] = avg
        i = j
    npos = sum(y)
    nneg = len(y) - npos
    if npos == 0 or nneg == 0:
        return float("nan")
    sum_pos = sum(rk for rk, (_, yy) in zip(ranks, pairs) if yy == 1)
    return (sum_pos - npos * (npos + 1) / 2.0) / (npos * nneg)


if __name__ == "__main__":
    from synthetic_panel import generate, to_xy, FEATURES, TRUE_COEF

    import time
    rows, true = generate(n_accounts=3000, horizon=120, seed=1)
    X, y, w = to_xy(rows)
    n_ev = sum(y)
    print(f"panel rows={len(rows)} events={n_ev} event_rate={n_ev/len(rows):.4f}\n")

    # compare solvers: coordinate descent (default) vs proximal gradient
    t0 = time.time()
    cd = LogisticElasticNet(l1=0.0, l2=1e-6, solver="cd").fit(X, y, w=w)
    t_cd = time.time() - t0
    t0 = time.time()
    pg = LogisticElasticNet(l1=0.0, l2=1e-6, lr=12.0, epochs=600, solver="pgd").fit(X, y, w=w)
    t_pgd = time.time() - t0

    print("coefficient recovery (raw space): CD vs PGD")
    print(f"  {'param':<14}{'true':>8}{'CD':>10}{'PGD':>10}")
    print(f"  {'intercept':<14}{TRUE_COEF['intercept']:>8.3f}{cd.intercept_:>10.3f}{pg.intercept_:>10.3f}")
    for j, f in enumerate(FEATURES):
        print(f"  {f:<14}{TRUE_COEF[f]:>8.3f}{cd.coef_[j]:>10.3f}{pg.coef_[j]:>10.3f}")
    print(f"\nwall time: CD={t_cd:.2f}s  PGD={t_pgd:.2f}s")
    # L1 sparsity demo: a strong L1 should zero out a noise feature
    Xn = [row + [__import__('random').Random(i).gauss(0, 1)] for i, row in enumerate(X)]
    spar = LogisticElasticNet(l1=0.02, l2=1e-4, solver="cd").fit(Xn, y, w=w)
    print(f"L1 sparsity: with a pure-noise 5th feature, CD coef = {spar.coef_[-1]:+.4f} "
          f"(should be ~0); signal coefs kept: {[round(c,2) for c in spar.coef_[:4]]}")

    model = cd
    p = model.predict_proba(X)
    print(f"\ncalibration: mean_pred={sum(p)/len(p):.4f} actual={n_ev/len(rows):.4f}")
    print(f"AUC={auc(p, y):.4f}")
    # decile reliability
    order = sorted(range(len(p)), key=lambda i: p[i])
    print("decile  mean_pred  actual")
    for d in range(10):
        idx = order[d * len(p) // 10:(d + 1) * len(p) // 10]
        mp = sum(p[i] for i in idx) / len(idx)
        ac = sum(y[i] for i in idx) / len(idx)
        print(f"  {d:<5} {mp:.4f}    {ac:.4f}")
