"""run_pipeline.py -- the ALM master runner (pure stdlib), fully autonomous end-to-end.

ONE command for the ALM team. Runs the operational scripts in the governed order and
writes an audit trail:

  MONTHLY (routine):     python run_pipeline.py --data-dir "D:\\efm_dumps"
  GOVERNED (on demand):  python run_pipeline.py --data-dir "D:\\efm_dumps" --recalibrate
  DEMO (no bank data):   python run_pipeline.py --demo

Full chain: download -> [eval] -> fit -> daily -> stress -> report -> book(IRRBB).
The last step (runoff_book) produces the multi-product workbook report.xlsx with the
per-book run-off + dEVE/dNII + crisis + uncertainty + GBM challenger.

SELF-GOVERNING recalibration (the "--recalibrate if it flags a regime change" behaviour):
after refreshing data, a CUSUM mean-shift test runs on the money-market rate; if a break
fired in the last few months the pipeline AUTO-ESCALATES to a governed recalibration
(re-selects HPs). No human trigger needed. --no-auto-recalibrate disables it; the FIRST
run always recalibrates (no frozen HPs yet).

Routine  = reuse the frozen, signed-off hyper-parameters (hp_selected.json); re-fit
           coefficients on ALL data. Does NOT re-select HPs.
Governed = adds runoff_eval BEFORE fit (re-selects HPs + frozen-OOS report); model.json
           is flagged "pending model-risk sign-off".

Governance / audit: every run appends _out/run_log.txt and writes _out/run_manifest.json
(timestamp, host, mode, panel end month, model version, per-step status+timing,
deliverable inventory). Exit code is 0 only if every required step succeeded -- safe to
drive from Windows Task Scheduler / cron.

Steps (the same scripts the team can run individually):
  runoff_download.py  update data    runoff_eval.py    recalibrate (governed only)
  runoff_fit.py       deploy fit     runoff_daily.py   score
  runoff_stress.py    stress test    runoff_report.py  report

Only aggregates leave the bank (coefficients, S(t), calibration, report) -- never rows.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import json
import os
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
DATA_DIR = os.path.join(HERE, "data")
MACRO_PIT = os.path.join(DATA_DIR, "_out", "macro_panel_pit.csv")
PANEL_CSV = os.path.join(HERE, "panel", "_out", "panel.csv")
ART = os.path.join(HERE, "_artifacts")
OUT = os.path.join(HERE, "_out")
HP_SELECTED = os.path.join(ART, "hp_selected.json")
MODEL_JSON = os.path.join(ART, "model.json")

DEFAULT_MC_PATHS = 1200
MIN_PYTHON = (3, 8)
DELIVERABLES = [
    ("_artifacts/model.json", True),
    ("_artifacts/validation_report.txt", False),   # only after a recalibrate
    ("_out/calibration.csv", False),
    ("_out/stress/mc_stress.json", False),
    ("_out/report.html", True),
    ("_out/book/report.xlsx", False),               # multi-product + IRRBB workbook
    ("_out/book/book_runoff.json", False),
]


def _utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _script(name):
    return [PY, os.path.join(HERE, name)]


def _panel_end(path):
    if not os.path.exists(path):
        return None
    last = None
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for r in csv.DictReader(f):
                m = int(r["month_int"])
                last = m if last is None else max(last, m)
    except (KeyError, ValueError, OSError):
        return None
    return last


def _model_version(path):
    try:
        return json.load(open(path)).get("version")
    except (OSError, ValueError):
        return None


def regime_break_active():
    """Self-governance: run a CUSUM mean-shift test on the freshly-downloaded money-market
    rate and report whether a break fired in the last few months. If so, the pipeline
    AUTO-ESCALATES to a governed recalibration (the '--recalibrate if it flags a regime
    change' behaviour) -- no human trigger needed. Returns (active, detail)."""
    wide = os.path.join(DATA_DIR, "_out", "macro_panel_wide.csv")
    if not os.path.exists(wide):
        return False, "no macro panel yet"
    try:
        sys.path.insert(0, os.path.join(HERE, "model"))
        from structural_breaks import cusum_detect
        series, months = [], []
        with open(wide, newline="", encoding="utf-8") as f:
            rd = csv.DictReader(f)
            for r in rd:
                v = r.get("money_market")
                if v not in (None, ""):
                    try:
                        series.append(float(v))
                        months.append(r.get("ref_month"))
                    except ValueError:
                        pass
        if len(series) < 24:
            return False, "series too short for CUSUM"
        cu = cusum_detect(series)
        active = bool(any(cu["changepoint"][-3:]))
        return active, (f"CUSUM break in last 3 months (cps={cu.get('cps')})" if active
                        else "no recent break")
    except Exception as e:                                    # never let monitoring crash the run
        return False, f"break-check skipped ({type(e).__name__})"


def preflight(args):
    """Fail fast with actionable messages before doing any work."""
    problems = []
    if not args.dav_dir and not args.demo:
        problems.append(
            "no data source. Pass --dav-dir \"<dumps dir>\" for real data, or --demo "
            "for the synthetic demo. Refusing a bare run: it would train on SYNTHETIC "
            "data and overwrite the deployed model.json.")
    if sys.version_info < MIN_PYTHON:
        problems.append(f"Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required "
                        f"(found {sys.version_info.major}.{sys.version_info.minor}).")
    if args.dav_dir:
        if not os.path.isdir(args.dav_dir):
            problems.append(f"--dav-dir not found: {args.dav_dir}")
        else:
            import glob
            dumps = glob.glob(os.path.join(args.dav_dir, "DAV_*.txt")) + \
                glob.glob(os.path.join(args.dav_dir, "DAV_*.text"))
            has_efm = False
            if not dumps:                                 # accept a converted EFM tree too
                try:
                    sys.path.insert(0, os.path.join(HERE, "panel"))
                    import efm_collect
                    has_efm = next(iter(efm_collect.find_efm_files(args.dav_dir)), None) is not None
                except Exception:
                    has_efm = False
            if not dumps and not has_efm:
                problems.append(f"no DAV_*.txt dumps and no EFM workbooks "
                                f"(06-EFM/*.xlsx) in {args.dav_dir}")
    if not args.skip_macro and not os.path.exists(MACRO_PIT) and args.dav_dir:
        print(f"  note: macro panel absent -> will fetch (needs internet to "
              f"BoA/IMF/World Bank). Use --skip-macro to skip.")
    return problems


def run(step, label, argv, fatal=True):
    """Run one step; append (name, rc, seconds) to `step` list. Returns rc."""
    print("\n" + "=" * 72 + f"\n{label}\n" + "=" * 72, flush=True)
    print("  $ " + " ".join(os.path.basename(a) if a.endswith(".py") else a for a in argv))
    t0 = time.time()
    rc = subprocess.call(argv)
    dt = round(time.time() - t0, 1)
    step.append({"name": label, "rc": rc, "seconds": dt})
    if rc != 0:
        print(f"  >> STEP FAILED (exit {rc}) after {dt}s." + (" Aborting." if fatal else " Continuing."))
        if fatal:
            _finish(step, mode="ABORTED", ok=False)
            sys.exit(rc)
    else:
        print(f"  >> ok ({dt}s)")
    return rc


def _finish(steps, mode, ok):
    """Write the audit manifest + append the run log, print the summary."""
    os.makedirs(OUT, exist_ok=True)
    deliverables = {}
    for rel, required in DELIVERABLES:
        full = os.path.join(HERE, *rel.split("/"))
        ex = os.path.exists(full)
        deliverables[rel] = {"exists": ex, "required": required,
                             "bytes": os.path.getsize(full) if ex else 0}
    manifest = {
        "run_utc": _utc(), "mode": mode, "host": socket.gethostname(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
        "panel_end_month": _panel_end(PANEL_CSV),
        "model_version": _model_version(MODEL_JSON),
        "model_status": ("RECALIBRATED -- pending model-risk sign-off"
                         if mode == "recalibrate" else "routine recalibration (frozen HPs)"),
        "steps": steps, "deliverables": deliverables, "ok": ok,
    }
    json.dump(manifest, open(os.path.join(OUT, "run_manifest.json"), "w"), indent=2)
    with open(os.path.join(OUT, "run_log.txt"), "a", encoding="utf-8") as f:
        f.write(f"{manifest['run_utc']}  mode={mode:<11} ok={ok!s:<5} "
                f"panel_end={manifest['panel_end_month']} model={manifest['model_version']} "
                f"host={manifest['host']}\n")

    print("\n" + "=" * 72)
    print(f"PIPELINE {'COMPLETE' if ok else 'INCOMPLETE'}  (mode={mode})")
    print("=" * 72)
    print(f"  model version : {manifest['model_version']}   ({manifest['model_status']})")
    print(f"  panel end     : {manifest['panel_end_month']}")
    print("  deliverables:")
    for rel, d in deliverables.items():
        tag = "OK " if d["exists"] else ("MISSING!" if d["required"] else "skipped ")
        print(f"    [{tag}] {rel}" + (f"  ({d['bytes']:,} B)" if d["exists"] else ""))
    print(f"  audit: _out/run_manifest.json  (+ appended _out/run_log.txt)")
    print("=" * 72)


def main():
    ap = argparse.ArgumentParser(
        description="DAV run-off ALM master runner.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--dav-dir", "--data-dir", dest="dav_dir", default=None,
                    help="dir of real data: DAV_*.txt dumps OR a converted EFM tree "
                         "(06-EFM/*.xlsx). --data-dir is an alias.")
    ap.add_argument("--demo", action="store_true",
                    help="run on SYNTHETIC data (required if no --data-dir; never in production)")
    ap.add_argument("--recalibrate", action="store_true",
                    help="GOVERNED: re-select hyper-parameters (runoff_eval) before fitting")
    ap.add_argument("--no-auto-recalibrate", action="store_true",
                    help="do NOT auto-escalate to recalibrate on a detected regime break")
    ap.add_argument("--no-augment", action="store_true",
                    help="disable the engineered path features in the book (they are ON by "
                         "default, per-segment validation-gated so they never overfit)")
    ap.add_argument("--model", default="auto", choices=["auto", "hazard", "ecm", "convention"],
                    help="deployed run-off model (auto = frozen-OOS winner); set at recalibrate")
    ap.add_argument("--refresh-macro", action="store_true", help="force re-fetching macro")
    ap.add_argument("--skip-macro", action="store_true", help="never fetch macro (use cached)")
    ap.add_argument("--floor", type=float, default=50.0, help="balance-below-floor event threshold (KDA)")
    ap.add_argument("--scope", default=None, help="comma-separated Rubrique keywords (default: keep all)")
    ap.add_argument("--paths", type=int, default=DEFAULT_MC_PATHS, help="Monte-Carlo paths for stress")
    ap.add_argument("--crisis-elasticity", type=float, default=None,
                    help="ALM what-if: imposed deposit-flight elasticity for the reverse-stress crisis (default 0.8)")
    ap.add_argument("--crisis-oil-drop", type=float, default=None,
                    help="ALM what-if: imposed oil-price drop fraction in the crisis (default 0.40 = -40%%)")
    ap.add_argument("--crisis-months", type=int, default=None,
                    help="ALM what-if: duration of the imposed oil crash, in months (default 6)")
    ap.add_argument("--dry-run", action="store_true", help="preflight + print the plan, run nothing")
    args = ap.parse_args()

    print("=" * 72 + f"\nDAV run-off pipeline  |  {_utc()}  |  host={socket.gethostname()}\n" + "=" * 72)
    problems = preflight(args)
    if problems:
        print("PREFLIGHT FAILED:")
        for p in problems:
            print(f"  - {p}")
        sys.exit(2)

    # First run (no frozen HPs) must recalibrate; otherwise honour the flag OR auto-escalate
    # when a regime break is detected (self-governing: "--recalibrate if it flags a change").
    first_run = not os.path.exists(HP_SELECTED)
    brk_active, brk_detail = (False, "monitoring off")
    if not args.no_auto_recalibrate:
        brk_active, brk_detail = regime_break_active()
    recalibrate = args.recalibrate or first_run or brk_active
    mode = "recalibrate" if recalibrate else "routine"
    why = ("first run (no frozen HPs yet)" if (first_run and not args.recalibrate) else
           "--recalibrate" if args.recalibrate else
           f"AUTO: regime break detected ({brk_detail})" if brk_active else "reuse frozen HPs")
    print(f"  regime monitor: {'BREAK -> auto-recalibrate' if brk_active else brk_detail}")
    print(f"  mode: {mode.upper()}  ({why})")
    print(f"  cadence: download -> " + ("eval -> " if recalibrate else "") +
          "fit -> daily -> stress -> report -> book(IRRBB)")
    if args.dry_run:
        print("  --dry-run: preflight OK, nothing executed.")
        return

    steps = []
    n = (7 if recalibrate else 6)                            # + book/IRRBB step
    i = [0]

    def step_label(name):
        i[0] += 1
        return f"[{i[0]}/{n}] {name}"

    # 1) update data
    dl = _script("runoff_download.py")
    if args.dav_dir:
        dl += ["--dav-dir", args.dav_dir]
    if args.refresh_macro:
        dl += ["--refresh-macro"]
    if args.skip_macro:
        dl += ["--skip-macro"]
    dl += ["--floor", str(args.floor)]
    if args.scope:
        dl += ["--scope", args.scope]
    run(steps, step_label("runoff_download  (update data)"), dl)

    panel_arg = [PANEL_CSV] if os.path.exists(PANEL_CSV) else []

    # 2) (governed) re-select HPs + frozen-OOS validation report
    if recalibrate:
        run(steps, step_label("runoff_eval  (re-select HPs + frozen-OOS + model selection)"),
            _script("runoff_eval.py") + panel_arg + ["--model", args.model])
    # 3) deployed fit on ALL data (reuses HPs)
    run(steps, step_label("runoff_fit  (deploy: fit ALL data, frozen HPs)"),
        _script("runoff_fit.py") + panel_arg)
    # 4) score
    run(steps, step_label("runoff_daily  (score S(t))"), _script("runoff_daily.py") + panel_arg)
    # 5) Monte-Carlo stress (non-fatal: heavier, optional)
    run(steps, step_label("runoff_stress  (Monte-Carlo fan + WAL tail)"),
        _script("runoff_stress.py") + panel_arg + ["--paths", str(args.paths)], fatal=False)
    # 6) single-product report (html + svg)
    run(steps, step_label("runoff_report  (report.html + svg)"), _script("runoff_report.py"))
    # 7) multi-product book + IRRBB (dEVE/dNII, crisis, uncertainty, GBM challenger) -> report.xlsx
    book_argv = _script("runoff_book.py") + panel_arg
    if not args.no_augment:                                  # augment ON by default (gated)
        book_argv += ["--augment"]
    if args.crisis_elasticity is not None:                   # ALM what-if pass-through
        book_argv += ["--crisis-elasticity", str(args.crisis_elasticity)]
    if args.crisis_oil_drop is not None:
        book_argv += ["--crisis-oil-drop", str(args.crisis_oil_drop)]
    if args.crisis_months is not None:
        book_argv += ["--crisis-months", str(args.crisis_months)]
    run(steps, step_label("runoff_book  (multi-product + IRRBB -> report.xlsx)"),
        book_argv, fatal=False)

    # success = every REQUIRED step passed; stress + book are non-fatal (heavier, optional)
    ok = all(s["rc"] == 0 for s in steps
             if "runoff_stress" not in s["name"] and "runoff_book" not in s["name"])
    _finish(steps, mode=mode, ok=ok)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
