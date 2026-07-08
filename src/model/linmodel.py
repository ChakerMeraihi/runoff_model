"""Linear elastic-net + Huber (pure stdlib) -- the continuous-response regressor.

Counterpart to the logistic hazard, for CONTINUOUS targets: balance erosion r(t)
(erosion.py) and the robust ECM. Two solvers:
  loss='ls'    -> elastic-net least squares via cyclic coordinate descent (glmnet).
  loss='huber' -> Huber-robust elastic net via IRLS: reweight each row by the Huber
                  weight (1 if |resid|<=delta else delta/|resid|), then a weighted-LS
                  CD step; iterate. Bounds the influence of balance outliers / data
                  errors -- the right robustness for a continuous target (a Bernoulli
                  hazard has no outliers, so Huber lives HERE, not in the hazard).

Features standardized internally (unpenalized intercept); coef exposed in raw space.
"""
from __future__ import annotations

import math


class Scaler:
    def fit(self, X):
        n, d = len(X), len(X[0])
        self.mu = [sum(r[j] for r in X) / n for j in range(d)]
        self.sd = []
        for j in range(d):
            v = sum((r[j] - self.mu[j]) ** 2 for r in X) / n
            self.sd.append(math.sqrt(v) or 1.0)
        return self

    def transform(self, X):
        mu, sd = self.mu, self.sd
        return [[(r[j] - mu[j]) / sd[j] for j in range(len(mu))] for r in X]


class LinearElasticNet:
    def __init__(self, l1=0.0, l2=1e-6, loss="ls", huber_delta=1.345,
                 epochs=200, max_irls=15, tol=1e-9):
        self.l1, self.l2, self.loss = l1, l2, loss
        self.huber_delta = huber_delta
        self.epochs, self.max_irls, self.tol = epochs, max_irls, tol

    def _cd(self, Z, y, w, n, d):
        """Weighted-LS coordinate descent with elastic-net soft-threshold."""
        b = [0.0] * d
        b0 = sum(w[i] * y[i] for i in range(n)) / (sum(w) or 1e-12)
        r = [y[i] - b0 for i in range(n)]
        W = sum(w) or 1e-12
        for _ in range(self.epochs):
            db0 = sum(w[i] * r[i] for i in range(n)) / W
            b0 += db0
            for i in range(n):
                r[i] -= db0
            mc = 0.0
            for j in range(d):
                num = den = 0.0
                for i in range(n):
                    zij = Z[i][j]
                    num += w[i] * zij * (r[i] + b[j] * zij)
                    den += w[i] * zij * zij
                num /= n
                den /= n
                if num > self.l1:
                    bj = (num - self.l1) / (den + self.l2)
                elif num < -self.l1:
                    bj = (num + self.l1) / (den + self.l2)
                else:
                    bj = 0.0
                ch = bj - b[j]
                if ch != 0.0:
                    for i in range(n):
                        r[i] -= ch * Z[i][j]
                    b[j] = bj
                    mc = max(mc, abs(ch))
            if mc < self.tol:
                break
        return b, b0

    def fit(self, X, y, w=None):
        self.scaler = Scaler().fit(X)
        Z = self.scaler.transform(X)
        n, d = len(Z), len(Z[0])
        w0 = [1.0] * n if w is None else list(w)
        if self.loss == "ls":
            b, b0 = self._cd(Z, y, w0, n, d)
        else:                                            # huber IRLS
            b, b0 = [0.0] * d, sum(y) / n
            for _ in range(self.max_irls):
                wh = []
                for i in range(n):
                    res = y[i] - (b0 + sum(b[j] * Z[i][j] for j in range(d)))
                    a = abs(res)
                    hw = 1.0 if a <= self.huber_delta else self.huber_delta / (a + 1e-12)
                    wh.append(w0[i] * hw)
                nb, nb0 = self._cd(Z, y, wh, n, d)
                if max(abs(nb[j] - b[j]) for j in range(d)) < self.tol and abs(nb0 - b0) < self.tol:
                    b, b0 = nb, nb0
                    break
                b, b0 = nb, nb0
        self.coef_std_, self.intercept_std_ = b, b0
        mu, sd = self.scaler.mu, self.scaler.sd
        self.coef_ = [b[j] / sd[j] for j in range(d)]
        self.intercept_ = b0 - sum(b[j] * mu[j] / sd[j] for j in range(d))
        return self

    def predict(self, X):
        return [self.intercept_ + sum(self.coef_[j] * row[j] for j in range(len(row)))
                for row in X]


if __name__ == "__main__":
    import random
    rng = random.Random(0)
    # y = 1.0 + 2.0 x1 - 1.5 x2 + 0 x3(noise) + noise
    X, y = [], []
    for _ in range(400):
        x1, x2, x3 = rng.gauss(0, 1), rng.gauss(0, 1), rng.gauss(0, 1)
        X.append([x1, x2, x3])
        y.append(1.0 + 2.0 * x1 - 1.5 * x2 + rng.gauss(0, 0.3))

    ls = LinearElasticNet(l1=0.01, l2=1e-4, loss="ls").fit(X, y)
    print("LS elastic-net (true 1.0, 2.0, -1.5, 0.0):")
    print(f"  intercept={ls.intercept_:+.3f}  coef={[round(c,3) for c in ls.coef_]}")
    print(f"  noise feature x3 -> {ls.coef_[2]:+.3f} (L1 should shrink ~0)")

    # inject 5% gross outliers into y, compare LS vs Huber
    yo = list(y)
    out_idx = rng.sample(range(len(yo)), int(0.05 * len(yo)))
    for i in out_idx:
        yo[i] += rng.choice([-1, 1]) * 20.0
    ls2 = LinearElasticNet(l1=0.0, l2=1e-6, loss="ls").fit(X, yo)
    hb = LinearElasticNet(l1=0.0, l2=1e-6, loss="huber").fit(X, yo)
    def rmse_true(m):
        b = m.coef_
        return math.sqrt((b[0]-2.0)**2 + (b[1]+1.5)**2 + b[2]**2)
    print("\nwith 5% gross outliers in y (true slopes 2.0, -1.5, 0.0):")
    print(f"  LS    coef={[round(c,3) for c in ls2.coef_]}  coef-error={rmse_true(ls2):.3f}")
    print(f"  Huber coef={[round(c,3) for c in hb.coef_]}  coef-error={rmse_true(hb):.3f}")
    print(f"  -> Huber more robust: {rmse_true(hb) < rmse_true(ls2)}")
