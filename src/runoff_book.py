"""runoff_book.py -- MULTI-PRODUCT behavioral run-off + book aggregation (pure stdlib).

The single-product entrypoints (runoff_eval/fit/daily) model ONE pooled panel. This
driver runs the SAME validated machinery once per BEHAVIORAL book (comptes a vue dinars /
devises, epargne, decouverts, HB engagement -- see model/products.py), then aggregates the
per-segment run-off into a whole-book curve by BALANCE weight:

    for each behavioral segment s:
        evaluate(seg_rows)        -> HPs + frozen-OOS headline + model comparison   (reused)
        fit_deployed(seg_rows)    -> hazard A_s(t) + erosion r_s(t)                  (reused)
        book_survival(alive_s)    -> B_s(t) = A_s(t)*r_s(t), + a +200bp stress       (reused)
        W_s = current book balance of s
    B_book(t) = sum_s W_s * B_s(t) / sum_s W_s          (balance-weighted book run-off)

Nothing here reimplements the numerics -- it imports the frozen scorers from runoff_daily
and the fit/eval functions, so the per-segment model is bit-for-bit the single-product one.
Contractual books (garantie, ...) are tagged in the panel but EXCLUDED (products.py), because
the EFM stock snapshot carries no maturity to build a contractual echeancier from.

Usage:  python runoff_book.py [panel.csv]        (no arg -> multi-segment synthetic demo)
Writes: _out/book/book_runoff.json , _out/book/model_<segment>.json , _out/book/report.xlsx
"""
from __future__ import annotations

import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for sub in ("model", "panel", "data"):
    p = os.path.join(HERE, sub)
    if p not in sys.path:
        sys.path.insert(0, p)

from runoff_common import (prepare, ensure_demo_panel, filter_regime_series,      # noqa: E402
                           fit_hmm, HMM_CANDIDATE_MACRO)
from runoff_eval import evaluate                                            # noqa: E402
from runoff_fit import fit_deployed                                         # noqa: E402
from runoff_daily import FrozenHazard, FrozenErosion, book_survival, wal    # noqa: E402
from products import behavioral_keys, is_behavioral, display_name          # noqa: E402

OUT = os.path.join(HERE, "_out", "book")
H_FULL = 360                                        # full ecoulement horizon (30y, IRRBB)
RUNOFF_EPS = 0.005                                  # "run-off to 0" = B(t) below 0.5%
SEASON_STEP = 1.0 / 40.0
STRESS_BP = 2.0                                    # +200bp on the money-market rate


def roll_book_fast(alive, hazard, erosion, H, season_step=SEASON_STEP):
    """O(H*n_alive) balance-weighted book roll -> (B, A). Accumulates survival S and the
    erosion cumulant incrementally across the horizon instead of re-rolling 1..h for every
    h (which is what runoff_daily.book_survival does, O(H^2) -- fine at H=12, too slow at
    H=360). Identical math; a self-test asserts parity with book_survival at H=12."""
    wsum = sum(w for w, _ in alive) or 1e-12
    B = [0.0] * (H + 1)
    A = [0.0] * (H + 1)
    B[0] = A[0] = 1.0
    per = [[w, dict(feat), 1.0, 0.0] for w, feat in alive]   # w, feat, S, cum_g
    for h in range(1, H + 1):
        b_tot = a_tot = 0.0
        for st in per:
            w, feat, S, cum = st
            fd = dict(feat)
            if "seasoning" in fd:
                fd["seasoning"] = feat["seasoning"] + h
            if "z_seasoning" in fd:
                fd["z_seasoning"] = feat["z_seasoning"] + h * season_step
            S *= (1.0 - hazard.proba(fd))
            if erosion:
                cum += erosion.increment(fd)
            st[2], st[3] = S, cum
            r = math.exp(cum) if erosion else 1.0
            a_tot += w * S
            b_tot += w * S * r
        A[h] = a_tot / wsum
        B[h] = b_tot / wsum
    return B, A


def group_by_segment(rows):
    by = {}
    for r in rows:
        by.setdefault(r.get("segment", "other"), []).append(r)
    return by


def _macro_series(seg_rows, hmm_feats, month_key):
    """One macro row per month (macro is book-wide, identical across accounts)."""
    bym = {}
    for r in seg_rows:
        m = r[month_key]
        if m not in bym:
            try:
                bym[m] = [float(r[f]) for f in hmm_feats]
            except (KeyError, ValueError):
                pass
    return [bym[m] for m in sorted(bym)]


def score_segment(seg_rows, model, base_features, month_key, weight_key, H):
    """Roll the frozen per-segment hazard + erosion for the cohort alive at the segment's
    latest month -> (B, A, r, B_stressed, W, n_alive). B=A*r book run-off (=1 at t=0)."""
    cur = max(r[month_key] for r in seg_rows)
    by_acc = {}
    for r in seg_rows:
        by_acc.setdefault(r["account_id"], []).append(r)

    # current regime posterior (frozen HMM filter) if the deployed model uses it
    regime = None
    reg_names = model.get("regime_features") or []
    if model.get("use_regime") and model.get("hmm") and reg_names:
        series = _macro_series(seg_rows, model["hmm"]["features"], month_key)
        post = filter_regime_series(model["hmm"], series)
        regime = post[-1] if post else None

    alive = []
    for _, rs in by_acc.items():
        rs.sort(key=lambda r: r[month_key])
        last = rs[-1]
        if last[month_key] != cur or int(last.get("event", 0)) != 0:
            continue
        try:
            feat = {f: float(last[f]) for f in base_features}
        except (KeyError, ValueError):
            continue
        if regime:
            for k, nm in enumerate(reg_names):
                feat[nm] = regime[k] if k < len(regime) else 0.0
        w = float(last.get(weight_key, 1.0) or 1.0)
        alive.append((w, feat))
    if not alive:
        return None

    hazard = FrozenHazard(model)
    erosion = FrozenErosion(model["erosion"]) if model.get("erosion") else None
    B, A = roll_book_fast(alive, hazard, erosion, H)

    stressed = B
    if "money_market" in model["features"]:
        bumped = [(w, {**f, "money_market": f.get("money_market", 0.0) + STRESS_BP})
                  for w, f in alive]
        stressed, _ = roll_book_fast(bumped, hazard, erosion, H)

    r = [(B[i] / A[i] if A[i] > 1e-12 else 1.0) for i in range(len(B))]
    W = sum(w for w, _ in alive)
    runoff_month = next((h for h in range(len(B)) if B[h] < RUNOFF_EPS), None)
    return {"B": B, "A": A, "r": r, "B_stressed": stressed, "W": W,
            "n_alive": len(alive), "asof_month": cur, "runoff_month": runoff_month}


def _augment_gate(seg_rows, base_features, aug_feats, weight_key, month_key,
                  val_months=18, margin=0.995):
    """Decide base vs base+augment for ONE segment on a validation split -- keep the
    engineered features only if they LOWER validation NLL by a margin (parsimony). Fully
    automatic per-segment gate; prevents augment overfitting thin books. Uses a moderately
    regularized HP (also tames quasi-separation), so the two gate fits are fast + stable."""
    from runoff_common import fit_hazard, lam_alpha_to_l1l2
    if not aug_feats:
        return base_features, False
    months = sorted({r[month_key] for r in seg_rows})
    if len(months) < val_months + 12:
        return base_features, False
    cut = months[-val_months - 1]
    tr = [r for r in seg_rows if r[month_key] <= cut]
    va = [r for r in seg_rows if r[month_key] > cut]
    if not tr or not va:
        return base_features, False
    l1, l2 = lam_alpha_to_l1l2(0.01, 0.5)                 # moderate regularization for the gate

    def val_nll(feats):
        try:
            m = fit_hazard(tr, feats, l1, l2, weight_key)
        except Exception:
            return float("inf")
        yv = [int(float(r["event"])) for r in va]
        p = m.predict_proba([[float(r[f]) for f in feats] for r in va])
        return (-sum(yv[i] * math.log(max(p[i], 1e-12))
                     + (1 - yv[i]) * math.log(max(1 - p[i], 1e-12))
                     for i in range(len(yv))) / max(1, len(yv)))

    nll_base = val_nll(base_features)
    nll_aug = val_nll(base_features + aug_feats)
    if nll_aug < nll_base * margin:
        return base_features + aug_feats, True
    return base_features, False


def run_book(panel_path=None, H=H_FULL, val_months=18, test_months=18,
             min_rows=200, verbose=True, use_signatures=False, augment=False,
             crisis_elasticity=0.8, crisis_oil_drop=0.40, crisis_months=6):
    """Fit + score every behavioral segment and aggregate to a book run-off curve, and
    collect ALL diagnostics (training/validation/regime/break/macro) for the xlsx report.
    crisis_* = the imposed reverse-stress ALM assumptions (flight elasticity, oil-drop
    fraction, crash duration in months) surfaced on the Crise_Stress report sheet."""
    if not panel_path:
        panel_path = ensure_demo_panel()
    rows, features, weight_key, month_key = prepare(panel_path)
    # generous feature engineering (OPT-IN): attach the path/change dictionary columns, then
    # GATE them PER SEGMENT on a validation split (base vs base+augment) so they are kept only
    # where they genuinely help OOS -- automatic, no human. This prevents the overfit seen on
    # thin segments (e.g. vue_devises), while keeping them where they win (vue_dinars/epargne).
    aug = []
    if augment:
        from runoff_common import augment_features
        aug = augment_features(rows, month_key)          # attach columns only (no fit)
        if verbose and aug:
            print(f"  [features] {len(aug)} engineered features attached (per-segment gated): {aug}")
    by_seg = group_by_segment(rows)

    segments, skipped = {}, {}
    for key in behavioral_keys():
        seg_rows = by_seg.get(key)
        if not seg_rows:
            continue
        n_months = len({r[month_key] for r in seg_rows})
        if len(seg_rows) < min_rows or n_months < (val_months + test_months + 12):
            skipped[key] = {"reason": "too few rows/months",
                            "n_rows": len(seg_rows), "n_months": n_months}
            if verbose:
                print(f"  [skip] {key:<14} n_rows={len(seg_rows)} n_months={n_months}")
            continue

        # per-segment augment GATE (base vs base+augment on a validation split)
        seg_features, aug_kept = _augment_gate(seg_rows, features, aug, weight_key,
                                               month_key, val_months) if aug else (features, False)
        if verbose:
            print(f"  [fit ] {key:<14} n_rows={len(seg_rows)} n_months={n_months} "
                  f"augment={'KEPT' if aug_kept else 'dropped'} ...", flush=True)
        sel = evaluate(seg_rows, seg_features, weight_key, month_key,
                       val_months=val_months, test_months=test_months,
                       run_ablation=False, use_signatures=use_signatures)
        model = fit_deployed(seg_rows, seg_features, weight_key, month_key, sel)
        sc = score_segment(seg_rows, model, seg_features, month_key, weight_key, H)
        if not sc:
            skipped[key] = {"reason": "no alive cohort at latest month"}
            continue

        oos = sel.get("frozen_oos_eval", {})
        cmp = sel.get("runoff_model_comparison", {})
        segments[key] = {
            "display": display_name(key),
            "W": sc["W"], "n_alive": sc["n_alive"], "asof_month": sc["asof_month"],
            "B": sc["B"], "A": sc["A"], "r": sc["r"], "B_stressed": sc["B_stressed"],
            "WAL": wal(sc["B"]), "WAL_stressed": wal(sc["B_stressed"]),
            "runoff_month": sc["runoff_month"],
            "model_version": model["version"],
            "test_nll": oos.get("nll"), "test_ece": oos.get("ece"),
            "test_brier": oos.get("brier"), "test_pit_ks": oos.get("pit_ks"),
            "runoff_model_winner": cmp.get("winner"),
            "use_regime": model.get("use_regime", False),
            "augment_kept": aug_kept,
            "erosion": bool(model.get("erosion")),
            "hp": sel["hp"],
            # ---- training / validation diagnostics (for the report charts) ----
            "diagnostics": sel.get("diagnostics"),          # reliability, pit_counts, hp_grid
            "frozen_oos": oos,
            "model_comparison": cmp,                          # realised vs conv/ecm/hazard
            "regime_gate": sel.get("regime_gate"),
        }
        # persist the per-segment deployed model (audit + reuse)
        os.makedirs(OUT, exist_ok=True)
        with open(os.path.join(OUT, f"model_{key}.json"), "w") as f:
            json.dump(model, f, indent=2)
        if verbose:
            print(f"         -> W={sc['W']:.0f} WAL={wal(sc['B']):.2f}mo "
                  f"alive={sc['n_alive']} test_nll={oos.get('nll')}")

    if not segments:
        raise RuntimeError("no behavioral segment could be fit -- check the panel/scope")

    # ---- balance-weighted book aggregation ---------------------------------- #
    W_total = sum(s["W"] for s in segments.values()) or 1e-9
    Hn = len(next(iter(segments.values()))["B"])
    def agg(field):
        return [sum(s["W"] * s[field][h] for s in segments.values()) / W_total
                for h in range(Hn)]
    B_book, A_book, Bs_book = agg("B"), agg("A"), agg("B_stressed")
    runoff_month = next((h for h in range(Hn) if B_book[h] < RUNOFF_EPS), None)
    book = {
        "W_total": W_total, "asof_month": max(s["asof_month"] for s in segments.values()),
        "H": H, "n_segments": len(segments), "runoff_month": runoff_month,
        "B": B_book, "A": A_book, "B_stressed": Bs_book,
        "WAL": wal(B_book), "WAL_stressed": wal(Bs_book),
        "r": [(B_book[i] / A_book[i] if A_book[i] > 1e-12 else 1.0) for i in range(Hn)],
    }
    for k, s in segments.items():
        s["weight_pct"] = 100.0 * s["W"] / W_total

    diagnostics_book = collect_book_diagnostics(rows, features, month_key)
    irrbb_layer = compute_irrbb_layer(rows, features, weight_key, month_key,
                                      segments, book, verbose=verbose,
                                      crisis_elasticity=crisis_elasticity,
                                      crisis_oil_drop=crisis_oil_drop,
                                      crisis_months=crisis_months)
    return {"segments": segments, "book": book, "skipped": skipped,
            "diagnostics_book": diagnostics_book, "irrbb": irrbb_layer,
            "panel": os.path.abspath(panel_path)}


# --------------------------------------------------------------------------- #
# IRRBB layer: dEVE/dNII (irrbb.py) + parameter-uncertainty fan (param_uncertainty.py)
# + generative-macro positioning & IMPOSED crisis overlay (macro_sim.py). Risk-management
# framing: the WORST (most adverse) scenario leads. Runs on the already-fit models.
# --------------------------------------------------------------------------- #
def _last_macro(rows, month_key, feat):
    cur = max(r[month_key] for r in rows)
    for r in rows:
        if r[month_key] == cur and feat in r:
            try:
                return float(r[feat])
            except (KeyError, ValueError):
                pass
    return None


def _macro_matrix(rows, feats, month_key):
    """One macro row per month (dedup: macro is book-wide), ordered by month -> T x F."""
    by_m = {}
    for r in rows:
        m = r[month_key]
        if m not in by_m:
            try:
                by_m[m] = [float(r[f]) for f in feats]
            except (KeyError, ValueError):
                by_m[m] = None
    return [by_m[m] for m in sorted(by_m) if by_m.get(m) is not None]


class _HazAdapter:
    def __init__(self, model, feats):
        self.m, self.f = model, feats

    def proba(self, fd):
        return self.m.predict_proba([[fd.get(x, 0.0) for x in self.f]])[0]


class _EroAdapter:
    def __init__(self, params):
        self.f, self.c, self.i = params["features"], params["coef"], params["intercept"]

    def increment(self, fd):
        return self.i + sum(self.c[x] * fd.get(x, 0.0) for x in self.f)


def compute_irrbb_layer(rows, features, weight_key, month_key, segments, book,
                        n_boot=20, H_unc=120, n_paths=250, elasticity=0.6, verbose=True,
                        crisis_elasticity=0.8, crisis_oil_drop=0.40, crisis_months=6):
    import irrbb
    import macro_sim
    import param_uncertainty as pu
    from runoff_common import fit_hazard, fit_erosion, fit_hmm, lam_alpha_to_l1l2

    # base discount rate from the last money-market observation (percent -> decimal)
    mm = _last_macro(rows, month_key, "money_market")
    base_rate = (mm / 100.0) if mm is not None else 0.03

    # REAL 3-point short-end curve from the IMF points already in the macro panel
    # (money-market ~0.1y, policy ~0.25y, T-bill ~1y), flat beyond ~1y (no liquid DZD long
    # curve exists). Falls back to the flat base_rate if the points are missing.
    macw = _load_macro_wide()
    def _lastwide(c):
        if not macw or c not in macw["cols"]:
            return None
        i = macw["cols"].index(c)
        for row in reversed(macw["rows"]):
            try:
                return float(row[i])
            except (ValueError, TypeError):
                continue
        return None
    pts = []
    for col, tenor in (("money_market", 0.10), ("policy_discount", 0.25), ("tbill_yield", 1.0)):
        v = _lastwide(col)
        if v is not None:
            pts.append((tenor, v / 100.0))
    base_curve = irrbb.build_curve(pts) if len(pts) >= 2 else base_rate
    curve_points = [(t, round(r, 5)) for t, r in pts]

    # --- 1) dEVE / dNII per book + total (irrbb.py) ---
    books = {k: {"B0": s["W"], "B": s["B"]} for k, s in segments.items()}
    eve = irrbb.book_irrbb(books, base_curve)
    eve["curve_points"] = curve_points
    eve["curve_note"] = ("3-point short-end curve (money-market/policy/T-bill, IMF) flat "
                         "beyond 1y" if len(pts) >= 2 else "flat curve (no curve points)")
    # order EBA scenarios by severity (most adverse dEVE first) for risk reporting
    order = sorted(eve["total_delta_eve"], key=lambda x: eve["total_delta_eve"][x])
    eve["scenarios_by_severity"] = order

    # --- 2) generative-macro positioning + IMPOSED crisis (macro_sim.py) ---
    positioning = crisis = regime_labels = None
    generator_used = None
    hmm = fit_hmm(rows, features, month_key)
    if hmm and "oil_brent" in hmm["features"]:
        regime_labels = macro_sim.label_regimes(hmm)
        oil_i = hmm["features"].index("oil_brent")
        x0 = [(_last_macro(rows, month_key, f) or 0.0) for f in hmm["features"]]
        oil0 = x0[oil_i] or 1.0
        # PREFER the regime-switching VAR+GARCH (multivariate oil->fx->rate propagation +
        # vol clustering + fat tails); fall back to the AR+Student-t if the fit is unstable.
        Y = _macro_matrix(rows, hmm["features"], month_key)
        sim, generator_used = None, None
        if len(Y) >= 30:
            try:
                import regime_var_garch as rvg
                post = filter_regime_series(hmm, Y)
                gen = rvg.RegimeVARGARCH.fit(Y, hmm, post)
                sim = gen.simulate(x0, T=60, n_paths=n_paths, seed=1, df=6)
                generator_used = "regime-VAR-GARCH"
            except Exception:
                sim = None
        if sim is None:
            sim = macro_sim.RegimeMacro.from_hmm(hmm, kappa=0.3, df=5).simulate(
                x0, T=60, n_paths=n_paths, seed=1)
            generator_used = "regime-AR-Student-t"
        positioning = pu_safe(lambda: macro_sim.positioning_distribution(
            book["B"], sim["macro"], oil_i, oil0, elasticity))
        if positioning:
            positioning["generator"] = generator_used
        # imposed reverse-stress crisis anchored to an oil-crash analog (e.g. 2014-16)
        crisis = macro_sim.imposed_crisis(book["B"], base_rate, book["W_total"],
                                          oil_drop=crisis_oil_drop, months=crisis_months,
                                          elasticity=crisis_elasticity, irrbb_mod=irrbb)

    # --- 3) parameter uncertainty: coefficient bootstrap on the pooled book (param_uncertainty.py) ---
    uncertainty = None
    if n_boot and rows and features:
        hp = segments[max(segments, key=lambda k: segments[k]["W"])]["hp"]
        l1, l2 = lam_alpha_to_l1l2(hp["lambda"], hp["alpha"])
        cur = max(r[month_key] for r in rows)
        by_acc = {}
        for r in rows:
            by_acc.setdefault(r["account_id"], []).append(r)
        alive = []
        for _, rs in by_acc.items():
            rs.sort(key=lambda r: r[month_key])
            last = rs[-1]
            if last[month_key] == cur and int(last.get("event", 0)) == 0:
                try:
                    fd = {f: float(last[f]) for f in features}
                except (KeyError, ValueError):
                    continue
                alive.append((float(last.get(weight_key, 1.0) or 1.0), fd))

        def draw_fn(resampled):
            try:
                m = fit_hazard(resampled, features, l1, l2, weight_key, epochs=150)
                ep = fit_erosion(resampled, month_key=month_key, weight_key=weight_key)
                er = _EroAdapter(ep) if ep else None
                B, _ = roll_book_fast(alive, _HazAdapter(m, features), er, H_unc)
                return B
            except Exception:
                return None

        if alive:
            if verbose:
                print(f"  [uncertainty] {n_boot} coef-bootstrap refits on {len(alive)} "
                      f"pooled alive accounts ...", flush=True)
            uncertainty = pu.bootstrap_runoff_fan(rows, month_key, draw_fn,
                                                  n_boot=n_boot, block=6, seed=7)

    # --- 4) GBM challenger (gbm.py): does a LightGBM-style booster beat the GLM OOS? ---
    challenger = gbm_challenger(rows, features, weight_key, month_key,
                                segments[max(segments, key=lambda k: segments[k]["W"])]["hp"],
                                verbose=verbose)

    return {"base_rate": base_rate, "eve": eve, "positioning": positioning,
            "crisis": crisis, "regime_labels": regime_labels, "uncertainty": uncertainty,
            "challenger": challenger, "betas": irrbb.DEFAULT_BETA}


def _nll(p, y):
    return -sum(y[i] * math.log(max(p[i], 1e-12)) + (1 - y[i]) * math.log(max(1 - p[i], 1e-12))
               for i in range(len(y))) / max(1, len(y))


def _brier(p, y):
    return sum((p[i] - y[i]) ** 2 for i in range(len(y))) / max(1, len(y))


def gbm_challenger(rows, features, weight_key, month_key, hp, H_cmp=12, verbose=True):
    """Head-to-head: elastic-net logistic hazard vs a pure-stdlib GBM (LightGBM-style),
    fit on the SAME train, judged out-of-sample (NLL / Brier) AND on the realised cohort
    book run-off MAE. Settles 'why not XGBoost?' with a number, not an opinion."""
    from gbm import GBMHazard
    from runoff_common import fit_hazard, fit_erosion, lam_alpha_to_l1l2, cohort_realized_runoff
    months = sorted({r[month_key] for r in rows})
    if len(months) < H_cmp + 30:
        return None
    cutoff = months[-H_cmp - 1]                     # leave H_cmp months to observe realised run-off
    train = [r for r in rows if r[month_key] <= cutoff]
    test = [r for r in rows if r[month_key] > cutoff]
    if not train or not test:
        return None
    try:
        Xtr = [[float(r[f]) for f in features] for r in train]
        ytr = [int(float(r["event"])) for r in train]
        wtr = [float(r.get(weight_key, 1.0) or 1.0) for r in train]
        Xte = [[float(r[f]) for f in features] for r in test]
        yte = [int(float(r["event"])) for r in test]
    except (KeyError, ValueError):
        return None

    l1, l2 = lam_alpha_to_l1l2(hp["lambda"], hp["alpha"])
    if verbose:
        print(f"  [challenger] fitting GBM vs logistic on {len(train)} rows ...", flush=True)
    log_m = fit_hazard(train, features, l1, l2, weight_key)
    gbm_m = GBMHazard(n_estimators=60, max_depth=3, learning_rate=0.1,
                      min_child_weight=5.0).fit(Xtr, ytr, w=wtr)
    p_log, p_gbm = log_m.predict_proba(Xte), gbm_m.predict_proba(Xte)

    # run-off MAE on the realised cohort book run-off over (cutoff, cutoff+H]
    ero = fit_erosion(train, month_key=month_key, weight_key=weight_key)
    er = _EroAdapter(ero) if ero else None
    alive = []
    for r in rows:
        if r[month_key] == cutoff and int(float(r.get("event", 0))) == 0:
            try:
                fd = {f: float(r[f]) for f in features}
            except (KeyError, ValueError):
                continue
            alive.append((float(r.get(weight_key, 1.0) or 1.0), fd))
    realized = cohort_realized_runoff(rows, cutoff, H_cmp, month_key)
    mae = {}
    if alive:
        B_log, _ = roll_book_fast(alive, _HazAdapter(log_m, features), er, H_cmp)
        B_gbm, _ = roll_book_fast(alive, _HazAdapter(gbm_m, features), er, H_cmp)
        mae["logistic"] = sum(abs(B_log[t] - realized[t]) for t in range(H_cmp + 1)) / (H_cmp + 1)
        mae["gbm"] = sum(abs(B_gbm[t] - realized[t]) for t in range(H_cmp + 1)) / (H_cmp + 1)

    res = {
        "n_train": len(train), "n_test": len(test), "cutoff_month": cutoff,
        "logistic": {"nll": _nll(p_log, yte), "brier": _brier(p_log, yte),
                     "runoff_mae": mae.get("logistic")},
        "gbm": {"nll": _nll(p_gbm, yte), "brier": _brier(p_gbm, yte),
                "runoff_mae": mae.get("gbm")},
    }
    # winner on OOS run-off MAE (the deployed quantity); ties/close -> prefer the simple GLM
    lo, gb = res["logistic"], res["gbm"]
    if lo["runoff_mae"] is not None and gb["runoff_mae"] is not None:
        res["winner_runoff"] = ("gbm" if gb["runoff_mae"] < lo["runoff_mae"] * 0.98
                                else "logistic")
    res["winner_nll"] = "gbm" if gb["nll"] < lo["nll"] else "logistic"
    if verbose:
        print(f"         OOS NLL: logistic={lo['nll']:.4f} gbm={gb['nll']:.4f} | "
              f"run-off MAE: logistic={mae.get('logistic')} gbm={mae.get('gbm')}")
    return res


def pu_safe(fn):
    try:
        return fn()
    except Exception:
        return None


def collect_book_diagnostics(rows, features, month_key):
    """Book-wide series for the regime + break-monitoring + macro report panels: fit ONE
    regime HMM on the shared macro (macro is book-wide), produce the causal filtered
    posterior time series, a CUSUM break statistic, and the full macro table."""
    from structural_breaks import cusum_detect
    months = sorted({r[month_key] for r in rows})
    # month_int -> [macro feature vector] for the HMM (dedup: macro identical across accounts)
    hmm = fit_hmm(rows, features, month_key)
    regime = {"months": months, "K": None, "posterior": None, "state": None}
    if hmm:
        by_m = {}
        for r in rows:
            m = r[month_key]
            if m not in by_m:
                try:
                    by_m[m] = [float(r[f]) for f in hmm["features"]]
                except (KeyError, ValueError):
                    by_m[m] = None
        series = [by_m[m] for m in months if by_m.get(m) is not None]
        smonths = [m for m in months if by_m.get(m) is not None]
        post = filter_regime_series(hmm, series)
        from macro_sim import label_regimes
        labels = label_regimes(hmm)
        regime = {"months": smonths, "K": hmm["K"], "features": hmm["features"],
                  "posterior": post, "labels": labels,
                  "state": [max(range(len(p)), key=lambda k: p[k]) for p in post],
                  "bic_by_k": hmm.get("bic_by_k")}

    # CUSUM break statistic on the money-market rate (the monitored macro driver)
    mm_by_m = {}
    for r in rows:
        m = r[month_key]
        if m not in mm_by_m and "money_market" in r:
            try:
                mm_by_m[m] = float(r["money_market"])
            except (KeyError, ValueError):
                pass
    mm_months = sorted(mm_by_m)
    mm_series = [mm_by_m[m] for m in mm_months]
    cusum = None
    if len(mm_series) >= 24:
        cu = cusum_detect(mm_series)
        cusum = {"months": mm_months, "money_market": mm_series,
                 "seg_mean": cu["seg_mean"], "changepoint": cu["changepoint"],
                 "cps": cu["cps"], "recent_break": bool(any(cu["changepoint"][-3:]))}

    macro = _load_macro_wide()
    return {"regime": regime, "cusum": cusum, "macro": macro}


def _load_macro_wide():
    """Load the full downloaded macro panel (oil / cpi / rates / fx / ramadan / regimes)
    for the DATA_macro sheet + its charts. Returns {cols, rows} or None if not fetched."""
    import csv
    path = os.path.join(HERE, "data", "_out", "macro_panel_wide.csv")
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        cols = list(rd.fieldnames)
        data = [[r.get(c, "") for c in cols] for r in rd]
    return {"cols": cols, "rows": data}


# --------------------------------------------------------------------------- #
# xlsx report (pure stdlib via model/xlsx_writer). ONE .xlsx with tables AND native
# embedded charts (no VBA needed): glossaire (FR), synthese (B(t) en vert), courbes
# d'ecoulement 30 ans par livre + global, diagnostics training/validation, regime,
# surveillance de rupture, DATA (clients) + DATA_macro (petrole/cpi/taux/change/ramadan).
# --------------------------------------------------------------------------- #
CURVE_HDR = ["mois_h", "A_t_attrition", "r_t_retention", "B_t_ecoulement", "B_t_+200bp"]
CURVE_NAMES = ["A(t) survie", "B(t) ecoulement", "B(t) +200bp"]
ZOOM = 61                                              # 5-year zoom (months 0..60)


def write_xlsx_report(result, path):
    from xlsx_writer import Workbook
    import report_text as rt
    wb = Workbook()
    book, segs = result["book"], result["segments"]
    dbook = result.get("diagnostics_book", {}) or {}
    order = sorted(segs, key=lambda k: -segs[k]["W"])   # heaviest book first

    _sheet_glossaire(wb, rt)
    _sheet_guide(wb, rt)
    _sheet_summary(wb, book, segs, order, rt)
    _sheet_curve(wb, "Ecoulement_Livre", "Livre global (pondere)", book)
    for k in order:
        _sheet_curve(wb, f"Courbe_{k}", segs[k]["display"], segs[k])
    _sheet_training(wb, segs, order)
    _sheet_compare(wb, segs, order)
    _sheet_hp_surface(wb, segs, order)
    _sheet_reliability(wb, segs, order)
    _sheet_pit(wb, segs, order)
    irr = result.get("irrbb") or {}
    _sheet_irrbb(wb, irr, segs, order)
    _sheet_challenger(wb, irr)
    _sheet_crisis(wb, irr, book)
    _sheet_uncertainty(wb, irr)
    _sheet_regime(wb, dbook.get("regime"))
    _sheet_break(wb, dbook.get("cusum"))
    _sheet_data_macro(wb, dbook.get("macro"))
    _sheet_data_clients(wb, result.get("panel"))
    _sheet_skipped(wb, result.get("skipped") or {})
    _sheet_vba(wb, rt)
    return wb.save(path)


# ---- IRRBB / risk sheets (risk-management framing: worst scenario leads) ------ #
_SCEN_FR = {"parallel_up": "parallele +", "parallel_down": "parallele -",
            "short_up": "court +", "short_down": "court -", "steepener": "pentification",
            "flattener": "aplatissement", "up_200bp": "+200bp", "down_200bp": "-200bp"}


def _sheet_irrbb(wb, irr, segs, order):
    """dEVE (all EBA scenarios, WORST first) + dNII (+/-200bp). Deposits = liability:
    dEVE>0 favourable (equity up), the binding risk is the most NEGATIVE dEVE."""
    eve = irr.get("eve")
    if not eve:
        wb.add_sheet("IRRBB_EVE_NII", [["IRRBB non calcule"]], header=["note"])
        return
    tot = eve["total_delta_eve"]
    sev = eve.get("scenarios_by_severity") or sorted(tot, key=lambda x: tot[x])
    nii = eve["total_delta_nii"]
    worst = sev[0]
    cpts = eve.get("curve_points") or []
    curve_str = " ; ".join(f"{int(t*12)}m={round(r*100,2)}%" for t, r in cpts) if cpts else "flat"
    rows = [["Courbe d'actualisation (points reels IMF)", curve_str, "", eve.get("curve_note", ""), ""],
            ["Taux d'actualisation (base, 1an)", round(eve.get("base_rate_1y", 0.0) * 100, 3), "%",
             "", ""],
            ["PIRE scenario dEVE (risque liant)", _SCEN_FR.get(worst, worst),
             round(tot[worst], 0), "KDA", "le plus adverse"],
            ["", "", "", "", ""],
            ["-- dEVE par scenario (KDA, du PIRE au meilleur) --", "", "", "", ""]]
    for s in sev:
        rows.append([_SCEN_FR.get(s, s), round(tot[s], 0), "KDA",
                     ("ADVERSE" if tot[s] < 0 else "favorable"), ""])
    rows += [["", "", "", "", ""],
             ["-- dNII 1 an (KDA) --", "", "", "", ""],
             ["+200bp", round(nii["up_200bp"], 0), "KDA",
              ("ADVERSE" if nii["up_200bp"] < 0 else "favorable"), ""],
             ["-200bp", round(nii["down_200bp"], 0), "KDA",
              ("ADVERSE" if nii["down_200bp"] < 0 else "favorable"), ""]]
    ntab = 2
    sh = wb.add_sheet("IRRBB_EVE_NII", rows,
                      header=["mesure", "valeur", "unite", "risque", "note"],
                      as_table=True, table_rows=ntab)

    # per-book dEVE under the worst scenario + a bar chart (which book drives the risk)
    pb = eve.get("per_book", {})
    brows = [[k, round(pb[k]["delta_eve"].get(worst, 0.0), 0),
              round(pb[k].get("beta", 0.0), 2),
              round(pb[k].get("repricing_1y", 0.0), 0)] for k in order if k in pb]
    bh = ["livre", f"dEVE_{_SCEN_FR.get(worst, worst)}_KDA", "beta_depot", "reprice_1an_KDA"]
    sh2 = wb.add_sheet("IRRBB_par_livre", brows, header=bh, as_table=True)
    wb.add_bar_chart(sh2, f"dEVE par livre - scenario le plus adverse ({_SCEN_FR.get(worst, worst)})",
                     cat_col=0, val_cols=[1], y_title="dEVE (KDA)", x_title="livre")


def _sheet_challenger(wb, irr):
    """GBM (LightGBM-style) vs elastic-net logistic hazard, out-of-sample. The number
    settles 'why not XGBoost/DNN?' -- on this data the booster should NOT beat the
    calibrated GLM (or overfits)."""
    ch = irr.get("challenger")
    if not ch:
        wb.add_sheet("Challenger_GBM", [["challenger non calcule"]], header=["note"])
        return
    lo, gb = ch["logistic"], ch["gbm"]
    rows = [["OOS NLL (bas=mieux)", _r(lo["nll"], 4), _r(gb["nll"], 4),
             ch.get("winner_nll", "")],
            ["OOS Brier (bas=mieux)", _r(lo["brier"], 5), _r(gb["brier"], 5), ""],
            ["Run-off MAE OOS (bas=mieux)", _r(lo.get("runoff_mae"), 4),
             _r(gb.get("runoff_mae"), 4), ch.get("winner_runoff", "")],
            ["n_train / n_test", ch["n_train"], ch["n_test"], ""]]
    verdict = ("Le GBM NE bat PAS le logistique OOS -> plus de capacite = surapprentissage "
               "sur donnees rares/courtes; on garde le GLM calibre."
               if ch.get("winner_runoff") == "logistic"
               else "Le GBM bat le logistique OOS -> a examiner (attention surapprentissage).")
    rows.append(["verdict", verdict, "", ""])
    sh = wb.add_sheet("Challenger_GBM", rows,
                      header=["metrique", "logistique_EN", "GBM_stdlib", "gagnant"],
                      as_table=True, table_rows=4)
    # bar chart: logistic vs gbm on NLL / Brier / MAE
    crows = [["NLL", _r(lo["nll"], 4), _r(gb["nll"], 4)],
             ["Brier", _r(lo["brier"], 5), _r(gb["brier"], 5)],
             ["Runoff_MAE", _r(lo.get("runoff_mae"), 4), _r(gb.get("runoff_mae"), 4)]]
    sh2 = wb.add_sheet("Challenger_bars", crows,
                       header=["metrique", "logistique", "GBM"], as_table=True)
    wb.add_bar_chart(sh2, "OOS: logistique elastic-net vs GBM (bas = mieux)", cat_col=0,
                     val_cols=[1, 2], names=["logistique EN", "GBM stdlib"],
                     y_title="score OOS", x_title="metrique")


def _sheet_crisis(wb, irr, book):
    """IMPOSED crisis (reverse stress) + generative-macro positioning band. The crisis
    SEVERITY is an assumption (calibrate on 2014-16), NOT fitted -- the only honest way to
    size an unseen oil->dinar->flight tail."""
    cr = irr.get("crisis")
    pos = irr.get("positioning")
    rows = [["=== CHOC DE CRISE IMPOSE (reverse stress) ===", "", ""]]
    if cr:
        rows += [["hypothese: chute petrole", f"{int(cr['oil_drop']*100)}%",
                  f"pendant {cr['months']} mois (ancrer sur 2014-16)"],
                 ["hypothese: elasticite de fuite (imposee)", cr["elasticity"],
                  "ALM assumption, NON estimee"],
                 ["WAL base", round(cr["wal_base"], 1), "mois"],
                 ["WAL sous crise", round(cr["wal_stressed"], 1), "mois"],
                 ["raccourcissement WAL", round(cr["wal_shortening_mo"], 1), "mois"]]
        if "delta_eve_down200_base" in cr:
            rows += [["dEVE -200bp (base)", round(cr["delta_eve_down200_base"], 0), "KDA"],
                     ["dEVE -200bp (sous crise)", round(cr["delta_eve_down200_crisis"], 0),
                      "KDA (livre plus court = moins expose au bas de taux)"]]
    else:
        rows.append(["crise non calculee (pas d'oil dans le HMM)", "", ""])
    if pos:
        w = pos["wal_pct"]
        rows += [["", "", ""],
                 ["=== POSITIONNEMENT (macro generatif, regime-switch AR + Student-t) ===", "", ""],
                 [f"WAL base", round(pos["wal_base"], 1), "mois"],
                 [f"WAL p05 / p50 / p95 ({pos['n_paths']} trajectoires)",
                  f"{_pk(w, 5):.1f} / {_pk(w, 50):.1f} / {_pk(w, 95):.1f}", "mois"],
                 ["note", "vue economique (couverture), NON reglementaire", ""]]
    wb.add_sheet("Crise_Stress", rows, header=["element", "valeur", "detail"],
                 as_table=True, table_rows=1 if not cr else 6)

    # positioning run-off band as a chart, if available
    if pos and pos.get("B_pct"):
        Bp = pos["B_pct"]
        lv = pos["levels"]
        b0, b1, b2 = _pk(Bp, lv[0]), _pk(Bp, lv[1]), _pk(Bp, lv[2])
        n = min(len(b0), 121)                              # first 10 years for readability
        crows = [[t, round(b0[t], 5), round(b1[t], 5), round(b2[t], 5)] for t in range(n)]
        sh = wb.add_sheet("Crise_Bande", crows,
                          header=["mois", f"p{lv[0]}", f"p{lv[1]}", f"p{lv[2]}"],
                          as_table=True)
        wb.add_line_chart(sh, "Ecoulement sous stress oil - bande de positionnement (10 ans)",
                          cat_col=0, val_cols=[1, 2, 3],
                          names=[f"p{lv[0]}", f"median", f"p{lv[2]}"],
                          y_title="fraction du solde", x_title="mois")


def _sheet_uncertainty(wb, irr):
    """Parameter (coefficient) uncertainty: block-bootstrap refit -> B(t) fan + WAL CI.
    On ~120 months this is the BINDING uncertainty, not the macro path."""
    unc = irr.get("uncertainty")
    if not unc:
        wb.add_sheet("Incertitude", [["incertitude parametrique non calculee"]],
                     header=["note"])
        return
    lv = unc["levels"]
    Bp = unc["B_pct"]
    w = unc["wal_pct"]
    b0, b1, b2 = _pk(Bp, lv[0]), _pk(Bp, lv[1]), _pk(Bp, lv[2])
    rows = [[t, round(b0[t], 5), round(b1[t], 5), round(b2[t], 5)]
            for t in range(unc["H"] + 1)]
    sh = wb.add_sheet("Incertitude", rows,
                      header=["mois", f"B_p{lv[0]}", f"B_p{lv[1]}", f"B_p{lv[2]}"],
                      as_table=True)
    wb.add_line_chart(sh, f"Incertitude parametrique de B(t) ({unc['n_boot']} bootstraps)",
                      cat_col=0, val_cols=[1, 2, 3],
                      names=[f"p{lv[0]}", "median", f"p{lv[2]}"],
                      y_title="fraction du solde", x_title="mois")
    # WAL CI note sheet
    wb.add_sheet("Incertitude_WAL",
                 [["WAL median", round(_pk(w, lv[1]), 1), "mois"],
                  [f"WAL {lv[0]}%-{lv[2]}%",
                   f"[{_pk(w, lv[0]):.1f}, {_pk(w, lv[2]):.1f}]", "mois (IC bootstrap)"],
                  ["n_bootstraps", unc["n_boot"], ""]],
                 header=["mesure", "valeur", "unite"], as_table=True)


def _r(x, n):
    return round(x, n) if isinstance(x, (int, float)) else (x if x is not None else "")


def _pk(d, lv):
    """Percentile-band lookup robust to int vs str keys (JSON turns int keys into strings)."""
    if d is None:
        return None
    if lv in d:
        return d[lv]
    return d.get(str(lv))


def _colL(i):
    s, n = "", i + 1
    while n:
        n, rem = divmod(n - 1, 26)
        s = chr(65 + rem) + s
    return s


def _sheet_glossaire(wb, rt):
    wb.add_sheet("Glossaire", [[t, d] for t, d in rt.GLOSSAIRE],
                 header=["Terme", "Definition"], as_table=True)


def _sheet_guide(wb, rt):
    """Plain-French guide: what each sheet is + what to look for."""
    rows = [[nm, quoi, regarder] for nm, quoi, regarder in rt.SHEET_GUIDE]
    wb.add_sheet("Guide", rows, header=["Feuille", "C'est quoi", "A regarder"], as_table=True)


def _sheet_summary(wb, book, segs, order, rt):
    header = ["segment", "livre", "solde_KDA", "poids_%", "n_comptes", "WAL_base_mo",
              "WAL_+200bp_mo", "runoff<0.5%_mois", "test_NLL", "modele_gagnant", "version"]
    rows = []
    for k in order:
        s = segs[k]
        rows.append([k, s["display"], round(s["W"], 1), round(s["weight_pct"], 2),
                     s["n_alive"], round(s["WAL"], 1), round(s["WAL_stressed"], 1),
                     (s["runoff_month"] if s["runoff_month"] is not None else ">360"),
                     _r(s["test_nll"], 5), s["runoff_model_winner"], s["model_version"]])
    rows.append(["BOOK", "Livre global (pondere solde)", round(book["W_total"], 1), 100.0,
                 sum(s["n_alive"] for s in segs.values()), round(book["WAL"], 1),
                 round(book["WAL_stressed"], 1),
                 (book["runoff_month"] if book["runoff_month"] is not None else ">360"),
                 "", "", ""])
    n_table = len(order) + 1                            # segments + BOOK row (before notes)
    rows.append([""])
    for note in rt.SUMMARY_NOTES:
        rows.append([note])
    sh = wb.add_sheet("Synthese", rows, header=header, as_table=True, table_rows=n_table)
    wb.set_green_columns(sh, [5])                       # WAL_base = resume de B(t) -> vert


def _sheet_curve(wb, name, display, obj):
    B, A, r, Bs = obj["B"], obj["A"], obj["r"], obj["B_stressed"]
    rows = [[h, round(A[h], 6), round(r[h], 6), round(B[h], 6), round(Bs[h], 6)]
            for h in range(len(B))]
    sh = wb.add_sheet(name, rows, header=CURVE_HDR, as_table=True)
    wb.set_green_columns(sh, [3])                       # B(t) en vert
    wb.add_line_chart(sh, f"{display} - ecoulement B(t) 30 ans", cat_col=0,
                      val_cols=[1, 3, 4], names=CURVE_NAMES, y_title="fraction du solde",
                      x_title="mois")
    wb.add_line_chart(sh, f"{display} - ecoulement B(t) 5 ans (zoom)", cat_col=0,
                      val_cols=[1, 3, 4], names=CURVE_NAMES, y_title="fraction du solde",
                      x_title="mois", n_rows=ZOOM)


def _bin_entropy(q):
    q = min(max(q, 1e-12), 1 - 1e-12)
    return -(q * math.log(q) + (1 - q) * math.log(1 - q))


def _ks_pvalue(d, n):
    """Two-sided Kolmogorov-Smirnov p-value (asymptotic, Numerical-Recipes small-sample
    correction) for a KS statistic d on n points. p>0.05 => cannot reject uniform PIT
    => calibrated. Pure stdlib."""
    if not d or not n:
        return None
    en = math.sqrt(n)
    lam = (en + 0.12 + 0.11 / en) * d
    s, sign = 0.0, 1.0
    for k in range(1, 101):
        s += sign * math.exp(-2.0 * lam * lam * k * k)
        sign = -sign
    return max(0.0, min(1.0, 2.0 * s))


def _skill_pct(score, base):
    """% improvement of a proper score vs a naive baseline (1 - score/base). >0 = better."""
    return round(100.0 * (1.0 - score / base), 1) if (base and score is not None) else ""


def _sheet_training(wb, segs, order):
    # NLL/Brier are proper SCORES (not p-values) -> report them as a SKILL % vs the naive
    # base-rate model (predict the marginal event rate for everyone), so the number is
    # interpretable. PIT-KS is a real test statistic -> add its p-value + calibration
    # verdict. ECE is already a probability-scale miscalibration -> pass/fail at 1%.
    header = ["segment", "n_OOS", "NLL_skill_%", "Brier_skill_%", "ECE",
              "ECE_ok(<1%)", "PIT_KS", "PIT_KS_pvalue", "calibre?(5%)"]
    rows = []
    for k in order:
        oos = segs[k].get("frozen_oos") or {}
        q = oos.get("actual", 0.0)
        nll_sk = _skill_pct(oos.get("nll"), _bin_entropy(q)) if q else "n/a (0 evt)"
        br_sk = _skill_pct(oos.get("brier"), q * (1 - q)) if q else "n/a (0 evt)"
        ece = oos.get("ece")
        ksp = _ks_pvalue(oos.get("pit_ks"), oos.get("n"))
        rows.append([k, oos.get("n"), nll_sk, br_sk, _r(ece, 4),
                     ("oui" if (ece is not None and ece < 0.01) else "non"),
                     _r(oos.get("pit_ks"), 4), _r(ksp, 4),
                     ("oui" if (ksp is None or ksp > 0.05) else "non")])
    sh = wb.add_sheet("Training", rows, header=header, as_table=True)
    wb.add_bar_chart(sh, "Skill hors-echantillon vs modele naif (base-rate), %", cat_col=0,
                     val_cols=[2, 3], names=["NLL skill %", "Brier skill %"],
                     y_title="% amelioration", x_title="livre")


def _sheet_compare(wb, segs, order):
    header = ["segment", "convention_MAE", "ecm_MAE", "hazard_MAE", "gagnant"]
    rows = []
    for k in order:
        m = (segs[k].get("model_comparison") or {}).get("models", {})
        rows.append([k, _r((m.get("convention") or {}).get("mae"), 4),
                     _r((m.get("ecm") or {}).get("mae"), 4),
                     _r((m.get("hazard") or {}).get("mae"), 4),
                     segs[k].get("runoff_model_winner")])
    sh = wb.add_sheet("Comparaison_Modeles", rows, header=header, as_table=True)
    wb.add_bar_chart(sh, "MAE d'ecoulement OOS: convention vs ECM vs hasard", cat_col=0,
                     val_cols=[1, 2, 3], names=["convention", "ECM", "hasard"],
                     y_title="MAE", x_title="livre")


def _sheet_hp_surface(wb, segs, order):
    """HP lambda x alpha NLL heatmap (colorScale) for the heaviest book."""
    for k in order:
        grid = ((segs[k].get("diagnostics") or {}).get("hp_grid")) or []
        if not grid:
            continue
        lambdas = sorted({g["lambda"] for g in grid})
        alphas = sorted({g["alpha"] for g in grid})
        nll = {(g["lambda"], g["alpha"]): g["nll"] for g in grid}
        header = ["lambda\\alpha"] + [f"a={a}" for a in alphas]
        rows = [[f"{lam:.2e}"] + [_r(nll.get((lam, a)), 5) for a in alphas]
                for lam in lambdas]
        sh = wb.add_sheet(f"HP_Surface_{k}"[:31], rows, header=header)
        last = _colL(len(alphas))
        wb.add_color_scale(sh, f"B2:{last}{len(lambdas) + 1}")
        return                                          # one heatmap (heaviest book) is enough


def _sheet_reliability(wb, segs, order):
    """Reliability (pred vs actual) scatter per book + a y=x diagonal, side-by-side."""
    rels = {k: [(b["pred"], b["actual"]) for b in
                ((segs[k].get("diagnostics") or {}).get("reliability") or []) if b["n"] > 0]
            for k in order}
    if not any(rels.values()):
        return
    maxn = max((len(v) for v in rels.values()), default=0)
    header = []
    for k in order:
        header += [f"{k}_pred", f"{k}_act"]
    rows = []
    for j in range(maxn):
        row = []
        for k in order:
            v = rels[k]
            row += ([round(v[j][0], 4), round(v[j][1], 4)] if j < len(v) else ["", ""])
        rows.append(row)
    sh = wb.add_sheet("Fiabilite", rows, header=header)
    for i, k in enumerate(order):
        if rels[k]:
            wb.add_scatter_chart(sh, f"Fiabilite {k} (calibration)", x_col=2 * i,
                                 y_cols=[2 * i + 1], diagonal=True, n_rows=len(rels[k]))


def _sheet_pit(wb, segs, order):
    """PIT histogram (uniforme = bien calibre): grouped bar, one series per book."""
    header = ["bin"] + list(order)
    rows = []
    for b in range(10):
        row = [b]
        for k in order:
            pc = (segs[k].get("diagnostics") or {}).get("pit_counts") or [0] * 10
            row.append(pc[b] if b < len(pc) else 0)
        rows.append(row)
    sh = wb.add_sheet("PIT", rows, header=header, as_table=True)
    wb.add_bar_chart(sh, "Histogramme PIT par livre (plat = calibre)", cat_col=0,
                     val_cols=list(range(1, len(order) + 1)), names=list(order),
                     y_title="comptes", x_title="bin PIT (0-9)")


def _sheet_regime(wb, regime):
    if not regime or not regime.get("posterior"):
        wb.add_sheet("Regime", [["Regime HMM indisponible (macro insuffisante)"]],
                     header=["note"])
        return
    months, post, K = regime["months"], regime["posterior"], regime["K"]
    state = regime["state"]
    # human labels from fitted macro means (never expose raw 'regime 0/1')
    labels = regime.get("labels") or [f"regime {j}" for j in range(K)]
    header = ["mois_int"] + [f"P[{labels[j]}]" for j in range(K)] + ["regime_dominant"]
    rows = [[months[i]] + [round(post[i][j], 4) for j in range(K)] + [labels[state[i]]]
            for i in range(len(months))]
    sh = wb.add_sheet("Regime", rows, header=header, as_table=True)
    wb.add_line_chart(sh, "Probabilite de regime filtree (causale)", cat_col=0,
                      val_cols=list(range(1, K + 1)), names=labels, y_title="proba",
                      x_title="mois")
    # current regime posterior (last month) as a small bar on its own sheet
    cur = post[-1]
    rows2 = [[labels[j], round(cur[j], 4)] for j in range(K)]
    bic = regime.get("bic_by_k")
    if bic:
        rows2 += [[""], ["BIC par K:", ""]] + [[f"K={kk}", round(v, 1)]
                                               for kk, v in sorted(bic.items())]
    sh2 = wb.add_sheet("Regime_Actuel", rows2, header=["regime", "proba"],
                       as_table=True, table_rows=K)
    wb.add_bar_chart(sh2, "Regime courant (dernier mois observe)", cat_col=0,
                     val_cols=[1], n_rows=K, y_title="proba", x_title="regime")


def _sheet_break(wb, cusum):
    if not cusum:
        wb.add_sheet("Surveillance", [["CUSUM indisponible"]], header=["note"])
        return
    m, mm, seg, cp = cusum["months"], cusum["money_market"], cusum["seg_mean"], cusum["changepoint"]
    header = ["mois_int", "taux_marche", "moyenne_segment", "rupture(0/1)"]
    rows = [[m[i], round(mm[i], 4), round(seg[i], 4), cp[i]] for i in range(len(m))]
    ntab = len(m)
    rows.append([""])
    rows.append([f"alarme rupture recente: {'OUI' if cusum['recent_break'] else 'non'}"])
    rows.append([f"points de rupture (index): {cusum.get('cps')}"])
    sh = wb.add_sheet("Surveillance", rows, header=header, as_table=True, table_rows=ntab)
    wb.add_line_chart(sh, "Surveillance de rupture (CUSUM) - taux de marche monetaire",
                      cat_col=0, val_cols=[1, 2], names=["taux marche", "moyenne segment"],
                      y_title="taux (%)", x_title="mois", n_rows=ntab)


def _sheet_data_macro(wb, macro):
    if not macro:
        wb.add_sheet("DATA_macro", [["macro non telechargee (lancer data/run_all.py)"]],
                     header=["note"])
        return
    cols, data = macro["cols"], macro["rows"]
    conv = []
    for r in data:
        row = []
        for c, v in zip(cols, r):
            if c == "ref_month":
                row.append(v)
            else:
                try:
                    row.append(float(v))
                except (ValueError, TypeError):
                    row.append(v)
        conv.append(row)
    sh = wb.add_sheet("DATA_macro", conv, header=cols, as_table=True)
    idx = {c: i for i, c in enumerate(cols)}
    mc = idx.get("ref_month", 0)
    n = len(conv)

    def line(title, names, y=None):
        vc = [idx[c] for c in names if c in idx]
        if vc:
            wb.add_line_chart(sh, title, cat_col=mc, val_cols=vc, names=names,
                              y_title=y, x_title="mois", n_rows=n)

    line("Petrole Brent (USD/bbl)", ["oil_brent"])
    line("Inflation - CPI & CPI YoY", ["cpi", "cpi_yoy"])
    line("Taux - marche / directeur / T-bill", ["money_market", "policy_discount", "tbill_yield"])
    line("Change - USD/DZD & EUR/DZD", ["usd_dzd", "eur_dzd"])
    line("Prime de change parallele (%)", ["parallel_premium_pct"])
    line("Calendrier Ramadan (fraction du mois)", ["ramadan_frac"])
    line("Regime exogene (etat / severite)", ["regime_state", "severity"])


def _sheet_data_clients(wb, panel_path, max_rows=200000):
    if not panel_path or not os.path.exists(panel_path):
        wb.add_sheet("DATA", [["panel indisponible"]], header=["note"])
        return
    import csv
    with open(panel_path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        cols = list(rd.fieldnames)
        num_cols = {"month_int", "year", "month", "event", "seasoning", "log_balance",
                    "balance_kda", "cal_month", "cpi", "cpi_yoy", "money_market",
                    "policy_discount", "tbill_yield", "oil_brent", "usd_dzd", "eur_dzd",
                    "ramadan_frac", "ramadan_days", "severity", "parallel_premium_pct"}
        rows = []
        for i, r in enumerate(rd):
            if i >= max_rows:
                break
            row = []
            for c in cols:
                v = r.get(c, "")
                if c in num_cols and v not in ("", None):
                    try:
                        v = float(v)
                    except (ValueError, TypeError):
                        pass
                row.append(v)
            rows.append(row)
    wb.add_sheet("DATA", rows, header=cols, as_table=True)


def _sheet_skipped(wb, skipped):
    rows = [[k, v.get("reason", ""), v.get("n_rows", ""), v.get("n_months", "")]
            for k, v in skipped.items()]
    if not rows:
        rows = [["(aucun livre exclu)", "", "", ""]]
    wb.add_sheet("Skipped", rows, header=["segment", "raison", "n_rows", "n_months"],
                 as_table=True)


def _sheet_vba(wb, rt):
    rows = [[ln] for ln in rt.VBA_REFERENCE.splitlines()]
    wb.add_sheet("VBA_Source", rows, header=["' RunoffCharts.bas (reference; import via Alt+F11)"])


def main():
    import argparse
    ap = argparse.ArgumentParser(description="Multi-product behavioral run-off + IRRBB book.")
    ap.add_argument("panel", nargs="?", default=None, help="panel.csv (omit -> synth demo)")
    ap.add_argument("--augment", action="store_true",
                    help="engineered path/change features, PER-SEGMENT validation-gated")
    ap.add_argument("--signatures", action="store_true", help="path-signature features (slow)")
    ap.add_argument("--crisis-elasticity", type=float, default=0.8,
                    help="ALM assumption: imposed deposit-flight elasticity for the reverse-stress crisis")
    ap.add_argument("--crisis-oil-drop", type=float, default=0.40,
                    help="ALM assumption: imposed oil-price drop fraction (0.40 = -40%%)")
    ap.add_argument("--crisis-months", type=int, default=6,
                    help="ALM assumption: duration of the imposed oil crash, in months")
    args = ap.parse_args()
    panel_path = args.panel
    tag = "[panel]" if panel_path else "[demo]"
    print(f"{tag} multi-product behavioral run-off (augment={args.augment}, "
          f"signatures={args.signatures}; crisis eta={args.crisis_elasticity} "
          f"oil-drop={args.crisis_oil_drop} months={args.crisis_months})")
    result = run_book(panel_path, use_signatures=args.signatures, augment=args.augment,
                      crisis_elasticity=args.crisis_elasticity,
                      crisis_oil_drop=args.crisis_oil_drop,
                      crisis_months=args.crisis_months)
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "book_runoff.json"), "w") as f:
        json.dump(result, f, indent=2)
    xlsx = write_xlsx_report(result, os.path.join(OUT, "report.xlsx"))

    book = result["book"]
    rm = book.get("runoff_month")
    print("\nBOOK AGGREGATE (balance-weighted):")
    print(f"  segments modeled : {result['book']['n_segments']}  "
          f"total balance = {book['W_total']:.0f} KDA")
    print(f"  book WAL base/stress: {book['WAL']:.2f} / {book['WAL_stressed']:.2f} mo "
          f"(full horizon H={book['H']}mo)")
    print(f"  run-off to <0.5%: {'month ' + str(rm) if rm else 'not within horizon'}")
    print(f"  book B(t) first 12mo: {[round(x, 3) for x in book['B'][:13]]}")
    yrs = [12, 24, 60, 120, 240, 360]
    print(f"  book B(t) by year:  " +
          ", ".join(f"{y//12}y={round(book['B'][y], 3)}" for y in yrs if y < len(book['B'])))
    try:
        from viz import ascii_spark
        print("  B(t) shape: " + ascii_spark(book["B"]))
    except Exception:
        pass
    print(f"\n  per-segment weights: "
          + ", ".join(f"{k}={result['segments'][k]['weight_pct']:.0f}%"
                      for k in sorted(result['segments'],
                                      key=lambda k: -result['segments'][k]['W'])))
    print(f"\nartifacts -> {OUT}")
    print(f"  book_runoff.json , model_<segment>.json , report.xlsx")
    print(f"  -> {xlsx}")


if __name__ == "__main__":
    main()
