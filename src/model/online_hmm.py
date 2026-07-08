"""Online / adaptive Gaussian HMM with a DYNAMIC transition matrix (pure stdlib).

A standard Baum-Welch HMM has ONE static transition matrix A for all time. That is
wrong when the regime *dynamics* themselves change (calm persistent years vs a
fast-switching crisis). Here A_t, the means and the variances are updated
RECURSIVELY every step from exponentially-forgotten sufficient statistics
(forgetting factor rho; effective memory ~ 1/(1-rho) months). Everything uses only
the filtered (causal) responsibilities, so it is online and look-ahead-safe.

Warm start: a short batch fit (hmm_regime.GaussianHMM) seeds the params; then the
model adapts online over the rest of the stream.
"""
from __future__ import annotations

import math

from hmm_regime import GaussianHMM, _log_gauss_diag


class OnlineGaussianHMM:
    def __init__(self, n_states=3, rho=0.97, warmup=48, var_floor=1e-4, prior_count=20.0, seed=0):
        self.K, self.rho, self.warmup = n_states, rho, warmup
        self.var_floor, self.prior_count, self.seed = var_floor, prior_count, seed

    def _seed_from_batch(self, Xw):
        b = GaussianHMM(self.K, n_iter=60, seed=self.seed, var_floor=self.var_floor).fit(Xw)
        self.mu = [list(m) for m in b.mu]
        self.var = [list(v) for v in b.var]
        self.A = [list(row) for row in b.A]
        self.D = b.D
        c = self.prior_count
        # running discounted sufficient statistics
        self.W = [c] * self.K
        self.S1 = [[c * self.mu[k][d] for d in range(self.D)] for k in range(self.K)]
        self.S2 = [[c * (self.var[k][d] + self.mu[k][d] ** 2) for d in range(self.D)] for k in range(self.K)]
        self.Tfrom = [c] * self.K
        self.T1 = [[c * self.A[j][k] for k in range(self.K)] for j in range(self.K)]
        self.filt = list(b.filter(Xw)[-1])

    def _emit(self, x):
        lp = [_log_gauss_diag(x, self.mu[k], self.var[k]) for k in range(self.K)]
        m = max(lp)
        return [math.exp(v - m) for v in lp], m

    def update(self, x):
        """Ingest one observation; returns (filtered posterior, current A copy)."""
        K, rho = self.K, self.rho
        b, _ = self._emit(x)
        prev = self.filt
        # filtered posterior (causal)
        pred = [sum(prev[j] * self.A[j][k] for j in range(K)) for k in range(K)]
        a = [pred[k] * b[k] for k in range(K)]
        s = sum(a) or 1e-300
        a = [v / s for v in a]
        # one-step transition responsibilities xi[j][k]
        xi = [[prev[j] * self.A[j][k] * b[k] for k in range(K)] for j in range(K)]
        sx = sum(sum(r) for r in xi) or 1e-300
        xi = [[xi[j][k] / sx for k in range(K)] for j in range(K)]
        # decay + accumulate transition stats -> dynamic A
        for j in range(K):
            self.Tfrom[j] = rho * self.Tfrom[j] + sum(xi[j])
            for k in range(K):
                self.T1[j][k] = rho * self.T1[j][k] + xi[j][k]
            tot = self.Tfrom[j] or 1e-300
            self.A[j] = [self.T1[j][k] / tot for k in range(K)]
        # decay + accumulate emission stats -> adaptive mu, var
        for k in range(K):
            self.W[k] = rho * self.W[k] + a[k]
            wk = self.W[k] or 1e-300
            for d in range(self.D):
                self.S1[k][d] = rho * self.S1[k][d] + a[k] * x[d]
                self.S2[k][d] = rho * self.S2[k][d] + a[k] * x[d] * x[d]
                self.mu[k][d] = self.S1[k][d] / wk
                self.var[k][d] = max(self.S2[k][d] / wk - self.mu[k][d] ** 2, self.var_floor)
        self.filt = a
        return a, [row[:] for row in self.A]

    def run(self, X):
        """Stream X; return filtered posteriors and the A_t self-transition trace."""
        self._seed_from_batch(X[:self.warmup])
        filt, A_diag = [], []
        for x in X:
            a, A = self.update(x)
            filt.append(a)
            A_diag.append([A[k][k] for k in range(self.K)])
        return {"filtered": filt, "A_diag": A_diag}


if __name__ == "__main__":
    import random
    rng = random.Random(5)

    # 2-state data; dynamics CHANGE at t=150: persistent (0.95) -> fast-switching (0.60)
    means = {0: (-2.0,), 1: (2.0,)}
    X, true = [], []
    s = 0
    for t in range(360):
        p_stay = 0.95 if t < 150 else 0.60
        if rng.random() > p_stay:
            s = 1 - s
        true.append(s)
        X.append([means[s][0] + rng.gauss(0, 0.7)])

    on = OnlineGaussianHMM(n_states=2, rho=0.95, warmup=60).run(X)

    def avg_selfp(lo, hi):
        seg = on["A_diag"][lo:hi]
        return sum((d[0] + d[1]) / 2 for d in seg) / len(seg)

    print("ONLINE HMM dynamic transition matrix:")
    print(f"  mean self-transition, persistent phase (t=80..149):  {avg_selfp(80,150):.3f}  (true 0.95)")
    print(f"  mean self-transition, switching phase (t=200..359):  {avg_selfp(200,360):.3f}  (true 0.60)")

    # static batch HMM for contrast
    static = GaussianHMM(n_states=2, n_iter=80, seed=0).fit(X)
    print(f"\nSTATIC batch HMM self-transition (one value for all time): "
          f"{(static.A[0][0]+static.A[1][1])/2:.3f}  <- cannot adapt")
    # filtered-state accuracy of the online model (best of 2 perms), causal
    pred = [0 if f[0] >= f[1] else 1 for f in on["filtered"]]
    acc = max(sum(1 for p, t in zip(pred, true) if p == t),
              sum(1 for p, t in zip(pred, true) if (1 - p) == t)) / len(true)
    print(f"\nonline filtered-state accuracy (causal): {acc:.3f}")
