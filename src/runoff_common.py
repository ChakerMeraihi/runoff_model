"""Shared helpers for the runoff orchestrators (pure stdlib).

Loaded by runoff_eval.py (HP selection + OOS performance, reporting) and
runoff_fit.py (deployed fit on ALL data). Keeping these here means eval and fit
agree exactly on how the panel is read, features are chosen, and the hazard is fit.
"""
from __future__ import annotations

import csv
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for sub in ("model", "panel", "data"):
    p = os.path.join(HERE, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from hazard import LogisticElasticNet                      # noqa: E402
from hmm_regime import GaussianHMM, select_k_bic, _log_gauss_diag  # noqa: E402

ART = os.path.join(HERE, "_artifacts")
DEFAULT_FEATURES = ["seasoning", "log_balance", "cpi_yoy", "money_market",
                    "oil_brent", "ramadan_frac"]
HMM_CANDIDATE_MACRO = ("cpi_yoy", "money_market", "oil_brent", "z_macro")


def load_panel_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _numeric(rows, features, event_key, weight_key, month_key):
    out = []
    for r in rows:
        try:
            rec = {f: float(r[f]) for f in features}
            rec[event_key] = int(float(r[event_key]))
            rec[month_key] = int(float(r[month_key]))
            rec[weight_key] = (float(r[weight_key]) if weight_key in r and r[weight_key] != ""
                               else 1.0)
        except (KeyError, ValueError, TypeError):
            continue
        # carry id/segment + extra macro columns (FX / parallel-premium) for augment_features;
        # these are passthrough, NOT base model features (augment gates them).
        for k in ("account_id", "segment", "log_balance",
                  "parallel_premium_pct", "usd_dzd", "eur_dzd"):
            if k in r and k not in rec:
                rec[k] = r[k]
        out.append(rec)
    return out


def ensure_demo_panel():
    """Build the real-format synthetic DAV panel.csv for the no-arg demo, so the demo
    exercises the SAME pipeline as real data (money-market stress, balance erosion,
    regime feature) instead of a degenerate toy. Built once if missing; deterministic
    (fixed seed). On the work PC you pass the real panel.csv and this is never called."""
    panel_csv = os.path.join(HERE, "panel", "_out", "panel.csv")
    if os.path.exists(panel_csv):
        return panel_csv
    panel_dir = os.path.join(HERE, "panel")
    if panel_dir not in sys.path:
        sys.path.insert(0, panel_dir)
    import synth_dav_files
    import panel_builder
    synth_dir = os.path.join(panel_dir, "_synth")
    synth_dav_files.generate(synth_dir, n_clients=300, seed=1)
    macro = os.path.join(HERE, "data", "_out", "macro_panel_pit.csv")
    macro = macro if os.path.exists(macro) else None
    rows, _ = panel_builder.build_panel(synth_dir, floor=50.0, macro_pit=macro)
    panel_builder.write_panel(rows, panel_csv)
    return panel_csv


def prepare(panel_path=None):
    """Return (rows, features, weight_key, month_key). With a path -> that panel.csv;
    without -> the real-format synthetic demo panel (built via ensure_demo_panel).
    Both branches now use the identical panel schema, so demo == production path."""
    if not panel_path:
        panel_path = ensure_demo_panel()
    raw = load_panel_csv(panel_path)
    feats = [f for f in DEFAULT_FEATURES if raw and f in raw[0]]
    rows = _numeric(raw, feats, "event", "balance_kda", "month_int")
    return rows, feats, "balance_kda", "month_int"


def fit_hazard(rows, features, l1, l2, weight_key, epochs=400):
    X = [[r[f] for f in features] for r in rows]
    y = [r["event"] for r in rows]
    w = [r[weight_key] for r in rows]
    return LogisticElasticNet(l1=l1, l2=l2, solver="cd", epochs=epochs).fit(X, y, w=w)


def serialize_hazard(m):
    return {"coef_std": m.coef_std_, "intercept_std": m.intercept_std_,
            "scaler_mu": m.scaler.mu, "scaler_sd": m.scaler.sd,
            "coef_raw": m.coef_, "intercept_raw": m.intercept_}


def fit_erosion(panel_rows, month_key="month_int", weight_key="balance_kda"):
    """Fit the balance-erosion r(t) model on monthly log-balance increments derived
    from the panel (PLANv2 6.5). Returns a serializable param dict, or None if the
    panel lacks the columns to compute increments. Macro/seasoning drivers only."""
    from erosion import ErosionModel
    # need per-account ordered log_balance to form increments
    need = {"account_id", "log_balance", month_key}
    if panel_rows and not need.issubset(panel_rows[0]):
        return None
    by_acc = {}
    for r in panel_rows:
        by_acc.setdefault(r["account_id"], []).append(r)
    feats = [f for f in ("cpi_yoy", "money_market", "oil_brent", "z_macro", "seasoning")
             if panel_rows and f in panel_rows[0]]
    if not feats:
        return None
    erows = []
    for _, rs in by_acc.items():
        rs = sorted(rs, key=lambda r: r[month_key])
        for k in range(1, len(rs)):
            if rs[k][month_key] != rs[k - 1][month_key] + 1:
                continue
            rec = {f: float(rs[k][f]) for f in feats}
            rec["d_logbal"] = float(rs[k]["log_balance"]) - float(rs[k - 1]["log_balance"])
            rec["weight"] = float(rs[k - 1].get(weight_key, 1.0) or 1.0)
            erows.append(rec)
    if len(erows) < 50:
        return None
    m = ErosionModel(l1=0.0, l2=1e-5, loss="huber").fit(erows, feats)
    return {"features": feats, "coef": m.coef_, "intercept": m.intercept_}


def _macro_series(rows, macro_feats, month_key):
    by_month = {}
    for r in rows:
        by_month.setdefault(r[month_key], [r[f] for f in macro_feats])
    months = sorted(by_month)
    return months, [by_month[m] for m in months]


def fit_hmm(rows, features, month_key, ks=(2, 3)):
    """Fit the regime HMM on the macro series with STANDARDIZED emissions and a
    BIC-selected number of states (PLANv2 6.7 Role-1). Serializes the input
    standardizer (in_mu/in_sd) so the deployed filter reproduces it exactly."""
    macro_feats = [f for f in HMM_CANDIDATE_MACRO if f in features]
    if not macro_feats:
        return None
    _, series = _macro_series(rows, macro_feats, month_key)
    if len(series) < 24:
        return None
    hmm, K, bic_by_k = select_k_bic(series, ks=ks)
    return {"K": K, "features": macro_feats, "mu": hmm.mu, "var": hmm.var,
            "A": hmm.A, "pi": hmm.pi, "in_mu": hmm.in_mu, "in_sd": hmm.in_sd,
            "bic_by_k": bic_by_k}


def regime_feature_names(K):
    """K-1 posterior columns (the last state is dropped: posteriors sum to 1)."""
    return [f"regime_p{k}" for k in range(K - 1)]


def filter_regime_series(hmm_params, series):
    """Causal filtered posterior P(state_t | macro_<=t) using FROZEN, standardized
    HMM params. Pure forward pass -> no look-ahead. Returns one K-vector per month."""
    K = hmm_params["K"]
    mu, var, A, pi = hmm_params["mu"], hmm_params["var"], hmm_params["A"], hmm_params["pi"]
    in_mu, in_sd = hmm_params.get("in_mu"), hmm_params.get("in_sd")

    def std(x):
        return x if in_mu is None else [(x[d] - in_mu[d]) / in_sd[d] for d in range(len(x))]

    out, prev = [], None
    for x in series:
        xz = std(x)
        lp = [_log_gauss_diag(xz, mu[k], var[k]) for k in range(K)]
        m = max(lp)
        b = [math.exp(v - m) for v in lp]
        a = ([pi[k] * b[k] for k in range(K)] if prev is None
             else [sum(prev[j] * A[j][k] for j in range(K)) * b[k] for k in range(K)])
        s = sum(a) or 1e-300
        a = [v / s for v in a]
        out.append(a)
        prev = a
    return out


def attach_regime_posterior(rows, hmm_params, month_key):
    """Append the filtered regime posterior as hazard features (Role-1), IN PLACE.
    The posterior at month m is macro-driven (same for every account at m) and causal.
    Returns the list of feature names added (empty if no HMM)."""
    if not hmm_params:
        return []
    months, series = _macro_series(rows, hmm_params["features"], month_key)
    post = filter_regime_series(hmm_params, series)
    pmap = dict(zip(months, post))
    names = regime_feature_names(hmm_params["K"])
    for r in rows:
        p = pmap.get(r[month_key])
        for k, nm in enumerate(names):
            r[nm] = p[k] if p else 0.0
    return names


def augment_features(rows, month_key="month_int"):
    """Add a DICTIONARY of explainable path / change / interaction features IN PLACE, then
    let L1 (elastic-net) select the useful ones -- 'generous engineering + sparse selection'.
    All strictly CAUSAL (trailing only): per-account balance path (drawdown, decline streak,
    momentum, own-mean gap) + shared macro path (oil/rate drawdown & momentum, rate surprise,
    real rate) + tenure nonlinearity + a few interactions. Returns the added feature names.
    Missing base columns are skipped gracefully. On Markovian data L1 drops most of these; on
    real deposit data (path-dependent flight) they carry the signal (see nonlin_experiment)."""
    have = set(rows[0].keys()) if rows else set()
    added = []

    def col(name):
        added.append(name)
        return name

    # ---- shared macro path (one value per month) ----
    def _month_series(feat):
        by = {}
        for r in rows:
            m = r[month_key]
            if m not in by and feat in r:
                try:
                    by[m] = float(r[feat])
                except (KeyError, ValueError):
                    pass
        return by

    # macro-path features are CROSS-SECTIONALLY CONSTANT (same for every account in a month)
    # -> highly collinear with each other + the base macro, which stalls coordinate descent
    # for little account-level signal. Keep ONLY oil_drawdown (the single stress signal) and
    # real_rate; the account-level VARIATION (and the real path signal) is in the BALANCE
    # features below.
    months = sorted({r[month_key] for r in rows})
    macro_path = {}
    if "oil_brent" in have:
        s = _month_series("oil_brent")
        cummax = -1e18
        for m in months:
            v = s.get(m)
            if v is None:
                continue
            cummax = max(cummax, v)
            macro_path.setdefault(m, {})["oil_drawdown"] = (cummax - v) / cummax if cummax > 0 else 0.0
        col("oil_drawdown")
    if {"money_market", "cpi_yoy"} <= have:
        col("real_rate")
    # Algeria FX-stress signals (esp. for the FX-sensitive vue_devises book): the parallel-
    # market "square" premium and the DZD depreciation rate. Macro (constant across accounts)
    # -> gated; expected to help the FX book specifically, dropped elsewhere.
    if "parallel_premium_pct" in have:
        sp = _month_series("parallel_premium_pct")
        for m in months:
            if sp.get(m) is not None:
                macro_path.setdefault(m, {})["parallel_premium"] = sp[m]
        col("parallel_premium")
    for fx, tag in (("usd_dzd", "usd_dep"), ("eur_dzd", "eur_dep")):
        if fx in have:
            s = _month_series(fx)
            ordered = [(m, s[m]) for m in months if s.get(m) is not None]
            for i, (m, v) in enumerate(ordered):
                v3 = ordered[max(0, i - 3)][1]
                macro_path.setdefault(m, {})[tag] = (v - v3) / v3 if v3 else 0.0   # depreciation
            col(tag)

    # ---- per-account balance path ----
    bal_col = "log_balance" if "log_balance" in have else None
    by_acc = {}
    for r in rows:
        by_acc.setdefault(r.get("account_id"), []).append(r)
    if bal_col:
        for _, rs in by_acc.items():
            rs.sort(key=lambda r: r[month_key])
            peak = -1e18
            peak_i = 0
            prev = None
            streak = 0
            hist = []
            diffs = []                                       # log-balance increments
            for i, r in enumerate(rs):
                try:
                    b = float(r[bal_col])
                except (KeyError, ValueError):
                    b = 0.0
                if b > peak:
                    peak, peak_i = b, i
                streak = streak + 1 if (prev is not None and b < prev) else 0
                if prev is not None:
                    diffs.append(b - prev)
                hist.append(b)
                mean = sum(hist) / len(hist)
                b3 = hist[-4] if len(hist) >= 4 else hist[0]
                b6 = hist[-7] if len(hist) >= 7 else hist[0]
                d6 = diffs[-6:]                              # trailing 6 increments
                vmean = (sum(d6) / len(d6)) if d6 else 0.0
                vol = (sum((x - vmean) ** 2 for x in d6) / len(d6)) ** 0.5 if len(d6) >= 2 else 0.0
                r["bal_drawdown"] = (peak - b) / peak if peak > 1e-9 else 0.0
                r["decline_streak"] = float(streak)
                r["bal_mom3"] = b - b3
                r["bal_accel"] = (b - b3) - (b3 - b6)        # change in momentum (2nd diff)
                r["bal_vs_mean"] = b - mean
                r["bal_vol6"] = vol                          # behavioral volatility (account-level)
                r["months_since_peak"] = float(i - peak_i)   # recency of the drawdown
                prev = b
        for nm in ("bal_drawdown", "decline_streak", "bal_mom3", "bal_accel", "bal_vs_mean",
                   "bal_vol6", "months_since_peak"):
            col(nm)

    # cross-sectional balance RANK within each month (is this a big/small depositor now):
    # percentile in [0,1], account-level variation the macro can't provide.
    if bal_col:
        by_month = {}
        for r in rows:
            by_month.setdefault(r[month_key], []).append(r)
        for _, mr in by_month.items():
            vals = sorted((float(x.get(bal_col, 0.0) or 0.0), idx) for idx, x in enumerate(mr))
            nrank = len(mr)
            for rank, (_, idx) in enumerate(vals):
                mr[idx]["xs_rank_bal"] = rank / (nrank - 1) if nrank > 1 else 0.5
        col("xs_rank_bal")

    # real_rate + attach the macro-path columns row-wise. NOTE: we deliberately DROP the
    # polynomial/interaction terms (seasoning^2, young, oil*young, rate*seasoning) -- on the
    # L1 fit they were near-zero or heavily COLLINEAR (with their parents), which both adds
    # little signal and stalls coordinate descent. The kept set is the low-collinearity
    # path/change dictionary (drawdowns / momentum / surprise / real-rate / streak).
    for r in rows:
        if "cpi_yoy" in r and "money_market" in r:
            try:
                r["real_rate"] = float(r["money_market"]) - float(r["cpi_yoy"])
            except (KeyError, ValueError):
                r["real_rate"] = 0.0
        mp = macro_path.get(r[month_key], {})
        for k, v in mp.items():
            r[k] = v

    # de-dup preserving order, then GUARANTEE every engineered feature is present on EVERY
    # row (default 0.0) -- some months lack a macro value (e.g. early parallel-premium), and
    # the hazard fit requires a consistent feature vector across all rows.
    seen, names = set(), []
    for nm in added:
        if nm not in seen:
            seen.add(nm)
            names.append(nm)
    for r in rows:
        for nm in names:
            if nm not in r:
                r[nm] = 0.0
    return names


SIG_CHANNELS = ("log_balance", "money_market")


def attach_signatures(rows, month_key, channels=SIG_CHANNELS, depth=2, max_window=12):
    """Append truncated path-signature features (PLANv2 6.2) IN PLACE. At each row
    (account, month) the signature is of that account's OWN trailing path of `channels`
    up to that month -> strictly causal (no future, no cross-account leak), so unlike the
    regime HMM it needs no train-only fit. Returns the signature column names."""
    from sig_features import path_signature_features
    feats = [c for c in channels if rows and c in rows[0]]
    if len(feats) < 1:
        return []
    by_acc = {}
    for r in rows:
        by_acc.setdefault(r.get("account_id"), []).append(r)
    names = None
    for _, rs in by_acc.items():
        rs.sort(key=lambda r: r[month_key])
        path = []
        for r in rs:
            try:
                path.append([float(r[c]) for c in feats])
            except (KeyError, ValueError, TypeError):
                path.append([0.0] * len(feats))
            sig = path_signature_features(path[-max_window:], depth=depth, lead_lag_on=True)
            if names is None:
                names = [f"sig{i}" for i in range(len(sig))]
            for i, v in enumerate(sig):
                r[names[i]] = v
    return names or []


def lam_alpha_to_l1l2(lam, alpha):
    return lam * alpha, lam * (1 - alpha)


# --------------------------------------------------------------------------- #
# Run-off MODEL COMPARISON + SELECTION (PLANv2 5/6.6: convention vs ECM vs hazard)
# The deployed quantity is the cohort BOOK run-off B(t) = today's balance decaying.
# All three candidates produce B(0..H); we score each against the realised cohort
# book run-off on the frozen-OOS window and pick the winner (the Gate-B decision).
# --------------------------------------------------------------------------- #

def aggregate_for_ecm(rows, month_key, fit_max=None):
    """Monthly book aggregate for the ECM: (months, log_total_balance, rate, infl, season)."""
    agg = {}
    for r in rows:
        m = r[month_key]
        if fit_max is not None and m > fit_max:
            continue
        a = agg.setdefault(m, {"bal": 0.0, "rate": [], "infl": [], "ram": []})
        a["bal"] += float(r.get("balance_kda", 0.0) or 0.0)
        for key, col in (("rate", "money_market"), ("infl", "cpi_yoy"), ("ram", "ramadan_frac")):
            if col in r and r[col] not in ("", None):
                a[key].append(float(r[col]))
    months = sorted(agg)
    mean = lambda xs: (sum(xs) / len(xs)) if xs else 0.0
    logbal = [math.log(max(agg[m]["bal"], 1e-9)) for m in months]
    rate = [mean(agg[m]["rate"]) for m in months]
    infl = [mean(agg[m]["infl"]) for m in months]
    season = [mean(agg[m]["ram"]) for m in months]
    return months, logbal, rate, infl, season


def fit_ecm_model(rows, month_key, fit_max=None):
    """Fit the Engle-Granger ECM on the book aggregate -> elasticity, reversion, equilibrium
    (PLANv2 5b). Returns a serializable dict, or None if too short."""
    from ecm import fit_ecm
    months, logbal, rate, infl, season = aggregate_for_ecm(rows, month_key, fit_max=fit_max)
    if len(logbal) < 24:
        return None
    res = fit_ecm(logbal, rate, infl, season=season)
    lr, sr = res["long_run"], res["short_run"]
    half_life = (math.log(0.5) / math.log(1.0 + sr["reversion_speed_phi"])
                 if -1.0 < sr["reversion_speed_phi"] < 0.0 else None)
    return {"rate_elasticity": lr["rate_elasticity"], "infl_elasticity": lr["infl_elasticity"],
            "const": lr["const"], "reversion_phi": sr["reversion_speed_phi"],
            "half_life_months": half_life, "elasticity_ci": res["elasticity_block_ci"],
            "last_logbal": logbal[-1], "last_rate": rate[-1], "last_infl": infl[-1]}


def ecm_book_runoff(ecm, H, rate_bump=0.0):
    """Project the existing stock under held macro via the ECM reversion -> B(0..H).
    Caveat: the ECM is a NET-balance model (includes inflows); read as run-off it is
    'the stock reverting toward equilibrium' -- B>1 means the base is below equilibrium
    (growing), not decaying. Honest approximation, flagged in the report."""
    if not ecm:
        return None
    eq = ecm["const"] + ecm["rate_elasticity"] * (ecm["last_rate"] + rate_bump) \
        + ecm["infl_elasticity"] * ecm["last_infl"]
    phi = ecm["reversion_phi"]
    lb0 = ecm["last_logbal"]
    lb = lb0
    B = [1.0]
    for _h in range(1, H + 1):
        lb = lb + phi * (lb - eq)
        B.append(math.exp(lb - lb0))
    return B


def cohort_realized_runoff(rows, cutoff, H, month_key):
    """Realised book run-off of the cohort alive at `cutoff`: sum balance of those SAME
    accounts forward / their balance at cutoff (attrition + erosion combined)."""
    by_acc = {}
    for r in rows:
        by_acc.setdefault(r["account_id"], {})[r[month_key]] = float(r.get("balance_kda", 0.0) or 0.0)
    cohort = {a: mb[cutoff] for a, mb in by_acc.items() if cutoff in mb}
    B0 = sum(cohort.values()) or 1e-9
    B = [1.0]
    for h in range(1, H + 1):
        B.append(sum(by_acc[a].get(cutoff + h, 0.0) for a in cohort) / B0)
    return B


def convention_book_runoff(rows, asof, H, month_key, window=12, vol_life=3, core_life=60):
    """Convention core/volatile run-off for the cohort present at `asof`."""
    from convention import book_convention_runoff
    by_acc = {}
    for r in rows:
        if r[month_key] <= asof:
            by_acc.setdefault(r["account_id"], []).append(
                (r[month_key], float(r.get("balance_kda", 0.0) or 0.0)))
    series = []
    for _, lst in by_acc.items():
        lst.sort()
        if lst and lst[-1][0] == asof:                 # present at asof = in the cohort
            series.append([b for _, b in lst])
    return book_convention_runoff(series, H, window=window, vol_life=vol_life, core_life=core_life)


def roll_book_runoff(alive, proba_fn, inc_fn, H, season_step=1.0 / 40.0):
    """Generic hazard roll: alive=[(b0, featdict)] -> B(0..H)=balance-weighted A(t)*r(t).
    proba_fn(featdict)->hazard; inc_fn(featdict)->log-balance increment (or None)."""
    wsum = sum(b for b, _ in alive) or 1e-9
    B = [0.0] * (H + 1)
    for b0, feat in alive:
        S, cum = 1.0, 0.0
        B[0] += b0
        for h in range(1, H + 1):
            fd = dict(feat)
            if "seasoning" in fd:
                fd["seasoning"] = feat["seasoning"] + h
            if "z_seasoning" in fd:
                fd["z_seasoning"] = feat["z_seasoning"] + h * season_step
            S *= (1.0 - proba_fn(fd))
            if inc_fn:
                cum += inc_fn(fd)
            B[h] += b0 * S * (math.exp(cum) if inc_fn else 1.0)
    return [x / wsum for x in B]


def _erosion_inc_fn(erosion_params):
    if not erosion_params:
        return None
    ef, ec, ei = erosion_params["features"], erosion_params["coef"], erosion_params["intercept"]
    return lambda fd: ei + sum(ec[f] * fd.get(f, 0.0) for f in ef)


def hazard_cohort_runoff(rows, cutoff, features, hazard_model, erosion_params, H, month_key):
    """Roll the (dev-fit) hazard+erosion forward for the cohort alive at `cutoff`
    -> predicted book run-off B(0..H), comparable to the realised cohort run-off."""
    alive = []
    for r in rows:
        if r[month_key] == cutoff and int(r.get("event", 0)) == 0:
            try:
                feat = {f: float(r[f]) for f in features}
            except (KeyError, ValueError):
                continue
            alive.append((float(r.get("balance_kda", 1.0) or 1.0), feat))
    if not alive:
        return None
    proba_fn = lambda fd: hazard_model.predict_proba([[fd[f] for f in features]])[0]
    return roll_book_runoff(alive, proba_fn, _erosion_inc_fn(erosion_params), H)


def _mae(a, b):
    n = min(len(a), len(b))
    return sum(abs(a[i] - b[i]) for i in range(n)) / n if n else float("inf")


def compare_runoff_models(rows, cutoff, H, hazard_B, month_key, ecm=None):
    """Score convention / ECM / hazard book run-off against the realised cohort run-off
    over (cutoff, cutoff+H]. hazard_B = the hazard's predicted B(0..H) for that cohort.
    Returns {'realized':..., 'models':{name:{'mae','B'}}, 'winner':name}."""
    realized = cohort_realized_runoff(rows, cutoff, H, month_key)
    conv_B, core_share = convention_book_runoff(rows, cutoff, H, month_key)
    models = {"convention": {"B": conv_B, "mae": _mae(conv_B, realized), "core_share": core_share}}
    if ecm:
        ecm_B = ecm_book_runoff(ecm, H)
        models["ecm"] = {"B": ecm_B, "mae": _mae(ecm_B, realized)}
    if hazard_B:
        models["hazard"] = {"B": hazard_B, "mae": _mae(hazard_B, realized)}
    winner = min(models, key=lambda k: models[k]["mae"])
    return {"realized": realized, "models": models, "winner": winner,
            "cutoff_month": cutoff, "H": H}
