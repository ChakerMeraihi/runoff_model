"""macro_sim.py -- generative macro paths for ALM POSITIONING (economic, NOT regulatory),
with an IMPOSED crisis overlay. Pure stdlib.

Two honest design decisions, both answering the "how do you capture an unseen crisis?" question:

1. The HMM is a good regime LABELER but a poor GENERATOR (Gaussian iid emissions -> no
   persistence / vol-clustering, thin tails, homogeneous transitions). So we use the HMM only
   as the regime SKELETON (transition matrix A + per-regime means) and simulate with a
   REGIME-SWITCHING AR(1) + STUDENT-t innovations: persistence toward the regime mean + fat
   tails. Better central dynamics than raw HMM sampling.

2. NO fitted generator (HMM or fancier) can produce a crisis it never saw. So the crisis TAIL
   is IMPOSED as an overlay, not learned: a deposit-flight elasticity maps a stress index
   (oil drawdown / FX depreciation) to EXCESS attrition on the run-off. Severity is an ALM
   assumption (analog episodes / expert / regulator), not a data estimate. This is the only
   honest way to size oil->dinar->DAV-flight when the sample has no such episode.

Regulatory note: this is the ECONOMIC positioning view (distribution of WAL / dEVE for
hedging). The REGULATORY run-off stays scenario-conditioned (irrbb.py), never forecast-driven.
"""
from __future__ import annotations

import math
from random import Random


def stationary_dist(A, iters=200):
    """Stationary distribution of a row-stochastic transition matrix A (power iteration)."""
    K = len(A)
    p = [1.0 / K] * K
    for _ in range(iters):
        p = [sum(p[j] * A[j][k] for j in range(K)) for k in range(K)]
        s = sum(p) or 1.0
        p = [x / s for x in p]
    return p


def _student_t(rng, df):
    """Standard Student-t draw (fat tails) via normal / sqrt(chi2/df); df->inf ~ normal."""
    if df is None or df > 200:
        return rng.gauss(0.0, 1.0)
    z = rng.gauss(0.0, 1.0)
    chi2 = sum(rng.gauss(0.0, 1.0) ** 2 for _ in range(int(df)))
    return z / math.sqrt(chi2 / df) if chi2 > 0 else z


class RegimeMacro:
    """Regime-switching AR(1) simulator. Per feature f and regime k: pulls x toward mu[k][f]
    at speed kappa with regime vol sd[k][f]; innovations are Student-t (fat tails)."""

    def __init__(self, A, mu, sd, features, kappa=0.3, df=5, pi=None):
        self.A, self.mu, self.sd, self.features = A, mu, sd, features
        self.kappa, self.df = kappa, df
        self.pi = pi or stationary_dist(A)

    @classmethod
    def from_hmm(cls, hmm, kappa=0.3, df=5):
        """Build from a fitted HMM: A = transition, per-regime mean/sd DE-standardized back
        to raw units via in_mu/in_sd. Uses the HMM only as the regime skeleton."""
        K, feats = hmm["K"], hmm["features"]
        in_mu = hmm.get("in_mu") or [0.0] * len(feats)
        in_sd = hmm.get("in_sd") or [1.0] * len(feats)
        mu = [[hmm["mu"][k][d] * in_sd[d] + in_mu[d] for d in range(len(feats))]
              for k in range(K)]
        sd = [[math.sqrt(max(hmm["var"][k][d], 1e-8)) * in_sd[d] for d in range(len(feats))]
              for k in range(K)]
        return cls(hmm["A"], mu, sd, feats, kappa=kappa, df=df, pi=hmm.get("pi"))

    def simulate(self, x0, T, n_paths, seed=0):
        """Simulate n_paths of length T. x0 = starting macro vector (raw units).
        Returns {'macro': paths[n][T][F], 'regime': paths[n][T]}."""
        rng = Random(seed)
        F = len(self.features)
        macro_paths, regime_paths = [], []
        for _ in range(n_paths):
            x = list(x0)
            state = _categorical(rng, self.pi)
            mp, rp = [], []
            for _ in range(T):
                state = _categorical(rng, self.A[state])
                x = [x[f] + self.kappa * (self.mu[state][f] - x[f])
                     + self.sd[state][f] * _student_t(rng, self.df) for f in range(F)]
                mp.append(list(x))
                rp.append(state)
            macro_paths.append(mp)
            regime_paths.append(rp)
        return {"macro": macro_paths, "regime": regime_paths, "features": self.features}


def label_regimes(hmm):
    """Give the HMM's numbered states HUMAN labels from their fitted macro means, so a
    reader never sees 'regime 0/1'. Rank by a stress score (high inflation + high rate +
    LOW oil = stress) on the standardized means; tier into Calme / Intermediaire / Stress
    and append the dominant driver (e.g. 'oil bas')."""
    feats, mu, K = hmm["features"], hmm["mu"], hmm["K"]
    idx = {f: i for i, f in enumerate(feats)}

    def score(k):
        s = 0.0
        if "cpi_yoy" in idx:
            s += mu[k][idx["cpi_yoy"]]
        if "money_market" in idx:
            s += mu[k][idx["money_market"]]
        if "oil_brent" in idx:
            s -= mu[k][idx["oil_brent"]]
        return s

    tiers = {1: ["Unique"], 2: ["Calme", "Stress"],
             3: ["Calme", "Intermediaire", "Stress"]}.get(K, [f"R{i}" for i in range(K)])
    order = sorted(range(K), key=score)                 # ascending stress
    labels = [f"R{k}" for k in range(K)]
    for rank, k in enumerate(order):
        base = tiers[rank] if rank < len(tiers) else f"R{k}"
        # dominant-driver hint from the most extreme standardized mean
        hint = ""
        if "oil_brent" in idx:
            ov = mu[k][idx["oil_brent"]]
            if ov < -0.5:
                hint = " (oil bas)"
            elif ov > 0.5:
                hint = " (oil haut)"
        labels[k] = base + hint
    return labels


def _categorical(rng, probs):
    u, c = rng.random(), 0.0
    for i, p in enumerate(probs):
        c += p
        if u <= c:
            return i
    return len(probs) - 1


# --------------------------------------------------------------------------- #
# IMPOSED crisis overlay: the tail the data cannot teach us.
# --------------------------------------------------------------------------- #
def oil_stress_index(oil_path, oil0):
    """Stress in [0,1] from the oil DRAWDOWN vs the starting level: max(0, (oil0-oil_t)/oil0).
    A 50% oil crash -> stress 0.5. FX depreciation could be added the same way."""
    return [max(0.0, (oil0 - o) / oil0) if oil0 else 0.0 for o in oil_path]


def apply_deposit_flight(B_runoff, stress, elasticity):
    """Impose EXCESS attrition on the run-off during stress. excess hazard in month t =
    elasticity * stress_t (capped at 1). B'(t) = B(t) * prod_{s<=t}(1 - excess_s). The
    elasticity is an ALM ASSUMPTION (analog/expert), NOT fitted -> this is what lets the
    model produce an oil->dinar->flight tail absent from the sample."""
    H = len(B_runoff) - 1
    out = [B_runoff[0]]
    surv = 1.0
    for t in range(1, H + 1):
        s = stress[t - 1] if t - 1 < len(stress) else (stress[-1] if stress else 0.0)
        surv *= (1.0 - min(1.0, elasticity * s))
        out.append(B_runoff[t] * surv)
    return out


def wal(B):
    return sum(B[1:])


def positioning_distribution(B_runoff, macro_paths, oil_idx, oil0, elasticity,
                             levels=(5, 50, 95)):
    """For each simulated macro path: derive the oil stress, impose deposit flight, and
    collect the stressed WAL -> distribution (the economic positioning view). Returns WAL
    percentiles + the run-off band."""
    H = len(B_runoff) - 1
    wals, curves = [], []
    for path in macro_paths:
        oil = [row[oil_idx] for row in path]
        stress = oil_stress_index(oil, oil0)
        Bp = apply_deposit_flight(B_runoff, stress, elasticity)
        wals.append(wal(Bp))
        curves.append(Bp)
    wals.sort()
    band = {lv: [] for lv in levels}
    for t in range(H + 1):
        col = sorted(c[t] for c in curves)
        for lv in levels:
            band[lv].append(_pct(col, lv))
    return {"n_paths": len(macro_paths), "wal_pct": {lv: _pct(wals, lv) for lv in levels},
            "B_pct": band, "levels": list(levels), "H": H,
            "wal_base": wal(B_runoff)}


def imposed_crisis(B_runoff, base_rate, B0, oil_drop=0.40, months=6, elasticity=0.8,
                   irrbb_mod=None):
    """A named REVERSE-STRESS scenario with imposed severity: an oil crash of `oil_drop`
    for `months`, deposit-flight elasticity `elasticity`. Returns the stressed run-off, WAL,
    and (if irrbb is passed) the dEVE under the current curve. Severity is a governance
    assumption, printed as such."""
    H = len(B_runoff) - 1
    stress = [oil_drop if t < months else oil_drop * 0.5 for t in range(H)]  # crash then partial
    Bp = apply_deposit_flight(B_runoff, stress, elasticity)
    out = {"oil_drop": oil_drop, "months": months, "elasticity": elasticity,
           "B_stressed": Bp, "wal_base": wal(B_runoff), "wal_stressed": wal(Bp),
           "wal_shortening_mo": wal(B_runoff) - wal(Bp)}
    if irrbb_mod is not None:
        de_base = irrbb_mod.delta_eve(B0, B_runoff, base_rate)["down_200bp"]["delta_eve"]
        de_str = irrbb_mod.delta_eve(B0, Bp, base_rate)["down_200bp"]["delta_eve"]
        out["delta_eve_down200_base"] = de_base
        out["delta_eve_down200_crisis"] = de_str
    return out


def _pct(sorted_vals, p):
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo = int(k)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] * (1 - (k - lo)) + sorted_vals[hi] * (k - lo)


# --------------------------------------------------------------------------- #
def _self_test():
    # a 2-regime skeleton: regime 0 = calm (oil ~100), regime 1 = stress (oil ~55)
    A = [[0.95, 0.05], [0.30, 0.70]]
    features = ["oil"]
    mu = [[100.0], [55.0]]
    sd = [[3.0], [8.0]]
    rm = RegimeMacro(A, mu, sd, features, kappa=0.4, df=5)

    sim = rm.simulate(x0=[100.0], T=24, n_paths=200, seed=1)
    assert len(sim["macro"]) == 200 and len(sim["macro"][0]) == 24
    # fat tails: some paths must dip well below the calm mean (a real drawdown)
    mins = [min(r[0] for r in p) for p in sim["macro"]]
    assert min(mins) < 80.0, f"no oil drawdown simulated (min {min(mins):.1f})"

    # baseline sticky run-off
    H = 120
    B = [max(0.0, 1.0 - 0.5 * t / H) for t in range(H + 1)]

    # crisis overlay strictly shortens the run-off, monotone in elasticity
    stress = [0.4] * H
    B_lo = apply_deposit_flight(B, stress, 0.2)
    B_hi = apply_deposit_flight(B, stress, 0.8)
    assert all(B_hi[t] <= B_lo[t] + 1e-12 <= B[t] + 1e-12 for t in range(H + 1))
    assert wal(B_hi) < wal(B_lo) < wal(B), (wal(B_hi), wal(B_lo), wal(B))

    # positioning distribution: ordered WAL percentiles, base >= median (flight only shortens)
    dist = positioning_distribution(B, sim["macro"], oil_idx=0, oil0=100.0, elasticity=0.6)
    w = dist["wal_pct"]
    assert w[5] <= w[50] <= w[95] <= dist["wal_base"] + 1e-9

    # imposed reverse-stress crisis
    import irrbb
    cr = imposed_crisis(B, base_rate=0.03, B0=1_000_000.0, oil_drop=0.4, months=6,
                        elasticity=0.8, irrbb_mod=irrbb)
    assert cr["wal_stressed"] < cr["wal_base"]
    # crisis makes the down-200bp dEVE LESS negative (shorter book = less rate-down exposure)
    assert cr["delta_eve_down200_crisis"] > cr["delta_eve_down200_base"]

    print("macro_sim self-test PASSED")
    print(f"  regime-switching AR + Student-t: {len(sim['macro'])} paths, "
          f"oil min {min(mins):.1f} (calm mean 100)")
    print(f"  WAL 90% positioning band = [{w[5]:.1f}, {w[95]:.1f}] mo (base {dist['wal_base']:.1f})")
    print(f"  IMPOSED crisis (oil -40%/6mo, eta 0.8): WAL {cr['wal_base']:.1f} -> "
          f"{cr['wal_stressed']:.1f} mo (shortening {cr['wal_shortening_mo']:.1f})")


if __name__ == "__main__":
    _self_test()
