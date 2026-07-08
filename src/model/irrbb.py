"""irrbb.py -- turn a behavioral run-off B(t) into the IRRBB numbers ALM reports:
ΔEVE (economic value of equity) and ΔNII (net interest income), under the ±200bp
parallel shock AND the six Basel/EBA scenarios. Pure stdlib.

The behavioral model gives the RUN-OFF PROFILE B(t) (fraction of the deposit book still
present at month t). That profile IS the notional repricing/amortization schedule of the
non-maturing deposit (NMD): the amount running off in month t reprices at the t-tenor rate.
From there:

  cash flow (principal) in month t : CF_t = B0 * (B(t-1) - B(t))          (money leaving)
  EVE of the deposit (a LIABILITY) : PV_liab = sum_t CF_t * DF(t)
  ΔEVE_equity(shock)               : = PV_liab(base) - PV_liab(shocked)
                                       (rate UP -> DF down -> owe less in PV -> equity UP)
  ΔNII_1y(shock)                   : = -beta * shock * (balance repricing within 1y)
                                       (deposit cost rises with rates on the repriceable part;
                                        beta = deposit pass-through, ~0 for non-remunerated DAV)

Curve: data-poor (Algeria), so a FLAT base curve at the anchor short rate + the standard
EBA shock SHAPES is the defensible choice; shock sizes are configurable (the per-currency
EBA calibration is a placeholder until the official DZD numbers are set). Everything here
is an ALM overlay on top of the (data-driven) behavioral run-off -- see the module docstring
of runoff_book for the "body = data, tail = scenario" split.

Sign convention: ΔEVE_equity > 0 means the shock INCREASES equity value (favourable);
deposits behave like long cheap funding, so a rate-up shock is favourable (large +ΔEVE).
"""
from __future__ import annotations

import math

# EBA/BCBS shock SHAPES use an exponential decay with x=4 years for the short-rate factor.
EBA_X = 4.0

# Default shock sizes in basis points (PLACEHOLDER pending the official per-currency EBA
# calibration; "other currencies" order of magnitude). parallel / short / long.
DEFAULT_SHOCKS_BP = {"parallel": 200.0, "short": 250.0, "long": 100.0}

# Per-book deposit beta (pass-through of a market-rate move to the client rate). Non-
# remunerated demand accounts ~0 (rate stays 0 -> cost insensitive); savings reprice more.
# ALM assumptions, overridable.
DEFAULT_BETA = {"vue_dinars": 0.0, "vue_devises": 0.0, "vue_bu": 0.0, "vue_other": 0.0,
                "decouverts": 0.0, "hb_engagement": 0.0, "epargne": 0.40, "garantie": 0.0}


def eba_shock_fns(shocks_bp=None):
    """Return {scenario_name: f(t_years)->rate shock in DECIMAL} for the 6 EBA scenarios
    plus the two plain ±200bp parallels. t in YEARS."""
    s = dict(DEFAULT_SHOCKS_BP)
    if shocks_bp:
        s.update(shocks_bp)
    par, sh, lo = s["parallel"] / 1e4, s["short"] / 1e4, s["long"] / 1e4

    def short_f(t):                     # decays from the short end
        return math.exp(-t / EBA_X)

    def long_f(t):                      # builds up toward the long end
        return 1.0 - math.exp(-t / EBA_X)

    return {
        "parallel_up":   lambda t: +par,
        "parallel_down": lambda t: -par,
        "short_up":      lambda t: +sh * short_f(t),
        "short_down":    lambda t: -sh * short_f(t),
        # EBA steepener/flattener combine the short (down/up) and long (up/down) factors
        "steepener":     lambda t: -0.65 * sh * short_f(t) + 0.90 * lo * long_f(t),
        "flattener":     lambda t: +0.80 * sh * short_f(t) - 0.60 * lo * long_f(t),
        # the two supervisory parallels reported alongside
        "up_200bp":      lambda t: +0.02,
        "down_200bp":    lambda t: -0.02,
    }


def nmd_cashflows(B0, B_runoff):
    """Principal run-off cash flows per month from the run-off profile B(0..H) (B[0]=1).
    CF_t = B0*(B[t-1]-B[t]) for t=1..H, plus the residual B0*B[H] at the horizon end
    (the still-present tail is modelled as repricing at the horizon = conservative)."""
    H = len(B_runoff) - 1
    cf = [0.0] * (H + 1)
    for t in range(1, H + 1):
        cf[t] = B0 * (B_runoff[t - 1] - B_runoff[t])
    cf[H] += B0 * B_runoff[H]                # residual tail at the last bucket
    return cf


def flat_curve(r):
    """A constant base curve r(t)=r (the data-poor default)."""
    return lambda t: r


def build_curve(points):
    """A real base curve from (tenor_years, rate_decimal) POINTS -- linear interpolation in
    tenor, flat-extrapolated beyond the first/last point. For Algeria the only real points
    are the short end (money-market ~0.1y, T-bill ~1y, policy anchor) pulled from IMF; there
    is NO liquid DZD curve past a couple of years, so we flat-extrapolate the long end
    (honest: the tail is an assumption, not a market)."""
    pts = sorted((float(t), float(r)) for t, r in points if r is not None)
    if not pts:
        return flat_curve(0.03)
    if len(pts) == 1:
        return flat_curve(pts[0][1])

    def f(t):
        if t <= pts[0][0]:
            return pts[0][1]
        if t >= pts[-1][0]:
            return pts[-1][1]
        for k in range(1, len(pts)):
            if t <= pts[k][0]:
                t0, r0 = pts[k - 1]
                t1, r1 = pts[k]
                return r0 + (r1 - r0) * (t - t0) / (t1 - t0)
        return pts[-1][1]
    return f


def _as_curve(base):
    """Accept either a scalar base rate (-> flat) or a callable base curve f(t_years)->rate."""
    return base if callable(base) else flat_curve(base)


def discount_factors(base, shock_fn, H, monthly=True):
    """DF(t) = exp(-(base_curve(t) + shock(t)) * t), t in years, t=0..H months. `base` is a
    scalar (flat curve) OR a callable term-structure f(t_years)->rate (from build_curve)."""
    curve = _as_curve(base)
    dfs = [1.0] * (H + 1)
    for t in range(1, H + 1):
        yr = t / 12.0 if monthly else float(t)
        r = curve(yr) + shock_fn(yr)
        dfs[t] = math.exp(-r * yr)
    return dfs


def pv(cashflows, dfs):
    return sum(cashflows[t] * dfs[t] for t in range(len(cashflows)))


def delta_eve(B0, B_runoff, base_rate, scenarios=None, shocks_bp=None):
    """ΔEVE_equity per scenario for a single deposit book (a liability).
    Returns {scenario: {'pv_base','pv_shock','delta_eve','delta_eve_pct'}}.
    delta_eve = pv_base - pv_shock (equity gain when the liability PV falls)."""
    scenarios = scenarios or eba_shock_fns(shocks_bp)
    cf = nmd_cashflows(B0, B_runoff)
    H = len(B_runoff) - 1
    base_df = discount_factors(base_rate, lambda t: 0.0, H)
    pv_base = pv(cf, base_df)
    out = {}
    for name, fn in scenarios.items():
        pv_shock = pv(cf, discount_factors(base_rate, fn, H))
        d = pv_base - pv_shock
        out[name] = {"pv_base": pv_base, "pv_shock": pv_shock, "delta_eve": d,
                     "delta_eve_pct": (100.0 * d / pv_base if pv_base else 0.0)}
    return out


def repricing_within(B0, B_runoff, months=12):
    """Balance that runs off (reprices) within `months` = B0*(1 - B(months))."""
    m = min(months, len(B_runoff) - 1)
    return B0 * (1.0 - B_runoff[m])


def delta_nii(B0, B_runoff, beta, shock_bp=200.0, horizon_m=12):
    """1y (default) ΔNII for one book under a parallel shock. Deposits are a cost:
    a rate rise lifts the client rate by beta*shock on the part that reprices within the
    horizon -> interest expense up -> NII down (negative for an up shock)."""
    repr_bal = repricing_within(B0, B_runoff, horizon_m)
    return -beta * (shock_bp / 1e4) * repr_bal


def book_irrbb(books, base_rate, shocks_bp=None, betas=None, nii_horizon_m=12):
    """Aggregate IRRBB across behavioral books.
    `books` = {key: {'B0': balance_kda, 'B': run-off profile, 'beta'?: float}}.
    Returns per-book + total ΔEVE (all scenarios) and ΔNII (±200bp)."""
    betas = betas or DEFAULT_BETA
    scen = eba_shock_fns(shocks_bp)
    per_book, totals = {}, {}
    nii = {"up_200bp": 0.0, "down_200bp": 0.0}
    for k, b in books.items():
        beta = b.get("beta", betas.get(k, 0.0))
        de = delta_eve(b["B0"], b["B"], base_rate, scenarios=scen)
        n_up = delta_nii(b["B0"], b["B"], beta, +200.0, nii_horizon_m)
        n_dn = delta_nii(b["B0"], b["B"], beta, -200.0, nii_horizon_m)
        per_book[k] = {"beta": beta, "delta_eve": {s: de[s]["delta_eve"] for s in de},
                       "delta_nii": {"up_200bp": n_up, "down_200bp": n_dn},
                       "repricing_1y": repricing_within(b["B0"], b["B"], nii_horizon_m)}
        for s in de:
            totals[s] = totals.get(s, 0.0) + de[s]["delta_eve"]
        nii["up_200bp"] += n_up
        nii["down_200bp"] += n_dn
    worst = min(totals, key=totals.get) if totals else None
    # store a SERIALIZABLE representative rate (curve at 1y if a curve was passed)
    rep_rate = _as_curve(base_rate)(1.0)
    return {"per_book": per_book, "total_delta_eve": totals, "total_delta_nii": nii,
            "worst_eve_scenario": worst, "base_rate_1y": rep_rate,
            "scenarios": list(scen.keys())}


# --------------------------------------------------------------------------- #
def _self_test():
    # a sticky deposit book: slow linear-ish run-off over 30y, B0=1,000,000 KDA
    H = 360
    B = [max(0.0, 1.0 - 0.7 * (t / H)) for t in range(H + 1)]   # 1 -> 0.30 residual
    B0 = 1_000_000.0
    base = 0.03

    cf = nmd_cashflows(B0, B)
    assert abs(sum(cf) - B0) < 1e-6, f"cash flows must sum to notional: {sum(cf)}"

    de = delta_eve(B0, B, base)
    # rate UP (parallel_up / up_200bp) -> liability PV down -> equity UP -> ΔEVE > 0
    assert de["up_200bp"]["delta_eve"] > 0, de["up_200bp"]
    assert de["parallel_up"]["delta_eve"] > 0
    # rate DOWN -> ΔEVE < 0
    assert de["down_200bp"]["delta_eve"] < 0, de["down_200bp"]
    # PV is CONVEX in the shock -> the down-shock gain exceeds the up-shock loss in
    # magnitude, so ΔEVE_up + ΔEVE_down < 0 (a real financial property, not symmetry)
    assert de["up_200bp"]["delta_eve"] + de["down_200bp"]["delta_eve"] < 0
    assert abs(de["down_200bp"]["delta_eve"]) > abs(de["up_200bp"]["delta_eve"])

    # NII: remunerated book (beta>0) loses NII on an up shock; non-remunerated DAV ~0
    nii_dav = delta_nii(B0, B, beta=0.0, shock_bp=200.0)
    nii_sav = delta_nii(B0, B, beta=0.4, shock_bp=200.0)
    assert nii_dav == 0.0, nii_dav
    assert nii_sav < 0.0, nii_sav

    # 3-point term-structure curve: interp short end, flat long end
    curve = build_curve([(0.1, 0.03), (0.25, 0.035), (1.0, 0.045)])
    assert abs(curve(0.1) - 0.03) < 1e-9 and abs(curve(1.0) - 0.045) < 1e-9
    assert 0.03 < curve(0.5) < 0.045                  # interpolated short end
    assert abs(curve(10.0) - 0.045) < 1e-9            # flat-extrapolated long end
    de_curve = delta_eve(B0, B, curve)                # curve-based EVE still well-signed
    assert de_curve["up_200bp"]["delta_eve"] > 0 and de_curve["down_200bp"]["delta_eve"] < 0

    # aggregate two books (flat base)
    books = {"vue_dinars": {"B0": 800_000.0, "B": B},
             "epargne": {"B0": 200_000.0, "B": B}}
    res = book_irrbb(books, base)
    assert "base_rate_1y" in res                       # serializable (no raw callable)
    assert set(res["total_delta_eve"]) >= {"parallel_up", "parallel_down", "steepener",
                                           "flattener", "short_up", "short_down"}
    assert res["total_delta_nii"]["up_200bp"] < 0     # epargne beta drives it negative
    # worst scenario is a real key
    assert res["worst_eve_scenario"] in res["total_delta_eve"]

    print("irrbb self-test PASSED")
    print(f"  dEVE +200bp = {de['up_200bp']['delta_eve']:,.0f} KDA "
          f"({de['up_200bp']['delta_eve_pct']:+.2f}%)  | "
          f"-200bp = {de['down_200bp']['delta_eve']:,.0f}")
    print(f"  worst EBA scenario (book) = {res['worst_eve_scenario']} "
          f"({res['total_delta_eve'][res['worst_eve_scenario']]:,.0f} KDA)")
    print(f"  dNII 1y +200bp (epargne beta 0.4) = {nii_sav:,.0f} KDA")


if __name__ == "__main__":
    _self_test()
