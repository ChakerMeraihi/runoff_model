"""Monte-Carlo stress engine (pure stdlib) -- PLANv2 9 / 7.

Today's stress is deterministic (a +200bp bump, the Role-2 Markov generator) plus a
conformal-proxy band. For IRRBB you want the *distribution* of the run-off and its
*tail* (e.g. a 99th-percentile WAL for capital), not one shocked curve. The regime
HMM we fit is GENERATIVE -- regimes follow the transition matrix A, macro is Gaussian
per state -- so we forward-simulate macro/regime trajectories and push each through the
FROZEN hazard (+ erosion). The binding uncertainty here is temporal/macro (PLANv2 0.2),
so a macro-path Monte Carlo is the right first object; coefficient (parameter)
uncertainty is a documented second layer (resample the time-block bootstrap fits and
pass them as `coef_draws`).

Self-contained: operates on the serialized model.json dict directly (reconstructs the
standardized linear predictor inline), so it imports nothing from the orchestrators.
"""
from __future__ import annotations

import math
import random


def _sig(x):
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def _pctile(sorted_xs, q):
    n = len(sorted_xs)
    if n == 0:
        return float("nan")
    if n == 1:
        return sorted_xs[0]
    pos = (q / 100.0) * (n - 1)
    lo = int(pos)
    frac = pos - lo
    if lo + 1 < n:
        return sorted_xs[lo] * (1 - frac) + sorted_xs[lo + 1] * frac
    return sorted_xs[lo]


def _sample_cat(probs, rng):
    r = rng.random()
    c = 0.0
    for k, p in enumerate(probs):
        c += p
        if r < c:
            return k
    return len(probs) - 1


def simulate_macro(hmm, p0, H, n_paths, rng, rate_feat="money_market", rate_bump=0.0,
                   force_state=None):
    """Sample n_paths macro trajectories of length H from the frozen, STANDARDIZED HMM,
    de-standardized to real units. Returns (macro_paths, regime_paths):
      macro_paths[p][h] = [cpi, rate, oil, ...]  (the hmm['features'] order)
      regime_paths[p][h] = sampled state index.
    p0 = initial-state distribution (the current filtered posterior). force_state pins
    the path to start in one regime (the currency-stress scenario)."""
    K, mu, var = hmm["K"], hmm["mu"], hmm["var"]
    A, in_mu, in_sd = hmm["A"], hmm["in_mu"], hmm["in_sd"]
    D = len(in_mu)
    feats = hmm["features"]
    ridx = feats.index(rate_feat) if rate_feat in feats else None
    macro_paths, regime_paths = [], []
    for _ in range(n_paths):
        s = force_state if force_state is not None else _sample_cat(p0, rng)
        mp, rp = [], []
        for _h in range(H):
            s = _sample_cat(A[s], rng)                         # next month's regime
            x = [(mu[s][d] + rng.gauss(0.0, 1.0) * math.sqrt(var[s][d])) * in_sd[d] + in_mu[d]
                 for d in range(D)]
            if ridx is not None and rate_bump:
                x[ridx] += rate_bump
            mp.append(x)
            rp.append(s)
        macro_paths.append(mp)
        regime_paths.append(rp)
    return macro_paths, regime_paths


def _hazard_proba(hz, features, fd):
    z = 0.0
    b = hz["coef_std"]
    mu, sd = hz["scaler_mu"], hz["scaler_sd"]
    lin = hz["intercept_std"]
    for i, f in enumerate(features):
        lin += b[i] * ((fd[f] - mu[i]) / sd[i])
    return _sig(lin)


def _erosion_inc(ev, fd):
    return ev["intercept"] + sum(ev["coef"][f] * fd.get(f, 0.0) for f in ev["features"])


def mc_runoff(model, alive, H=12, n_paths=2000, seed=0, p0=None, rate_bump=0.0,
              force_state=None, season_step=1.0 / 40.0):
    """Forward Monte-Carlo of the book run-off B(t) under simulated macro/regime paths.
    alive = [(balance0, base_featdict), ...]. Returns the S(t) fan (p5/p50/p95 by
    horizon) and the WAL distribution incl. tail percentiles."""
    hmm = model.get("hmm")
    if not hmm:
        raise ValueError("mc_runoff needs a fitted HMM in model.json (the path generator).")
    rng = random.Random(seed)
    features = model["features"]
    hz = model["hazard"]
    ev = model.get("erosion")
    macro_feats = hmm["features"]
    reg_feats = model.get("regime_features") or []
    K = hmm["K"]
    if p0 is None:
        p0 = [1.0 / K] * K

    macro_paths, regime_paths = simulate_macro(
        hmm, p0, H, n_paths, rng, rate_bump=rate_bump, force_state=force_state)

    wsum = sum(b for b, _ in alive) or 1e-12
    S_paths = []
    for p in range(n_paths):
        mp, rp = macro_paths[p], regime_paths[p]
        Bt = [0.0] * (H + 1)
        for b0, feat in alive:
            S, cum_g = 1.0, 0.0
            Bt[0] += b0                                        # S(0)*r(0) = 1
            for h in range(1, H + 1):
                fd = dict(feat)
                if "seasoning" in fd:
                    fd["seasoning"] = feat["seasoning"] + h
                if "z_seasoning" in fd:
                    fd["z_seasoning"] = feat["z_seasoning"] + h * season_step
                for d, mf in enumerate(macro_feats):           # inject simulated macro
                    if mf in fd:
                        fd[mf] = mp[h - 1][d]
                if reg_feats:                                   # regime one-hot of sim state
                    st = rp[h - 1]
                    for k, nm in enumerate(reg_feats):
                        fd[nm] = 1.0 if k == st else 0.0
                S *= (1.0 - _hazard_proba(hz, features, fd))
                if ev:
                    cum_g += _erosion_inc(ev, fd)
                Bt[h] += b0 * S * (math.exp(cum_g) if ev else 1.0)
        S_paths.append([x / wsum for x in Bt])

    fan = []
    for h in range(H + 1):
        col = sorted(S_paths[p][h] for p in range(n_paths))
        fan.append({"h": h, "p05": _pctile(col, 5), "p50": _pctile(col, 50),
                    "p95": _pctile(col, 95)})
    wal = sorted(sum(S_paths[p][1:]) for p in range(n_paths))
    return {
        "n_paths": n_paths, "H": H,
        "fan": fan,
        "wal_p01": _pctile(wal, 1), "wal_p05": _pctile(wal, 5), "wal_p50": _pctile(wal, 50),
        "wal_p95": _pctile(wal, 95), "wal_p99": _pctile(wal, 99),
        "wal_mean": sum(wal) / len(wal),
    }


if __name__ == "__main__":
    # tiny hand-built model: 2-state HMM (calm/stress), one rate-sensitive hazard.
    model = {
        "features": ["seasoning", "money_market"],
        "base_features": ["seasoning", "money_market"],
        "regime_features": [],
        "hazard": {"coef_std": [-0.4, 0.5], "intercept_std": -4.0,
                   "scaler_mu": [60.0, 3.0], "scaler_sd": [30.0, 1.0]},
        "erosion": {"features": ["money_market"], "coef": {"money_market": -0.003},
                    "intercept": -0.002},
        "hmm": {"K": 2, "features": ["money_market"],
                "mu": [[0.0], [2.0]], "var": [[0.25], [0.25]],
                "A": [[0.9, 0.1], [0.2, 0.8]], "in_mu": [3.0], "in_sd": [1.0]},
    }
    alive = [(100.0, {"seasoning": 50.0, "money_market": 3.0}) for _ in range(200)]

    base = mc_runoff(model, alive, H=12, n_paths=1500, seed=1, p0=[1.0, 0.0])
    stress = mc_runoff(model, alive, H=12, n_paths=1500, seed=1, force_state=1)   # start stressed
    rate = mc_runoff(model, alive, H=12, n_paths=1500, seed=1, p0=[1.0, 0.0], rate_bump=2.0)

    def line(tag, r):
        f = r["fan"]
        print(f"  {tag:<16} S(12) p05/p50/p95 = {f[12]['p05']:.3f}/{f[12]['p50']:.3f}/"
              f"{f[12]['p95']:.3f}   WAL p50={r['wal_p50']:.2f}  p01(tail)={r['wal_p01']:.2f}")

    print(f"Monte-Carlo run-off ({base['n_paths']} paths, H=12):")
    line("baseline", base)
    line("+200bp rate", rate)
    line("stress-regime", stress)
    # sanity: a fan must be a band (p05 <= p50 <= p95) and monotone non-increasing median
    f = base["fan"]
    ok_band = all(c["p05"] <= c["p50"] + 1e-9 <= c["p95"] + 1e-9 for c in f)
    ok_mono = all(f[h]["p50"] >= f[h + 1]["p50"] - 1e-9 for h in range(len(f) - 1))
    print(f"\n  fan is a valid band (p05<=p50<=p95): {ok_band}")
    print(f"  median S(t) monotone non-increasing:  {ok_mono}")
    print(f"  stress WAL p50 ({stress['wal_p50']:.2f}) < baseline ({base['wal_p50']:.2f}): "
          f"{stress['wal_p50'] < base['wal_p50']}")
    print(f"  +200bp WAL p50 ({rate['wal_p50']:.2f}) < baseline ({base['wal_p50']:.2f}): "
          f"{rate['wal_p50'] < base['wal_p50']}")
