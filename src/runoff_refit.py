"""runoff_refit.py -- convenience wrapper: full recalibration = eval THEN fit.

For when you want both in one command. The two steps are independent and can be run
alone:
  python runoff_eval.py [panel.csv]   # HP selection + frozen-OOS + ablation (reporting)
  python runoff_fit.py  [panel.csv]   # deployed fit on ALL data (operational)

This wrapper just runs eval then fit so a monthly governed recalibration is one call.
"""
from __future__ import annotations

import sys

import runoff_eval
import runoff_fit


def main():
    print("=" * 60, "\n[1/2] runoff_eval (HP selection + frozen-OOS + ablation)\n" + "=" * 60)
    runoff_eval.main()
    print("\n" + "=" * 60, "\n[2/2] runoff_fit (deployed fit on ALL data)\n" + "=" * 60)
    runoff_fit.main()


if __name__ == "__main__":
    main()
