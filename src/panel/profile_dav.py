"""Profile the real anonymized DAV dumps to drive Phase-0 decisions (pure stdlib).

RUN THIS ON THE WORK PC, then share the printed report (and profile_report.txt).
It reads (does not modify) every DAV_*.txt and reports what we need to finalize:
  - file inventory, encodings, delimiters, header drift, missing columns
  - distinct Rubriques / CODE TYPE COMPTE / Ressources-Remplois / Business Line (+counts)
  - GRAIN evidence: rows per (client, month); clients holding multiple rubriques/types
  - SCOPE evidence: which Rubriques dominate; is 'Depot de garantie' material?
  - balance (CTRVL KDA): missing / zero / negative / quantiles
  - date coverage and DATE OUVERTURE parse rate
No client data leaves the bank -- only aggregate counts.

Usage:  python profile_dav.py  "<dir of DAV_*.txt>"
"""
from __future__ import annotations

import collections
import glob
import os
import sys

from dav_reader import read_dav_file, parse_us_date, norm


def _quantiles(vals, qs=(0.0, 0.25, 0.5, 0.75, 0.95, 1.0)):
    if not vals:
        return {q: None for q in qs}
    s = sorted(vals)
    out = {}
    for q in qs:
        i = q * (len(s) - 1)
        lo = int(i)
        out[q] = s[lo] if lo + 1 >= len(s) else s[lo] + (i - lo) * (s[lo + 1] - s[lo])
    return out


def main(in_dir, report_path="profile_report.txt"):
    files = sorted(glob.glob(os.path.join(in_dir, "DAV_*.txt")) +
                   glob.glob(os.path.join(in_dir, "DAV_*.text")))
    lines = []

    def out(s=""):
        print(s)
        lines.append(s)

    out("=" * 74)
    out(f"DAV PROFILE  ({len(files)} files)  dir={in_dir}")
    out("=" * 74)
    if not files:
        out("No DAV_*.txt files found.")
        return

    rub = collections.Counter()
    typ = collections.Counter()
    ress = collections.Counter()
    bline = collections.Counter()
    enc_c = collections.Counter()
    delim_c = collections.Counter()
    header_variants = collections.Counter()
    missing_any = collections.Counter()
    periods = []
    rows_per_client_month = []          # (#rows) per (client, file)
    client_rubriques = collections.defaultdict(set)
    client_types = collections.defaultdict(set)
    bal_vals = []
    bal_missing = bal_zero = bal_neg = 0
    open_ok = open_bad = 0
    open_years = []

    for path in files:
        recs, meta = read_dav_file(path)
        enc_c[meta.get("encoding")] += 1
        if not meta.get("ok"):
            out(f"  !! {os.path.basename(path)}: {meta.get('error')}")
            continue
        delim_c[repr(meta["delimiter"])] += 1
        header_variants[tuple(norm(c) for c in meta["raw_header"])] += 1
        for f in meta["missing"]:
            missing_any[f] += 1
        if meta["period"]:
            periods.append(meta["period"])

        per_client = collections.Counter()
        for r in recs:
            rub[r["rubrique"] or "(none)"] += 1
            typ[r["type_compte"] or "(none)"] += 1
            ress[r["ressources"] or "(none)"] += 1
            bline[r["business_line"] or "(none)"] += 1
            per_client[r["client_id"]] += 1
            if r["rubrique"]:
                client_rubriques[r["client_id"]].add(r["rubrique"])
            if r["type_compte"]:
                client_types[r["client_id"]].add(r["type_compte"])
            b = r["ctrvl_kda"]
            if b is None:
                bal_missing += 1
            else:
                bal_vals.append(b)
                if b == 0:
                    bal_zero += 1
                elif b < 0:
                    bal_neg += 1
            d = parse_us_date(r["date_ouverture"])
            if d:
                open_ok += 1
                open_years.append(d[0])
            elif r["date_ouverture"]:
                open_bad += 1
        rows_per_client_month.extend(per_client.values())

    # ---- report ----
    if periods:
        periods.sort()
        out(f"\nperiod coverage: {periods[0][1]:02d}/{periods[0][0]} .. "
            f"{periods[-1][1]:02d}/{periods[-1][0]}  ({len(periods)} months)")
    out(f"encodings: {dict(enc_c)}")
    out(f"delimiters: {dict(delim_c)}")
    out(f"distinct header layouts (column drift): {len(header_variants)}")
    if missing_any:
        out(f"columns missing in some files: {dict(missing_any)}")

    out("\n--- SCOPE: Rubriques (counts) ---")
    for k, v in rub.most_common():
        out(f"  {v:>10,}  {k}")
    out("--- CODE TYPE COMPTE (counts) ---")
    for k, v in typ.most_common(20):
        out(f"  {v:>10,}  {k}")
    out("--- Ressources-Remplois ---")
    for k, v in ress.most_common():
        out(f"  {v:>10,}  {k}")
    out("--- Business Line ---")
    for k, v in bline.most_common(15):
        out(f"  {v:>10,}  {k}")

    out("\n--- GRAIN evidence ---")
    if rows_per_client_month:
        rc = collections.Counter(rows_per_client_month)
        out(f"  rows per (client, month): " +
            ", ".join(f"{k}->{rc[k]}" for k in sorted(rc)[:8]))
    multi_rub = sum(1 for s in client_rubriques.values() if len(s) > 1)
    multi_typ = sum(1 for s in client_types.values() if len(s) > 1)
    out(f"  clients with >1 distinct Rubrique: {multi_rub} / {len(client_rubriques)}")
    out(f"  clients with >1 distinct TYPE COMPTE: {multi_typ} / {len(client_types)}")
    out("  -> if these are large, the account key must include rubrique/type, "
        "not client alone")

    out("\n--- BALANCE (CTRVL KDA) ---")
    out(f"  missing={bal_missing}  zero={bal_zero}  negative={bal_neg}  "
        f"valid={len(bal_vals)}")
    q = _quantiles(bal_vals)
    out("  quantiles: " + ", ".join(f"{int(p*100)}%={q[p]:,.1f}" for p in q if q[p] is not None))

    out("\n--- DATE OUVERTURE ---")
    out(f"  parsed ok={open_ok}  unparseable={open_bad}")
    if open_years:
        out(f"  opening-year range: {min(open_years)} .. {max(open_years)}")

    with open(report_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    out(f"\n(report written to {report_path})")


if __name__ == "__main__":
    d = sys.argv[1] if len(sys.argv) > 1 else os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "_synth")
    main(d)
