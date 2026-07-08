"""gbm.py -- pure-stdlib gradient-boosted decision trees for the binary HAZARD, as a
CHALLENGER to the elastic-net logistic hazard. No numpy / sklearn / lightgbm (the bank PC
has none), but the SAME inductive bias as LightGBM: histogram split finding on quantile
bins, second-order (grad/hessian) Newton leaves, depth-wise trees, shrinkage.

Purpose: settle "why not XGBoost/DNN?" empirically instead of by opinion. Fit this next to
the logistic hazard on the SAME train, roll both to a book run-off, and let the OUT-OF-SAMPLE
number decide. On ~120 monthly obs with a ~0.5%/mo rare event we expect it to NOT beat the
calibrated GLM (or to overfit) -- but the comparison is honest and reproducible.

API mirrors LogisticElasticNet: fit(X, y, w=None) -> self ; predict_proba(X) -> [p].
"""
from __future__ import annotations

import math


def _sigmoid(z):
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def _quantile_bins(col, max_bins):
    """Bin edges at empirical quantiles (histogram binning, LightGBM-style). Returns the
    strictly-increasing interior edges; at most max_bins-1 of them."""
    xs = sorted(set(col))
    if len(xs) <= 1:
        return []
    if len(xs) <= max_bins:
        return [(xs[i] + xs[i + 1]) / 2.0 for i in range(len(xs) - 1)]
    edges = []
    s = sorted(col)
    n = len(s)
    for b in range(1, max_bins):
        q = s[min(n - 1, int(b * n / max_bins))]
        if not edges or q > edges[-1]:
            edges.append(q)
    return edges


def _bin_index(x, edges):
    # binary search: number of edges < x  -> bin in [0, len(edges)]
    lo, hi = 0, len(edges)
    while lo < hi:
        mid = (lo + hi) // 2
        if x <= edges[mid]:
            hi = mid
        else:
            lo = mid + 1
    return lo


class _Node:
    __slots__ = ("feat", "edge", "left", "right", "value")

    def __init__(self):
        self.feat = -1
        self.edge = 0.0
        self.left = None
        self.right = None
        self.value = 0.0


class GBMHazard:
    """Histogram gradient-boosted trees for a weighted binary target (the discrete hazard)."""

    def __init__(self, n_estimators=60, max_depth=3, learning_rate=0.1, max_bins=32,
                 min_child_weight=5.0, reg_lambda=1.0, subsample=1.0, seed=0):
        self.n_estimators = n_estimators
        self.max_depth = max_depth
        self.lr = learning_rate
        self.max_bins = max_bins
        self.min_child_weight = min_child_weight
        self.reg_lambda = reg_lambda
        self.subsample = subsample
        self.seed = seed
        self.trees = []
        self.base = 0.0
        self.edges = []
        self.nfeat = 0

    # ---- fit ------------------------------------------------------------- #
    def fit(self, X, y, w=None):
        n = len(X)
        self.nfeat = len(X[0]) if n else 0
        w = w or [1.0] * n
        # binned feature matrix (columns of bin indices)
        self.edges = []
        binned = [[0] * self.nfeat for _ in range(n)]
        for f in range(self.nfeat):
            col = [X[i][f] for i in range(n)]
            e = _quantile_bins(col, self.max_bins)
            self.edges.append(e)
            for i in range(n):
                binned[i][f] = _bin_index(col[i], e)

        # base score = weighted log-odds of the event rate
        sw = sum(w)
        py = sum(w[i] * y[i] for i in range(n)) / (sw or 1.0)
        py = min(max(py, 1e-6), 1 - 1e-6)
        self.base = math.log(py / (1 - py))
        F = [self.base] * n

        rng = _Lcg(self.seed)
        for _ in range(self.n_estimators):
            # gradients/hessians of weighted logistic loss
            g = [0.0] * n
            h = [0.0] * n
            idx = []
            for i in range(n):
                if self.subsample < 1.0 and rng.next() > self.subsample:
                    continue
                p = _sigmoid(F[i])
                g[i] = w[i] * (p - y[i])          # gradient
                h[i] = w[i] * p * (1 - p)         # hessian
                idx.append(i)
            tree = self._build_tree(binned, g, h, idx)
            for i in range(n):
                F[i] += self.lr * self._eval(tree, binned[i])
            self.trees.append(tree)
        return self

    def _build_tree(self, binned, g, h, idx):
        root = _Node()
        self._split(root, binned, g, h, idx, depth=0)
        return root

    def _leaf_value(self, g, h, idx):
        G = sum(g[i] for i in idx)
        H = sum(h[i] for i in idx)
        return -G / (H + self.reg_lambda)

    def _split(self, node, binned, g, h, idx, depth):
        node.value = self._leaf_value(g, h, idx)
        if depth >= self.max_depth or len(idx) < 2:
            return
        G = sum(g[i] for i in idx)
        H = sum(h[i] for i in idx)
        best_gain, best_f, best_edge_bin = 0.0, -1, -1
        lam = self.reg_lambda
        for f in range(self.nfeat):
            nbins = len(self.edges[f]) + 1
            if nbins < 2:
                continue
            hg = [0.0] * nbins
            hh = [0.0] * nbins
            for i in idx:
                b = binned[i][f]
                hg[b] += g[i]
                hh[b] += h[i]
            GL = HL = 0.0
            for b in range(nbins - 1):            # threshold after bin b
                GL += hg[b]
                HL += hh[b]
                GR, HR = G - GL, H - HL
                if HL < self.min_child_weight or HR < self.min_child_weight:
                    continue
                gain = (GL * GL / (HL + lam) + GR * GR / (HR + lam)
                        - G * G / (H + lam))
                if gain > best_gain:
                    best_gain, best_f, best_edge_bin = gain, f, b
        if best_f < 0:
            return
        node.feat, node.edge = best_f, best_edge_bin
        li = [i for i in idx if binned[i][best_f] <= best_edge_bin]
        ri = [i for i in idx if binned[i][best_f] > best_edge_bin]
        if not li or not ri:
            node.feat = -1
            return
        node.left, node.right = _Node(), _Node()
        self._split(node.left, binned, g, h, li, depth + 1)
        self._split(node.right, binned, g, h, ri, depth + 1)

    def _eval(self, node, brow):
        while node.feat >= 0:
            node = node.left if brow[node.feat] <= node.edge else node.right
        return node.value

    # ---- predict --------------------------------------------------------- #
    def _bin_row(self, row):
        return [_bin_index(row[f], self.edges[f]) for f in range(self.nfeat)]

    def predict_proba(self, X):
        out = []
        for row in X:
            br = self._bin_row(row)
            F = self.base + self.lr * sum(self._eval(t, br) for t in self.trees)
            out.append(_sigmoid(F))
        return out


class _Lcg:
    """Tiny deterministic PRNG (Math.random is unavailable in some sandboxes; keep it local)."""
    def __init__(self, seed):
        self.s = (seed * 2654435761 + 12345) & 0xFFFFFFFF

    def next(self):
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return self.s / 0x7FFFFFFF


# --------------------------------------------------------------------------- #
def _nll(p, y):
    return -sum(y[i] * math.log(max(p[i], 1e-12)) + (1 - y[i]) * math.log(max(1 - p[i], 1e-12))
               for i in range(len(y))) / len(y)


def _self_test():
    import random
    rng = random.Random(0)
    # (a) NONLINEAR pattern GBM should capture but a linear logit cannot: XOR-ish interaction
    Xtr, ytr, Xte, yte = [], [], [], []
    for _ in range(3000):
        x1, x2 = rng.uniform(-1, 1), rng.uniform(-1, 1)
        pr = 0.85 if (x1 * x2 > 0) else 0.15          # depends on the SIGN PRODUCT
        y = 1 if rng.random() < pr else 0
        (Xtr if rng.random() < 0.7 else Xte).append([x1, x2])
        (ytr if len(Xtr) > len(ytr) else yte).append(y)
    # keep lists aligned
    Xtr, ytr, Xte, yte = [], [], [], []
    for _ in range(4000):
        x1, x2 = rng.uniform(-1, 1), rng.uniform(-1, 1)
        y = 1 if rng.random() < (0.85 if x1 * x2 > 0 else 0.15) else 0
        if rng.random() < 0.7:
            Xtr.append([x1, x2]); ytr.append(y)
        else:
            Xte.append([x1, x2]); yte.append(y)

    m = GBMHazard(n_estimators=60, max_depth=3, learning_rate=0.15).fit(Xtr, ytr)
    p = m.predict_proba(Xte)
    assert all(0.0 <= v <= 1.0 for v in p), "proba out of range"
    nll_gbm = _nll(p, yte)
    # a constant base-rate predictor as the naive floor
    base = sum(ytr) / len(ytr)
    nll_base = _nll([base] * len(yte), yte)
    assert nll_gbm < nll_base * 0.85, f"GBM did not learn the XOR pattern: {nll_gbm} vs {nll_base}"

    # (b) discrimination: high-signal region separated
    hi = [pp for pp, x in zip(p, Xte) if x[0] * x[1] > 0]
    lo = [pp for pp, x in zip(p, Xte) if x[0] * x[1] <= 0]
    assert sum(hi) / len(hi) > sum(lo) / len(lo) + 0.3, "no separation learned"

    # (c) sample weights honoured (all-weight-on-positives pushes probas up)
    m2 = GBMHazard(n_estimators=20, max_depth=2).fit(
        Xtr, ytr, w=[1.0 if y else 0.01 for y in ytr])
    assert sum(m2.predict_proba(Xte)) / len(Xte) > base

    print("gbm self-test PASSED")
    print(f"  nonlinear XOR: NLL GBM={nll_gbm:.3f} vs base-rate={nll_base:.3f} "
          f"(GBM learns the interaction a linear logit cannot)")
    print(f"  learned separation: hi-region p={sum(hi)/len(hi):.2f} "
          f"vs lo-region p={sum(lo)/len(lo):.2f}")


if __name__ == "__main__":
    _self_test()
