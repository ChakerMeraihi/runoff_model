#!/usr/bin/env python3
"""efm_collect.py -- walk the Controle-de-gestion tree, read the 'base' sheet of each
monthly EFM .xlsx, extract the key columns, and write ONE csv per month named
MM_YYYY.csv (+ an optional stacked panel). PURE STDLIB only (zipfile + xml.etree) --
no pandas / openpyxl, so it runs on the locked-down bank PC. .xlsx is just a zip of
XML, which is why stdlib can read it.

Folder layout it expects (period is taken from the FOLDER, never an internal column):
  <ROOT>\\Analyses_commentaires_Resultats <YYYY>\\<MM-Mois>\\01-PNB\\06-EFM\\...\\EFM*.xlsx

conUSAGE
  1) profile first (see real sheet names + the header row, so you can fix WANT below):
       py -3 panel/efm_collect.py profile "<ROOT>"
  2) collect all months to csv:
       py -3 panel/efm_collect.py collect "<ROOT>" --out panel/_out/efm --sheet base
     add  --panel  to also write one stacked efm_panel.csv across all months.

After profiling, EDIT the WANT dict to match the real header labels.
"""
import os, re, csv, sys, zipfile, unicodedata
import xml.etree.ElementTree as ET

NS   = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
NS_R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# ---------------------------------------------------------------- column wishlist
# normalized header  ->  output column name. Normalization strips accents/spaces/case
# and non-alphanumerics, so "CTRVL KDA" -> "ctrvlkda", "Date d'ouverture" -> "datedouverture".
# EDIT these keys to match what `profile` prints for your files.
WANT = {
    "periode":            "periode",          # internal period col (we still prefer filename)
    "businessline":       "business_line",
    "bl":                 "business_line",
    "identifnational":    "identif_national", # CLIENT id (panel key when no account id)
    "identifcompte":      "identif_compte",   # account id, if present
    "rubrique":           "rubrique",         # may appear twice (niveau 1 & 2) -> _2 suffix
    "rubriques":          "rubrique",
    "codetypecompte":     "code_type_compte",
    "ordinalducompte":    "ordinal_compte",   # sometimes instead of code type compte
    "ordinaldecompte":    "ordinal_compte",
    "segmentationniveau2":"segmentation_n2",  # Remplois extra segmentation
    "segmentation2":      "segmentation_n2",
    "devise":             "devise",
    "ctrvlkda":           "solde_kda",        # <-- the BALANCE (encours), thousands DZD
    "ctrvl":              "solde_kda",
    "soldekda":           "solde_kda",
    "encourskda":         "solde_kda",
    "dateouverture":      "date_ouverture",
    "datedouverture":     "date_ouverture",
    "dateecheance":       "date_echeance",
}

# The three section sheets in the consistent EFM workbook (one per balance-sheet side).
# For the DEPOSIT run-off you want 'Details Ressources'.
SECTION_SHEETS = {
    "ressources": "Détails Ressources",
    "remplois":   "Détail Remplois",
    "hb":         "Détails Hors Bilan",
}

def _norm(s):
    if s is None: return ""
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]", "", s.lower())

# ---------------------------------------------------------------- minimal xlsx reader
def _col_to_idx(ref):                       # "AB12" -> 27 (0-based column index)
    letters = re.match(r"([A-Z]+)", ref or "A").group(1)
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1

def detect_format(path):
    """Look at magic bytes, not the extension: 'xlsx' (zip 'PK'), 'xls' (OLE2 D0CF11E0),
    or 'unknown'. .xls cannot be read by pure stdlib -> convert it first (see ps1 helper)."""
    with open(path, "rb") as f:
        sig = f.read(8)
    if sig[:2] == b"PK":                                    # zip -> xlsx
        return "xlsx"
    if sig[:4] == b"\xD0\xCF\x11\xE0":                      # OLE2 compound file -> old .xls
        return "xls"
    return "unknown"

def period_from_filename(path):
    """'EFM 05 2026.xls' -> ('2026-05-01','05_2026'); here the number IS the month. None if no match."""
    base = os.path.basename(path)
    m = re.search(r"efm\D*?(\d{1,2})\D+(20\d{2})", _norm(base.replace(".", " ")))
    if not m: return None, None
    mm = f"{int(m.group(1)):02d}"; yr = m.group(2)
    if not (1 <= int(mm) <= 12): return None, None
    return f"{yr}-{mm}-01", f"{mm}_{yr}"

def list_sheets(path):
    with zipfile.ZipFile(path) as z:
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        return [sh.get("name") for sh in wb.find(f"{NS}sheets")]

def read_xlsx_sheet(path, sheet_name):
    """Return rows (list of lists of cell strings) for the named sheet, pure stdlib (.xlsx only)."""
    fmt = detect_format(path)
    if fmt == "xls":
        raise ValueError("OLD .xls binary format -- pure stdlib cannot read it. "
                         "Convert to .xlsx/.csv first (run efm_convert_xls.ps1 on the bank PC, Excel is present).")
    if fmt != "xlsx":
        raise ValueError(f"not an .xlsx (magic={fmt})")
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        # shared strings
        shared = []
        if "xl/sharedStrings.xml" in names:
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            for si in root.findall(f"{NS}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))
        # workbook: sheet name -> r:id
        wb = ET.fromstring(z.read("xl/workbook.xml"))
        name_to_rid = {sh.get("name"): sh.get(f"{NS_R}id") for sh in wb.find(f"{NS}sheets")}
        # rels: r:id -> worksheet target
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        rid_to_target = {r.get("Id"): r.get("Target") for r in rels}
        # resolve sheet by normalized name
        target = None
        for nm, rid in name_to_rid.items():
            if _norm(nm) == _norm(sheet_name):
                t = rid_to_target.get(rid, "")
                target = t if t.startswith("xl/") else "xl/" + t.lstrip("/")
                break
        if target is None:
            raise KeyError(f"sheet '{sheet_name}' not found; have {list(name_to_rid)}")
        ws = ET.fromstring(z.read(target))
        rows = []
        for row in ws.iter(f"{NS}row"):
            cells = []
            for c in row.findall(f"{NS}c"):
                cidx = _col_to_idx(c.get("r", "A1"))
                t = c.get("t"); v = c.find(f"{NS}v")
                if t == "s":                                   # shared string
                    val = shared[int(v.text)] if v is not None and v.text is not None else ""
                elif t == "inlineStr":                         # inline string
                    isn = c.find(f"{NS}is")
                    val = "".join(x.text or "" for x in isn.iter(f"{NS}t")) if isn is not None else ""
                else:                                          # number / formula-str / bool
                    val = v.text if v is not None else ""
                while len(cells) < cidx:                        # pad skipped (empty) cells
                    cells.append("")
                cells.append(val if val is not None else "")
            rows.append(cells)
        return rows

# French month NAME -> number. The month is read from the NAME ('Mai'), NOT the leading
# digits: in '05-Mai' the '05' is just document ordering, not the month. By bank convention
# the period is the FIRST DAY of that month (YYYY-MM-01).
MONTHS = {"janvier":"01","fevrier":"02","mars":"03","avril":"04","mai":"05","juin":"06",
          "juillet":"07","aout":"08","septembre":"09","octobre":"10","novembre":"11","decembre":"12"}
ABBR   = {"janv":"01","fev":"02","fevr":"02","avr":"04","juil":"07","juill":"07",
          "sept":"09","oct":"10","nov":"11","dec":"12"}

def month_from_part(part):
    """'05-Mai'/'Mai' -> '05' by NAME (leading order number ignored); topic folders
    like '06-EFM','01-PNB' -> None. Requires a real French month name."""
    m = re.match(r"^\s*\d{1,2}\s*[-_. ]\s*(.+)$", part)        # strip optional 'NN-' ordering
    name = m.group(1) if m else part
    n = _norm(name)
    for mon, num in MONTHS.items():
        if n.startswith(mon): return num                       # mai, mars, septembre, ...
    return ABBR.get(n)                                          # exact abbreviation (sept, fev, ...)

def period_from_path(path):
    """year from 'Analyses...Resultats <YYYY>' folder; month from the month NAME folder.
    Returns (period 'YYYY-MM-01', label 'MM_YYYY')."""
    yr = mm = None
    for part in os.path.normpath(path).split(os.sep):
        if "esultat" in _norm(part):
            m = re.search(r"(20\d{2})", part)
            if m: yr = m.group(1)
        got = month_from_part(part)
        if got: mm = got
    if not (yr and mm):
        return None, None
    return f"{yr}-{mm}-01", f"{mm}_{yr}"

def find_efm_files(root):
    """yield EFM workbook paths (.xlsx/.xlsm/.xls) under a '06-EFM' folder (any depth below)."""
    for dirpath, _dirs, files in os.walk(root):
        if "06efm" not in _norm(dirpath):
            continue
        for fn in files:
            low = fn.lower()
            if low.endswith((".xlsx", ".xlsm", ".xls")) and "efm" in _norm(fn) and not fn.startswith("~$"):
                yield os.path.join(dirpath, fn)

def resolve_period(path):
    """prefer the filename ('EFM 05 2026' -> month=05); fall back to the month-NAME folder."""
    p, lbl = period_from_filename(path)
    return (p, lbl) if p else period_from_path(path)

def _build_idx_map(header):
    """map normalized header -> output cols; duplicate wanted names get _2,_3 (e.g. two 'Rubriques')."""
    idx_map = {}                                                # output name -> source col idx
    for j, h in enumerate(header):
        k = _norm(h)
        if k in WANT:
            name = WANT[k]
            if name in idx_map:                                  # duplicate (rubrique niveau 1 & 2)
                n = 2
                while f"{name}_{n}" in idx_map: n += 1
                name = f"{name}_{n}"
            idx_map[name] = j
    return idx_map

# ---------------------------------------------------------------- modes
def cmd_profile(root, sheet):
    files = list(find_efm_files(root))
    xlsx = [p for p in files if detect_format(p) == "xlsx"]
    xls  = [p for p in files if detect_format(p) == "xls"]
    print(f"found {len(files)} EFM workbooks under {root}  ({len(xlsx)} readable .xlsx, {len(xls)} old .xls)")
    if xls:
        print(f"  !! {len(xls)} are OLD .xls (binary) -> NOT readable by stdlib. Run efm_convert_xls.ps1 first.")
    if not xlsx:
        if xls: print("  -> convert the .xls files, then re-run."); return
        print("  (none -- check ROOT / that files sit under a '06-EFM' folder)"); return
    p = xlsx[0]
    print("first readable file:", p)
    period, label = resolve_period(p); print("period parsed:", period, "-> file", f"{label}.csv")
    print("sheets:", list_sheets(p), "  (deposit run-off uses 'Détails Ressources')")
    try:
        rows = read_xlsx_sheet(p, sheet)
    except (KeyError, ValueError) as e:
        print("!!", e); return
    if not rows:
        print("  (sheet empty)"); return
    header = rows[0]
    print(f"\n'{sheet}' header ({len(header)} cols) -> normalized key -> WANT match:")
    for j, h in enumerate(header):
        key = _norm(h); print(f"  col[{j:2d}] {repr(h)[:38]:40s} norm={key:22s} -> {WANT.get(key,'')}")
    print(f"\n{len(rows)-1} data rows. Edit WANT to map any missing balance/key columns.")

def cmd_collect(root, out_dir, sheet, stacked=False):
    os.makedirs(out_dir, exist_ok=True)
    files = sorted(find_efm_files(root))
    print(f"collecting from {len(files)} EFM workbooks -> {out_dir}  (sheet='{sheet}')")
    panel_rows = []; panel_cols = None; n_ok = 0; n_xls = 0
    for p in files:
        period, label = resolve_period(p)
        if not period:
            print("SKIP (no period):", p); continue
        try:
            rows = read_xlsx_sheet(p, sheet)
        except ValueError as e:
            if "xls" in str(e): n_xls += 1
            print("ERR ", os.path.basename(p), "::", e); continue
        except Exception as e:
            print("ERR ", os.path.basename(p), "::", e); continue
        if not rows:
            print("SKIP (empty):", p); continue
        idx_map = _build_idx_map(rows[0])
        if not idx_map:
            print("WARN no wanted cols in", os.path.basename(p), "-- header:", rows[0][:8]); continue
        out_cols = list(idx_map.keys())
        out_path = os.path.join(out_dir, f"{label}.csv")
        with open(out_path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(["period"] + out_cols)
            for r in rows[1:]:
                if not any((c or "").strip() for c in r): continue
                rec = [period] + [(r[idx_map[c]] if idx_map[c] < len(r) else "") for c in out_cols]
                w.writerow(rec)
                if stacked: panel_rows.append(rec)
        if stacked and panel_cols is None: panel_cols = ["period"] + out_cols
        n_ok += 1
        print(f"OK {label}: {len(rows)-1:6d} rows, cols={out_cols} -> {os.path.basename(out_path)}")
    if stacked and panel_rows:
        pp = os.path.join(out_dir, "efm_panel.csv")
        with open(pp, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f); w.writerow(panel_cols); w.writerows(panel_rows)
        print(f"stacked panel -> {pp}  ({len(panel_rows)} rows)")
    print(f"done: {n_ok}/{len(files)} months written" + (f"; {n_xls} were old .xls (convert first)" if n_xls else ""))

# ---------------------------------------------------------------- cli
if __name__ == "__main__":
    a = sys.argv[1:]
    if not a or a[0] not in ("profile", "collect"):
        print(__doc__); sys.exit(0)
    mode = a[0]; root = a[1] if len(a) > 1 else "."
    def opt(name, default=None):
        return a[a.index(name)+1] if name in a else default
    # default sheet = the deposit (Ressources) section of the consistent EFM workbook
    sheet = opt("--sheet", SECTION_SHEETS.get(_norm(opt("--section", "")), "Détails Ressources"))
    if mode == "profile":
        cmd_profile(root, sheet)
    else:
        cmd_collect(root, opt("--out", "panel/_out/efm"), sheet, stacked=("--panel" in a))
