"""Generate synthetic monthly DAV files in the REAL format (pure stdlib).

Reproduces the quirks of the production dumps so the reader / profiler / panel
builder can be validated locally before touching bank data:
  - 'Titre du rapport' preamble
  - header located by IDENTIF+NATIONAL, with COLUMN DRIFT across years
  - US dates MM/DD/YYYY, comma-thousands numbers
  - tab delimiter, cp1252 encoding
  - multiple Rubriques incl. 'Depot de garantie' (out-of-scope product)
  - KNOWN attritions (disappearance) and balance-floor events -> ground truth

Writes files DAV_MMYYYY.txt into an output dir + a ground_truth.json.
"""
from __future__ import annotations

import json
import os
import random

# Multi-product behavioral catalogue with DISTINCT run-off dynamics per book, so the
# balance-weighted book aggregate is a genuine mix (not five copies of one curve). Each
# tuple = (Rubrique label, CODE TYPE COMPTE, erosion/month, attrition prob, floor prob,
# balance lognormal mu, sigma). 'Depot de garantie' is kept but is contractual (excluded
# from the behavioral engine by model/products.py). Fields map to segment_of() keys.
SEG_SPECS = [
    # rubrique                          code  eros/mo  attr  floor  bmu  bsig
    ("Comptes a vue DINARS",            700,  0.0040, 0.20, 0.10, 8.0, 1.3),  # vue_dinars
    ("Comptes a vue DEVISES",           701,  0.0080, 0.28, 0.08, 7.5, 1.4),  # vue_devises
    ("Epargne / Livret",                703,  0.0015, 0.08, 0.03, 8.3, 1.1),  # epargne (sticky)
    ("Decouverts (compte debiteur)",    710,  0.0200, 0.40, 0.15, 6.5, 1.5),  # decouverts (fast)
    ("Engagement de financement HB",    900,  0.0120, 0.22, 0.05, 7.8, 1.2),  # hb_engagement
    ("Depot de garantie",               702,  0.0030, 0.15, 0.05, 7.0, 1.2),  # garantie (contractual)
]
RUBRIQUES = [s[0] for s in SEG_SPECS]                       # back-compat export
_SPEC_BY_IDX = {i: s for i, s in enumerate(SEG_SPECS)}

# two header layouts to simulate year-to-year drift
HEADER_A = ["Ressources-Remplois", "PERIODE", "Business Line", "Segmentation niveau 2",
            "CODE SEGMENTATION COMMERCIAL", "IDENTIF. NATIONAL -B35T",
            "CODE TYPE COMPTE -B35M", "Rubriques", "DATE OUVERTURE COMPTE -B35M",
            "CTRVL KDA", "SOLDE REPORTING FRENCH / DEV CPTE"]
HEADER_B = ["Ressources-Remplois", "PERIODE", "Business Line", "CODE TYPE COMPTE -B35M",
            "IDENTIF. NATIONAL -B35T", "Rubriques", "DATE OUVERTURE COMPTE -B35M",
            "CTRVL KDA", "SOLDE REPORTING FRENCH / DEV CPTE"]


def _fmt_thousands(x):
    return f"{x:,.2f}"


def _us_date(y, m, d):
    return f"{m}/{d}/{y}"


def generate(out_dir, start=(2015, 1), n_months=120, n_clients=300, seed=0, floor=50.0):
    rng = random.Random(seed)
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        if f.startswith("DAV_") or f == "ground_truth.json":
            os.remove(os.path.join(out_dir, f))

    sy, sm = start
    months = [(sy + (sm - 1 + k) // 12, (sm - 1 + k) % 12 + 1) for k in range(n_months)]
    start_mi = (sy * 12 + (sm - 1))

    # build accounts: (client, type_compte, rubrique), opening date, balance path,
    # attrition month (disappearance) and/or floor-event month
    accounts = []
    for c in range(n_clients):
        cid = f"CLI_{c+1:06d}"
        n_prod = rng.choices([1, 2, 3], weights=[0.6, 0.3, 0.1])[0]
        prods = rng.sample(range(len(SEG_SPECS)), min(n_prod, len(SEG_SPECS)))
        for idx in prods:
            rub, code, eros, attr_p, floor_p, bmu, bsig = _SPEC_BY_IDX[idx]
            # ~70% opened before the panel (left-truncated); ~30% open mid-panel
            if rng.random() < 0.70:
                oy = rng.randint(2005, start[0] - 1)
                om = rng.randint(1, 12)
            else:
                mid = rng.randint(0, n_months - 8)
                oy, om = months[mid][0], months[mid][1]
            od = rng.randint(1, 28)
            open_mi = oy * 12 + (om - 1)
            appear_idx = max(0, open_mi - start_mi)        # first month index present
            bal0 = rng.lognormvariate(bmu, bsig)           # KDA scale (per-segment)
            # per-segment attrition / floor rates -> distinct A(t) per book
            attr = (rng.randint(appear_idx + 4, n_months - 1)
                    if (rng.random() < attr_p and appear_idx + 4 < n_months - 1) else None)
            floor_evt = (rng.randint(appear_idx + 4, n_months - 1)
                         if (attr is None and rng.random() < floor_p
                             and appear_idx + 4 < n_months - 1) else None)
            accounts.append({"cid": cid, "code": code, "rub": rub, "eros": eros,
                             "open": (oy, om, od), "appear": appear_idx, "bal0": bal0,
                             "attr": attr, "floor": floor_evt})

    truth = {"n_months": n_months, "months": [f"{y}-{m:02d}" for y, m in months],
             "attritions": {}, "floor_events": {}}

    for k, (y, m) in enumerate(months):
        header = HEADER_A if y % 2 == 0 else HEADER_B     # drift by year parity
        path = os.path.join(out_dir, f"DAV_{m:02d}{y}.txt")
        with open(path, "w", encoding="cp1252", newline="") as fh:
            fh.write("Titre du rapport\n")
            fh.write("\t".join(header) + "\n")
            for a in accounts:
                if k < a["appear"]:
                    continue                              # not yet opened
                if a["attr"] is not None and k >= a["attr"]:
                    continue                              # disappeared (closure)
                bal = a["bal0"] * math_drift(rng, k, a["eros"])
                if a["floor"] is not None and k >= a["floor"]:
                    bal = rng.uniform(0.0, floor * 0.5)   # below floor
                acc_key = f"{a['cid']}|{a['code']}"
                if a["attr"] == k:
                    truth["attritions"][acc_key] = f"{y}-{m:02d}"
                if a["floor"] == k:
                    truth["floor_events"].setdefault(acc_key, f"{y}-{m:02d}")
                vals = {
                    "Ressources-Remplois": "Ressources",
                    "PERIODE": f"{m:02d}/{y}",
                    "Business Line": rng.choice(["Corporate", "OBL", "Retail"]),
                    "Segmentation niveau 2": "SEG2",
                    "CODE SEGMENTATION COMMERCIAL": "0.00",
                    "IDENTIF. NATIONAL -B35T": a["cid"],
                    "CODE TYPE COMPTE -B35M": str(a["code"]),
                    "Rubriques": a["rub"],
                    "DATE OUVERTURE COMPTE -B35M": _us_date(*a["open"]),
                    "CTRVL KDA": _fmt_thousands(bal),
                    "SOLDE REPORTING FRENCH / DEV CPTE": _fmt_thousands(bal * 1000),
                }
                fh.write("\t".join(vals[h] for h in header) + "\n")

    # record true attrition month index per account for panel validation
    for a in accounts:
        key = f"{a['cid']}|{a['code']}"
        if a["attr"] is not None:
            truth["attritions"][key] = truth["months"][a["attr"] - 1]   # last present month
    with open(os.path.join(out_dir, "ground_truth.json"), "w") as fh:
        json.dump(truth, fh, indent=2)
    return truth


def math_drift(rng, k, eros=0.004):
    # multiplicative balance path on a SURVIVING account: gradual draw-down at the
    # segment's erosion rate + noise. r(t)<1 so the book run-off B(t)=A(t).r(t) is a
    # proper decay curve. Distinct `eros` per segment -> distinct erosion legs, so the
    # balance-weighted book aggregate genuinely mixes the books. Real data carries its own.
    return max(0.05, 1.0 - eros * k + rng.gauss(0, 0.02))


if __name__ == "__main__":
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_synth")
    t = generate(out, n_clients=300, seed=1)
    files = sorted(f for f in os.listdir(out) if f.startswith("DAV_"))
    print(f"wrote {len(files)} files to {out}")
    print(f"  first={files[0]} last={files[-1]}")
    print(f"  attritions={len(t['attritions'])} floor_events={len(t['floor_events'])}")
    # peek at one file
    with open(os.path.join(out, files[0]), "r", encoding="cp1252") as fh:
        head = [next(fh) for _ in range(4)]
    print("  sample file head:")
    for ln in head:
        print("   ", ln.rstrip()[:100])
