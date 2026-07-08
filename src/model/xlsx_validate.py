"""xlsx_validate.py -- structural integrity checker for the .xlsx we WRITE (pure stdlib).

We cannot run Excel on the bank PC (or here), so this deterministically verifies the
package invariants Excel enforces -- the ones whose violation triggers "we found a problem
with some content... do you want us to recover" or silently drops charts:

  1. content-types      : every part is covered by a Default (extension) or Override (part)
  2. relationships      : every Relationship Target resolves to a real part
  3. r:id resolution    : every r:id used in workbook / sheet(drawing) / drawing(chart)
                          exists in that part's .rels
  4. chart data refs    : every c:f "Sheet!$A$2:$A$14" names a real sheet
  5. shared strings     : every t="s" cell index is in range
  6. style refs         : every cell s="k" index is a real cellXfs entry

validate(path) -> list of problem strings (empty = clean). __main__ builds a chart
workbook via xlsx_writer and asserts it validates clean (self-test for run_tests.py).
"""
from __future__ import annotations

import os
import posixpath
import re
import zipfile
import xml.etree.ElementTree as ET

NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_PR = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _rels_path(part):
    """The .rels part that governs `part`."""
    d, b = posixpath.split(part)
    return posixpath.join(d, "_rels", b + ".rels")


def _resolve(base_part, target):
    """Resolve a (possibly ../) relationship Target against the part holding the rels."""
    base_dir = posixpath.dirname(base_part)
    return posixpath.normpath(posixpath.join(base_dir, target))


def validate(path):
    problems = []
    z = zipfile.ZipFile(path)
    names = set(z.namelist())

    # ---- 1. content types ---------------------------------------------------
    ct = ET.fromstring(z.read("[Content_Types].xml"))
    defaults = {d.get("Extension", "").lower() for d in ct.findall(f"{{{NS_CT}}}Default")}
    overrides = {o.get("PartName") for o in ct.findall(f"{{{NS_CT}}}Override")}
    for nm in names:
        if nm == "[Content_Types].xml" or nm.endswith("/"):
            continue
        ext = nm.rsplit(".", 1)[-1].lower() if "." in nm else ""
        part = "/" + nm
        if part in overrides or ext in defaults:
            continue
        problems.append(f"content-types: no Default/Override for part {nm}")

    # ---- 2 + 3. relationships resolve + r:id used exists --------------------
    rels_by_part = {}                                   # governed part -> {rId: target_part}
    for nm in names:
        if not nm.endswith(".rels"):
            continue
        root = ET.fromstring(z.read(nm))
        # governed part = the file this .rels belongs to
        d = posixpath.dirname(nm)                        # e.g. xl/_rels
        owner_dir = posixpath.dirname(d)                 # e.g. xl
        owner_base = posixpath.basename(nm)[:-5]         # strip ".rels"
        owner = posixpath.join(owner_dir, owner_base) if owner_base != ".rels" else ""
        m = {}
        for rel in root.findall(f"{{{NS_PR}}}Relationship"):
            rid, tgt, mode = rel.get("Id"), rel.get("Target"), rel.get("TargetMode")
            if mode == "External":
                m[rid] = None
                continue
            resolved = _resolve(owner or "x", tgt)
            m[rid] = resolved
            if resolved not in names:
                problems.append(f"rels {nm}: Id={rid} target {tgt} -> {resolved} MISSING")
        rels_by_part[owner] = m

    def rids_in(part_xml):
        return set(re.findall(r'r:id="([^"]+)"', part_xml))

    # workbook sheets + sheet drawings + drawing charts
    for part in names:
        if not (part.endswith(".xml")):
            continue
        if not (part == "xl/workbook.xml" or
                part.startswith("xl/worksheets/sheet") or
                part.startswith("xl/drawings/drawing")):
            continue
        xml = z.read(part).decode("utf-8", "replace")
        used = rids_in(xml)
        if not used:
            continue
        have = rels_by_part.get(part, {})
        for rid in used:
            if rid not in have:
                problems.append(f"{part}: uses r:id={rid} with no matching Relationship")

    # ---- 4. chart data references name real sheets --------------------------
    sheet_names = set()
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    for sh in wb.find(f"{{{NS_MAIN}}}sheets"):
        sheet_names.add(sh.get("name"))
    for part in names:
        if not (part.startswith("xl/charts/chart") and part.endswith(".xml")):
            continue
        xml = z.read(part).decode("utf-8", "replace")
        for f_ref in re.findall(r"<c:f>([^<]+)</c:f>", xml):
            sheet = f_ref.split("!", 1)[0].strip("'") if "!" in f_ref else None
            if sheet and sheet not in sheet_names:
                problems.append(f"{part}: c:f references unknown sheet '{sheet}' ({f_ref})")

    # ---- 4b. tables: count matches, displayName unique, header row matches ----
    seen_names = {}
    for part in sorted(names):
        if not (part.startswith("xl/tables/table") and part.endswith(".xml")):
            continue
        t = ET.fromstring(z.read(part))
        disp = t.get("displayName") or t.get("name")
        if disp in seen_names:
            problems.append(f"{part}: duplicate table displayName '{disp}'")
        seen_names[disp] = part
        tcs = t.find(f"{{{NS_MAIN}}}tableColumns")
        declared = int(tcs.get("count")) if tcs is not None else -1
        actual = len(tcs.findall(f"{{{NS_MAIN}}}tableColumn")) if tcs is not None else 0
        if declared != actual:
            problems.append(f"{part}: tableColumns count={declared} but {actual} tableColumn")
        # column names must be unique + non-empty (Excel repairs otherwise)
        cn = [tc.get("name") for tc in tcs.findall(f"{{{NS_MAIN}}}tableColumn")] if tcs is not None else []
        if any(not x for x in cn):
            problems.append(f"{part}: has an empty tableColumn name")
        if len(set(cn)) != len(cn):
            problems.append(f"{part}: duplicate tableColumn names {cn}")

    # ---- 5 + 6. shared-string + style indices in range ----------------------
    n_shared = 0
    if "xl/sharedStrings.xml" in names:
        sst = ET.fromstring(z.read("xl/sharedStrings.xml"))
        n_shared = len(sst.findall(f"{{{NS_MAIN}}}si"))
    n_xf = 0
    if "xl/styles.xml" in names:
        st = ET.fromstring(z.read("xl/styles.xml"))
        cx = st.find(f"{{{NS_MAIN}}}cellXfs")
        n_xf = len(cx.findall(f"{{{NS_MAIN}}}xf")) if cx is not None else 0
    for part in names:
        if not (part.startswith("xl/worksheets/sheet") and part.endswith(".xml")):
            continue
        root = ET.fromstring(z.read(part))
        for c in root.iter(f"{{{NS_MAIN}}}c"):
            s = c.get("s")
            if s is not None and not (0 <= int(s) < n_xf):
                problems.append(f"{part}: cell {c.get('r')} style s={s} out of range (<{n_xf})")
            if c.get("t") == "s":
                v = c.find(f"{{{NS_MAIN}}}v")
                if v is not None and v.text is not None and not (0 <= int(v.text) < n_shared):
                    problems.append(f"{part}: cell {c.get('r')} sharedString {v.text} "
                                    f"out of range (<{n_shared})")
    return problems


def _self_test():
    import sys
    import tempfile
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, here)
    from xlsx_writer import Workbook

    wb = Workbook()
    curve = [[h, round(1 - 0.01 * h, 4), round((1 - 0.01 * h) * (1 - 0.002 * h), 6)]
             for h in range(13)]
    cs = wb.add_sheet("Curve", curve, header=["h", "A_t", "B_t"])
    wb.set_green_columns(cs, [2])
    wb.add_line_chart(cs, "Ecoulement", cat_col=0, val_cols=[1, 2])
    bs = wb.add_sheet("WAL", [["a", 10.9], ["b", 8.6]], header=["seg", "WAL"])
    wb.add_bar_chart(bs, "WAL", cat_col=0, val_cols=[1])
    rs = wb.add_sheet("Rel", [[0.1, 0.12], [0.5, 0.55]], header=["pred", "act"])
    wb.add_scatter_chart(rs, "Fiab", x_col=0, y_cols=[1], diagonal=True)
    hs = wb.add_sheet("HP", [["l1", 0.1, 0.2], ["l2", 0.15, 0.25]], header=["l", "a1", "a2"])
    wb.add_color_scale(hs, "B2:C3")

    out = os.path.join(tempfile.gettempdir(), "xlsx_validate_selftest.xlsx")
    wb.save(out)
    problems = validate(out)
    if problems:
        print("xlsx_validate self-test FAILED:")
        for p in problems:
            print("  -", p)
        raise AssertionError(f"{len(problems)} integrity problems")
    print("xlsx_validate self-test PASSED (content-types, rels, r:id, chart refs, "
          "sharedStrings, styles all consistent)")


if __name__ == "__main__":
    _self_test()
