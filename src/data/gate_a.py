"""Gate A (PLANv2 2.1 / 4): did rates and inflation actually vary 2015-2024?

If the rate series is largely administered/flat, the rate-sensibilite is weakly
identified -> lean the stress story on inflation + scenarios, and declare the rate
elasticity OOS-unvalidated. This script quantifies variation over the full window,
the dev sub-window (2015-2022) and the frozen-OOS sub-window (2023-2024), and
prints a verdict. Reads _out/macro_panel_wide.csv. Pure stdlib.
"""
from __future__ import annotations

import csv
import os
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "_out")
EPS = 1e-9

RATE_COLS = ["money_market", "policy_discount", "tbill_yield"]
INFL_COLS = ["cpi_yoy"]


def load_wide():
    with open(os.path.join(OUT, "macro_panel_wide.csv"), newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def series_vals(rows, col, lo, hi):
    out = []
    for r in rows:
        if lo <= r["ref_month"] <= hi and r[col] not in ("", None):
            out.append(float(r[col]))
    return out


def stats(vals):
    if not vals:
        return dict(n=0, distinct=0, min=None, max=None, std=None, n_changes=0, max_flat=0)
    changes = flat = max_flat = 0
    for a, b in zip(vals, vals[1:]):
        if abs(b - a) > EPS:
            changes += 1
            flat = 0
        else:
            flat += 1
            max_flat = max(max_flat, flat)
    return dict(n=len(vals), distinct=len(set(round(v, 6) for v in vals)),
                min=round(min(vals), 4), max=round(max(vals), 4),
                std=round(statistics.pstdev(vals), 4) if len(vals) > 1 else 0.0,
                n_changes=changes, max_flat=max_flat + 1)


WINDOWS = [("full 2015-2024", "2015-01", "2024-12"),
           ("dev 2015-2022", "2015-01", "2022-12"),
           ("OOS 2023-2024", "2023-01", "2024-12")]


def main():
    rows = load_wide()
    print("=" * 78)
    print("GATE A - rate & inflation identifiability")
    print("=" * 78)
    summary = {}
    for col in RATE_COLS + INFL_COLS:
        print(f"\n{col}")
        for label, lo, hi in WINDOWS:
            s = stats(series_vals(rows, col, lo, hi))
            summary[(col, label)] = s
            print(f"  {label:<16} n={s['n']:<3} distinct={s['distinct']:<4} "
                  f"range=[{s['min']},{s['max']}] std={s['std']:<8} "
                  f"changes={s['n_changes']:<3} max_flat_run={s['max_flat']}")

    # regimes per sub-window (for OOS placement, PLANv2 2.1)
    print("\nregime_state coverage:")
    for label, lo, hi in WINDOWS:
        states = sorted({r["regime_state"] for r in rows
                         if lo <= r["ref_month"] <= hi and r["regime_state"]})
        print(f"  {label:<16} {states}")

    # ---- verdict ----
    print("\n" + "=" * 78)
    print("VERDICT")
    print("=" * 78)
    mm = summary[("money_market", "full 2015-2024")]
    pol = summary[("policy_discount", "full 2015-2024")]
    mm_oos = summary[("money_market", "OOS 2023-2024")]

    if pol["distinct"] <= 5:
        print(f"- POLICY/DISCOUNT rate is administered: only {pol['distinct']} distinct values, "
              f"max flat run {pol['max_flat']} months. Rate-sensibilite is NOT identifiable "
              f"from the policy rate.")
    if mm["distinct"] >= 30 and mm["std"] > 0.2:
        print(f"- MONEY-MARKET rate genuinely varies ({mm['distinct']} distinct, std={mm['std']}). "
              f"=> USE money_market as the rate driver for the ECM / hazard.")
    if mm_oos["std"] is not None and mm_oos["std"] < 0.15:
        print(f"- WARNING: money-market variation in the frozen OOS (2023-2024) is low "
              f"(std={mm_oos['std']}). The rate elasticity will be weakly OOS-validated; "
              f"keep the main rate-varying episode in DEV and declare OOS rate-sensibilite tentative.")
    else:
        print(f"- Money-market varies in the OOS window too (std={mm_oos['std']}) -> rate elasticity "
              f"is OOS-checkable.")
    infl = summary[("cpi_yoy", "full 2015-2024")]
    print(f"- Inflation (cpi_yoy) range [{infl['min']},{infl['max']}], std={infl['std']} "
          f"-> usable as a primary driver alongside the money-market rate.")
    print("- OOS split: confirm a macro-varying episode exists in BOTH dev and OOS (see regimes above) "
          "before fixing the frozen window.")


if __name__ == "__main__":
    main()
