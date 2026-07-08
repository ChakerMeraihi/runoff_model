"""runoff_fit.py -- fit the DEPLOYED model on ALL data (pure stdlib).

The operational fit, run MONTHLY. It REUSES the HPs chosen by runoff_eval.py (no
re-tuning monthly -> stable, governed) and fits the hazard on ALL data up to today
(no held-out tail -- the live run-off must be current). Also fits the regime HMM
params used by runoff_daily's online filter.

  load hp_selected.json -> fit hazard on ALL rows -> fit HMM -> write model.json

Usage:  python runoff_fit.py [panel.csv]   (no arg -> synthetic demo)
Requires: runoff_eval.py has been run first (hp_selected.json must exist).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

from runoff_common import (ART, prepare, fit_hazard, fit_hmm, fit_erosion,
                           serialize_hazard, lam_alpha_to_l1l2, attach_regime_posterior,
                           attach_signatures, fit_ecm_model)


def fit_deployed(rows, base_features, weight_key, month_key, sel):
    lam, alpha = sel["hp"]["lambda"], sel["hp"]["alpha"]
    l1, l2 = lam_alpha_to_l1l2(lam, alpha)

    # Refit the regime HMM on ALL data (same K the eval's BIC chose, for consistency).
    K = sel.get("regime_K")
    hmm = fit_hmm(rows, base_features, month_key, ks=(K,) if K else (2, 3))
    # Attach extra feature families ONLY if validation kept them (regime 6.7 / signatures 6.2).
    regime_names = []
    if hmm and sel.get("use_regime"):
        regime_names = attach_regime_posterior(rows, hmm, month_key)
    sig_names = []
    if sel.get("use_signatures"):
        sig_names = attach_signatures(rows, month_key)
    fit_features = list(base_features) + regime_names + sig_names

    m = fit_hazard(rows, fit_features, l1, l2, weight_key)       # ALL data
    erosion = fit_erosion(rows, month_key=month_key, weight_key=weight_key)  # r(t), PLANv2 6.5
    ecm = fit_ecm_model(rows, month_key)                          # deployed ECM economics (ALL data)
    runoff_model = sel.get("runoff_model", "hazard")
    months = sorted({r[month_key] for r in rows})
    ya = [r["event"] for r in rows]
    model = {
        "features": fit_features, "base_features": list(base_features),
        "regime_features": regime_names, "use_regime": bool(regime_names),
        "signature_features": sig_names, "use_signatures": bool(sig_names),
        "runoff_model": runoff_model,
        "runoff_model_comparison": sel.get("runoff_model_comparison"),
        "hp": sel["hp"],
        "hazard": serialize_hazard(m), "hmm": hmm, "erosion": erosion, "ecm": ecm,
        "conformal_q90_hazard": sel.get("conformal_q90_hazard", 0.0),
        "trained_through_month": months[-1],
        "deployed_event_rate": sum(ya) / len(ya),
        "eval_version": sel.get("eval_version"),
        "regime_gate": sel.get("regime_gate"),
        "frozen_oos_eval": sel.get("frozen_oos_eval"),
        "note": "DEPLOYED: trained on ALL data with HPs from runoff_eval. run-off = "
                "A(t)*r(t) when erosion present. Regime posterior is a hazard feature "
                "iff Gate B kept it (use_regime). frozen_oos_eval is held-out reporting.",
    }
    model["version"] = hashlib.sha256(
        json.dumps(model["hazard"], sort_keys=True).encode()).hexdigest()[:12]
    return model


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    sel_path = os.path.join(ART, "hp_selected.json")
    if not os.path.exists(sel_path):
        print("no hp_selected.json -- run runoff_eval.py first.")
        return
    with open(sel_path) as f:
        sel = json.load(f)

    rows, features, weight_key, month_key = prepare(path)
    # use the exact base feature set eval selected on (guard against panel drift)
    base_features = sel.get("base_features", sel.get("features", features))
    print(f"{'[demo]' if not path else '[panel]'} {len(rows)} rows; "
          f"HP from eval: {sel['hp']}; use_regime={sel.get('use_regime')}")

    model = fit_deployed(rows, base_features, weight_key, month_key, sel)
    os.makedirs(ART, exist_ok=True)
    with open(os.path.join(ART, "model.json"), "w") as f:
        json.dump(model, f, indent=2)
    print(f"deployed model.json written  version={model['version']}  "
          f"trained_through={model['trained_through_month']}  "
          f"event_rate={model['deployed_event_rate']:.4f}  "
          f"hmm={'K=%d' % model['hmm']['K'] if model['hmm'] else 'none'}  "
          f"regime_feat={'yes' if model['use_regime'] else 'no'}  "
          f"erosion={'yes' if model['erosion'] else 'none'}  "
          f"runoff_model={model['runoff_model'].upper()}")
    print(f"-> {os.path.join(ART, 'model.json')}")


if __name__ == "__main__":
    main()
