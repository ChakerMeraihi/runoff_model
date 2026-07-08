"""runoff_daily.py -- the operational heartbeat (pure stdlib).

Run every day (or on demand): `python runoff_daily.py`. IDEMPOTENT -- on days with
no new monthly close it re-emits the current S(t) and exits; the day the close lands
it ingests and scores. It NEVER changes model coefficients (only runoff_refit does).

Each run, when there IS new data:
  1. load the FROZEN model.json (coefficients, scaler, HMM params, conformal q)
  2. advance the online regime FILTER one step (frozen HMM params) -> regime posterior
  3. run a CUSUM break ALARM on the macro series (recommend early refit, no auto-act)
  4. roll the frozen hazard forward -> book S(t) base + stressed (+200bp) + bands + WAL
  5. write outputs + update the state file

Usage:  python runoff_daily.py [panel.csv]   (no arg -> synthetic demo, same as refit)
Writes: _out/daily/runoff_<month>.json , _out/daily/state.json
"""
from __future__ import annotations

import csv
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for sub in ("model", "panel", "data"):
    sys.path.insert(0, os.path.join(HERE, sub))

from hazard import _sigmoid                                # noqa: E402
from structural_breaks import cusum_detect                 # noqa: E402
from runoff_common import filter_regime_series, ensure_demo_panel  # noqa: E402

ART = os.path.join(HERE, "_artifacts")
OUT = os.path.join(HERE, "_out", "daily")
STATE = os.path.join(OUT, "state.json")


class FrozenHazard:
    """Reconstruct the deployed hazard predictor from model.json (standardized space)."""
    def __init__(self, model):
        h = model["hazard"]
        self.features = model["features"]
        self.b0 = h["intercept_std"]
        self.b = h["coef_std"]
        self.mu = h["scaler_mu"]
        self.sd = h["scaler_sd"]

    def proba(self, featdict):
        z = [(featdict[f] - self.mu[i]) / self.sd[i] for i, f in enumerate(self.features)]
        return _sigmoid(self.b0 + sum(self.b[i] * z[i] for i in range(len(z))))


def filtered_regime(hmm_params, series):
    """Current causal filtered posterior P(state_t | x_<=t) using FROZEN, STANDARDIZED
    HMM params (shared with eval/fit via runoff_common.filter_regime_series)."""
    if not hmm_params:
        return None
    post = filter_regime_series(hmm_params, series)
    return post[-1] if post else None


class FrozenErosion:
    """Reconstruct the deployed erosion r(t) model from model.json['erosion']."""
    def __init__(self, params):
        self.features = params["features"]
        self.coef = params["coef"]
        self.intercept = params["intercept"]

    def increment(self, featdict):
        return self.intercept + sum(self.coef[f] * featdict.get(f, 0.0) for f in self.features)


def book_survival(alive, hazard, macro_now, ramadan_fwd, H, season_step, erosion=None):
    """Roll frozen hazard forward (and erosion r(t) if present), freezing macro at its
    current value (future macro unknown); seasoning increments; ramadan known.
    Returns (B_t, A_t): B = balance-weighted A(t)*r(t); A = attrition-only survival."""
    wsum = sum(w for w, _ in alive) or 1e-12
    B_out, A_out = [], []
    for h in range(H + 1):
        b_tot = a_tot = 0.0
        for w, feat in alive:
            S, cum_g = 1.0, 0.0
            for hh in range(1, h + 1):
                fd = dict(feat)
                if "seasoning" in fd:
                    fd["seasoning"] = feat["seasoning"] + hh
                if "z_seasoning" in fd:
                    fd["z_seasoning"] = feat["z_seasoning"] + hh * season_step
                if "ramadan_frac" in fd and ramadan_fwd:
                    fd["ramadan_frac"] = ramadan_fwd[(hh - 1) % len(ramadan_fwd)]
                S *= (1 - hazard.proba(fd))
                if erosion:
                    cum_g += erosion.increment(fd)
            r = math.exp(cum_g) if erosion else 1.0
            a_tot += w * S
            b_tot += w * S * r
        A_out.append(a_tot / wsum)
        B_out.append(b_tot / wsum)
    return B_out, A_out


def wal(St):
    """Weighted average life (months) = sum of S(t) over the horizon."""
    return sum(St[1:])


def load_state():
    if os.path.exists(STATE):
        with open(STATE) as f:
            return json.load(f)
    return {"last_processed_month": None, "last_version": None}


def _panel_inputs(path, model):
    """Real panel.csv -> alive-account feature rows (at the latest month) + the macro
    series for the regime filter. Alive = account whose last observed month is the
    panel's latest month and whose final event flag is 0."""
    import csv
    with open(path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # build account features from the BASE (panel) columns; regime posterior columns are
    # injected separately in main(). Signature columns (if the deployed model uses them)
    # are reconstructed here from each account's causal path.
    base_feats = model.get("base_features", model["features"])
    sig_names = model.get("signature_features") or []
    use_sig = bool(model.get("use_signatures") and sig_names)
    if use_sig:
        for r in rows:
            try:
                r["month_int"] = int(r["month_int"])
                for c in ("log_balance", "money_market"):
                    if c in r:
                        r[c] = float(r[c])
            except (KeyError, ValueError):
                pass
        from runoff_common import attach_signatures
        attach_signatures(rows, "month_int")
    feats = list(base_feats) + (sig_names if use_sig else [])
    by_acc = {}
    for r in rows:
        by_acc.setdefault(r["account_id"], []).append(r)
    cur = max(int(r["month_int"]) for r in rows)
    alive = []
    for _, rs in by_acc.items():
        rs.sort(key=lambda r: int(r["month_int"]))
        last = rs[-1]
        if int(last["month_int"]) == cur and int(float(last["event"])) == 0:
            try:
                feat = {fe: float(last[fe]) for fe in feats}
            except (KeyError, ValueError):
                continue
            w = float(last.get("balance_kda", 1.0) or 1.0)
            alive.append((w, feat))
    # macro series for the HMM filter (one row per month, from the HMM's feature set)
    hmm = model.get("hmm")
    series = []
    if hmm:
        bym = {}
        for r in rows:
            m = int(r["month_int"])
            if m not in bym:
                try:
                    bym[m] = [float(r[fe]) for fe in hmm["features"]]
                except (KeyError, ValueError):
                    pass
        series = [bym[m] for m in sorted(bym)]
    return alive, series, cur, feats


def _read_panel_rows(path):
    """Minimal panel read (account_id, month_int, balance_kda) for the convention model."""
    out = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                out.append({"account_id": r["account_id"], "month_int": int(r["month_int"]),
                            "balance_kda": float(r["balance_kda"])})
            except (KeyError, ValueError):
                pass
    return out


def main():
    os.makedirs(OUT, exist_ok=True)
    panel_path = sys.argv[1] if len(sys.argv) > 1 else None
    model_path = os.path.join(ART, "model.json")
    if not os.path.exists(model_path):
        print("no model.json -- run runoff_fit.py first.")
        return
    with open(model_path) as f:
        model = json.load(f)

    # inputs: real panel.csv if given, else the real-format synthetic demo panel
    if not (panel_path and os.path.exists(panel_path)):
        panel_path = ensure_demo_panel()
    alive, series, current_month, _ = _panel_inputs(panel_path, model)
    if not alive:
        print("no alive accounts to score.")
        return

    state = load_state()
    if state["last_processed_month"] == current_month and state["last_version"] == model["version"]:
        print(f"[no-op] month {current_month} already scored with model "
              f"{model['version']}. Nothing new. (idempotent)")
        return

    hazard = FrozenHazard(model)
    erosion = FrozenErosion(model["erosion"]) if model.get("erosion") else None
    H = 12

    # regime filter (frozen params) + break alarm
    regime = filtered_regime(model.get("hmm"), series)
    # Role-1: inject the current regime posterior into each alive account's features,
    # held constant over the roll-forward horizon (future regime unknown). Only when
    # Gate B kept the regime feature; otherwise it is reported but not used.
    if model.get("use_regime") and regime and model.get("regime_features"):
        for k, nm in enumerate(model["regime_features"]):
            val = regime[k] if k < len(regime) else 0.0
            for _, feat in alive:
                feat[nm] = val
    macro_1d = [row[0] for row in series]
    cu = cusum_detect(macro_1d)
    recent_break = any(cu["changepoint"][-3:])

    # roll run-off per the DEPLOYED model choice (convention | ecm | hazard) -- the
    # frozen Gate-B selection from runoff_eval. base + stressed (+200bp) + WAL.
    season_step = 1.0 / 40.0
    runoff_model = model.get("runoff_model", "hazard")
    if runoff_model == "ecm" and model.get("ecm"):
        from runoff_common import ecm_book_runoff
        base_B = ecm_book_runoff(model["ecm"], H)
        base_A = base_B
        stressed_B = ecm_book_runoff(model["ecm"], H, rate_bump=2.0)
        quantity = "B(t) [ECM stock decay]"
    elif runoff_model == "convention":
        from runoff_common import convention_book_runoff
        base_B, _ = convention_book_runoff(_read_panel_rows(panel_path), current_month, H, "month_int")
        base_A = base_B
        stressed_B = base_B                  # convention is rate-insensitive by construction
        quantity = "B(t) [core/volatile]"
    else:                                    # hazard (account-level A(t)*r(t))
        runoff_model = "hazard"
        base_B, base_A = book_survival(alive, hazard, None, [0.0], H, season_step, erosion)
        stressed_B = base_B
        if "money_market" in model["features"]:
            bumped = [(w, {**feat, "money_market": feat.get("money_market", 0.0) + 2.0})
                      for w, feat in alive]
            stressed_B, _ = book_survival(bumped, hazard, None, [0.0], H, season_step, erosion)
        quantity = "B(t)=A(t)*r(t)" if erosion else "A(t)"

    q = model.get("conformal_q90_hazard", 0.0)
    out = {
        "asof_month": current_month, "model_version": model["version"],
        "runoff_model": runoff_model,
        "n_alive_accounts": len(alive), "run_off_quantity": quantity,
        "B_t_base": [round(s, 4) for s in base_B],
        "A_t_attrition_only": [round(s, 4) for s in base_A],
        "B_t_stressed_+200bp": [round(s, 4) for s in stressed_B],
        "S_t_base": [round(s, 4) for s in base_B],            # back-compat alias
        "S_t_stressed_+200bp": [round(s, 4) for s in stressed_B],
        "WAL_months_base": round(wal(base_B), 2),
        "WAL_months_stressed": round(wal(stressed_B), 2),
        "hazard_conformal_q90": round(q, 4),
        "regime_posterior": [round(p, 3) for p in regime] if regime else None,
        "break_alarm": bool(recent_break),
        "recommend_early_refit": bool(recent_break),
    }
    base = base_B
    with open(os.path.join(OUT, f"runoff_{current_month}.json"), "w") as f:
        json.dump(out, f, indent=2)
    with open(STATE, "w") as f:
        json.dump({"last_processed_month": current_month,
                   "last_version": model["version"]}, f)

    try:
        from viz import ascii_spark
        spark = "  S(t) shape: " + ascii_spark(base)
    except Exception:
        spark = ""
    print(f"[scored] month {current_month}  model {model['version']}  "
          f"alive={len(alive)}")
    if spark:
        print(spark)
    print(f"  S(t) base:     {out['S_t_base']}")
    print(f"  S(t) +200bp:   {out['S_t_stressed_+200bp']}")
    print(f"  WAL base/stress: {out['WAL_months_base']} / {out['WAL_months_stressed']} mo")
    print(f"  regime posterior: {out['regime_posterior']}")
    print(f"  break alarm: {out['break_alarm']}  (recommend early refit: "
          f"{out['recommend_early_refit']})")
    print(f"  -> {os.path.join(OUT, f'runoff_{current_month}.json')}")


if __name__ == "__main__":
    main()
