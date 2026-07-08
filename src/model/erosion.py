"""Balance-erosion model r_i(t) -- the second half of run-off (PLANv2 6.5).

Attrition A(t) says whether the account survives; EROSION says how much balance a
SURVIVING account keeps. For DAV this is usually material (clients keep the account
but draw it down). We model the monthly log-balance increment on surviving accounts
with a LinearElasticNet (Huber option for outlier-robustness), then

    r_i(t) = B_i(t)/B_i(0) | alive = exp( sum_{h<=t} E[d_logbal_h | features] )

Same PIT / walk-forward / balance-weighting discipline as the hazard. Combined with
A(t): B_i(t) = B_i(0) * A_i(t) * r_i(t) (survival.py).
"""
from __future__ import annotations

import math

from linmodel import LinearElasticNet


class ErosionModel:
    def __init__(self, l1=0.0, l2=1e-5, loss="ls"):
        self.l1, self.l2, self.loss = l1, l2, loss

    def fit(self, rows, features, target="d_logbal", weight_key="weight"):
        self.features = features
        X = [[r[f] for f in features] for r in rows]
        y = [r[target] for r in rows]
        w = [r.get(weight_key, 1.0) for r in rows]
        self.model = LinearElasticNet(l1=self.l1, l2=self.l2, loss=self.loss).fit(X, y, w=w)
        self.coef_ = dict(zip(features, self.model.coef_))
        self.intercept_ = self.model.intercept_
        return self

    def expected_increment(self, featdict):
        x = [[featdict[f] for f in self.features]]
        return self.model.predict(x)[0]

    def retention_path(self, feat_by_horizon):
        """r(t) for t=0..H. feat_by_horizon[h] = feature dict at horizon h (h>=1).
        Returns [1.0, exp(g1), exp(g1+g2), ...]."""
        out, cum = [1.0], 0.0
        for h in sorted(feat_by_horizon):
            cum += self.expected_increment(feat_by_horizon[h])
            out.append(math.exp(cum))
        return out


if __name__ == "__main__":
    from synthetic_dav import generate, to_erosion_rows, TRUE_EROSION

    d = generate(n_accounts=600, T=120, seed=2)
    rows = to_erosion_rows(d)
    feats = ["macro", "seasoning"]
    m = ErosionModel(l1=0.0, l2=1e-6, loss="ls").fit(rows, feats)
    print("erosion coefficient recovery (monthly log-balance drift):")
    print(f"  {'param':<12}{'true':>10}{'fit':>10}")
    print(f"  {'intercept':<12}{TRUE_EROSION['intercept']:>10.4f}{m.intercept_:>10.4f}")
    for f in feats:
        print(f"  {f:<12}{TRUE_EROSION[f]:>10.4f}{m.coef_[f]:>10.4f}")

    # retention curve over 12 months at the mean macro, fixed seasoning
    mbar = sum(d["macro"]) / len(d["macro"])
    fbh = {h: {"macro": mbar, "seasoning": 0.6} for h in range(1, 13)}
    r = m.retention_path(fbh)
    print(f"\nr(t) retention over 12m (mean macro): "
          f"{[round(x,3) for x in r]}")
    print(f"  12m balance retained on survivors: {r[-1]*100:.1f}%  monotone="
          f"{all(r[i]>=r[i+1] for i in range(len(r)-1)) or all(r[i]<=r[i+1] for i in range(len(r)-1))}")

    # Huber robustness check: inject a few corrupt increments
    import random
    rng = random.Random(0)
    corrupt = [dict(x) for x in rows]
    for i in rng.sample(range(len(corrupt)), int(0.03 * len(corrupt))):
        corrupt[i]["d_logbal"] += rng.choice([-1, 1]) * 5.0
    ls = ErosionModel(loss="ls").fit(corrupt, feats)
    hb = ErosionModel(loss="huber").fit(corrupt, feats)
    err = lambda mm: abs(mm.coef_["macro"] - TRUE_EROSION["macro"])
    print(f"\nwith 3% corrupt increments (true macro {TRUE_EROSION['macro']}):")
    print(f"  LS    macro={ls.coef_['macro']:+.4f}  err={err(ls):.4f}")
    print(f"  Huber macro={hb.coef_['macro']:+.4f}  err={err(hb):.4f}  "
          f"more robust={err(hb)<err(ls)}")
