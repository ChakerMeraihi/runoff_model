"""Autonomous regime detection: Gaussian HMM (pure stdlib).

States are LEARNED by EM (Baum-Welch) on macro observations -- no hand labels.
The regime FEATURE emitted for the hazard (PLANv2 6.7 Role-1) is the FILTERED
posterior P(state_t | x_1..t): forward-only, causal, NO smoothing/hindsight, so it
carries no look-ahead. In a walk-forward, fit() is called on the training window
only and filter() is run forward on later data (the splitter enforces this).

EM uses forward-backward (smoothed) for PARAMETER learning on the training set,
which is not look-ahead as long as params are learned on train and applied forward.
"""
from __future__ import annotations

import math
import random


def _log_gauss_diag(x, mu, var):
    s = 0.0
    for d in range(len(x)):
        s += -0.5 * (math.log(2 * math.pi * var[d]) + (x[d] - mu[d]) ** 2 / var[d])
    return s


class GaussianHMM:
    def __init__(self, n_states=3, n_iter=60, seed=0, var_floor=1e-4):
        self.K, self.n_iter, self.seed, self.var_floor = n_states, n_iter, seed, var_floor

    def _emit(self, X):
        """T x K matrix of emission probs, scaled per-t (constant cancels in EM)."""
        B = []
        for x in X:
            lp = [_log_gauss_diag(x, self.mu[k], self.var[k]) for k in range(self.K)]
            m = max(lp)
            B.append([math.exp(v - m) for v in lp])
        return B

    def _fit_std(self, X):
        """Per-channel mean/std on the TRAINING series. Without this the diagonal-
        Gaussian emission is dominated by the largest-scale channel (e.g. oil ~65 vs
        cpi ~0.05) -> degenerate transitions and single-state collapse. Standardizing
        puts every macro channel on equal footing so the regimes are real."""
        n, D = len(X), len(X[0])
        mu = [sum(x[d] for x in X) / n for d in range(D)]
        sd = [(sum((x[d] - mu[d]) ** 2 for x in X) / n) ** 0.5 or 1.0 for d in range(D)]
        return mu, sd

    def _apply_std(self, X):
        mu, sd = self.in_mu, self.in_sd
        return [[(x[d] - mu[d]) / sd[d] for d in range(len(x))] for x in X]

    def fit(self, X):
        self.in_mu, self.in_sd = self._fit_std(X)
        X = self._apply_std(X)                       # EM runs in standardized space
        K, T, D = self.K, len(X), len(X[0])
        self.D = D
        rng = random.Random(self.seed)
        self.mu = [list(X[i]) for i in rng.sample(range(T), K)]
        gmean = [sum(x[d] for x in X) / T for d in range(D)]
        gvar = [max(sum((x[d] - gmean[d]) ** 2 for x in X) / T, self.var_floor) for d in range(D)]
        self.var = [list(gvar) for _ in range(K)]
        self.A = [[1.0 / K] * K for _ in range(K)]
        self.pi = [1.0 / K] * K

        for _ in range(self.n_iter):
            B = self._emit(X)
            alpha = [[0.0] * K for _ in range(T)]
            c = [0.0] * T
            for k in range(K):
                alpha[0][k] = self.pi[k] * B[0][k]
            c[0] = sum(alpha[0]) or 1e-300
            alpha[0] = [a / c[0] for a in alpha[0]]
            for t in range(1, T):
                for k in range(K):
                    alpha[t][k] = sum(alpha[t - 1][j] * self.A[j][k] for j in range(K)) * B[t][k]
                c[t] = sum(alpha[t]) or 1e-300
                alpha[t] = [a / c[t] for a in alpha[t]]
            beta = [[0.0] * K for _ in range(T)]
            beta[T - 1] = [1.0] * K
            for t in range(T - 2, -1, -1):
                for k in range(K):
                    beta[t][k] = sum(self.A[k][j] * B[t + 1][j] * beta[t + 1][j]
                                     for j in range(K)) / c[t + 1]
            gamma = []
            for t in range(T):
                g = [alpha[t][k] * beta[t][k] for k in range(K)]
                s = sum(g) or 1e-300
                gamma.append([v / s for v in g])
            A_num = [[0.0] * K for _ in range(K)]
            for t in range(T - 1):
                tmp = [[alpha[t][j] * self.A[j][k] * B[t + 1][k] * beta[t + 1][k]
                        for k in range(K)] for j in range(K)]
                den = sum(sum(r) for r in tmp) or 1e-300
                for j in range(K):
                    for k in range(K):
                        A_num[j][k] += tmp[j][k] / den
            self.pi = list(gamma[0])
            for j in range(K):
                gs = sum(gamma[t][j] for t in range(T - 1)) or 1e-300
                self.A[j] = [A_num[j][k] / gs for k in range(K)]
            for k in range(K):
                gk = sum(gamma[t][k] for t in range(T)) or 1e-300
                for d in range(D):
                    self.mu[k][d] = sum(gamma[t][k] * X[t][d] for t in range(T)) / gk
                    self.var[k][d] = max(sum(gamma[t][k] * (X[t][d] - self.mu[k][d]) ** 2
                                             for t in range(T)) / gk, self.var_floor)
        return self

    def log_likelihood(self, X):
        """Total log P(X) via the scaled forward pass (for BIC model selection)."""
        X = self._apply_std(X)
        K = self.K
        ll, prev = 0.0, None
        for t, x in enumerate(X):
            lp = [_log_gauss_diag(x, self.mu[k], self.var[k]) for k in range(K)]
            m = max(lp)
            b = [math.exp(v - m) for v in lp]
            if t == 0:
                a = [self.pi[k] * b[k] for k in range(K)]
            else:
                a = [sum(prev[j] * self.A[j][k] for j in range(K)) * b[k] for k in range(K)]
            s = sum(a) or 1e-300
            ll += math.log(s) + m                    # P(x_t|x_<t) = s * exp(m)
            prev = [v / s for v in a]
        return ll

    def filter(self, X):
        """Causal filtered posteriors P(state_t | x_1..t) -- the look-ahead-safe feature."""
        X = self._apply_std(X)
        K = self.K
        out, prev = [], None
        for t, x in enumerate(X):
            lp = [_log_gauss_diag(x, self.mu[k], self.var[k]) for k in range(K)]
            m = max(lp)
            b = [math.exp(v - m) for v in lp]
            if t == 0:
                a = [self.pi[k] * b[k] for k in range(K)]
            else:
                a = [sum(prev[j] * self.A[j][k] for j in range(K)) * b[k] for k in range(K)]
            s = sum(a) or 1e-300
            a = [v / s for v in a]
            out.append(a)
            prev = a
        return out

    def predict_state(self, X):
        return [max(range(self.K), key=lambda k: p[k]) for p in self.filter(X)]


def select_k_bic(X, ks=(2, 3), n_iter=60, seed=0, var_floor=1e-4):
    """Pick the number of regimes by BIC (data-driven, not hard-coded). Given ~120
    months and few macro episodes (Gate A), 2-3 states is the identifiable range;
    BIC penalizes the extra state unless it earns its parameters. Returns
    (best_hmm, best_K, bic_by_k)."""
    T, D = len(X), len(X[0])
    best, bic_by_k = None, {}
    for K in ks:
        hmm = GaussianHMM(K, n_iter=n_iter, seed=seed, var_floor=var_floor).fit(X)
        ll = hmm.log_likelihood(X)
        n_params = 2 * K * D + K * (K - 1) + (K - 1)   # means+vars, transitions, init
        bic = -2.0 * ll + n_params * math.log(T)
        bic_by_k[K] = bic
        if best is None or bic < best[2]:
            best = (hmm, K, bic)
    return best[0], best[1], bic_by_k


# ---- synthetic validation ----
def _synth(n=300, seed=3):
    rng = random.Random(seed)
    means = {0: (-2.0, 0.5), 1: (0.0, 0.0), 2: (2.0, -0.5)}   # 3 regimes, 2 obs dims
    A = {0: [0.94, 0.05, 0.01], 1: [0.04, 0.92, 0.04], 2: [0.01, 0.05, 0.94]}
    s = 1
    states, X = [], []
    for _ in range(n):
        r = rng.random()
        cum = 0.0
        for k in range(3):
            cum += A[s][k]
            if r < cum:
                s = k
                break
        states.append(s)
        mu = means[s]
        X.append([mu[0] + rng.gauss(0, 0.6), mu[1] + rng.gauss(0, 0.6)])
    return X, states


if __name__ == "__main__":
    import itertools

    X, true = _synth()
    hmm = GaussianHMM(n_states=3, n_iter=80, seed=0).fit(X)
    pred = hmm.predict_state(X)

    # states are unidentified -> score over best label permutation
    best = 0.0
    for perm in itertools.permutations(range(3)):
        acc = sum(1 for p, t in zip(pred, true) if perm[p] == t) / len(true)
        best = max(best, acc)
    print(f"HMM filtered-state accuracy (best perm): {best:.3f}  (T={len(X)})")
    print("learned means (sorted by dim0):")
    for k in sorted(range(3), key=lambda k: hmm.mu[k][0]):
        print(f"  state {k}: mu=({hmm.mu[k][0]:+.2f},{hmm.mu[k][1]:+.2f})  "
              f"var=({hmm.var[k][0]:.2f},{hmm.var[k][1]:.2f})")
    print("true means: (-2.0,+0.5) (0.0,0.0) (+2.0,-0.5)")
    # self-transition persistence recovered?
    print("learned self-transition probs:", [round(hmm.A[k][k], 2) for k in range(3)])
