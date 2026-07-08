"""Run every model-module self-test and report PASS/FAIL (pure stdlib).

Each module's `__main__` is a validation against known ground truth. This harness
imports and runs them in a subprocess-free way by exec'ing their main guard, so a
single `python run_tests.py` re-validates the whole numerical kit.
"""
from __future__ import annotations

import runpy
import sys
import time

MODULES = [
    "linalg", "fracdiff", "explosive", "structural_breaks", "range_vol",
    "signatures", "splitter", "ecm", "survival",
    "hazard", "hmm_regime", "online_hmm", "bootstrap", "conformal", "viz",
    "montecarlo", "convention", "linmodel", "erosion", "xlsx_writer", "xlsx_validate",
    "products", "irrbb", "param_uncertainty", "macro_sim", "gbm", "nonlin_experiment",
    "regime_var_garch",
]


def main():
    results = []
    for m in MODULES:
        t0 = time.time()
        try:
            runpy.run_module(m, run_name="__main__")
            ok = True
            err = ""
        except Exception as exc:  # noqa
            ok = False
            err = f"{type(exc).__name__}: {exc}"
        results.append((m, ok, time.time() - t0, err))
        print(f"\n{'-'*70}")

    print("\n" + "=" * 70)
    print("MODEL KIT TEST SUMMARY")
    print("=" * 70)
    npass = sum(1 for _, ok, _, _ in results if ok)
    for m, ok, dt, err in results:
        tag = "PASS" if ok else "FAIL"
        print(f"  [{tag}] {m:<20} {dt:5.1f}s  {err}")
    print(f"\n{npass}/{len(results)} modules passed")
    return 0 if npass == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
