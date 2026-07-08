"""Wire path signatures into the hazard feature set (PLANv2 6.2 nonlinearity).

The deployed hazard uses raw point-in-time features. A SIGNATURE feature builder adds
path-dependent terms: at decision time tau it computes the truncated signature of the
account's recent (log-balance, macro) path -- time-augmented, basepointed, lead-lagged
-- so the hazard can see the TRAJECTORY (e.g. a sustained downtrend), not just the
current level. This module turns per-account histories into augmented feature rows and
is switchable raw|signature in the orchestration.

Validation here proves signatures EARN their place: on a path-dependent synthetic
hazard, signature features beat raw features out-of-time.
"""
from __future__ import annotations

from signatures import signature_vector, time_augment, add_basepoint, lead_lag


def path_signature_features(path, depth=2, lead_lag_on=True):
    """path = list of channel-vectors over [entry..tau] (e.g. [[logbal, macro], ...]).
    Returns a flat signature feature vector (time-aug + basepoint [+ lead-lag])."""
    if len(path) < 2:
        path = [path[0], path[0]] if path else [[0.0], [0.0]]
    aug = add_basepoint(time_augment(path))
    if lead_lag_on:
        aug = lead_lag(aug)
    vec, _ = signature_vector(aug, depth=depth)
    return vec


def build_rows(accts_paths, depth=2, max_window=12, lead_lag_on=True):
    """accts_paths = list of dicts:
        {account, month_int, event, channels:[[..],..] up to tau, base:{raw feats}, weight}
    Returns (rows, feature_names) with raw feats + signature terms concatenated."""
    rows = []
    nsig = None
    for r in accts_paths:
        ch = r["channels"][-max_window:]
        sig = path_signature_features(ch, depth=depth, lead_lag_on=lead_lag_on)
        nsig = len(sig)
        feat = dict(r["base"])
        for i, v in enumerate(sig):
            feat[f"sig{i}"] = v
        feat["event"] = r["event"]
        feat["month_int"] = r["month_int"]
        feat["weight"] = r.get("weight", 1.0)
        rows.append(feat)
    sig_names = [f"sig{i}" for i in range(nsig or 0)]
    return rows, sig_names


if __name__ == "__main__":
    import time
    from synthetic_dav import generate
    from hazard import LogisticElasticNet
    from hp_search import nll, brier
    from splitter import walk_forward

    # Build raw rows and signature-augmented rows from the SAME path-dependent panel.
    d = generate(n_accounts=250, T=96, seed=4)
    macro, ram = d["macro"], d["ramadan"]
    raw_rows, oracle_rows, sig_inputs = [], [], []
    for ac in d["accts"]:
        months = sorted(ac["path"])
        chans = []
        for k, t in enumerate(months):
            lb = ac["path"][t]
            chans.append([lb, macro[t]])
            season = ac["season0"] + (t - ac["entry"])
            base = {"seasoning": (season - 60) / 40.0, "log_balance": (lb - 7) / 1.4,
                    "macro": macro[t], "ramadan": ram[t]}
            ev = 1 if ac["event"] == t else 0
            prev_lb = ac["path"][months[k - 1]] if k > 0 else lb
            downtrend = max(0.0, prev_lb - lb)              # the oracle path feature
            raw_rows.append({**base, "event": ev, "month_int": t, "weight": 2.718 ** lb})
            oracle_rows.append({**base, "downtrend": downtrend, "event": ev,
                                "month_int": t, "weight": 2.718 ** lb})
            sig_inputs.append({"account": ac["id"], "month_int": t, "event": ev,
                               "channels": [list(c) for c in chans], "base": base,
                               "weight": 2.718 ** lb})

    t0 = time.time()
    sig_rows, sig_names = build_rows(sig_inputs, depth=2, max_window=9, lead_lag_on=True)
    t_build = time.time() - t0

    raw_feats = ["seasoning", "log_balance", "macro", "ramadan"]
    oracle_feats = raw_feats + ["downtrend"]
    all_feats = raw_feats + sig_names

    # Use the PRODUCTION selector (hp_search) on each feature set -- it tunes the L1
    # strength (lambda x alpha) exactly as deployment does. No hand-tuning.
    from hp_search import hp_search

    def select(rows, feats):
        best, _ = hp_search(rows, feats, H=1, min_train=48, step=18, val_len=12,
                            score="nll", rule="min", n_jobs=None)
        return best

    def n_surviving_sig(best):
        l1 = best["lambda"] * best["alpha"]
        l2 = best["lambda"] * (1 - best["alpha"])
        mdl = LogisticElasticNet(l1=l1, l2=l2, solver="cd", max_irls=8).fit(
            [[r[f] for f in all_feats] for r in sig_rows], [r["event"] for r in sig_rows],
            w=[r["weight"] for r in sig_rows])
        return sum(1 for c in mdl.coef_[len(raw_feats):] if abs(c) > 1e-8)

    braw = select(raw_rows, raw_feats)
    boracle = select(oracle_rows, oracle_feats)
    bsig = select(sig_rows, all_feats)
    kept = n_surviving_sig(bsig)
    print("path-dependent hazard -- HP-SEARCH-SELECTED out-of-time NLL (grid tunes L1):")
    print(f"  raw features      ({len(raw_feats)}): NLL={braw['score']:.5f}  "
          f"(L1strength={braw['lambda']*braw['alpha']:.1e})")
    print(f"  raw + ORACLE path ({len(oracle_feats)}): NLL={boracle['score']:.5f}  "
          f"(path signal real: helps={boracle['score'] < braw['score']})")
    print(f"  raw + signatures  ({len(all_feats)}): NLL={bsig['score']:.5f}  "
          f"L1 selected lambda={bsig['lambda']:.1e} alpha={bsig['alpha']} -> "
          f"kept {kept}/{len(sig_names)} sig terms")
    print(f"  -> signatures beat raw (grid-tuned): {bsig['score'] < braw['score']} "
          f"({100*(braw['score']-bsig['score'])/braw['score']:+.2f}%)")
    print(f"  L1 acts as the selector: pruned {len(sig_names)-kept}/{len(sig_names)} "
          f"signature terms. (Synthetic path=crude 1-step drop; real paths richer -> "
          f"re-test on real panel.)")
    print(f"  signature build time: {t_build:.2f}s for {len(sig_rows)} rows")
