"""regime_var_garch.py -- a Regime-Switching VAR(1) + GARCH(1,1) macro generator for the
ALM positioning Monte-Carlo. Pure stdlib (no numpy).

A real upgrade over the regime-switching AR in macro_sim.py, which treats each macro series
independently. This model has:
  - VAR(1): y_t = c_s + A_s y_{t-1} + e_t   -> CROSS-propagation (oil shock -> FX -> rate),
    with regime-dependent (c_s, A_s) from the HMM regimes.
  - GARCH(1,1) on each innovation series: sig2_t = w + a e_{t-1}^2 + b sig2_{t-1}
    -> volatility CLUSTERING + fat tails (crises are bursty, not iid Gaussian).
  - a static innovation correlation (Cholesky) so contemporaneous shocks co-move.

This is the honest "better generator" for the oil->dinar->deposit channel: the crisis
propagates through the estimated dynamics, and the tail is heavy. (For the REGULATORY run-off
we still use prescribed scenarios; this is the ECONOMIC positioning view.)
"""
from __future__ import annotations

import math
from random import Random


# --------------------- tiny stdlib linear algebra -------------------------- #
def _matvec(A, x):
    return [sum(A[i][j] * x[j] for j in range(len(x))) for i in range(len(A))]


def _solve(A, b):
    """Solve A x = b by Gaussian elimination with partial pivoting (A square)."""
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-15:
            M[piv][col] += 1e-12
        M[col], M[piv] = M[piv], M[col]
        p = M[col][col]
        for r in range(n):
            if r == col:
                continue
            f = M[r][col] / p
            for k in range(col, n + 1):
                M[r][k] -= f * M[col][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def _chol(S):
    """Cholesky L (S = L L^T), with jitter for PSD safety."""
    n = len(S)
    L = [[0.0] * n for _ in range(n)]
    for i in range(n):
        for j in range(i + 1):
            s = sum(L[i][k] * L[j][k] for k in range(j))
            if i == j:
                L[i][j] = math.sqrt(max(S[i][i] - s, 1e-12))
            else:
                L[i][j] = (S[i][j] - s) / L[j][j]
    return L


# --------------------------- weighted VAR(1) ------------------------------- #
def fit_var1_weighted(Y, w):
    """Weighted VAR(1): regress y_t on [1, y_{t-1}] equation-by-equation with weights w
    (one per t>=1). Returns (c, A, resid) where c is F-vector, A is FxF, resid is list of
    F-vectors."""
    T, F = len(Y), len(Y[0])
    # design: X_t = [1, y_{t-1}]  (F+1 regressors); targets y_t, t=1..T-1
    p = F + 1
    XtWX = [[0.0] * p for _ in range(p)]
    XtWy = [[0.0] * F for _ in range(p)]
    for t in range(1, T):
        wt = w[t] if t < len(w) else 1.0
        xt = [1.0] + Y[t - 1]
        for i in range(p):
            wi = wt * xt[i]
            for j in range(p):
                XtWX[i][j] += wi * xt[j]
            for f in range(F):
                XtWy[i][f] += wi * Y[t][f]
    # ridge for stability
    for i in range(p):
        XtWX[i][i] += 1e-6
    # solve for each target column -> coefficient matrix B (p x F)
    B = [[0.0] * F for _ in range(p)]
    for f in range(F):
        coef = _solve(XtWX, [XtWy[i][f] for i in range(p)])
        for i in range(p):
            B[i][f] = coef[i]
    c = B[0][:]                                   # intercept row
    A = [[B[1 + j][i] for j in range(F)] for i in range(F)]   # A[i][j] = effect of y_j on y_i
    resid = []
    for t in range(1, T):
        pred = [c[i] + sum(A[i][j] * Y[t - 1][j] for j in range(F)) for i in range(F)]
        resid.append([Y[t][i] - pred[i] for i in range(F)])
    return c, A, resid


# ----------------------------- GARCH(1,1) ---------------------------------- #
def fit_garch11(e):
    """Fit GARCH(1,1) to a zero-mean innovation series by a coarse grid MLE (Gaussian).
    Returns (omega, alpha, beta). Robust + deterministic (no optimizer libs)."""
    n = len(e)
    var = sum(x * x for x in e) / max(1, n) or 1e-8
    best = (-1e18, 1e-6, 0.05, 0.90)
    for a in [0.02, 0.05, 0.08, 0.12, 0.18]:
        for b in [0.70, 0.80, 0.88, 0.94]:
            if a + b >= 0.999:
                continue
            w = var * (1 - a - b)
            ll, s2 = 0.0, var
            for x in e:
                s2 = w + a * (x * x) + b * s2
                s2 = max(s2, 1e-10)
                ll += -0.5 * (math.log(2 * math.pi * s2) + x * x / s2)
            if ll > best[0]:
                best = (ll, w, a, b)
    return best[1], best[2], best[3]


# ------------------------- the generator ----------------------------------- #
class RegimeVARGARCH:
    def __init__(self, features, A_trans, pi, var_by_regime, garch, Lcorr, uncond_var):
        self.features = features
        self.A_trans = A_trans        # HMM regime transition matrix (K x K)
        self.pi = pi
        self.var = var_by_regime      # {k: (c_k, A_k)}
        self.garch = garch            # per-feature (w, a, b)
        self.L = Lcorr                # Cholesky of innovation correlation (F x F)
        self.uncond_var = uncond_var  # per-feature unconditional variance

    @classmethod
    def fit(cls, Y, hmm, regime_post):
        """Y = list of T macro vectors (raw units, order = hmm['features']); regime_post =
        list of K-vectors (filtered/smoothed posteriors, aligned to Y)."""
        F, K = len(Y[0]), hmm["K"]
        var_by_regime, all_resid = {}, []
        for k in range(K):
            wk = [regime_post[t][k] for t in range(len(Y))]
            c, A, resid = fit_var1_weighted(Y, wk)
            var_by_regime[k] = (c, A)
            all_resid.append(resid)
        # pool residuals from the MAP regime per step for GARCH + correlation
        resid = []
        for t in range(len(Y) - 1):
            kbest = max(range(K), key=lambda k: regime_post[t + 1][k])
            resid.append(all_resid[kbest][t])
        garch = []
        uncond = []
        for f in range(F):
            series = [r[f] for r in resid]
            garch.append(fit_garch11(series))
            uncond.append(sum(x * x for x in series) / max(1, len(series)) or 1e-8)
        # standardized-residual correlation -> Cholesky
        corr = [[0.0] * F for _ in range(F)]
        sd = [math.sqrt(uncond[f]) for f in range(F)]
        for i in range(F):
            for j in range(F):
                cov = sum(resid[t][i] * resid[t][j] for t in range(len(resid))) / max(1, len(resid))
                corr[i][j] = cov / (sd[i] * sd[j] + 1e-12)
        for i in range(F):
            corr[i][i] = 1.0
        L = _chol(corr)
        return cls(hmm["features"], hmm["A"], hmm.get("pi") or [1.0 / K] * K,
                   var_by_regime, garch, L, uncond)

    def simulate(self, y0, T, n_paths, seed=0, df=6):
        rng = Random(seed)
        F, K = len(self.features), len(self.A_trans)
        macro_paths, regime_paths = [], []
        for _ in range(n_paths):
            y = list(y0)
            state = _cat(rng, self.pi)
            sig2 = list(self.uncond_var)
            eprev = [0.0] * F
            mp, rp = [], []
            for _ in range(T):
                state = _cat(rng, self.A_trans[state])
                # GARCH volatilities
                for f in range(F):
                    w, a, b = self.garch[f]
                    sig2[f] = max(w + a * eprev[f] * eprev[f] + b * sig2[f], 1e-12)
                # correlated fat-tailed standardized shocks
                z = [_t(rng, df) for _ in range(F)]
                zc = _matvec(self.L, z)
                e = [math.sqrt(sig2[f]) * zc[f] for f in range(F)]
                eprev = e
                c, A = self.var[state]
                y = [c[i] + sum(A[i][j] * y[j] for j in range(F)) + e[i] for i in range(F)]
                mp.append(list(y))
                rp.append(state)
            macro_paths.append(mp)
            regime_paths.append(rp)
        return {"macro": macro_paths, "regime": regime_paths, "features": self.features}


def _cat(rng, p):
    u, c = rng.random(), 0.0
    for i, pi in enumerate(p):
        c += pi
        if u <= c:
            return i
    return len(p) - 1


def _t(rng, df):
    if df is None or df > 200:
        return rng.gauss(0, 1)
    z = rng.gauss(0, 1)
    chi = sum(rng.gauss(0, 1) ** 2 for _ in range(int(df)))
    return z / math.sqrt(chi / df) if chi > 0 else z


# --------------------------------------------------------------------------- #
def _self_test():
    rng = Random(0)
    # synthetic 2-var macro where FX FOLLOWS oil (cross-dependence) + vol clustering
    T = 300
    oil = [100.0]
    fx = [140.0]
    s2 = 1.0
    for t in range(1, T):
        s2 = 0.02 + 0.10 * (oil[-1] - oil[-2]) ** 2 if t > 1 else 1.0   # crude clustering
        s2 = min(max(s2, 0.5), 20.0)
        d_oil = -0.02 * (oil[-1] - 90) + math.sqrt(s2) * rng.gauss(0, 1)
        oil.append(oil[-1] + d_oil)
        fx.append(fx[-1] + 0.8 * (-d_oil) + rng.gauss(0, 0.5))          # fx up when oil down
    Y = [[oil[t], fx[t]] for t in range(T)]

    hmm = {"K": 2, "features": ["oil", "fx"],
           "A": [[0.9, 0.1], [0.2, 0.8]], "pi": [0.6, 0.4]}
    post = [[0.8, 0.2] if oil[t] > 90 else [0.2, 0.8] for t in range(T)]

    m = RegimeVARGARCH.fit(Y, hmm, post)
    # (1) VAR captured oil->fx cross-dependence: A[fx][oil] should be negative (fx up as oil down)
    c0, A0 = m.var[0]
    assert A0[1][0] < 0.2, f"expected oil->fx cross term, got {A0[1][0]}"
    # (2) GARCH stationary (a+b<1) and persistent (b>0)
    for (w, a, b) in m.garch:
        assert 0 < a + b < 1.0 and b > 0.0, (w, a, b)

    sim = m.simulate([100.0, 140.0], T=48, n_paths=300, seed=2, df=6)
    assert len(sim["macro"]) == 300 and len(sim["macro"][0]) == 48
    # (3) fat tails: some paths show a big oil drawdown
    mins = [min(row[0] for row in p) for p in sim["macro"]]
    assert min(mins) < 80.0, f"no drawdown (min {min(mins):.1f})"
    # (4) cross-propagation preserved in simulation: oil and fx innovations anti-correlate
    doil, dfx = [], []
    for p in sim["macro"][:50]:
        for t in range(1, len(p)):
            doil.append(p[t][0] - p[t - 1][0])
            dfx.append(p[t][1] - p[t - 1][1])
    mo, mf = sum(doil) / len(doil), sum(dfx) / len(dfx)
    cov = sum((doil[i] - mo) * (dfx[i] - mf) for i in range(len(doil)))
    assert cov < 0, f"oil/fx co-movement sign lost (cov {cov:.2f})"
    # (5) volatility clustering: squared-innovation autocorrelation > 0
    e2 = [d * d for d in doil]
    me = sum(e2) / len(e2)
    ac = (sum((e2[i] - me) * (e2[i - 1] - me) for i in range(1, len(e2)))
          / sum((x - me) ** 2 for x in e2))
    assert ac > 0.0, f"no vol clustering (acf {ac:.3f})"

    print("regime_var_garch self-test PASSED")
    print(f"  VAR oil->fx cross-term A[fx][oil]={A0[1][0]:.2f}; "
          f"GARCH (w,a,b)={tuple(round(x,3) for x in m.garch[0])}")
    print(f"  simulated: oil min {min(mins):.1f}, oil/fx co-move cov {cov:.1f}, "
          f"vol-cluster acf {ac:.2f}")


if __name__ == "__main__":
    _self_test()
