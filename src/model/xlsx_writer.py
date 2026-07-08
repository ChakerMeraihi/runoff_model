"""xlsx_writer.py -- write a real .xlsx workbook with PURE STDLIB (zipfile + string XML).

An .xlsx is a zip of XML parts, so the same trick that lets efm_collect.py READ .xlsx
with only the stdlib lets us WRITE one -- we hand-emit the parts Excel needs and zip them.
No pandas / openpyxl (the bank PC has neither). Round-trips through the reader in
panel/efm_collect.py (see the self-test).

Supports, all in pure stdlib:
  - multiple sheets of tables (str/int/float/bool/None cells), bold header row
  - a SOLID GREEN cell fill on chosen columns  (e.g. highlight the B(t) run-off column)
  - a 3-colour colorScale conditional format over a range (HP heatmap)
  - NATIVE embedded charts -- lineChart / barChart / scatterChart -- rendered by Excel
    on open with NO macros (a .xlsm/VBA needs an OLE2 vbaProject.bin that pure stdlib
    cannot write, and locked-down PCs block macros; native chart XML needs neither).

Chart element order and the worksheet child order are load-bearing (Excel silently
"repairs"/drops content on the smallest violation); the templates below were validated
part-by-part against the ECMA-376 SpreadsheetML/DrawingML schemas.

Public API:
  wb = Workbook()
  s = wb.add_sheet("Book Runoff", rows, header=["h","A_t","r_t","B_t","B_t_200bp"])
  wb.set_green_columns(s, [3])                       # colour the B_t column green
  wb.add_line_chart(s, "Ecoulement B(t)", cat_col=0, val_cols=[1,3,4])
  wb.add_bar_chart(s2, "WAL par livre", cat_col=0, val_cols=[1])
  wb.add_scatter_chart(s3, "Fiabilite", x_col=0, y_cols=[1], diagonal=True)
  wb.add_color_scale(s4, "B2:F6")                    # heatmap
  wb.save("report.xlsx")

Charts reference COLUMNS of the sheet you pass (0-based, incl. the header row); the
writer pulls the cached values from that sheet's rows so Excel renders without a recalc.
"""
from __future__ import annotations

import numbers
import os
import zipfile

NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_CT = "http://schemas.openxmlformats.org/package/2006/content-types"
NS_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
NS_C = "http://schemas.openxmlformats.org/drawingml/2006/chart"
NS_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
NS_XDR = "http://schemas.openxmlformats.org/drawingml/2006/spreadsheetDrawing"
CTP_DRAWING = "application/vnd.openxmlformats-officedocument.drawing+xml"
CTP_CHART = "application/vnd.openxmlformats-officedocument.drawingml.chart+xml"
CTP_TABLE = "application/vnd.openxmlformats-officedocument.spreadsheetml.table+xml"
RT_TABLE = NS_R + "/table"
GREEN = "FF63BE7B"        # the standard Excel "good" green
DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
# Excel's default categorical palette (accent 1..6) -> distinct, legible series colours.
PALETTE = ["4472C4", "ED7D31", "A5A5A5", "FFC000", "5B9BD5", "70AD47",
           "264478", "9E480E", "636363", "997300"]
LINE_W = 19050            # ~1.5pt: thin, clean lines (default is thicker + markers=noisy)

# style ids in the cellXfs we emit: 0 normal, 1 bold, 2 green fill, 3 bold+green
S_NORMAL, S_BOLD, S_GREEN, S_BOLDGREEN = 0, 1, 2, 3


def _esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _col_letter(idx0):
    s, n = "", idx0 + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _is_number(v):
    return isinstance(v, numbers.Number) and not isinstance(v, bool)


def _num(v):
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return "0"
        return repr(v)
    return str(v)


class Workbook:
    def __init__(self):
        self._sheets = []          # list of dicts: {name, header, rows, green, cfs, charts}
        self._shared = {}
        self._shared_order = []

    def _intern(self, s):
        i = self._shared.get(s)
        if i is None:
            i = len(self._shared_order)
            self._shared[s] = i
            self._shared_order.append(s)
        return i

    # ---- sheet + decoration API ------------------------------------------ #

    def add_sheet(self, name, rows, header=None, as_table=False, table_rows=None,
                  freeze_header=None, auto_width=True):
        """Add a worksheet; returns the (sanitized) tab name, which you pass to the
        chart/format methods to target this sheet.

        as_table  -> render the header+data as a real Excel Table (ListObject): banded
                     rows, styled header, autofilter dropdowns. Needs a header and a clean
                     rectangular block (no blank rows inside the table range).
        table_rows-> if the sheet has trailing note rows AFTER the table, pass the number
                     of clean DATA rows so the table range excludes the notes.
        freeze_header -> freeze the header row (defaults to True when there's a header).
        auto_width-> size each column to its content (readability/spacing)."""
        safe = _sanitize_sheet_name(name, [s["name"] for s in self._sheets])
        self._sheets.append({"name": safe, "header": list(header) if header else None,
                             "rows": [list(r) for r in rows],
                             "green": set(), "cfs": [], "charts": [],
                             "as_table": bool(as_table and header),
                             "table_rows": table_rows,
                             "freeze": (freeze_header if freeze_header is not None
                                        else bool(header)),
                             "auto_width": auto_width})
        return safe

    def _sheet(self, ref):
        for i, s in enumerate(self._sheets):
            if s["name"] == ref:
                return i, s
        raise KeyError(f"no sheet named {ref!r}; have {[s['name'] for s in self._sheets]}")

    def set_green_columns(self, sheet, col_indices):
        """Fill the DATA cells (not the header) of these 0-based columns solid green."""
        _, s = self._sheet(sheet)
        s["green"].update(col_indices)

    def add_color_scale(self, sheet, sqref):
        """3-colour min/mid/max colorScale heatmap over an A1 range like 'B2:F6'."""
        _, s = self._sheet(sheet)
        s["cfs"].append(("colorScale", sqref))

    def add_line_chart(self, sheet, title, cat_col, val_cols, names=None,
                       y_title=None, x_title=None, anchor=None, n_rows=None):
        self._add_chart(sheet, "line", title, cat_col, list(val_cols), names, y_title,
                        x_title, anchor, n_rows)

    def add_bar_chart(self, sheet, title, cat_col, val_cols, names=None,
                      y_title=None, x_title=None, anchor=None, n_rows=None):
        self._add_chart(sheet, "bar", title, cat_col, list(val_cols), names, y_title,
                        x_title, anchor, n_rows)

    def add_scatter_chart(self, sheet, title, x_col, y_cols, names=None,
                          diagonal=False, anchor=None, n_rows=None):
        """Scatter of (x_col, each y_col) as markers; diagonal=True adds a y=x line
        (used for reliability diagrams). n_rows caps the referenced points (so blocks of
        different lengths can share a sheet)."""
        _, s = self._sheet(sheet)
        s["charts"].append({"kind": "scatter", "title": title, "x_col": x_col,
                            "y_cols": list(y_cols), "names": names, "diagonal": diagonal,
                            "n_rows": n_rows, "anchor": anchor or self._auto_anchor(s)})

    def _add_chart(self, sheet, kind, title, cat_col, val_cols, names, y_title, x_title,
                   anchor, n_rows=None):
        _, s = self._sheet(sheet)
        s["charts"].append({"kind": kind, "title": title, "cat_col": cat_col,
                            "val_cols": val_cols, "names": names, "y_title": y_title,
                            "x_title": x_title, "n_rows": n_rows,
                            "anchor": anchor or self._auto_anchor(s)})

    @staticmethod
    def _auto_anchor(s):
        """Stack charts down the right side of the sheet, below any previous chart.
        Bigger frames (12 cols x 19 rows) + a 1-col/row gap so they read clearly."""
        ncols = max([len(s["header"] or [])] + [len(r) for r in s["rows"]] + [1])
        from_col = ncols + 1
        from_row = 1 + 20 * len(s["charts"])
        return (from_col, from_row, from_col + 12, from_row + 19)

    # ---- data-cell helpers ----------------------------------------------- #

    def _data_values(self, s, col):
        """Cached values of a data column (rows only, not header) for a chart cache."""
        return [(r[col] if col < len(r) else None) for r in s["rows"]]

    # ---- XML part builders ----------------------------------------------- #

    def _sheet_xml(self, s, has_drawing, has_table, table_rid):
        header, rows, green, cfs = s["header"], s["rows"], s["green"], s["cfs"]
        rns = f' xmlns:r="{NS_R}"' if (has_drawing or has_table) else ""
        out = [DECL, f'<worksheet xmlns="{NS_MAIN}"{rns}>']
        # CT_Worksheet order: sheetViews, cols, sheetData, conditionalFormatting*, drawing, tableParts
        if s.get("freeze") and header is not None:
            out.append('<sheetViews><sheetView workbookViewId="0">'
                       '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
                       '<selection pane="bottomLeft"/></sheetView></sheetViews>')
        if s.get("auto_width"):
            widths = _auto_widths(header, rows)
            if widths:
                cols = "".join(f'<col min="{i+1}" max="{i+1}" width="{w}" customWidth="1"/>'
                               for i, w in enumerate(widths))
                out.append(f'<cols>{cols}</cols>')
        out.append('<sheetData>')
        grid = ([header] if header is not None else []) + rows
        header_rows = 1 if header is not None else 0
        for ri, row in enumerate(grid):
            r_no = ri + 1
            is_data = ri >= header_rows
            cells = []
            for ci, val in enumerate(row):
                if val is None or val == "":
                    continue
                ref = f"{_col_letter(ci)}{r_no}"
                style = S_BOLD if ri < header_rows else (S_GREEN if (is_data and ci in green) else S_NORMAL)
                sattr = f' s="{style}"' if style else ""
                if _is_number(val):
                    cells.append(f'<c r="{ref}"{sattr}><v>{_num(val)}</v></c>')
                else:
                    cells.append(f'<c r="{ref}"{sattr} t="s"><v>{self._intern(str(val))}</v></c>')
            out.append(f'<row r="{r_no}">' + "".join(cells) + "</row>")
        out.append("</sheetData>")
        for kind, sqref in cfs:
            if kind == "colorScale":
                out.append(
                    f'<conditionalFormatting sqref="{sqref}">'
                    f'<cfRule type="colorScale" priority="1"><colorScale>'
                    f'<cfvo type="min"/><cfvo type="percentile" val="50"/><cfvo type="max"/>'
                    f'<color rgb="FFF8696B"/><color rgb="FFFFEB84"/><color rgb="FF63BE7B"/>'
                    f'</colorScale></cfRule></conditionalFormatting>')
        if has_drawing:
            out.append('<drawing r:id="rId1"/>')
        if has_table:
            out.append(f'<tableParts count="1"><tablePart r:id="rId{table_rid}"/></tableParts>')
        out.append("</worksheet>")
        return "".join(out)

    def _table_xml(self, s, table_id):
        """A ListObject over the header + clean data rows (banded style + autofilter)."""
        header = s["header"]
        ndata = s["table_rows"] if s["table_rows"] is not None else len(s["rows"])
        ncol = len(header)
        ref = f"A1:{_col_letter(ncol - 1)}{ndata + 1}"
        # tableColumn names must be unique + non-empty; de-dup defensively
        seen, cols = {}, []
        for j, h in enumerate(header):
            nm = str(h) if h not in (None, "") else f"col{j+1}"
            if nm in seen:
                seen[nm] += 1
                nm = f"{nm}_{seen[nm]}"
            else:
                seen[nm] = 1
            cols.append(f'<tableColumn id="{j+1}" name="{_esc(nm)}"/>')
        return (f'{DECL}<table xmlns="{NS_MAIN}" id="{table_id}" name="Table{table_id}" '
                f'displayName="Table{table_id}" ref="{ref}" totalsRowShown="0">'
                f'<autoFilter ref="{ref}"/>'
                f'<tableColumns count="{ncol}">{"".join(cols)}</tableColumns>'
                f'<tableStyleInfo name="TableStyleMedium2" showFirstColumn="0" '
                f'showLastColumn="0" showRowStripes="1" showColumnStripes="0"/></table>')

    def _shared_strings_xml(self):
        parts = [DECL, f'<sst xmlns="{NS_MAIN}" count="{len(self._shared_order)}" '
                 f'uniqueCount="{len(self._shared_order)}">']
        for s in self._shared_order:
            parts.append(f'<si><t xml:space="preserve">{_esc(s)}</t></si>')
        parts.append("</sst>")
        return "".join(parts)

    def _workbook_xml(self):
        sheets = "".join(f'<sheet name="{_esc(s["name"])}" sheetId="{i+1}" r:id="rId{i+1}"/>'
                         for i, s in enumerate(self._sheets))
        return (f'{DECL}<workbook xmlns="{NS_MAIN}" xmlns:r="{NS_R}">'
                f'<sheets>{sheets}</sheets></workbook>')

    def _workbook_rels_xml(self):
        n = len(self._sheets)
        rels = [f'<Relationship Id="rId{i+1}" Type="{NS_R}/worksheet" '
                f'Target="worksheets/sheet{i+1}.xml"/>' for i in range(n)]
        rels.append(f'<Relationship Id="rId{n+1}" Type="{NS_R}/styles" Target="styles.xml"/>')
        rels.append(f'<Relationship Id="rId{n+2}" Type="{NS_R}/sharedStrings" '
                    f'Target="sharedStrings.xml"/>')
        return f'{DECL}<Relationships xmlns="{NS_REL}">{"".join(rels)}</Relationships>'

    def _content_types_xml(self, drawings, charts, tables):
        ov = [f'<Override PartName="/xl/worksheets/sheet{i+1}.xml" ContentType="application/vnd.'
              f'openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
              for i in range(len(self._sheets))]
        ov += [f'<Override PartName="/xl/drawings/drawing{d}.xml" ContentType="{CTP_DRAWING}"/>'
               for d in drawings]
        ov += [f'<Override PartName="/xl/charts/chart{c}.xml" ContentType="{CTP_CHART}"/>'
               for c in charts]
        ov += [f'<Override PartName="/xl/tables/table{t}.xml" ContentType="{CTP_TABLE}"/>'
               for t in tables]
        return (
            f'{DECL}<Types xmlns="{NS_CT}">'
            f'<Default Extension="rels" ContentType="application/vnd.openxmlformats-'
            f'package.relationships+xml"/>'
            f'<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
            f'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
            f'<Override PartName="/xl/styles.xml" ContentType="application/vnd.'
            f'openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
            f'<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.'
            f'openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>'
            f'{"".join(ov)}</Types>')

    @staticmethod
    def _root_rels_xml():
        return (f'{DECL}<Relationships xmlns="{NS_REL}">'
                f'<Relationship Id="rId1" Type="{NS_R}/officeDocument" '
                f'Target="xl/workbook.xml"/></Relationships>')

    @staticmethod
    def _styles_xml():
        # fonts: 0 normal, 1 bold. fills: 0 none, 1 gray(unused placeholder? no), 1 green.
        # cellXfs: 0 normal, 1 bold, 2 green fill, 3 bold+green.
        return (
            f'{DECL}<styleSheet xmlns="{NS_MAIN}">'
            f'<fonts count="2">'
            f'<font><sz val="11"/><name val="Calibri"/></font>'
            f'<font><b/><sz val="11"/><name val="Calibri"/></font></fonts>'
            f'<fills count="2">'
            f'<fill><patternFill patternType="none"/></fill>'
            f'<fill><patternFill patternType="solid"><fgColor rgb="{GREEN}"/></patternFill></fill>'
            f'</fills>'
            f'<borders count="1"><border/></borders>'
            f'<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/>'
            f'</cellStyleXfs>'
            f'<cellXfs count="4">'
            f'<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
            f'<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>'
            f'<xf numFmtId="0" fontId="0" fillId="1" borderId="0" xfId="0" applyFill="1"/>'
            f'<xf numFmtId="0" fontId="1" fillId="1" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
            f'</cellXfs></styleSheet>')

    def _sheet_rels_xml(self, drawing_id, table_id=None, table_rid=None):
        rels = []
        if drawing_id is not None:
            rels.append(f'<Relationship Id="rId1" Type="{NS_R}/drawing" '
                        f'Target="../drawings/drawing{drawing_id}.xml"/>')
        if table_id is not None:
            rels.append(f'<Relationship Id="rId{table_rid}" Type="{RT_TABLE}" '
                        f'Target="../tables/table{table_id}.xml"/>')
        return f'{DECL}<Relationships xmlns="{NS_REL}">{"".join(rels)}</Relationships>'

    def _drawing_rels_xml(self, chart_ids):
        rels = "".join(f'<Relationship Id="rId{i+1}" Type="{NS_R}/chart" '
                       f'Target="../charts/chart{cid}.xml"/>' for i, cid in enumerate(chart_ids))
        return f'{DECL}<Relationships xmlns="{NS_REL}">{rels}</Relationships>'

    def _drawing_xml(self, charts_local):
        """One drawing part per sheet, one twoCellAnchor per chart; rId is per-drawing."""
        anchors = []
        for i, ch in enumerate(charts_local):
            a = ch["anchor"]
            anchors.append(
                '<xdr:twoCellAnchor editAs="oneCell">'
                f'<xdr:from><xdr:col>{a[0]}</xdr:col><xdr:colOff>0</xdr:colOff>'
                f'<xdr:row>{a[1]}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:from>'
                f'<xdr:to><xdr:col>{a[2]}</xdr:col><xdr:colOff>0</xdr:colOff>'
                f'<xdr:row>{a[3]}</xdr:row><xdr:rowOff>0</xdr:rowOff></xdr:to>'
                '<xdr:graphicFrame macro="">'
                f'<xdr:nvGraphicFramePr><xdr:cNvPr id="{i+2}" name="Chart {i+1}"/>'
                '<xdr:cNvGraphicFramePr/></xdr:nvGraphicFramePr>'
                '<xdr:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/></xdr:xfrm>'
                f'<a:graphic><a:graphicData uri="{NS_C}">'
                f'<c:chart xmlns:c="{NS_C}" xmlns:r="{NS_R}" r:id="rId{i+1}"/>'
                '</a:graphicData></a:graphic></xdr:graphicFrame>'
                '<xdr:clientData/></xdr:twoCellAnchor>')
        return (f'{DECL}<xdr:wsDr xmlns:xdr="{NS_XDR}" xmlns:a="{NS_A}" '
                f'xmlns:r="{NS_R}" xmlns:c="{NS_C}">{"".join(anchors)}</xdr:wsDr>')

    def _chart_xml(self, s, ch):
        if ch["kind"] == "scatter":
            return self._scatter_chart_xml(s, ch)
        return self._catval_chart_xml(s, ch)

    def _catval_chart_xml(self, s, ch):
        """line / bar chart: a category column + one or more value columns."""
        name = s["name"]
        cats = [("" if v is None else v) for v in self._data_values(s, ch["cat_col"])]
        if ch.get("n_rows"):
            cats = cats[:ch["n_rows"]]
        n = len(cats)
        first, last = 2, n + 1                       # data rows (after header)
        ax_cat, ax_val = "111111111", "222222222"
        catL = _col_letter(ch["cat_col"])
        cat_range = f"{name}!${catL}${first}:${catL}${last}"
        sers = []
        for order, col in enumerate(ch["val_cols"]):
            vals = [(0.0 if not _is_number(v) else v) for v in self._data_values(s, col)]
            if ch.get("n_rows"):
                vals = vals[:ch["n_rows"]]
            colL = _col_letter(col)
            sname = (ch["names"][order] if ch["names"] and order < len(ch["names"])
                     else (s["header"][col] if s["header"] and col < len(s["header"]) else colL))
            name_ref = f"{name}!${colL}$1"
            val_range = f"{name}!${colL}${first}:${colL}${last}"
            colr = PALETTE[order % len(PALETTE)]
            tx = f'<c:tx>{_str_ref(name_ref, [sname])}</c:tx>'
            catref = f'<c:cat>{_str_ref(cat_range, cats)}</c:cat>'
            valref = f'<c:val>{_num_ref(val_range, vals)}</c:val>'
            if ch["kind"] == "bar":
                # coloured fill; spPr right after tx (CT_BarSer order)
                sppr = f'<c:spPr><a:solidFill><a:srgbClr val="{colr}"/></a:solidFill></c:spPr>'
                sers.append(f'<c:ser><c:idx val="{order}"/><c:order val="{order}"/>{tx}{sppr}'
                            f'{catref}{valref}</c:ser>')
            else:
                # thin coloured line + NO markers (markers on 361 pts = the "too thick" smear).
                # CT_LineSer order: idx, order, tx, spPr, marker, cat, val, smooth
                sppr = (f'<c:spPr><a:ln w="{LINE_W}" cap="rnd"><a:solidFill>'
                        f'<a:srgbClr val="{colr}"/></a:solidFill><a:round/></a:ln></c:spPr>')
                mk = '<c:marker><c:symbol val="none"/></c:marker>'
                sers.append(f'<c:ser><c:idx val="{order}"/><c:order val="{order}"/>{tx}{sppr}{mk}'
                            f'{catref}{valref}<c:smooth val="0"/></c:ser>')
        if ch["kind"] == "bar":
            plot_inner = (f'<c:barChart><c:barDir val="col"/><c:grouping val="clustered"/>'
                          f'<c:varyColors val="0"/>{"".join(sers)}'
                          f'<c:gapWidth val="150"/><c:overlap val="-27"/>'
                          f'<c:axId val="{ax_cat}"/><c:axId val="{ax_val}"/></c:barChart>')
        else:
            plot_inner = (f'<c:lineChart><c:grouping val="standard"/><c:varyColors val="0"/>'
                          f'{"".join(sers)}<c:marker val="1"/>'
                          f'<c:axId val="{ax_cat}"/><c:axId val="{ax_val}"/></c:lineChart>')
        # tickLblSkip/tickMarkSkip thin out crowded category labels (e.g. 361 months)
        skip = (f'<c:tickLblSkip val="{max(1, n // 12)}"/><c:tickMarkSkip val="{max(1, n // 12)}"/>'
                if n > 24 else "")
        cat_ax = (f'<c:catAx><c:axId val="{ax_cat}"/>'
                  f'<c:scaling><c:orientation val="minMax"/></c:scaling>'
                  f'<c:delete val="0"/><c:axPos val="b"/>{_ax_title(ch.get("x_title"), vertical=False)}'
                  f'<c:tickLblPos val="nextTo"/>'
                  f'<c:crossAx val="{ax_val}"/><c:auto val="1"/><c:lblAlgn val="ctr"/>'
                  f'<c:lblOffset val="100"/>{skip}<c:noMultiLvlLbl val="0"/></c:catAx>')
        val_ax = (f'<c:valAx><c:axId val="{ax_val}"/>'
                  f'<c:scaling><c:orientation val="minMax"/></c:scaling>'
                  f'<c:delete val="0"/><c:axPos val="l"/>'
                  f'<c:majorGridlines/>{_ax_title(ch.get("y_title"), vertical=True)}'
                  f'<c:crossAx val="{ax_cat}"/></c:valAx>')
        plot = f'<c:plotArea><c:layout/>{plot_inner}{cat_ax}{val_ax}</c:plotArea>'
        chart = (f'<c:chart>{_chart_title(ch["title"])}<c:autoTitleDeleted val="0"/>{plot}'
                 f'<c:legend><c:legendPos val="b"/><c:overlay val="0"/></c:legend>'
                 f'<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart>')
        return (f'{DECL}<c:chartSpace xmlns:c="{NS_C}" xmlns:a="{NS_A}" xmlns:r="{NS_R}">'
                f'{chart}</c:chartSpace>')

    def _scatter_chart_xml(self, s, ch):
        name = s["name"]
        nr = ch.get("n_rows")
        xcolL = _col_letter(ch["x_col"])
        xv = [(0.0 if not _is_number(v) else v) for v in self._data_values(s, ch["x_col"])]
        if nr:
            xv = xv[:nr]
        n = len(xv)
        first, last = 2, n + 1
        ax_x, ax_y = "111111111", "222222222"
        sers = []
        for order, col in enumerate(ch["y_cols"]):
            yv = [(0.0 if not _is_number(v) else v) for v in self._data_values(s, col)]
            if nr:
                yv = yv[:nr]
            colL = _col_letter(col)
            sname = (ch["names"][order] if ch["names"] and order < len(ch["names"])
                     else (s["header"][col] if s["header"] and col < len(s["header"]) else colL))
            sers.append(
                f'<c:ser><c:idx val="{order}"/><c:order val="{order}"/>'
                f'<c:tx><c:v>{_esc(sname)}</c:v></c:tx>'
                f'<c:spPr><a:ln w="28575"><a:noFill/></a:ln></c:spPr>'
                f'<c:marker><c:symbol val="circle"/><c:size val="5"/></c:marker>'
                f'<c:xVal>{_num_ref(f"{name}!${xcolL}${first}:${xcolL}${last}", xv)}</c:xVal>'
                f'<c:yVal>{_num_ref(f"{name}!${colL}${first}:${colL}${last}", yv)}</c:yVal>'
                f'<c:smooth val="0"/></c:ser>')
        if ch["diagonal"]:
            # y=x reference from the min to the max of x (points held in the numCache only,
            # referenced to a 2-cell range that need not hold real data -> Excel uses cache)
            lo, hi = (min(xv) if xv else 0.0), (max(xv) if xv else 1.0)
            sers.append(
                f'<c:ser><c:idx val="{len(ch["y_cols"])}"/><c:order val="{len(ch["y_cols"])}"/>'
                f'<c:tx><c:v>y=x</c:v></c:tx>'
                f'<c:spPr><a:ln w="19050"><a:solidFill><a:srgbClr val="FF0000"/></a:solidFill>'
                f'<a:prstDash val="dash"/></a:ln></c:spPr>'
                f'<c:marker><c:symbol val="none"/></c:marker>'
                f'<c:xVal>{_num_ref(f"{name}!$XFD$1:$XFD$2", [lo, hi])}</c:xVal>'
                f'<c:yVal>{_num_ref(f"{name}!$XFE$1:$XFE$2", [lo, hi])}</c:yVal>'
                f'<c:smooth val="0"/></c:ser>')
        scatter = (f'<c:scatterChart><c:scatterStyle val="lineMarker"/><c:varyColors val="0"/>'
                   f'{"".join(sers)}<c:axId val="{ax_x}"/><c:axId val="{ax_y}"/></c:scatterChart>')
        ax_x_xml = (f'<c:valAx><c:axId val="{ax_x}"/>'
                    f'<c:scaling><c:orientation val="minMax"/></c:scaling>'
                    f'<c:delete val="0"/><c:axPos val="b"/><c:majorTickMark val="out"/>'
                    f'<c:tickLblPos val="nextTo"/><c:crossAx val="{ax_y}"/>'
                    f'<c:crosses val="autoZero"/><c:crossBetween val="midCat"/></c:valAx>')
        ax_y_xml = (f'<c:valAx><c:axId val="{ax_y}"/>'
                    f'<c:scaling><c:orientation val="minMax"/></c:scaling>'
                    f'<c:delete val="0"/><c:axPos val="l"/><c:majorTickMark val="out"/>'
                    f'<c:tickLblPos val="nextTo"/><c:crossAx val="{ax_x}"/>'
                    f'<c:crosses val="autoZero"/><c:crossBetween val="midCat"/></c:valAx>')
        plot = f'<c:plotArea><c:layout/>{scatter}{ax_x_xml}{ax_y_xml}</c:plotArea>'
        chart = (f'<c:chart>{_chart_title(ch["title"])}<c:autoTitleDeleted val="0"/>{plot}'
                 f'<c:legend><c:legendPos val="b"/><c:overlay val="0"/></c:legend>'
                 f'<c:plotVisOnly val="1"/><c:dispBlanksAs val="gap"/></c:chart>')
        return (f'{DECL}<c:chartSpace xmlns:c="{NS_C}" xmlns:a="{NS_A}" xmlns:r="{NS_R}">'
                f'{chart}</c:chartSpace>')

    # ---- assemble ---------------------------------------------------------- #

    def save(self, path):
        if not self._sheets:
            raise RuntimeError("workbook has no sheets")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)

        # assign drawing/chart ids for sheets that have charts
        drawing_ids, chart_ids, table_ids = [], [], []
        sheet_draw = {}                    # sheet index -> (drawing_id, [chart_id...])
        sheet_table = {}                   # sheet index -> (table_id, table_rid)
        dcount = ccount = tcount = 0
        for i, s in enumerate(self._sheets):
            if s["charts"]:
                dcount += 1
                local_charts = []
                for _ in s["charts"]:
                    ccount += 1
                    local_charts.append(ccount)
                    chart_ids.append(ccount)
                drawing_ids.append(dcount)
                sheet_draw[i] = (dcount, local_charts)
            if s.get("as_table"):
                tcount += 1
                table_ids.append(tcount)
                table_rid = 2 if i in sheet_draw else 1     # drawing takes rId1 if present
                sheet_table[i] = (tcount, table_rid)

        # build sheet XML (this interns strings; must precede sharedStrings)
        sheet_xml = [self._sheet_xml(s, i in sheet_draw, i in sheet_table,
                                     sheet_table.get(i, (None, None))[1])
                     for i, s in enumerate(self._sheets)]

        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("[Content_Types].xml",
                       self._content_types_xml(drawing_ids, chart_ids, table_ids))
            z.writestr("_rels/.rels", self._root_rels_xml())
            z.writestr("xl/workbook.xml", self._workbook_xml())
            z.writestr("xl/_rels/workbook.xml.rels", self._workbook_rels_xml())
            z.writestr("xl/styles.xml", self._styles_xml())
            z.writestr("xl/sharedStrings.xml", self._shared_strings_xml())
            for i, xml in enumerate(sheet_xml):
                z.writestr(f"xl/worksheets/sheet{i+1}.xml", xml)
            for i, s in enumerate(self._sheets):
                did = sheet_draw.get(i, (None, None))[0]
                tid, trid = sheet_table.get(i, (None, None))
                if did is not None or tid is not None:
                    z.writestr(f"xl/worksheets/_rels/sheet{i+1}.xml.rels",
                               self._sheet_rels_xml(did, tid, trid))
                if did is not None:
                    cids = sheet_draw[i][1]
                    z.writestr(f"xl/drawings/drawing{did}.xml", self._drawing_xml(s["charts"]))
                    z.writestr(f"xl/drawings/_rels/drawing{did}.xml.rels",
                               self._drawing_rels_xml(cids))
                    for ch, cid in zip(s["charts"], cids):
                        z.writestr(f"xl/charts/chart{cid}.xml", self._chart_xml(s, ch))
                if tid is not None:
                    z.writestr(f"xl/tables/table{tid}.xml", self._table_xml(s, tid))
        return path


# ---- module-level helpers -------------------------------------------------- #
def _cell_len(v):
    if v is None:
        return 0
    if isinstance(v, float):
        return len(_num(v))
    return len(str(v))


def _auto_widths(header, rows, cap=60, min_w=8, sample=400):
    """Column widths sized to content (chars), capped for very long text (Definition).
    Samples the first `sample` rows so a 37k-row DATA sheet stays fast."""
    ncol = max([len(header or [])] + [len(r) for r in rows[:sample]] + [0])
    if not ncol:
        return None
    widths = []
    for c in range(ncol):
        m = _cell_len(header[c]) if header and c < len(header) else 0
        for r in rows[:sample]:
            if c < len(r):
                m = max(m, _cell_len(r[c]))
        widths.append(round(min(cap, max(min_w, m + 2)) + 0.71, 2))
    return widths


# ---- module-level XML fragment helpers ------------------------------------ #
def _str_ref(f_ref, values):
    pts = "".join(f'<c:pt idx="{i}"><c:v>{_esc(v)}</c:v></c:pt>' for i, v in enumerate(values))
    return (f'<c:strRef><c:f>{_esc(f_ref)}</c:f>'
            f'<c:strCache><c:ptCount val="{len(values)}"/>{pts}</c:strCache></c:strRef>')


def _num_ref(f_ref, values):
    pts = "".join(f'<c:pt idx="{i}"><c:v>{_num(v)}</c:v></c:pt>' for i, v in enumerate(values))
    return (f'<c:numRef><c:f>{_esc(f_ref)}</c:f>'
            f'<c:numCache><c:formatCode>General</c:formatCode>'
            f'<c:ptCount val="{len(values)}"/>{pts}</c:numCache></c:numRef>')


def _chart_title(text):
    return (f'<c:title><c:tx><c:rich><a:bodyPr/><a:lstStyle/>'
            f'<a:p><a:r><a:t>{_esc(text)}</a:t></a:r></a:p></c:rich></c:tx>'
            f'<c:overlay val="0"/></c:title>')


def _ax_title(text, vertical=True):
    if not text:
        return ""
    bodypr = '<a:bodyPr rot="-5400000" vert="horz"/>' if vertical else '<a:bodyPr/>'
    return (f'<c:title><c:tx><c:rich>{bodypr}<a:lstStyle/>'
            f'<a:p><a:r><a:t>{_esc(text)}</a:t></a:r></a:p></c:rich></c:tx>'
            f'<c:overlay val="0"/></c:title>')


def _sanitize_sheet_name(name, existing):
    bad = set('[]:*?/\\')
    clean = "".join(c for c in str(name) if c not in bad).strip() or "Sheet"
    clean = clean[:31]
    base, k = clean, 2
    while clean.lower() in {e.lower() for e in existing}:
        suffix = f"_{k}"
        clean = base[:31 - len(suffix)] + suffix
        k += 1
    return clean


# --------------------------------------------------------------------------- #
# self-test: write a workbook with tables, a green column, a colorScale, and one of
# each chart type; validate every part is well-formed + round-trips via the reader.
# --------------------------------------------------------------------------- #
def _self_test():
    import sys
    import tempfile
    import xml.etree.ElementTree as ET
    here = os.path.dirname(os.path.abspath(__file__))
    sys.path.insert(0, os.path.abspath(os.path.join(here, "..", "panel")))
    from efm_collect import read_xlsx_sheet, list_sheets, detect_format

    wb = Workbook()
    # 1) summary as a real Excel Table (banded rows), green B_t column, notes excluded
    summ = [["accounts", 238, 12.18], ["wal", 11.0, 0.5], [""], ["note: synthetic"]]
    ss = wb.add_sheet("Summary", summ, header=["metric", "value", "B_t"],
                      as_table=True, table_rows=2)
    wb.set_green_columns(ss, [2])

    # 2) run-off curve as a table + a line chart (A_t, B_t)
    curve = [[h, round(1 - 0.01 * h, 4), round((1 - 0.01 * h) * (1 - 0.002 * h), 6)]
             for h in range(13)]
    cs = wb.add_sheet("Curve", curve, header=["h", "A_t", "B_t"], as_table=True)
    wb.set_green_columns(cs, [2])
    wb.add_line_chart(cs, "Ecoulement", cat_col=0, val_cols=[1, 2],
                      y_title="fraction", x_title="mois")

    # 3) bar chart per segment (as a table too)
    bars = [["dinars", 10.9], ["epargne", 11.7], ["decouv", 8.6]]
    bsheet = wb.add_sheet("WAL", bars, header=["segment", "WAL"], as_table=True)
    wb.add_bar_chart(bsheet, "WAL par livre", cat_col=0, val_cols=[1], x_title="livre")

    # 4) scatter reliability + diagonal
    rel = [[0.1, 0.12], [0.3, 0.28], [0.5, 0.55], [0.8, 0.79]]
    rsheet = wb.add_sheet("Reliab", rel, header=["pred", "actual"])
    wb.add_scatter_chart(rsheet, "Fiabilite", x_col=0, y_cols=[1], diagonal=True)

    # 5) colorScale heatmap
    grid = [["l=1e-3", 0.17, 0.18, 0.19], ["l=1e-2", 0.16, 0.15, 0.20]]
    hsheet = wb.add_sheet("HP", grid, header=["lambda", "a=.1", "a=.5", "a=.9"])
    wb.add_color_scale(hsheet, "B2:D3")

    out = os.path.join(tempfile.gettempdir(), "xlsx_writer_charts_selftest.xlsx")
    wb.save(out)

    # (a) every part well-formed + zip ok
    with zipfile.ZipFile(out) as z:
        assert z.testzip() is None, "zip corrupt"
        for nm in z.namelist():
            if nm.endswith(".xml") or nm.endswith(".rels"):
                ET.fromstring(z.read(nm))          # raises if malformed
        names = set(z.namelist())
    for need in ("xl/charts/chart1.xml", "xl/charts/chart2.xml", "xl/charts/chart3.xml",
                 "xl/drawings/drawing1.xml", "xl/worksheets/_rels/sheet2.xml.rels",
                 "xl/tables/table1.xml"):
        assert need in names, f"missing part {need}"
    n_tables = len([n for n in names if n.startswith("xl/tables/table")])
    assert n_tables == 3, f"expected 3 tables, got {n_tables}"        # Summary, Curve, WAL

    # (b) data round-trips through the production reader
    assert detect_format(out) == "xlsx"
    assert list_sheets(out) == ["Summary", "Curve", "WAL", "Reliab", "HP"]
    c = read_xlsx_sheet(out, "Curve")
    assert c[0] == ["h", "A_t", "B_t"] and c[1][0] == "0" and c[1][1] == "1.0", c[:2]
    assert len(c) == 14
    h = read_xlsx_sheet(out, "HP")
    assert h[1][1] == "0.17", h[1]

    print("xlsx_writer self-test PASSED")
    print(f"  parts: {len(names)}; charts line+bar+scatter; {n_tables} Excel Tables; "
          f"green col + colorScale + frozen headers + auto widths")
    print(f"  every XML part well-formed, zip ok, data round-trips via reader")
    print(f"  -> {out}")


if __name__ == "__main__":
    _self_test()
