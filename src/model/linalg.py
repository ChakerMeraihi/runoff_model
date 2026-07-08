"""Minimal linear algebra + OLS (pure stdlib) — foundation for ADF/SADF/ECM.

Normal-equations OLS with ridge option, solved by Gaussian elimination with
partial pivoting. Returns coefficients, residuals, and the (X'X)^-1 needed for
HAC / classical standard errors.
"""
from __future__ import annotations

import math


def matmul_T(A):
    """Return A^T A (A is n x k, result k x k)."""
    n, k = len(A), len(A[0])
    out = [[0.0] * k for _ in range(k)]
    for i in range(n):
        row = A[i]
        for a in range(k):
            ra = row[a]
            if ra == 0.0:
                continue
            for b in range(a, k):
                out[a][b] += ra * row[b]
    for a in range(k):
        for b in range(a):
            out[a][b] = out[b][a]
    return out


def matvec_T(A, y):
    """Return A^T y."""
    n, k = len(A), len(A[0])
    out = [0.0] * k
    for i in range(n):
        yi = y[i]
        row = A[i]
        for a in range(k):
            out[a] += row[a] * yi
    return out


def solve(A, b):
    """Solve A x = b (A square) by Gaussian elimination with partial pivoting."""
    n = len(A)
    M = [list(A[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-15:
            M[piv][col] += 1e-12
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col]
        for r in range(n):
            if r == col:
                continue
            f = M[r][col] / pv
            if f == 0.0:
                continue
            for c in range(col, n + 1):
                M[r][c] -= f * M[col][c]
    return [M[i][n] / M[i][i] for i in range(n)]


def inv(A):
    """Inverse of square A via Gauss-Jordan (for covariance of coefficients)."""
    n = len(A)
    M = [list(A[i]) + [1.0 if j == i else 0.0 for j in range(n)] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        M[col], M[piv] = M[piv], M[col]
        pv = M[col][col] or 1e-12
        M[col] = [v / pv for v in M[col]]
        for r in range(n):
            if r == col:
                continue
            f = M[r][col]
            if f == 0.0:
                continue
            M[r] = [a - f * b for a, b in zip(M[r], M[col])]
    return [row[n:] for row in M]


def matmul(A, B):
    """Square (or conformable) matrix product A@B."""
    n, k, m = len(A), len(B), len(B[0])
    out = [[0.0] * m for _ in range(n)]
    for i in range(n):
        Ai = A[i]
        for t in range(k):
            a = Ai[t]
            if a == 0.0:
                continue
            Bt = B[t]
            oi = out[i]
            for j in range(m):
                oi[j] += a * Bt[j]
    return out


def expm(A, terms=20):
    """Matrix exponential via scaling-and-squaring + truncated Taylor (stdlib).
    Used for continuous-time Markov-generator survival S(t)=1^T exp((Q-diag a)t) p0."""
    n = len(A)
    norm = max((sum(abs(A[i][j]) for j in range(n)) for i in range(n)), default=0.0)
    s = 0
    while norm > 0.5:
        norm /= 2.0
        s += 1
    B = [[A[i][j] / (2 ** s) for j in range(n)] for i in range(n)]
    E = [[1.0 if i == j else 0.0 for j in range(n)] for i in range(n)]
    term = [row[:] for row in E]
    for k in range(1, terms + 1):
        term = matmul(term, B)
        term = [[term[i][j] / k for j in range(n)] for i in range(n)]
        for i in range(n):
            for j in range(n):
                E[i][j] += term[i][j]
    for _ in range(s):
        E = matmul(E, E)
    return E


def ols(X, y, ridge=0.0):
    """OLS (optionally ridge). Returns dict(beta, resid, xtx_inv, sigma2, n, k)."""
    n, k = len(X), len(X[0])
    XtX = matmul_T(X)
    if ridge:
        for i in range(k):
            XtX[i][i] += ridge
    Xty = matvec_T(X, y)
    beta = solve(XtX, Xty)
    resid = [y[i] - sum(X[i][j] * beta[j] for j in range(k)) for i in range(n)]
    dof = max(1, n - k)
    sigma2 = sum(r * r for r in resid) / dof
    return {"beta": beta, "resid": resid, "xtx_inv": inv(XtX),
            "sigma2": sigma2, "n": n, "k": k}


def ols_se(fit):
    """Classical OLS standard errors sqrt(sigma2 * diag((X'X)^-1))."""
    s2, inv_ = fit["sigma2"], fit["xtx_inv"]
    return [math.sqrt(max(s2 * inv_[j][j], 0.0)) for j in range(fit["k"])]


if __name__ == "__main__":
    import random
    rng = random.Random(0)
    # y = 1.5 + 2.0 x1 - 0.7 x2 + noise
    X, y = [], []
    for _ in range(500):
        x1, x2 = rng.gauss(0, 1), rng.gauss(0, 1)
        X.append([1.0, x1, x2])
        y.append(1.5 + 2.0 * x1 - 0.7 * x2 + rng.gauss(0, 0.3))
    fit = ols(X, y)
    se = ols_se(fit)
    print("OLS recovery (true 1.5, 2.0, -0.7):")
    for name, b, s in zip(["const", "x1", "x2"], fit["beta"], se):
        print(f"  {name:<6} {b:+.4f}  se={s:.4f}")
    print(f"  sigma={math.sqrt(fit['sigma2']):.4f} (true 0.30)")
