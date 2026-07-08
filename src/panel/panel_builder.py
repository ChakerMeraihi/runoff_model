"""Build the account-month survival panel from the monthly DAV dumps (PLANv2 4).

Stacks all DAV_*.txt -> one person-month panel ready for splitter -> hazard ->
survival. Grain and DAV scope are PARAMETERS (finalize from profile_dav.py output
on the work PC). Event = DISAPPEARANCE OR BALANCE-BELOW-FLOOR (user decision).

Survival bookkeeping:
  - account key = (client_id, *key_extra)         [default: + type_compte]
  - scope: keep Ressources + Rubrique matching `scope_keywords`               (DAV)
  - event month = earliest of {first month balance < floor, closure month}
       closure = account's last observed month, IF < panel_end (else censored)
  - right-censored: alive in the last file -> event=0 throughout
  - left-truncation: handled implicitly -- only observed person-months enter, and
    `seasoning` (months since opening) carries pre-observation tenure
  - internal gaps: carried forward as alive (configurable)

No look-ahead: every row's features use only that month; the macro join is PIT
(uses macro_panel_pit.csv, which already lags each series by its publication delay).
"""
from __future__ import annotations

import csv
import glob
import math
import os

from dav_reader import read_dav_file, parse_us_date, parse_number, norm, month_int

HERE = os.path.dirname(os.path.abspath(__file__))


def segment_of(rubrique):
    """Map a Rubrique label to a product segment (the catalogue lives in
    model/products.py). Behavioral books (comptes a vue dinars/devises, epargne,
    decouverts, HB engagement) each become their OWN hazard+erosion segment; guarantee
    deposits are KEPT but tagged separately (contractual lifecycle, not depositor
    behaviour -> excluded from the behavioral engine). Refine the keyword map once
    profile_dav.py / efm_collect profile shows the real Rubrique values."""
    r = norm(rubrique)
    # order matters: check the more specific labels first
    if "EPARGNE" in r or "LIVRET" in r:
        return "epargne"
    if "DECOUVERT" in r or "DEBITEUR" in r or ("VUE" in r and "DEBIT" in r):
        return "decouverts"
    if "ENGAGEMENT" in r or "FINANCEMENT" in r:
        return "hb_engagement"
    if "VUE" in r and "DINAR" in r:
        return "vue_dinars"
    if "VUE" in r and ("DEVISE" in r or "BU" in r):
        return "vue_devises"
    if "VUE" in r:
        return "vue_other"
    if "GARANTIE" in r:
        return "garantie"
    return "other"


def _in_scope(rec, scope_keywords, require_ressources):
    # deposits only: drop Remplois (uses/assets = loans), keep Ressources (liabilities)
    if require_ressources and rec["ressources"] and "REMPLOI" in norm(rec["ressources"]):
        return False
    # default: KEEP every deposit product (tagged by segment). scope_keywords only
    # RESTRICTS if explicitly provided -- nothing is dropped by default.
    if not scope_keywords:
        return True
    r = norm(rec["rubrique"])
    return any(k in r for k in scope_keywords)


def _dav_records(in_dir):
    """Yield reader records from every DAV_*.txt / .text dump in a directory."""
    files = sorted(glob.glob(os.path.join(in_dir, "DAV_*.txt")) +
                   glob.glob(os.path.join(in_dir, "DAV_*.text")))
    for path in files:
        recs, meta = read_dav_file(path)
        if not meta.get("ok"):
            continue
        for r in recs:
            yield r


def _efm_records(in_dir, sheet=None):
    """Yield reader-shaped records from the converted EFM .xlsx workbooks (the 'Détails
    Ressources' deposit section). Reuses efm_collect's pure-stdlib .xlsx reader + WANT
    column resolver, so the SAME survival logic serves both data sources. Grain is the
    client (IDENTIF.NATIONAL) since EFM usually has no per-account id -> the account key
    is (client, type_compte), exactly as for DAV. The period is the workbook's month
    (from its filename). Old binary .xls are skipped (convert them first with the ps1)."""
    import efm_collect as efm
    sheet = sheet or efm.SECTION_SHEETS["ressources"]
    for p in sorted(efm.find_efm_files(in_dir)):
        if efm.detect_format(p) != "xlsx":                # skip old .xls / unknown
            continue
        period, _label = efm.resolve_period(p)
        if not period:
            continue
        try:
            rows = efm.read_xlsx_sheet(p, sheet)
        except Exception:                                 # unreadable / sheet missing
            continue
        if not rows:
            continue
        idx = efm._build_idx_map(rows[0])
        if "identif_national" not in idx or "solde_kda" not in idx:
            continue                                      # not the expected section/schema
        y, mo = int(period[:4]), int(period[5:7])
        mi = month_int(y, mo)
        for row in rows[1:]:
            def cell(name):
                j = idx.get(name)
                v = row[j] if (j is not None and j < len(row)) else None
                return v.strip() if isinstance(v, str) else v
            cid = cell("identif_national")
            if not cid:
                continue
            yield {
                "client_id": cid,
                "year": y, "month": mo, "month_int": mi,
                "ressources": "Ressources",               # we read the Ressources section
                "business_line": cell("business_line"),
                "type_compte": cell("code_type_compte") or cell("ordinal_compte"),
                "rubrique": cell("rubrique"),
                "date_ouverture": cell("date_ouverture"),
                "ctrvl_kda": parse_number(cell("solde_kda")),
                "solde": None,
                "currency": cell("devise"),
            }


def _detect_source(in_dir):
    """'dav' if DAV_*.txt dumps are present, else 'efm' if EFM workbooks are found under
    a 06-EFM folder, else None."""
    if (glob.glob(os.path.join(in_dir, "DAV_*.txt")) or
            glob.glob(os.path.join(in_dir, "DAV_*.text"))):
        return "dav"
    try:
        import efm_collect as efm
        if next(iter(efm.find_efm_files(in_dir)), None) is not None:
            return "efm"
    except Exception:
        pass
    return None


def build_panel(in_dir, key_extra=("type_compte",), scope_keywords=None,
                require_ressources=True, floor=0.0, carry_gaps=True,
                macro_pit=None, source="auto"):
    if source == "auto":
        source = _detect_source(in_dir)
    if source == "efm":
        records = _efm_records(in_dir)
    elif source == "dav":
        records = _dav_records(in_dir)
    else:
        raise RuntimeError(f"no DAV_*.txt dumps or EFM workbooks found under {in_dir!r}")

    # account -> {month_int: balance}, plus opening month and label fields
    acc = {}
    all_months = set()
    for r in records:
        if not _in_scope(r, scope_keywords, require_ressources):
            continue
        key = (r["client_id"],) + tuple(r.get(k) for k in key_extra)
        mi = r["month_int"]
        all_months.add(mi)
        a = acc.setdefault(key, {"bal": {}, "open": None, "rub": r["rubrique"],
                                 "type": r["type_compte"],
                                 "segment": segment_of(r["rubrique"])})
        # if duplicate (key, month): aggregate balances
        a["bal"][mi] = (a["bal"].get(mi, 0.0) or 0.0) + (r["ctrvl_kda"] or 0.0)
        od = parse_us_date(r["date_ouverture"])
        if od and a["open"] is None:
            a["open"] = month_int(*od)

    if not all_months:
        raise RuntimeError(f"no in-scope records found under {in_dir!r} (source={source})")
    panel_start, panel_end = min(all_months), max(all_months)
    macro = _load_macro(macro_pit) if macro_pit else None
    macro_cols = macro["cols"] if macro else []

    rows = []
    n_event_floor = n_event_close = n_censored = 0
    n_bad_open = n_left_trunc = 0
    for key, a in acc.items():
        present = sorted(a["bal"])
        first_seen, last_seen = present[0], present[-1]
        open_mi = a["open"] if a["open"] is not None else first_seen
        # data quality: opening AFTER first observation is impossible -> clamp + count
        if open_mi > first_seen:
            n_bad_open += 1
            open_mi = first_seen
        # left-truncation flag: opened strictly before the panel began
        if open_mi < panel_start:
            n_left_trunc += 1

        # event month: earliest of first-below-floor and closure
        floor_mi = next((m for m in present if (a["bal"][m] is not None
                                                and a["bal"][m] < floor)), None) if floor > 0 else None
        disappeared = last_seen < panel_end
        close_mi = last_seen if disappeared else None
        cands = [m for m in (floor_mi, close_mi) if m is not None]
        event_mi = min(cands) if cands else None

        end_mi = event_mi if event_mi is not None else last_seen
        if event_mi is None:
            n_censored += 1
        elif event_mi == floor_mi:
            n_event_floor += 1
        else:
            n_event_close += 1

        last_bal = None
        for mi in range(first_seen, end_mi + 1):
            bal = a["bal"].get(mi)
            if bal is None:
                if not carry_gaps:
                    continue
                bal = last_bal
            if bal is None:
                continue
            last_bal = bal
            y, mo = mi // 12, mi % 12 + 1
            row = {
                "account_id": "|".join(str(x) for x in key),
                "month_int": mi, "year": y, "month": mo,
                "event": 1 if mi == event_mi else 0,
                "seasoning": mi - open_mi,
                "log_balance": math.log1p(max(bal, 0.0)),
                "balance_kda": bal,
                "cal_month": mo,
                "segment": a["segment"],
                "rubrique": a["rub"], "type_compte": a["type"],
            }
            if macro:
                mrow = macro["by_month"].get(mi, {})
                for c in macro_cols:
                    row[c] = mrow.get(c, "")
            rows.append(row)

    seg_counts = {}
    for r in rows:
        seg_counts[r["segment"]] = seg_counts.get(r["segment"], 0) + 1
    summary = {
        "n_accounts": len(acc), "n_person_months": len(rows),
        "panel_start": panel_start, "panel_end": panel_end,
        "events_floor": n_event_floor, "events_closure": n_event_close,
        "censored": n_censored,
        "event_rate": sum(r["event"] for r in rows) / max(1, len(rows)),
        "left_truncated_accounts": n_left_trunc,
        "bad_open_dates_clamped": n_bad_open,
        "person_months_by_segment": seg_counts,
        "macro_cols": macro_cols,
    }
    return rows, summary


def _load_macro(path):
    by_month, cols = {}, []
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.DictReader(f)
        cols = [c for c in rd.fieldnames if c != "ref_month"]
        for r in rd:
            y, m = r["ref_month"].split("-")
            by_month[month_int(int(y), int(m))] = {c: r[c] for c in cols}
    return {"by_month": by_month, "cols": cols}


def write_panel(rows, path):
    if not rows:
        raise RuntimeError("empty panel")
    cols = list(rows[0])
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    return path


def main():
    """CLI. On the work PC: `python panel_builder.py --dav-dir <DAV dumps> --out panel.csv`.
    With no --dav-dir it runs the synthetic validation demo (below)."""
    import argparse

    default_macro = os.path.join(HERE, "..", "data", "_out", "macro_panel_pit.csv")
    ap = argparse.ArgumentParser(description="Build the DAV account-month survival panel.")
    ap.add_argument("--dav-dir", default=None,
                    help="directory of monthly DAV_*.txt dumps (omit -> synth demo)")
    ap.add_argument("--out", default=os.path.join(HERE, "_out", "panel.csv"),
                    help="output panel.csv path")
    ap.add_argument("--macro", default=default_macro,
                    help="macro_panel_pit.csv for the PIT join (skipped if absent)")
    ap.add_argument("--floor", type=float, default=50.0,
                    help="balance-below-floor event threshold (KDA); 0 disables")
    ap.add_argument("--scope", default=None,
                    help="comma-separated Rubrique keywords to RESTRICT scope (default: keep all)")
    ap.add_argument("--no-ressources-filter", action="store_true",
                    help="do not drop Remplois (asset/loan) rows")
    args = ap.parse_args()

    if args.dav_dir is None:
        return _demo()

    macro = args.macro if (args.macro and os.path.exists(args.macro)) else None
    if args.macro and not macro:
        print(f"WARNING: macro file not found ({args.macro}); building panel WITHOUT "
              f"macro columns. Run data/run_all.py first for the PIT join.")
    scope = [s.strip().upper() for s in args.scope.split(",")] if args.scope else None
    rows, summ = build_panel(args.dav_dir, scope_keywords=scope, floor=args.floor,
                             require_ressources=not args.no_ressources_filter,
                             macro_pit=macro)
    out = write_panel(rows, args.out)
    print("PANEL SUMMARY:")
    for k, v in summ.items():
        print(f"  {k}: {v}")
    print(f"  -> {out}")


def _demo():
    import json

    synth = os.path.join(HERE, "_synth")
    macro = os.path.join(HERE, "..", "data", "_out", "macro_panel_pit.csv")
    macro = macro if os.path.exists(macro) else None

    # default: keep ALL deposit products, tagged by segment (nothing dropped)
    rows, summ = build_panel(synth, key_extra=("type_compte",),
                             scope_keywords=None, floor=50.0, macro_pit=macro)
    out = write_panel(rows, os.path.join(HERE, "_out", "panel.csv"))
    print("PANEL SUMMARY:")
    for k, v in summ.items():
        print(f"  {k}: {v}")
    print(f"  -> {out}")

    # ---- validate against synth ground truth ----
    with open(os.path.join(synth, "ground_truth.json")) as f:
        truth = json.load(f)
    # scope excludes 'Depot de garantie'; truth keys are CLI|code. Build event map.
    ev = {}
    for r in rows:
        if r["event"] == 1:
            ev[r["account_id"]] = f"{r['year']}-{r['month']:02d}"

    # ALL products kept now -> validate across every behavioral book + garantie.
    # The panel event = EARLIEST of {natural balance<floor, scripted closure}. With the
    # fast-eroding books (decouverts, hb) a surviving balance can legitimately cross the
    # floor BEFORE the scripted disappearance -> the honest check is "an event is detected
    # at or before the scripted closure month", not an exact-month equality.
    truth_attr = truth["attritions"]
    matched = sum(1 for k, v in truth_attr.items() if ev.get(k) is not None and ev[k] <= v)
    exact = sum(1 for k, v in truth_attr.items() if ev.get(k) == v)
    print(f"\nVALIDATION (all segments kept):")
    print(f"  disappearance events in truth: {len(truth_attr)}; detected at/<= scripted: "
          f"{matched} (exact-month: {exact})")
    truth_floor = truth["floor_events"]
    floor_caught = sum(1 for k, v in truth_floor.items() if k in ev and ev[k] <= v)
    print(f"  floor events in truth: {len(truth_floor)}; caught at/<= floor month: {floor_caught}")
    # no rows after event
    last_by_acc = {}
    for r in rows:
        last_by_acc.setdefault(r["account_id"], []).append((r["month_int"], r["event"]))
    bad = 0
    for k, seq in last_by_acc.items():
        seq.sort()
        evidx = [i for i, (_, e) in enumerate(seq) if e == 1]
        if evidx and evidx[0] != len(seq) - 1:
            bad += 1
    print(f"  accounts with rows AFTER their event (should be 0): {bad}")
    # garantie is KEPT and TAGGED (not dropped); demand-only is recoverable via segment
    seg = summ["person_months_by_segment"]
    print(f"  person-months by segment: {seg}")
    print(f"  garantie KEPT + tagged (not dropped): {'garantie' in seg}")
    n_vue = sum(v for s, v in seg.items() if s.startswith('vue'))
    print(f"  demand-only subset recoverable via segment: {n_vue} vue person-months")
    print(f"  seasoning>=0 for all rows: {all(r['seasoning'] >= 0 for r in rows)}")
    if macro:
        filled = sum(1 for r in rows if r.get('cpi_yoy', '') not in ('', None))
        print(f"  macro PIT join filled cpi_yoy on {filled}/{len(rows)} rows")


if __name__ == "__main__":
    main()
