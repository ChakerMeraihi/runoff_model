"""runoff_download.py -- STEP 1: update the data (pure stdlib).

The ALM team's DATA job. Run it whenever a new monthly DAV dump lands (just drop the
new DAV_MMYYYY.txt into the dumps dir) or to pull fresh macro. It does two things:

  1. refresh external macro  -> data/_out/macro_panel_{wide,pit}.csv  (Bank of Algeria
     rate, IMF CPI, World Bank oil, FX; needs outbound internet, idempotent/cached)
  2. (re)build the account-month panel from ALL dumps in the dir, PIT-joined to macro
     -> panel/_out/panel.csv

It does NOT touch the model -- it only refreshes inputs. Survival bookkeeping (an
account "closes" when its last-seen month < the panel end) depends on the FULL history,
so this is a clean full rebuild over every dump present (which naturally concatenates
each month) -- never a naive append that could mis-date closures.

Usage:
  python runoff_download.py --dav-dir "D:/dav_dumps"      # real data (full refresh)
  python runoff_download.py --dav-dir <dir> --skip-macro  # panel only, no fetch
  python runoff_download.py                               # synthetic demo dumps

After this: runoff_refit -> runoff_daily -> runoff_stress -> runoff_report
(or just run all of it: run_pipeline.py).
"""
from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
sys.path.insert(0, os.path.join(HERE, "panel"))

import panel_builder                                        # noqa: E402

MACRO_PIT = os.path.join(HERE, "data", "_out", "macro_panel_pit.csv")
PANEL_CSV = os.path.join(HERE, "panel", "_out", "panel.csv")


def _prev_max_month(path):
    if not os.path.exists(path):
        return None
    last = None
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                m = int(r["month_int"])
            except (KeyError, ValueError):
                continue
            last = m if last is None else max(last, m)
    return last


def _refresh_macro(skip, refresh):
    if skip:
        print("  macro: --skip-macro (using existing macro_panel_pit.csv).")
        return
    if not refresh and os.path.exists(MACRO_PIT):
        print(f"  macro: present ({os.path.basename(MACRO_PIT)}) -- pass --refresh-macro to re-fetch.")
        return
    print("  macro: fetching external series (data/run_all.py) ...")
    rc = subprocess.call([PY, os.path.join(HERE, "data", "run_all.py")])
    if rc != 0:
        print("  macro: WARNING fetch failed; panel will use whatever macro is present.")


def main():
    ap = argparse.ArgumentParser(description="Update DAV run-off data (macro + panel).")
    ap.add_argument("--dav-dir", "--data-dir", dest="dav_dir", default=None,
                    help="dir of real data: DAV_*.txt dumps OR a converted EFM tree "
                         "(06-EFM/*.xlsx). Omit -> synthetic demo. --data-dir is an alias.")
    ap.add_argument("--out", default=PANEL_CSV, help="output panel.csv path")
    ap.add_argument("--skip-macro", action="store_true")
    ap.add_argument("--refresh-macro", action="store_true")
    ap.add_argument("--floor", type=float, default=50.0)
    ap.add_argument("--scope", default=None, help="comma-separated Rubrique keywords (default: keep all)")
    args = ap.parse_args()

    print("=" * 70 + "\n[STEP 1] runoff_download -- update data\n" + "=" * 70)
    prev = _prev_max_month(args.out)
    _refresh_macro(args.skip_macro, args.refresh_macro)

    macro = MACRO_PIT if os.path.exists(MACRO_PIT) else None
    if args.dav_dir:
        scope = [s.strip().upper() for s in args.scope.split(",")] if args.scope else None
        src = panel_builder._detect_source(args.dav_dir) or "?"
        print(f"  panel: rebuilding from {src.upper()} data in {args.dav_dir} ...")
        rows, summ = panel_builder.build_panel(args.dav_dir, scope_keywords=scope,
                                               floor=args.floor, macro_pit=macro)
    else:
        synth_dir = os.path.join(HERE, "panel", "_synth")
        sys.path.insert(0, os.path.join(HERE, "panel"))
        import synth_dav_files
        print("  panel: no --dav-dir -> generating synthetic demo dumps ...")
        synth_dav_files.generate(synth_dir, n_clients=300, seed=1)
        rows, summ = panel_builder.build_panel(synth_dir, floor=args.floor, macro_pit=macro)

    out = panel_builder.write_panel(rows, args.out)
    new = summ["panel_end"]
    print("\n  PANEL SUMMARY:")
    for k in ("n_accounts", "n_person_months", "panel_start", "panel_end",
              "event_rate", "macro_cols"):
        print(f"    {k}: {summ[k]}")
    if prev is None:
        status = "initial build"
    elif new > prev:
        status = f"NEW DATA ingested (panel end {prev} -> {new})"
    elif new == prev:
        status = "no new month (panel end unchanged)"
    else:
        status = f"WARNING panel end went backwards ({prev} -> {new})"
    print(f"\n  -> {out}\n  status: {status}")
    print("  next: runoff_refit.py  (then daily / stress / report) -- or run_pipeline.py")


if __name__ == "__main__":
    main()
