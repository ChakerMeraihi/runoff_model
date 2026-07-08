"""products.py -- the behavioral-book catalogue for the multi-product run-off (stdlib).

Scope decision (locked 2026-06-30, refined 2026-07-01): the EFM 'Encours' is a STOCK
snapshot -- it carries the balance (CTRVL KDA) but NO date d'echeance. So we can only
build a CONTRACTUAL echeancier for DAT/BDC/credits from a separate deal/Matisse extract,
which we do NOT have. Therefore OUR statistical run-off = the BEHAVIORAL books only, where
balance history is the right input and no maturity is needed. Contractual books are listed
here purely so the panel can TAG and EXCLUDE them (never behaviorally modeled).

Each behavioral segment gets its OWN hazard A(t) + erosion r(t) -> B(t)=A(t)*r(t); the
whole-book run-off is the balance-weighted sum of the per-segment B(t). The segment KEY is
what panel_builder.segment_of() emits from the Rubrique label; refine both together once a
real EFM is profiled.
"""
from __future__ import annotations

# key -> spec. behavioral=True -> modeled (hazard+erosion). behavioral=False -> kept in the
# panel and tagged, but NEVER fed to the run-off engine (contractual / out-of-scope).
SEGMENTS = {
    "vue_dinars":    {"display": "Comptes a vue DINARS",      "behavioral": True,
                      "note": "primary DAV; non-maturing demand deposit (NMD)"},
    "vue_devises":   {"display": "Comptes a vue DEVISES",     "behavioral": True,
                      "note": "FX demand deposits; more volatile / FX-sensitive"},
    "epargne":       {"display": "Epargne",                   "behavioral": True,
                      "note": "savings; sticky, slow erosion, low attrition"},
    "decouverts":    {"display": "Decouverts",                "behavioral": True,
                      "note": "overdrafts (Remplois side); fast run-off / high churn"},
    "hb_engagement": {"display": "Engagement de financement (HB)", "behavioral": True,
                      "note": "off-balance commitment; drawdown / CCF behaviour"},
    # ---- contractual / out-of-scope: tagged + kept, NOT behaviorally modeled ----
    "garantie":      {"display": "Depot de garantie",         "behavioral": False,
                      "note": "runs off on contract lifecycle, not depositor behaviour"},
    "vue_bu":        {"display": "Comptes a vue BU",          "behavioral": True,
                      "note": "business-unit demand accounts (legacy label -> vue)"},
    "vue_other":     {"display": "Comptes a vue (autres)",    "behavioral": True,
                      "note": "other demand accounts"},
    "other":         {"display": "Autres",                    "behavioral": False,
                      "note": "unclassified; excluded until mapped"},
}


def behavioral_keys():
    """Segment keys we behaviorally model, in a stable display order."""
    return [k for k, v in SEGMENTS.items() if v["behavioral"]]


def is_behavioral(key):
    spec = SEGMENTS.get(key)
    return bool(spec and spec["behavioral"])


def display_name(key):
    spec = SEGMENTS.get(key)
    return spec["display"] if spec else key


if __name__ == "__main__":
    print("behavioral books (modeled):")
    for k in behavioral_keys():
        print(f"  {k:<14} {SEGMENTS[k]['display']:<32} -- {SEGMENTS[k]['note']}")
    print("excluded (tagged, not modeled):")
    for k, v in SEGMENTS.items():
        if not v["behavioral"]:
            print(f"  {k:<14} {v['display']:<32} -- {v['note']}")
