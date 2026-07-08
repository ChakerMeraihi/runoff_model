"""runoff_stress.py -- Monte-Carlo stress distribution of the run-off (pure stdlib).

On-demand / quarterly (NOT daily -- it is heavier than the point score and does not
change the model). Loads the FROZEN model.json, forward-simulates regime+macro paths
through the hazard (model/montecarlo.py), and writes the S(t) FAN and the WAL tail for
a set of IRRBB scenarios:

  baseline     -- start from the current filtered regime posterior
  +200bp/-200bp parallel rate shocks
  adverse-regime -- start pinned in the highest-rate (stress) regime

Output: _out/stress/mc_stress.json  (consumed by runoff_report.py for the fan chart).
This is the DISTRIBUTIONAL stress object (median + 5/95 band + 1/99 tail), complementing
the deterministic +200bp curve in runoff_daily and the Role-2 Markov generator.

Usage:  python runoff_stress.py [panel.csv] [--paths N] [--horizon H]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for sub in ("model", "panel", "data"):
    sys.path.insert(0, os.path.join(HERE, sub))

import montecarlo                                            # noqa: E402
from runoff_common import ensure_demo_panel                 # noqa: E402
from runoff_daily import _panel_inputs, filtered_regime     # noqa: E402

ART = os.path.join(HERE, "_artifacts")
OUT = os.path.join(HERE, "_out", "stress")


def _adverse_state(hmm, rate_feat="money_market"):
    """The 'rate-stress' regime = the state with the highest de-standardized mean rate
    (higher rate -> higher attrition via the hazard's positive rate coef)."""
    if rate_feat not in hmm["features"]:
        return None
    d = hmm["features"].index(rate_feat)
    mu, in_mu, in_sd = hmm["mu"], hmm["in_mu"], hmm["in_sd"]
    real_mean = [mu[k][d] * in_sd[d] + in_mu[d] for k in range(hmm["K"])]
    return max(range(hmm["K"]), key=lambda k: real_mean[k])


def main():
    ap = argparse.ArgumentParser(description="Monte-Carlo run-off stress distribution.")
    ap.add_argument("panel", nargs="?", default=None)
    ap.add_argument("--paths", type=int, default=2000)
    ap.add_argument("--horizon", type=int, default=12)
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    model_path = os.path.join(ART, "model.json")
    if not os.path.exists(model_path):
        print("no model.json -- run runoff_fit.py first.")
        return
    model = json.load(open(model_path))
    if not model.get("hmm"):
        print("model has no HMM (path generator) -- cannot Monte-Carlo. "
              "Re-run runoff_fit with macro features present.")
        return

    panel = args.panel if (args.panel and os.path.exists(args.panel)) else ensure_demo_panel()
    alive, series, current_month, _ = _panel_inputs(panel, model)
    if not alive:
        print("no alive accounts to score.")
        return

    p0 = filtered_regime(model["hmm"], series) or [1.0 / model["hmm"]["K"]] * model["hmm"]["K"]
    H, N = args.horizon, args.paths
    adv = _adverse_state(model["hmm"])

    scenarios = {
        "baseline": dict(p0=p0),
        "rate_+200bp": dict(p0=p0, rate_bump=2.0),
        "rate_-200bp": dict(p0=p0, rate_bump=-2.0),
        "adverse_regime": dict(force_state=adv),
    }
    out = {"asof_month": current_month, "model_version": model["version"],
           "n_alive_accounts": len(alive), "n_paths": N, "horizon": H,
           "adverse_state": adv, "use_regime": model.get("use_regime"),
           "note": "MACRO-PATH uncertainty (simulated regime+macro through the frozen "
                   "hazard). For the FULL band, combine with the time-block bootstrap "
                   "(parameter uncertainty) -- the binding constraint on 120 months / "
                   "few cycles (PLANv2 0.2/7). adverse_regime moves the curve only via "
                   "simulated macro when Gate B dropped the regime feature (use_regime).",
           "scenarios": {}}
    print(f"Monte-Carlo stress: {N} paths x {len(alive)} accounts x H={H}  "
          f"(asof {current_month}, model {model['version']})")
    for name, kw in scenarios.items():
        r = montecarlo.mc_runoff(model, alive, H=H, n_paths=N, seed=args.seed, **kw)
        out["scenarios"][name] = r
        f = r["fan"]
        print(f"  {name:<16} S({H}) p05/p50/p95 = {f[H]['p05']:.3f}/{f[H]['p50']:.3f}/"
              f"{f[H]['p95']:.3f}   WAL p50={r['wal_p50']:.2f}  "
              f"[p01={r['wal_p01']:.2f}, p99={r['wal_p99']:.2f}]")

    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "mc_stress.json")
    json.dump(out, open(path, "w"), indent=2)
    print(f"  -> {path}")


if __name__ == "__main__":
    main()
