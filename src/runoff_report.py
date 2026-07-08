"""runoff_report.py -- plot EVERYTHING into one self-contained HTML report (stdlib).

Pulls from the artifacts the pipeline already produces and renders a single
report.html with inline SVG (opens in any browser on the bank PC, no library):

  1. Run-off S(t): base + stressed, with the conformal/bootstrap fan band
  2. Calibration: reliability diagram (predicted vs realized hazard)
  3. PIT histogram (uniformity check)
  4. Stochastic ablation: OOT MAE bars (data-driven vs Markov vs parametric)
  5. HP surface heatmap (lambda x alpha) with the 1-SE pick marked
  6. Regime timeline: macro series shaded by online-HMM filtered state + breaks
  7. Macro panel overview: rate / inflation / oil sparklined

Inputs (best-effort -- each panel renders if its source exists):
  _artifacts/hp_selected.json, _artifacts/model.json,
  _out/daily/runoff_<month>.json, data/_out/macro_panel_wide.csv

Usage:  python runoff_report.py   ->  _out/report.html
"""
from __future__ import annotations

import csv
import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
for sub in ("model", "data"):
    sys.path.insert(0, os.path.join(HERE, sub))

import viz                                                # noqa: E402
from survival import reliability_table, pit_values        # noqa: E402

ART = os.path.join(HERE, "_artifacts")
OUTD = os.path.join(HERE, "_out")
MACRO = os.path.join(HERE, "data", "_out", "macro_panel_wide.csv")
PANEL = os.path.join(HERE, "panel", "_out", "panel.csv")


def _read_panel_balance():
    """Aggregate book balance per month from the panel -> the realised history series."""
    if not os.path.exists(PANEL):
        return None
    agg = {}
    with open(PANEL, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                agg[int(r["month_int"])] = agg.get(int(r["month_int"]), 0.0) + float(r["balance_kda"])
            except (KeyError, ValueError):
                continue
    return [agg[m] for m in sorted(agg)] if agg else None


def _load_json(path):
    return json.load(open(path)) if os.path.exists(path) else None


def _latest_daily():
    files = sorted(glob.glob(os.path.join(OUTD, "daily", "runoff_*.json")))
    return _load_json(files[-1]) if files else None


def _read_macro():
    if not os.path.exists(MACRO):
        return None
    with open(MACRO, newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if "2015-01" <= r["ref_month"] <= "2024-12"]
    return rows or None


def build_report():
    panels = []                     # (heading, svg, caption)

    # 1. S(t) from the latest daily score, banded by the model's conformal q
    daily = _latest_daily()
    model = _load_json(os.path.join(ART, "model.json"))
    if daily:
        base = daily["S_t_base"]
        stressed = daily.get("S_t_stressed_+200bp", base)
        q = (model or {}).get("conformal_q90_hazard", 0.0)
        # cumulative band proxy: hazard band widens the survival envelope with horizon
        band = [(i, max(0.0, s - q * i), min(1.0, s + q * i)) for i, s in enumerate(base)]
        svg = viz.svg_lines(
            [("base", list(enumerate(base))), ("+200bp", list(enumerate(stressed)))],
            title=f"Run-off B(t)  (asof month {daily['asof_month']})",
            xlabel="horizon (months)", ylabel="B(t) = fraction of book balance",
            band=("90% band", band),
            ymin=min(min(stressed), min(l for _, l, _ in band)) * 0.98, ymax=1.0)
        rm = daily.get("runoff_model") or (model or {}).get("runoff_model", "hazard")
        panels.append(("Run-off curve B(t) (deployed book run-off)", svg,
                       f"Deployed model: {str(rm).upper()}. Base WAL {daily['WAL_months_base']}mo, "
                       f"stressed {daily['WAL_months_stressed']}mo. Band = conformal q90 x horizon. "
                       f"B(t) = the fraction of today's deposit BALANCE still on the book at t."))

    # 1a. historical DAV book balance (the realised series the run-off is anchored on)
    hist = _read_panel_balance()
    if hist:
        svgh = viz.svg_lines([("book balance (KDA)", list(enumerate(hist)))],
                             title="Historical DAV book balance (aggregate)",
                             xlabel="month index (panel start -> end)", ylabel="total CTRVL KDA",
                             ymin=0.0, ymax=max(hist) * 1.05)
        panels.append(("Historical DAV balance", svgh,
                       f"Summed book balance per month over the panel ({len(hist)} months). "
                       "B(t) above projects the decay of the CURRENT stock; this is the realised "
                       "history it is anchored on."))

    # 1b. erosion decomposition B(t) = A(t) x r(t): pure attrition vs combined book
    if daily and daily.get("A_t_attrition_only") and daily.get("B_t_base"):
        a_t = daily["A_t_attrition_only"]
        b_t = daily["B_t_base"]
        lo = min(min(a_t), min(b_t))
        svg2 = viz.svg_lines(
            [("A(t) attrition only", list(enumerate(a_t))),
             ("B(t) = A(t).r(t)", list(enumerate(b_t)))],
            title="Run-off decomposition  B(t) = A(t) x r(t)",
            xlabel="horizon (months)", ylabel="surviving fraction of book",
            ymin=min(0.98, lo * 0.98), ymax=max(1.0, max(b_t)))
        ev = (model or {}).get("erosion", {})
        coefs = ", ".join(f"{k}={v:+.2e}" for k, v in (ev.get("coef") or {}).items())
        panels.append(("Erosion: B(t) = A(t).r(t)", svg2,
                       "A(t) = pure account attrition (survival hazard); r(t) = per-account "
                       "balance erosion. Their product B(t) is the deployed book run-off. "
                       f"Gap between the curves = the erosion contribution. r(t) coefs: {coefs}."))

    # 1c. Monte-Carlo stress distribution (runoff_stress.py) -- the fan + WAL tails
    mc = _load_json(os.path.join(OUTD, "stress", "mc_stress.json"))
    if mc and mc.get("scenarios"):
        sc = mc["scenarios"]
        base_fan = sc["baseline"]["fan"]
        band = [(c["h"], c["p05"], c["p95"]) for c in base_fan]
        lines = [("baseline median", [(c["h"], c["p50"]) for c in base_fan])]
        if "rate_+200bp" in sc:
            lines.append(("+200bp median", [(c["h"], c["p50"]) for c in sc["rate_+200bp"]["fan"]]))
        lo = min(c["p05"] for c in base_fan)
        svgmc = viz.svg_lines(lines, title=f"Monte-Carlo run-off ({mc['n_paths']} paths)",
                              xlabel="horizon (months)", ylabel="S(t)",
                              band=("baseline 5-95%", band), ymin=min(0.98, lo * 0.98), ymax=1.0)
        wal = ", ".join(f"{k}={sc[k]['wal_p50']:.1f}mo" for k in sc)
        panels.append(("Monte-Carlo stress distribution", svgmc,
                       "Forward-simulated regime+macro paths through the frozen hazard. "
                       f"Shaded = baseline 5-95% band; WAL p50 by scenario: {wal}. "
                       f"Baseline WAL tail p01/p99 = {sc['baseline']['wal_p01']:.1f}/"
                       f"{sc['baseline']['wal_p99']:.1f}mo. {mc.get('note', '')}"))

    # 1d. Run-off model selection: realised vs convention / ECM / hazard (the Gate-B pick)
    cmp = (model or {}).get("runoff_model_comparison")
    if cmp and cmp.get("models"):
        lines = [("realised", list(enumerate(cmp["realized"])))]
        for name in sorted(cmp["models"]):
            lines.append((name, list(enumerate(cmp["models"][name]["B"]))))
        allvals = list(cmp["realized"]) + [v for n in cmp["models"] for v in cmp["models"][n]["B"]]
        svgsel = viz.svg_lines(lines, title="Run-off model selection (frozen-OOS)",
                               xlabel="horizon (months)", ylabel="book run-off B(t)",
                               ymin=min(0.95, min(allvals) * 0.98), ymax=max(1.0, max(allvals)))
        order = sorted(cmp["models"], key=lambda k: cmp["models"][k]["mae"])
        maes = ", ".join(f"{n}={cmp['models'][n]['mae']:.4f}" for n in order)
        deployed = (model or {}).get("runoff_model") or cmp.get("winner")
        ev = (model or {}).get("ecm") or {}
        econ = (f" ECM rate-elasticity={ev['rate_elasticity']:+.4f}, "
                f"reversion phi={ev['reversion_phi']:+.4f}"
                f"{', half-life %.1fmo' % ev['half_life_months'] if ev.get('half_life_months') else ''}."
                if ev.get("rate_elasticity") is not None else "")
        panels.append(("Run-off model selection (convention vs ECM vs hazard)", svgsel,
                       "Each model's predicted book run-off vs REALISED cohort run-off on the "
                       f"held-out tail. MAE (lower=better): {maes}. Deployed: "
                       f"{str(deployed).upper()} (frozen-OOS winner).{econ}"))

    sel = _load_json(os.path.join(ART, "hp_selected.json"))
    diag = (sel or {}).get("diagnostics")

    # 2. reliability diagram (THE calibration plot for a run-off model)
    if diag and diag.get("reliability"):
        rel = diag["reliability"]
        panels.append(("Calibration: reliability diagram",
                       viz.svg_reliability([b["pred"] for b in rel],
                                           [b["actual"] for b in rel],
                                           title="Reliability (frozen-OOS hazard)"),
                       "Points on the diagonal = calibrated. A run-off model lives or "
                       "dies on calibration, not discrimination."))

    # 3. PIT histogram (uniformity = calibrated)
    if diag and diag.get("pit_counts"):
        pc, nb = diag["pit_counts"], diag["pit_bins"]
        # reconstruct a flat list of bin midpoints weighted by count for the hist
        vals = []
        for i, c in enumerate(pc):
            vals += [(i + 0.5) / nb] * c
        if vals:
            panels.append(("Calibration: PIT histogram",
                           viz.svg_hist(vals, bins=nb, title="PIT (uniform = calibrated)",
                                        xlabel="PIT value", ylabel="count", ref_uniform=True),
                           "Randomized PIT of the held-out hazard; flat against the red "
                           "line = well-calibrated."))

    # 3b. HP surface heatmap (lambda x alpha) -- shows the flat surface / 1-SE choice
    if diag and diag.get("hp_grid"):
        g = diag["hp_grid"]
        alphas = sorted({c["alpha"] for c in g})
        lams = sorted({c["lambda"] for c in g})
        cell = {(c["alpha"], c["lambda"]): c["nll"] for c in g}
        grid = [[cell.get((a, l)) for l in lams] for a in alphas]
        panels.append(("HP surface (walk-forward OOT NLL)",
                       viz.svg_heatmap([f"{l:.0e}" for l in lams],
                                       [f"{a:.2f}" for a in alphas], grid,
                                       title="HP grid NLL (lower=better)",
                                       xlabel="lambda", ylabel="alpha (L1 frac)"),
                       f"1-SE pick: lambda={sel['hp']['lambda']:.1e} "
                       f"alpha={sel['hp']['alpha']}. Flat surface -> 1-SE takes the "
                       f"most-regularized model."))

    # 4. stochastic ablation bars
    if sel and sel.get("ablation"):
        ab = sel["ablation"]
        names = sorted(ab, key=lambda k: ab[k]["mae"])
        panels.append(("Stochastic ablation",
                       viz.svg_bars(names, [ab[n]["mae"] for n in names],
                                    errs=[ab[n]["se"] * 1.96 for n in names],
                                    title="OOT book-S(t) MAE (lower=better)", ylabel="MAE"),
                       "Data-driven EN-logistic vs Markov-generator / parametric / floor."))

    # 5. macro overview + regime timeline
    macro = _read_macro()
    if macro:
        months = [r["ref_month"] for r in macro]
        oil = [float(r["oil_brent"]) for r in macro if r["oil_brent"]]
        regime_map = {"hydrocarbon_liquid": 0, "stagnant": 1, "currency_stress": 2}
        reg = [regime_map.get(r["regime_state"]) for r in macro]
        panels.append(("Oil + regime timeline",
                       viz.svg_timeline(months, oil, regime=reg,
                                        title="Brent oil shaded by regime",
                                        xlabel="month (2015-2024)", ylabel="Brent $/bbl"),
                       "Shading: green=hydrocarbon-liquid, orange=stagnant, "
                       "red=currency-stress. This is the EXOGENOUS scenario calendar "
                       "(PLANv2 6.7 Role-2, used for stressed S(t)) -- distinct from the "
                       "LEARNED HMM posterior panel below (Role-1, the hazard feature)."))
        mm = [float(r["money_market"]) for r in macro if r["money_market"]]
        infl = [float(r["cpi_yoy"]) * 100 for r in macro if r["cpi_yoy"]]
        panels.append(("Rate & inflation",
                       viz.svg_lines([("money-market %", list(enumerate(mm))),
                                      ("inflation YoY %", list(enumerate(infl)))],
                                     title="Money-market rate & inflation",
                                     xlabel="month index", ylabel="%"),
                       "Gate A: policy rate is administered; money-market is the rate driver."))

    # 6. regime posterior (current) as bars -- the LEARNED HMM (Role-1)
    if daily and daily.get("regime_posterior"):
        rp = daily["regime_posterior"]
        used = (model or {}).get("use_regime")
        gate = (model or {}).get("regime_gate", {}) or {}
        role = ("DEPLOYED as a hazard feature" if used else
                "reported only -- Gate B dropped it (did not improve frozen-OOS)")
        panels.append(("Learned-HMM regime posterior (Role-1)",
                       viz.svg_bars([f"state{i}" for i in range(len(rp))], rp,
                                    title="HMM filtered P(regime)  (standardized, BIC-K)",
                                    ylabel="prob"),
                       f"Causal filtered posterior of the BIC-selected K={gate.get('K', len(rp))}"
                       f"-state HMM on standardized macro. {role}. Break alarm: "
                       f"{daily.get('break_alarm')}."))

    return panels


def write_html(panels, path):
    parts = ['<!doctype html><html><head><meta charset="utf-8">',
             '<title>DAV run-off report</title>',
             '<style>body{font-family:sans-serif;margin:24px;background:#fafafa}'
             'h1{font-size:22px}section{background:white;border:1px solid #ddd;'
             'border-radius:8px;padding:16px;margin:18px 0;max-width:760px}'
             'p{color:#555;font-size:13px}</style></head><body>',
             '<h1>DAV run-off &mdash; diagnostics report</h1>',
             '<p>Self-contained, generated pure-stdlib (inline SVG). '
             'Aggregates only &mdash; no client data.</p>']
    for heading, svg, caption in panels:
        parts.append(f'<section><h2>{heading}</h2>{svg}<p>{caption}</p></section>')
    parts.append("</body></html>")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))
    return path


def main():
    panels = build_report()
    if not panels:
        print("no artifacts found -- run runoff_eval / runoff_fit / runoff_daily first.")
        return
    out = write_html(panels, os.path.join(OUTD, "report.html"))
    # also drop each SVG standalone for embedding in the LaTeX/Beamer report
    svgd = os.path.join(OUTD, "svg")
    for i, (heading, svg, _) in enumerate(panels):
        slug = "".join(c if c.isalnum() else "_" for c in heading.lower())[:30]
        viz.write_svg(svg, os.path.join(svgd, f"{i:02d}_{slug}.svg"))
    print(f"report -> {out}  ({len(panels)} panels)")
    print(f"standalone SVGs -> {svgd}")
    for heading, _, _ in panels:
        print(f"  - {heading}")


if __name__ == "__main__":
    main()
