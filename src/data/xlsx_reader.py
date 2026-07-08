"""Minimal stdlib .xlsx reader (zipfile + xml.etree). No openpyxl/pandas.

An .xlsx is a zip of XML parts. We read shared strings + a worksheet and return
rows as lists of cell values (str / float / None). Sufficient for the World Bank
Pink Sheet (oil) and ONS CPI bulletins. Accepts a path or raw bytes.
"""
from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET

REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _col_to_idx(ref: str) -> int:
    letters = re.match(r"([A-Z]+)", ref or "A").group(1)
    idx = 0
    for ch in letters:
        idx = idx * 26 + (ord(ch) - 64)
    return idx - 1


def _zip(source):
    if isinstance(source, (bytes, bytearray)):
        return zipfile.ZipFile(io.BytesIO(source))
    return zipfile.ZipFile(source)


def _shared_strings(z):
    try:
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    except KeyError:
        return []
    out = []
    for si in root:
        out.append("".join(t.text or "" for t in si.iter() if _local(t.tag) == "t"))
    return out


def _sheet_map(z):
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    rid_to_target = {r.get("Id"): r.get("Target") for r in rels}
    name_to_path = {}
    for sh in wb.iter():
        if _local(sh.tag) != "sheet":
            continue
        tgt = rid_to_target.get(sh.get(REL_NS), "")
        if not tgt.startswith("xl/"):
            tgt = "xl/" + tgt.lstrip("/")
        name_to_path[sh.get("name")] = tgt
    return name_to_path


def sheet_names(source):
    with _zip(source) as z:
        return list(_sheet_map(z).keys())


def read_sheet(source, sheet=None):
    """Return list of rows; each row is a list of cell values (str/float/None)."""
    with _zip(source) as z:
        ss = _shared_strings(z)
        smap = _sheet_map(z)
        if sheet is None:
            sheet = next(iter(smap))
        data = z.read(smap[sheet])
    root = ET.fromstring(data)
    rows = []
    for row in root.iter():
        if _local(row.tag) != "row":
            continue
        cells, maxc = {}, -1
        for c in row:
            if _local(c.tag) != "c":
                continue
            ci = _col_to_idx(c.get("r", "A"))
            t = c.get("t")
            vtext = istext = None
            for ch in c:
                lt = _local(ch.tag)
                if lt == "v":
                    vtext = ch.text
                elif lt == "is":
                    istext = "".join(x.text or "" for x in ch.iter() if _local(x.tag) == "t")
            if t == "s" and vtext is not None:
                val = ss[int(vtext)]
            elif t == "inlineStr":
                val = istext
            elif vtext is None:
                val = None
            else:
                try:
                    val = float(vtext)
                except ValueError:
                    val = vtext
            cells[ci] = val
            maxc = max(maxc, ci)
        rows.append([cells.get(i) for i in range(maxc + 1)])
    return rows


if __name__ == "__main__":
    print("xlsx_reader: stdlib .xlsx reader ready (sheet_names, read_sheet)")
