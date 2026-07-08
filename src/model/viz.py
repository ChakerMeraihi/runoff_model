"""Pure-stdlib plotting -- SVG (vector, browser-openable) + ASCII (terminal).

No matplotlib/plotly (not on the bank PC). SVG is just XML text, so we emit it with
string formatting -> opens in any browser, zero dependencies. Outputs are aggregates
(S(t) curves, calibration tables) -> clearing-safe to render. ASCII helpers give a
quick terminal glance in the daily/eval logs.

Charts: svg_lines (with optional fan band), svg_bars, svg_reliability, svg_timeline.
Each returns an SVG string; save with write_svg(). ascii_spark / ascii_bars for stdout.
"""
from __future__ import annotations

import html

W, H = 720, 440
ML, MR, MT, MB = 64, 24, 36, 48          # margins
PALETTE = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e"]


def _sx(x, xmin, xmax):
    rng = (xmax - xmin) or 1.0
    return ML + (x - xmin) / rng * (W - ML - MR)


def _sy(y, ymin, ymax):
    rng = (ymax - ymin) or 1.0
    return MT + (1 - (y - ymin) / rng) * (H - MT - MB)


def _frame(title, xmin, xmax, ymin, ymax, xlabel, ylabel, yticks=5, xticks=6):
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'font-family="sans-serif" font-size="12">',
           f'<rect width="{W}" height="{H}" fill="white"/>',
           f'<text x="{W/2}" y="20" text-anchor="middle" font-size="15" '
           f'font-weight="bold">{html.escape(title)}</text>']
    # axes
    x0, y0 = _sx(xmin, xmin, xmax), _sy(ymin, ymin, ymax)
    out.append(f'<line x1="{ML}" y1="{MT}" x2="{ML}" y2="{H-MB}" stroke="#333"/>')
    out.append(f'<line x1="{ML}" y1="{H-MB}" x2="{W-MR}" y2="{H-MB}" stroke="#333"/>')
    for i in range(yticks + 1):
        v = ymin + (ymax - ymin) * i / yticks
        y = _sy(v, ymin, ymax)
        out.append(f'<line x1="{ML-4}" y1="{y:.1f}" x2="{W-MR}" y2="{y:.1f}" '
                   f'stroke="#eee"/>')
        out.append(f'<text x="{ML-8}" y="{y+4:.1f}" text-anchor="end">{v:.3g}</text>')
    for i in range(xticks + 1):
        v = xmin + (xmax - xmin) * i / xticks
        x = _sx(v, xmin, xmax)
        out.append(f'<text x="{x:.1f}" y="{H-MB+16:.1f}" text-anchor="middle">{v:.3g}</text>')
    out.append(f'<text x="{W/2}" y="{H-8}" text-anchor="middle">{html.escape(xlabel)}</text>')
    out.append(f'<text x="16" y="{H/2}" text-anchor="middle" '
               f'transform="rotate(-90 16 {H/2})">{html.escape(ylabel)}</text>')
    return out


def svg_lines(series, title="", xlabel="x", ylabel="y", band=None,
              ymin=None, ymax=None, legend=True):
    """series = list of (name, [(x,y),...]). band = (name,[(x,lo,hi)...]) shaded."""
    allpts = [p for _, pts in series for p in pts]
    if band:
        allpts += [(x, lo) for x, lo, _ in band[1]] + [(x, hi) for x, _, hi in band[1]]
    xs = [p[0] for p in allpts]
    ys = [p[1] for p in allpts]
    xmin, xmax = min(xs), max(xs)
    ymin = min(ys) if ymin is None else ymin
    ymax = max(ys) if ymax is None else ymax
    out = _frame(title, xmin, xmax, ymin, ymax, xlabel, ylabel)
    if band:
        _, pts = band
        up = " ".join(f"{_sx(x,xmin,xmax):.1f},{_sy(hi,ymin,ymax):.1f}" for x, _, hi in pts)
        dn = " ".join(f"{_sx(x,xmin,xmax):.1f},{_sy(lo,ymin,ymax):.1f}"
                      for x, lo, _ in reversed(pts))
        out.append(f'<polygon points="{up} {dn}" fill="#1f77b4" fill-opacity="0.15"/>')
    for i, (name, pts) in enumerate(series):
        col = PALETTE[i % len(PALETTE)]
        poly = " ".join(f"{_sx(x,xmin,xmax):.1f},{_sy(y,ymin,ymax):.1f}" for x, y in pts)
        out.append(f'<polyline points="{poly}" fill="none" stroke="{col}" stroke-width="2"/>')
        if legend:
            ly = MT + 6 + i * 16
            out.append(f'<line x1="{W-MR-120}" y1="{ly}" x2="{W-MR-104}" y2="{ly}" '
                       f'stroke="{col}" stroke-width="2"/>')
            out.append(f'<text x="{W-MR-100}" y="{ly+4}">{html.escape(name)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def svg_bars(labels, values, title="", ylabel="value", errs=None):
    ymax = max(values + ([v + (errs[i] if errs else 0) for i, v in enumerate(values)])) * 1.15
    ymin = min(0, min(values))
    out = _frame(title, 0, len(labels), ymin, ymax, "", ylabel, xticks=1)
    bw = (W - ML - MR) / len(labels) * 0.6
    for i, (lab, v) in enumerate(zip(labels, values)):
        cx = _sx(i + 0.5, 0, len(labels))
        y = _sy(v, ymin, ymax)
        y0 = _sy(0, ymin, ymax)
        out.append(f'<rect x="{cx-bw/2:.1f}" y="{min(y,y0):.1f}" width="{bw:.1f}" '
                   f'height="{abs(y0-y):.1f}" fill="{PALETTE[i % len(PALETTE)]}"/>')
        if errs:
            e = errs[i]
            out.append(f'<line x1="{cx:.1f}" y1="{_sy(v-e,ymin,ymax):.1f}" x2="{cx:.1f}" '
                       f'y2="{_sy(v+e,ymin,ymax):.1f}" stroke="#333"/>')
        out.append(f'<text x="{cx:.1f}" y="{H-MB+16:.1f}" text-anchor="middle" '
                   f'font-size="10">{html.escape(str(lab))}</text>')
        out.append(f'<text x="{cx:.1f}" y="{y-4:.1f}" text-anchor="middle" '
                   f'font-size="10">{v:.3g}</text>')
    out.append("</svg>")
    return "\n".join(out)


def svg_reliability(pred, actual, title="Reliability (calibration)"):
    """pred/actual = lists of binned mean predicted vs realized rates."""
    series = [("model", list(zip(pred, actual)))]
    lim = max(max(pred), max(actual)) * 1.05
    out = _frame(title, 0, lim, 0, lim, "mean predicted", "observed rate")
    # diagonal y=x
    out.append(f'<line x1="{_sx(0,0,lim):.1f}" y1="{_sy(0,0,lim):.1f}" '
               f'x2="{_sx(lim,0,lim):.1f}" y2="{_sy(lim,0,lim):.1f}" '
               f'stroke="#999" stroke-dasharray="4"/>')
    poly = " ".join(f"{_sx(p,0,lim):.1f},{_sy(a,0,lim):.1f}" for p, a in zip(pred, actual))
    out.append(f'<polyline points="{poly}" fill="none" stroke="#1f77b4" stroke-width="2"/>')
    for p, a in zip(pred, actual):
        out.append(f'<circle cx="{_sx(p,0,lim):.1f}" cy="{_sy(a,0,lim):.1f}" r="3" '
                   f'fill="#1f77b4"/>')
    out.append("</svg>")
    return "\n".join(out)


def svg_timeline(months, value, regime=None, title="Regime timeline",
                 xlabel="month", ylabel="value"):
    """Line of `value` over `months`, optionally shaded by integer `regime` label."""
    xs = list(range(len(months)))
    ymin, ymax = min(value), max(value)
    out = _frame(title, 0, len(months) - 1, ymin, ymax, xlabel, ylabel)
    if regime is not None:
        cols = ["#2ca02c", "#ff7f0e", "#d62728", "#9467bd"]
        for i in range(len(months)):
            r = regime[i]
            if r is None:
                continue
            x1 = _sx(i - 0.5, 0, len(months) - 1)
            x2 = _sx(i + 0.5, 0, len(months) - 1)
            out.append(f'<rect x="{x1:.1f}" y="{MT}" width="{x2-x1:.1f}" '
                       f'height="{H-MT-MB}" fill="{cols[r % len(cols)]}" '
                       f'fill-opacity="0.12"/>')
    poly = " ".join(f"{_sx(i,0,len(months)-1):.1f},{_sy(v,ymin,ymax):.1f}"
                    for i, v in enumerate(value))
    out.append(f'<polyline points="{poly}" fill="none" stroke="#1f77b4" stroke-width="2"/>')
    out.append("</svg>")
    return "\n".join(out)


def svg_hist(values, bins=10, title="Histogram", xlabel="value", ylabel="count",
             ref_uniform=False):
    """Histogram; ref_uniform draws the expected flat line (for PIT uniformity)."""
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1.0
    counts = [0] * bins
    for v in values:
        counts[min(bins - 1, int((v - lo) / rng * bins))] += 1
    ymax = max(counts) * 1.15
    out = _frame(title, lo, hi, 0, ymax, xlabel, ylabel, xticks=5)
    bw = (W - ML - MR) / bins * 0.9
    for i, c in enumerate(counts):
        cx = _sx(lo + (i + 0.5) * rng / bins, lo, hi)
        y, y0 = _sy(c, 0, ymax), _sy(0, 0, ymax)
        out.append(f'<rect x="{cx-bw/2:.1f}" y="{y:.1f}" width="{bw:.1f}" '
                   f'height="{y0-y:.1f}" fill="#1f77b4" fill-opacity="0.75"/>')
    if ref_uniform:
        exp = len(values) / bins
        ye = _sy(exp, 0, ymax)
        out.append(f'<line x1="{ML}" y1="{ye:.1f}" x2="{W-MR}" y2="{ye:.1f}" '
                   f'stroke="#d62728" stroke-dasharray="5"/>')
        out.append(f'<text x="{W-MR-4}" y="{ye-4:.1f}" text-anchor="end" '
                   f'fill="#d62728" font-size="10">uniform</text>')
    out.append("</svg>")
    return "\n".join(out)


def svg_heatmap(xlabels, ylabels, grid, title="Heatmap", xlabel="", ylabel=""):
    """grid[r][c] -> color (low=blue, high=red). For the HP (lambda x alpha) surface."""
    flat = [v for row in grid for v in row if v is not None]
    vmin, vmax = min(flat), max(flat)
    rng = (vmax - vmin) or 1.0
    nr, nc = len(ylabels), len(xlabels)
    cw = (W - ML - MR) / nc
    ch = (H - MT - MB) / nr
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
           f'font-family="sans-serif" font-size="11">',
           f'<rect width="{W}" height="{H}" fill="white"/>',
           f'<text x="{W/2}" y="20" text-anchor="middle" font-size="15" '
           f'font-weight="bold">{html.escape(title)}</text>']
    for r in range(nr):
        for c in range(nc):
            v = grid[r][c]
            if v is None:
                continue
            t = (v - vmin) / rng
            col = f'rgb({int(60+195*t)},{int(90+60*(1-abs(2*t-1)))},{int(60+195*(1-t))})'
            x, y = ML + c * cw, MT + r * ch
            out.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{cw:.1f}" height="{ch:.1f}" '
                       f'fill="{col}"/>')
            out.append(f'<text x="{x+cw/2:.1f}" y="{y+ch/2+3:.1f}" text-anchor="middle" '
                       f'font-size="9">{v:.3g}</text>')
    for c in range(nc):
        out.append(f'<text x="{ML+(c+0.5)*cw:.1f}" y="{H-MB+16:.1f}" text-anchor="middle" '
                   f'font-size="9">{html.escape(str(xlabels[c]))}</text>')
    for r in range(nr):
        out.append(f'<text x="{ML-6:.1f}" y="{MT+(r+0.5)*ch+3:.1f}" text-anchor="end" '
                   f'font-size="9">{html.escape(str(ylabels[r]))}</text>')
    out.append(f'<text x="{W/2}" y="{H-8}" text-anchor="middle">{html.escape(xlabel)}</text>')
    out.append(f'<text x="14" y="{H/2}" text-anchor="middle" '
               f'transform="rotate(-90 14 {H/2})">{html.escape(ylabel)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def write_svg(svg, path):
    import os
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
    return path


# ---------------- ASCII (terminal) ----------------
# ASCII-only ramp (cp1252-safe -- the bank PC console is not UTF-8).
_RAMP = " .:-=+*#%@"


def ascii_spark(values):
    lo, hi = min(values), max(values)
    rng = (hi - lo) or 1.0
    n = len(_RAMP) - 1
    return "".join(_RAMP[min(n, int((v - lo) / rng * n))] for v in values)


def ascii_bars(labels, values, width=40):
    hi = max(abs(v) for v in values) or 1.0
    return "\n".join(f"  {str(l)[:16]:<16} {'#'*int(abs(v)/hi*width)} {v:.4g}"
                     for l, v in zip(labels, values))


if __name__ == "__main__":
    import os
    import xml.etree.ElementTree as ET

    out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_viz_test")
    St = [1.0, 0.94, 0.89, 0.85, 0.81, 0.78, 0.75]
    Ss = [1.0, 0.92, 0.86, 0.80, 0.75, 0.71, 0.67]
    band = [(i, s - 0.03 * i, s + 0.03 * i) for i, s in enumerate(St)]
    files = {
        "st.svg": svg_lines([("base", list(enumerate(St))), ("+200bp", list(enumerate(Ss)))],
                            title="Run-off S(t)", xlabel="horizon (months)", ylabel="S(t)",
                            band=("90% band", band), ymin=0.6, ymax=1.0),
        "reliab.svg": svg_reliability([0.01, 0.03, 0.06, 0.10, 0.20],
                                      [0.012, 0.025, 0.063, 0.09, 0.21]),
        "ablation.svg": svg_bars(["EN-log", "Tenure", "Markov", "Expon"],
                                 [0.038, 0.040, 0.103, 0.134], title="Ablation OOT MAE",
                                 ylabel="MAE", errs=[0.014, 0.015, 0.030, 0.031]),
        "regime.svg": svg_timeline(list(range(24)),
                                   [50 + 30 * (i % 7 == 0) - 0.5 * i for i in range(24)],
                                   regime=[0]*8 + [1]*8 + [2]*8, title="Oil + regime"),
    }
    print("validating SVG (well-formed XML + element counts):")
    for name, svg in files.items():
        p = write_svg(svg, os.path.join(out_dir, name))
        root = ET.fromstring(svg)                      # raises if malformed
        tags = [t.tag.split('}')[-1] for t in root.iter()]
        assert "svg" in tags and svg.endswith("</svg>")
        print(f"  {name:<14} OK  elements={len(tags)}  "
              f"(polyline={tags.count('polyline')} rect={tags.count('rect')} "
              f"circle={tags.count('circle')})")
    print(f"\nSVGs -> {out_dir} (open in any browser)")
    print("\nASCII sparkline S(t):", ascii_spark(St))
    print("ASCII ablation bars:")
    print(ascii_bars(["EN-log", "Tenure", "Markov", "Expon"], [0.038, 0.040, 0.103, 0.134]))
