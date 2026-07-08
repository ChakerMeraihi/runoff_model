"""runoff_eval.py -- HP selection + FROZEN-OOS performance + ablation (pure stdlib).

For REPORTING and GOVERNANCE (your benchmark), not deployment. Run quarterly or on
demand. Holds out the last `val_months` as a frozen OOS tail, never used in the
deployed fit. Writes the SELECTED HPs that runoff_fit.py then reuses.

  HP search (parallel walk-forward grid) -> select (lambda, alpha)
  frozen-OOS backtest on the held-out tail -> Brier / NLL / ECE / PIT + conformal q
  stochastic ablation (data-driven vs Markov-generator vs parametric)
  -> _artifacts/hp_selected.json  +  _artifacts/validation_report.txt

Usage:  python runoff_eval.py [panel.csv]   (no arg -> synthetic demo)
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import sys
import time

import csv

from runoff_common import (ART, prepare, fit_hazard, lam_alpha_to_l1l2,
                           fit_hmm, attach_regime_posterior, attach_signatures, fit_erosion,
                           fit_ecm_model, hazard_cohort_runoff, compare_runoff_models)
from hp_search import hp_search, brier, nll
from survival import calibration_error, pit_values, ks_uniform, reliability_table
from conformal import conformal_q
import stochastic_ablation

OUT = os.path.join(os.path.dirname(ART), "_out")


def _fit_score(fit_rows, score_rows, features, hp, weight_key):
    """Fit the hazard on fit_rows with HPs `hp`, score score_rows. Returns
    (metrics, model, p, w, y)."""
    l1, l2 = lam_alpha_to_l1l2(hp["lambda"], hp["alpha"])
    m = fit_hazard(fit_rows, features, l1, l2, weight_key)
    Xo = [[r[f] for f in features] for r in score_rows]
    yo = [r["event"] for r in score_rows]
    wo = [r.get(weight_key, 1.0) for r in score_rows]
    p = m.predict_proba(Xo)
    metrics = {"brier": brier(p, yo), "nll": nll(p, yo), "ece": calibration_error(p, yo),
               "pit_ks": ks_uniform(pit_values(p, yo)), "mean_pred": sum(p) / len(p),
               "actual": sum(yo) / len(yo), "n": len(score_rows)}
    return metrics, m, p, wo, yo


def _write_calibration_csv(reliab, reliab_w, path):
    """PLANv2 6.4: calibration table to CSV (unweighted AND balance-weighted)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["bin", "n", "pred", "actual_unweighted", "actual_balance_weighted"])
        for (b, n, mp, mo), (_, _, _, mow) in zip(reliab, reliab_w):
            w.writerow([b, n, f"{mp:.6f}", f"{mo:.6f}", f"{mow:.6f}"])
    return path


def evaluate(rows, features, weight_key, month_key="month_int", val_months=18,
             test_months=18, n_jobs=None, run_ablation=True, model_choice="auto",
             H_cmp=12, use_signatures=True, family_tol=0.01, model_tol=0.05):
    """Nested TRAIN / VALIDATION / TEST protocol (no selection bias on the headline):
      - TRAIN      : HP search (walk-forward 1-SE).
      - VALIDATION : pick the feature family (base / +regime / +signatures) AND the
                     run-off model (convention/ECM/hazard).
      - TEST       : touched ONCE -> the honest frozen-OOS headline + the deployed
                     comparison. Never used to select anything."""
    months = sorted({r[month_key] for r in rows})
    cut2 = months[-test_months - 1]
    cut1 = months[-test_months - val_months - 1]
    train = [r for r in rows if r[month_key] <= cut1]
    val = [r for r in rows if cut1 < r[month_key] <= cut2]
    test = [r for r in rows if r[month_key] > cut2]
    trainval = [r for r in rows if r[month_key] <= cut2]

    # Candidate feature FAMILIES. Regime posterior: HMM fit on TRAIN only (leakage-safe),
    # filtered forward. Signatures: causal transform, attach directly. L1 selects WITHIN a
    # family; validation NLL selects ACROSS families (so a feature that only matters under
    # special conditions is picked up automatically when those conditions appear).
    hmm_params = fit_hmm(train, features, month_key)
    regime_names = attach_regime_posterior(rows, hmm_params, month_key) if hmm_params else []
    sig_names = attach_signatures(rows, month_key) if use_signatures else []
    candidates = {"base": list(features)}
    if regime_names:
        candidates["base+regime"] = list(features) + regime_names
    if sig_names:
        candidates["base+signatures"] = list(features) + sig_names
    if regime_names and sig_names:
        candidates["base+regime+signatures"] = list(features) + regime_names + sig_names

    # HP on TRAIN (walk-forward, 1-SE). Families share it (they differ by a few columns).
    t0 = time.time()
    best, hp_table = hp_search(train, features, month_key=month_key, weight_key=weight_key,
                               score="nll", rule="1se", n_jobs=n_jobs)
    hp = {"lambda": best["lambda"], "alpha": best["alpha"]}
    t_hp = time.time() - t0

    # VALIDATION: pick the feature family by val NLL (never touches test).
    val_nll = {}
    for name, feats in candidates.items():
        vm, _, _, _, _ = _fit_score(train, val, feats, hp, weight_key)
        val_nll[name] = vm["nll"]
    # parsimony tie-break (1-SE spirit): among families within `family_tol` of the best
    # val NLL, deploy the SIMPLEST (fewest features) -- so a borderline feature is NOT
    # deployed on selection noise (e.g. a 0.3% regime edge stays = base).
    best_fam = min(val_nll.values())
    fam_within = [n for n, v in val_nll.items() if v <= best_fam + family_tol * abs(best_fam)]
    chosen = min(fam_within, key=lambda n: len(candidates[n]))
    use_regime = "regime" in chosen
    use_sig = "signatures" in chosen
    chosen_features = candidates[chosen]
    regime_gate = {"val_nll_by_family": val_nll, "chosen": chosen, "family_tol": family_tol}
    if hmm_params:
        regime_gate["K"] = hmm_params["K"]
        regime_gate["bic_by_k"] = hmm_params.get("bic_by_k")

    # VALIDATION: pick the run-off model (convention/ECM/hazard) on the val cohort run-off.
    l1c, l2c = lam_alpha_to_l1l2(hp["lambda"], hp["alpha"])
    m_tr = fit_hazard(train, chosen_features, l1c, l2c, weight_key)
    erosion_tr = fit_erosion(train, month_key=month_key, weight_key=weight_key)
    hazB_val = hazard_cohort_runoff(rows, cut1, chosen_features, m_tr, erosion_tr, H_cmp, month_key)
    ecm_tr = fit_ecm_model(train, month_key, fit_max=cut1)
    cmp_val = compare_runoff_models(rows, cut1, H_cmp, hazB_val, month_key, ecm=ecm_tr)
    if model_choice == "auto":
        # parsimony tie-break: among models within `model_tol` of the best val MAE, prefer
        # the simplest (convention < ECM < hazard) -- deploy the behavioural model only
        # when it beats the regulator baselines by a real margin, not noise.
        maes = {k: v["mae"] for k, v in cmp_val["models"].items()}
        best_m = min(maes.values())
        order = {"convention": 0, "ecm": 1, "hazard": 2}
        mdl_within = [k for k, v in maes.items() if v <= best_m + model_tol * abs(best_m)]
        runoff_model = min(mdl_within, key=lambda k: order.get(k, 9))
    else:
        runoff_model = model_choice

    # TEST (touched ONCE): headline frozen-OOS + deployed comparison. Refit on TRAIN+VAL.
    test_eval, m_tv, p_te, w_te, y_te = _fit_score(trainval, test, chosen_features, hp, weight_key)
    test_eval["cutoff_month"] = cut2
    test_eval["n_oos"] = len(test)
    q90 = conformal_q([abs(p - y) for p, y in zip(p_te, y_te)], alpha=0.10)
    erosion_tv = fit_erosion(trainval, month_key=month_key, weight_key=weight_key)
    hazB_test = hazard_cohort_runoff(rows, cut2, chosen_features, m_tv, erosion_tv, H_cmp, month_key)
    ecm_tv = fit_ecm_model(trainval, month_key, fit_max=cut2)
    cmp_test = compare_runoff_models(rows, cut2, H_cmp, hazB_test, month_key, ecm=ecm_tv)

    # diagnostics on TEST for the chosen model (calibration -> CSV, PLANv2 6.4)
    reliab = reliability_table(p_te, y_te, n_bins=10)
    reliab_w = reliability_table(p_te, y_te, n_bins=10, w=w_te)
    pit = pit_values(p_te, y_te)
    pit_counts = [0] * 10
    for v in pit:
        pit_counts[min(9, int(v * 10))] += 1
    diagnostics = {
        "reliability": [{"pred": mp, "actual": mo, "n": cnt} for _, cnt, mp, mo in reliab],
        "reliability_weighted": [{"pred": mp, "actual": mo, "n": cnt} for _, cnt, mp, mo in reliab_w],
        "pit_counts": pit_counts, "pit_bins": 10,
        "hp_grid": [{"lambda": c["lambda"], "alpha": c["alpha"], "nll": c["nll"]} for c in hp_table],
    }
    _write_calibration_csv(reliab, reliab_w, os.path.join(OUT, "calibration.csv"))

    ablation = None
    if run_ablation:
        ab = stochastic_ablation.run(seeds=2)
        ablation = {k: {"mae": statistics.mean(v),
                        "se": statistics.pstdev(v) / len(v) ** 0.5} for k, v in ab.items()}

    sel = {"hp": hp, "base_features": list(features), "features": chosen_features,
           "use_regime": use_regime, "use_signatures": use_sig,
           "regime_K": hmm_params["K"] if hmm_params else None,
           "regime_features": regime_names if use_regime else [],
           "signature_features": sig_names if use_sig else [],
           "regime_gate": regime_gate,
           "runoff_model": runoff_model, "runoff_model_choice": model_choice,
           "runoff_model_comparison": cmp_test, "runoff_model_comparison_val": cmp_val,
           "ecm": ecm_tv,
           "hp_rule": "1se", "hp_min": best.get("best_min"), "hp_1se": best.get("best_1se"),
           "conformal_q90_hazard": q90, "frozen_oos_eval": test_eval,
           "diagnostics": diagnostics, "ablation": ablation,
           "split": {"cut1": cut1, "cut2": cut2, "train_n": len(train), "val_n": len(val),
                     "test_n": len(test), "val_months": val_months, "test_months": test_months},
           "hp_search_seconds": round(t_hp, 1)}
    sel["eval_version"] = hashlib.sha256(json.dumps(sel["hp"], sort_keys=True).encode()).hexdigest()[:12]
    return sel


def write_eval(sel, out_dir=ART):
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "hp_selected.json"), "w") as f:
        json.dump(sel, f, indent=2)
    L = ["RUNOFF EVAL / VALIDATION REPORT (reporting only -- NOT the deployed fit)",
         "=" * 60,
         f"HP selection rule: {sel['hp_rule']} (overfit-safe: most-regularized within 1 SE)",
         f"  raw argmin : {sel.get('hp_min')}",
         f"  1-SE (used): {sel.get('hp_1se')}",
         f"selected HP: lambda={sel['hp']['lambda']:.2e} alpha={sel['hp']['alpha']}",
         f"HP search wall time: {sel['hp_search_seconds']}s"]
    sp = sel.get("split")
    if sp:
        L += ["", "NESTED SPLIT (train -> validation -> test; selection on val, headline on test):",
              f"  train n={sp['train_n']} (<= month {sp['cut1']})  "
              f"val n={sp['val_n']} ({sp['val_months']}mo)  "
              f"test n={sp['test_n']} ({sp['test_months']}mo, touched once)"]
    rg = sel.get("regime_gate")
    if rg:
        L += ["", "FEATURE-FAMILY SELECTION (validation NLL, lower=better):",
              f"  HMM states by BIC: K={rg.get('K')}  (bic_by_k={rg.get('bic_by_k')})"]
        vf = rg.get("val_nll_by_family", {})
        for name in sorted(vf, key=vf.get):
            L.append(f"  {name:<26} val NLL={vf[name]:.5f}")
        kept = []
        if sel.get("use_regime"):
            kept.append("regime")
        if sel.get("use_signatures"):
            kept.append("signatures")
        ft = rg.get("family_tol", 0.01)
        note = (", ".join(kept) if kept else
                f"base only -- richer families within the {ft:.0%} parsimony band "
                f"(not a real OOS gain), so the simplest is deployed")
        L.append(f"  -> deployed family: {rg.get('chosen')}  (kept: {note})")

    cmp = sel.get("runoff_model_comparison")
    if cmp:
        L += ["", "RUN-OFF MODEL (TEST cohort book-run-off MAE, lower=better; "
              "selected on validation):"]
        for name in sorted(cmp["models"], key=lambda k: cmp["models"][k]["mae"]):
            mk = cmp["models"][name]
            extra = f"  core_share={mk['core_share']:.2f}" if "core_share" in mk else ""
            L.append(f"  {name:<12} MAE={mk['mae']:.4f}{extra}")
        L.append(f"  -> deployed run-off model: {sel['runoff_model'].upper()}  "
                 f"(choice={sel['runoff_model_choice']})")
    ev = sel.get("ecm")
    if ev:
        ci = ev.get("elasticity_ci") or (None, None, None)
        hl = f"  half-life={ev['half_life_months']:.1f}mo" if ev.get("half_life_months") else ""
        L += ["", "ECM ECONOMICS (book aggregate, PLANv2 5b):",
              f"  rate elasticity = {ev['rate_elasticity']:+.4f}"
              + (f"  (95% block CI [{ci[0]:+.3f}, {ci[2]:+.3f}])" if ci[0] is not None else ""),
              f"  reversion phi   = {ev['reversion_phi']:+.4f}{hl}"]

    L += ["", "FROZEN-OOS EVAL (TEST set -- touched once, never used to select):"]
    for k, v in sel["frozen_oos_eval"].items():
        L.append(f"  {k}: {v:.5f}" if isinstance(v, float) else f"  {k}: {v}")
    if sel["ablation"]:
        L += ["", "STOCHASTIC ABLATION (OOT book-S(t) MAE, lower=better):"]
        for k, v in sorted(sel["ablation"].items(), key=lambda kv: kv[1]["mae"]):
            L.append(f"  {k:<16} {v['mae']:.4f} +/- {1.96*v['se']:.4f}")
    L += ["", "NEXT: runoff_fit.py reuses hp_selected.json to fit the DEPLOYED model "
          "on ALL data (no held-out tail)."]
    with open(os.path.join(out_dir, "validation_report.txt"), "w") as f:
        f.write("\n".join(L))
    return L


def main():
    import argparse
    ap = argparse.ArgumentParser(description="HP selection + frozen-OOS + run-off model selection.")
    ap.add_argument("panel", nargs="?", default=None)
    ap.add_argument("--model", default="auto", choices=["auto", "hazard", "ecm", "convention"],
                    help="deployed run-off model: auto = validation winner (default)")
    ap.add_argument("--no-signatures", action="store_true",
                    help="skip the path-signature candidate family (faster)")
    args = ap.parse_args()
    path = args.panel
    rows, features, weight_key, month_key = prepare(path)
    print(f"{'[demo]' if not path else '[panel]'} {len(rows)} rows; features={features}; "
          f"model={args.model}; signatures={not args.no_signatures}")
    sel = evaluate(rows, features, weight_key, month_key, model_choice=args.model,
                   use_signatures=not args.no_signatures)
    print("\n".join(write_eval(sel)))
    print(f"\nartifacts -> {ART}  (hp_selected.json, validation_report.txt)")


if __name__ == "__main__":
    main()
