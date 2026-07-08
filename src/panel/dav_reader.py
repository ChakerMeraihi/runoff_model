"""Robust reader for the anonymized monthly DAV dumps (pure stdlib).

Mirrors the anonymizer's proven detection logic, generalized to resolve ALL columns
by header name so year-to-year column drift is harmless (PLANv2 4). One file per
month, named DAV_MMYYYY.txt -> the FILENAME is the authoritative period (the PERIODE
column is inconsistent: '07/2019' vs '01/10/2016').

Quirks handled, all observed in the real data:
  - encodings UTF-16 / UTF-8(-sig) / cp1252 / latin-1 (BOM + null-byte sniff)
  - 'Titre du rapport' preamble line(s) before the header
  - delimiters tab / ';' / ','
  - accented / re-ordered / renamed headers -> keyword resolver
  - US dates MM/DD/YYYY ; numbers with comma thousands + dot decimal (3,598,368.51)
"""
from __future__ import annotations

import csv
import os
import re
import unicodedata

DATE_RE = re.compile(r"(0[1-9]|1[0-2])(\d{4})")            # MMYYYY in filename
ID_HEADER_KEYWORDS = ("IDENTIF", "NATIONAL")

# field -> required keywords (ALL must appear in the header cell, accent-insensitive)
COLUMN_SPECS = {
    "client_id":        ("IDENTIF", "NATIONAL"),
    "periode":          ("PERIODE",),
    "ressources":       ("RESSOURCE",),                    # 'Ressources-Remplois'
    "business_line":    ("BUSINESS",),
    "seg2":             ("SEGMENTATION", "NIVEAU"),
    "seg_commercial":   ("SEGMENTATION", "COMMERCIAL"),
    "type_compte":      ("TYPE", "COMPTE"),                # 'CODE TYPE COMPTE -B35M'
    "rubrique":         ("RUBRIQUE",),
    "date_ouverture":   ("OUVERTURE",),                    # 'DATE OUVERTURE COMPTE'
    "ctrvl_kda":        ("CTRVL",),                        # balance, thousands DZD
    "solde":            ("SOLDE",),                        # balance, account currency
    "currency":         ("DEVISE",),                       # if present
}


def strip_accents(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s)
                   if not unicodedata.combining(c))


def norm(s):
    return strip_accents((s or "").upper()).strip()


def read_lines(path):
    """Read a file, detecting UTF-16 / UTF-8 / cp1252 automatically."""
    with open(path, "rb") as f:
        raw = f.read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        enc = "utf-16"
    elif raw[:3] == b"\xef\xbb\xbf":
        enc = "utf-8-sig"
    elif b"\x00" in raw[:2000]:
        enc = "utf-16-le"
    else:
        enc = None
        for e in ("utf-8", "cp1252", "latin-1"):
            try:
                raw.decode(e)
                enc = e
                break
            except UnicodeDecodeError:
                continue
    return raw.decode(enc, errors="replace").splitlines(), enc


def detect_delimiter(line):
    for d in ("\t", ";", ","):
        if d in line:
            return d
    return "\t"


def find_header_line(lines):
    for i, line in enumerate(lines):
        up = norm(line)
        if all(k in up for k in ID_HEADER_KEYWORDS):
            return i
    return None


def resolve_columns(header_row):
    """Return (mapping field->index, missing[list]) by keyword match on header cells."""
    cells = [norm(c) for c in header_row]
    mapping = {}
    for field, kws in COLUMN_SPECS.items():
        for i, cell in enumerate(cells):
            if all(k in cell for k in kws) and i not in mapping.values():
                mapping[field] = i
                break
    missing = [f for f in COLUMN_SPECS if f not in mapping]
    return mapping, missing


def file_period(path):
    m = DATE_RE.search(os.path.basename(path))
    return (int(m.group(2)), int(m.group(1))) if m else None   # (year, month)


def month_int(year, month):
    return year * 12 + (month - 1)


def parse_number(s):
    if s is None:
        return None
    t = s.strip().replace("\xa0", "").replace(" ", "").replace(",", "")
    if t in ("", "-", "."):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def parse_us_date(s):
    """MM/DD/YYYY (or M/D/YYYY) -> (year, month). Returns None if unparseable."""
    if not s:
        return None
    parts = re.split(r"[/\-.]", s.strip())
    if len(parts) != 3:
        return None
    try:
        mm, dd, yy = int(parts[0]), int(parts[1]), int(parts[2])
    except ValueError:
        return None
    if yy < 100:
        yy += 2000 if yy < 50 else 1900
    if not (1 <= mm <= 12):
        # tolerate DD/MM/YYYY if day/month swapped
        if 1 <= dd <= 12:
            mm, dd = dd, mm
        else:
            return None
    return (yy, mm)


def read_dav_file(path):
    """Return (records, meta). records = list of dict with resolved fields + period.
    meta carries encoding/delimiter/header-line/missing-columns for profiling."""
    lines, enc = read_lines(path)
    meta = {"path": path, "encoding": enc, "n_lines": len(lines),
            "period": file_period(path), "ok": False}
    if not lines:
        meta["error"] = "empty"
        return [], meta
    h = find_header_line(lines)
    if h is None:
        meta["error"] = "no header (IDENTIF+NATIONAL) found"
        return [], meta
    delim = detect_delimiter(lines[h])
    rows = list(csv.reader(lines[h:], delimiter=delim))
    if not rows:
        meta["error"] = "no rows after header"
        return [], meta
    mapping, missing = resolve_columns(rows[0])
    meta.update({"delimiter": delim, "header_line": h, "columns": mapping,
                 "missing": missing, "raw_header": rows[0]})
    if "client_id" not in mapping:
        meta["error"] = "client_id column not resolved"
        return [], meta

    per = meta["period"]
    pm = month_int(*per) if per else None
    out = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue

        def get(field):
            i = mapping.get(field)
            return row[i].strip() if (i is not None and i < len(row)) else None

        rec = {
            "client_id": get("client_id"),
            "year": per[0] if per else None,
            "month": per[1] if per else None,
            "month_int": pm,
            "ressources": get("ressources"),
            "business_line": get("business_line"),
            "type_compte": get("type_compte"),
            "rubrique": get("rubrique"),
            "date_ouverture": get("date_ouverture"),
            "ctrvl_kda": parse_number(get("ctrvl_kda")),
            "solde": parse_number(get("solde")),
            "currency": get("currency"),
        }
        if rec["client_id"]:
            out.append(rec)
    meta["ok"] = True
    meta["n_records"] = len(out)
    return out, meta


if __name__ == "__main__":
    # self-test on parsing helpers (no files needed)
    assert parse_number("3,598,368.51") == 3598368.51
    assert parse_number("1613936") == 1613936.0
    assert parse_number("  ") is None
    assert parse_us_date("12/23/2015") == (2015, 12)
    assert parse_us_date("2/25/2002") == (2002, 2)
    assert parse_us_date("01/10/2016") == (2016, 1)        # MM/DD/YYYY
    assert file_period("DAV_072019.txt") == (2019, 7)
    assert month_int(2019, 7) - month_int(2019, 1) == 6
    hdr = ["Ressources-Remplois", "PERIODE", "Business Line",
           "IDENTIF. NATIONAL -B35T", "CODE TYPE COMPTE -B35M",
           "DATE OUVERTURE COMPTE -B35M", "CTRVL KDA"]
    mp, miss = resolve_columns(hdr)
    assert mp["client_id"] == 3 and mp["type_compte"] == 4 and mp["ctrvl_kda"] == 6, mp
    assert mp["date_ouverture"] == 5 and mp["periode"] == 1, mp
    print("dav_reader self-test: PASS")
    print("  resolved columns:", mp)
    print("  parse_number('3,598,368.51') =", parse_number("3,598,368.51"))
    print("  parse_us_date('12/23/2015')  =", parse_us_date("12/23/2015"))
