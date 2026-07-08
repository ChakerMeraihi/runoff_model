"""Generate a synthetic EFM .xlsx tree in the REAL folder/sheet layout (pure stdlib),
so the EFM -> survival-panel bridge can be validated locally before touching bank data.

Reproduces the consistent monthly EFM workbook:
  <out>/Analyses_commentaires_Resultats <YYYY>/<MM>-<Mois>/01-PNB/06-EFM/01-Encours/
        EFM <MM> <YYYY>.xlsx
  -> a 'Détails Ressources' sheet (deposit / liability side) whose columns map to the
     efm_collect WANT dict (Business Line, IDENTIF. NATIONAL, Rubriques, CODE TYPE
     COMPTE, DATE OUVERTURE, CTRVL KDA). Period resolves from the folder path (year from
     'Analyses...Resultats <YYYY>', month from the '<NN>-<Mois>' name), exactly as real
     data. Writes ground_truth.json alongside for panel validation.

Uses model/xlsx_writer.py -- the same pure-stdlib writer efm_collect reads back -- so no
numpy/pandas/openpyxl are needed anywhere. Runs as its own self-test (`python
synth_efm_files.py`): it generates the tree, builds the panel through the bridge, and
checks the detected events against the known ground truth.
"""
from __future__ import annotations

import json
import os
import random
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "model"))
from xlsx_writer import Workbook                                   # noqa: E402

# (rubrique label, CODE TYPE COMPTE, erosion/mo, attrition p, floor p, bal mu, bal sigma)
# distinct dynamics per book so the aggregate is a genuine mix; labels feed segment_of().
SEG_SPECS = [
    ("Comptes a vue DINARS",          700, 0.0040, 0.20, 0.10, 8.0, 1.3),
    ("Comptes a vue DEVISES",         701, 0.0080, 0.28, 0.08, 7.5, 1.4),
    ("Epargne / Livret",              703, 0.0015, 0.08, 0.03, 8.3, 1.1),
    ("Decouverts (compte debiteur)",  710, 0.0200, 0.40, 0.15, 6.5, 1.5),
    ("Engagement de financement HB",  900, 0.0120, 0.22, 0.05, 7.8, 1.2),
    ("Depot de garantie",             702, 0.0030, 0.15, 0.05, 7.0, 1.2),
]
MONTHS_FR = {1: "Janvier", 2: "Fevrier", 3: "Mars", 4: "Avril", 5: "Mai", 6: "Juin",
             7: "Juillet", 8: "Aout", 9: "Septembre", 10: "Octobre", 11: "Novembre",
             12: "Decembre"}
# header labels map to efm_collect.WANT after normalization (accents/spaces/case stripped)
HEADER = ["PERIODE", "Business Line", "IDENTIF. NATIONAL", "Rubriques",
          "CODE TYPE COMPTE", "DATE OUVERTURE", "CTRVL KDA"]
SHEET = "Détails Ressources"


def _drift(rng, k, eros):
    return max(0.05, 1.0 - eros * k + rng.gauss(0, 0.02))


def generate(out_dir, start=(2022, 1), n_months=36, n_clients=80, seed=0, floor=50.0):
    rng = random.Random(seed)
    if os.path.isdir(out_dir):
        shutil.rmtree(out_dir)
    os.makedirs(out_dir, exist_ok=True)

    sy, sm = start
    months = [(sy + (sm - 1 + k) // 12, (sm - 1 + k) % 12 + 1) for k in range(n_months)]
    start_mi = sy * 12 + (sm - 1)

    accounts = []
    for c in range(n_clients):
        cid = f"CLI_{c + 1:06d}"
        for idx in rng.sample(range(len(SEG_SPECS)), rng.choice([1, 1, 2])):
            rub, code, eros, attr_p, floor_p, bmu, bsig = SEG_SPECS[idx]
            if rng.random() < 0.70:                         # ~70% opened before the panel
                oy, om = rng.randint(2015, sy - 1), rng.randint(1, 12)
            else:
                oy, om = months[rng.randint(0, n_months - 8)]
            appear = max(0, (oy * 12 + (om - 1)) - start_mi)
            attr = (rng.randint(appear + 4, n_months - 1)
                    if (rng.random() < attr_p and appear + 4 < n_months - 1) else None)
            fl = (rng.randint(appear + 4, n_months - 1)
                  if (attr is None and rng.random() < floor_p and appear + 4 < n_months - 1)
                  else None)
            accounts.append({"cid": cid, "code": code, "rub": rub, "eros": eros,
                             "open": (oy, om, rng.randint(1, 28)), "appear": appear,
                             "bal0": rng.lognormvariate(bmu, bsig), "attr": attr, "floor": fl})

    truth = {"months": [f"{y}-{m:02d}" for y, m in months], "attritions": {}, "floor_events": {}}
    for k, (y, m) in enumerate(months):
        rows = []
        for a in accounts:
            if k < a["appear"]:
                continue                                    # not yet opened
            if a["attr"] is not None and k >= a["attr"]:
                continue                                    # disappeared
            bal = a["bal0"] * _drift(rng, k, a["eros"])
            if a["floor"] is not None and k >= a["floor"]:
                bal = rng.uniform(0.0, floor * 0.5)         # below floor
            key = f"{a['cid']}|{a['code']}"
            if a["floor"] == k:
                truth["floor_events"].setdefault(key, f"{y}-{m:02d}")
            oy, om, od = a["open"]
            rows.append([f"{m:02d}/{y}", rng.choice(["Corporate", "OBL", "Retail"]),
                         a["cid"], a["rub"], str(a["code"]),
                         f"{om}/{od}/{oy}", round(bal, 2)])
        d = os.path.join(out_dir, f"Analyses_commentaires_Resultats {y}",
                         f"{m:02d}-{MONTHS_FR[m]}", "01-PNB", "06-EFM", "01-Encours")
        os.makedirs(d, exist_ok=True)
        wb = Workbook()
        wb.add_sheet(SHEET, rows, header=HEADER)
        wb.save(os.path.join(d, f"EFM {m:02d} {y}.xlsx"))

    for a in accounts:                                      # true attrition = last present month
        if a["attr"] is not None:
            truth["attritions"][f"{a['cid']}|{a['code']}"] = truth["months"][a["attr"] - 1]
    with open(os.path.join(out_dir, "ground_truth.json"), "w") as f:
        json.dump(truth, f, indent=2)
    return truth


def _selftest():
    import panel_builder
    out = os.path.join(HERE, "_synth_efm")
    truth = generate(out, seed=1)
    n_wb = sum(1 for _, _, fs in os.walk(out) for x in fs if x.endswith(".xlsx"))
    assert panel_builder._detect_source(out) == "efm", "source auto-detect should be EFM"
    rows, summ = panel_builder.build_panel(out, floor=50.0)      # source='auto' -> efm

    ev = {r["account_id"]: f"{r['year']}-{r['month']:02d}" for r in rows if r["event"] == 1}
    attr = truth["attritions"]
    matched = sum(1 for k, v in attr.items() if ev.get(k) is not None and ev[k] <= v)
    fl = truth["floor_events"]
    caught = sum(1 for k, v in fl.items() if k in ev and ev[k] <= v)
    # no rows after an event
    seqs = {}
    for r in rows:
        seqs.setdefault(r["account_id"], []).append((r["month_int"], r["event"]))
    bad = 0
    for k, s in seqs.items():
        s.sort()
        ei = [i for i, (_, e) in enumerate(s) if e == 1]
        if ei and ei[0] != len(s) - 1:
            bad += 1
    seg = summ["person_months_by_segment"]

    print(f"synth_efm: {n_wb} workbooks, {summ['n_accounts']} accounts, "
          f"{summ['n_person_months']} person-months, panel {summ['panel_start']}..{summ['panel_end']}")
    print(f"  attritions in truth: {len(attr)}; detected at/<= scripted: {matched}")
    print(f"  floor events in truth: {len(fl)}; caught at/<= floor month: {caught}")
    print(f"  rows AFTER event (want 0): {bad}")
    print(f"  person-months by segment: {seg}")
    assert n_wb == 36, n_wb
    assert summ["n_person_months"] > 0
    assert matched >= 0.9 * len(attr), (matched, len(attr))
    assert caught >= 0.9 * len(fl), (caught, len(fl))
    assert bad == 0, f"{bad} accounts have rows after their event"
    assert len(seg) >= 4, seg
    shutil.rmtree(out, ignore_errors=True)
    print("synth_efm self-test: PASS")


if __name__ == "__main__":
    _selftest()
