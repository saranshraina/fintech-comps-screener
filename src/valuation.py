"""
Valuation layer: 3-scenario DCF + 2-D sensitivity, cross-checked against comps.

DESIGN STANCE
-------------
A 5-year DCF on a free-API dataset is NOT a precision instrument, and pretending
otherwise would be the wrong signal. What it IS good for is decomposing a share
price into the assumptions required to justify it. So this module is built to
answer one question:

    "What does the market have to believe for today's price to be right,
     and how much does the answer move when those beliefs change?"

Every scenario input is anchored to something observable -- the company's own
trailing growth/margin, or the sub-sector median -- rather than typed in by
hand. The anchoring is printed alongside the output so a reviewer can attack
the assumption rather than the arithmetic.

MODEL
  FCFF_t = EBIT_t x (1 - tax) + D&A_t - capex_t - dNWC_t
  We proxy (D&A - capex - dNWC) with a single reinvestment rate applied to
  revenue, calibrated off trailing capex intensity. This is deliberately
  coarse; the sensitivity table is the real output.

  TV = FCFF_N x (1+g) / (WACC - g)          [Gordon growth]
  EV = sum(FCFF_t / (1+WACC)^t) + TV / (1+WACC)^N
  Equity = EV - net debt;  per share = Equity / diluted shares
"""
from __future__ import annotations
import numpy as np

TAX_RATE = 0.23          # blended US federal + state for these filers
YEARS = 5

# Scenario levers are deliberately NARROW. An earlier calibration
# (growth x1.5, margin +5pp, WACC -1pp, all simultaneously) produced a Bull
# case of +584% for Priority Technology -- a spread that tells a reader
# nothing except that the model is unconstrained. Stacking three favourable
# levers multiplies rather than adds, so each is now capped.
SCENARIOS = {
    "Bear": dict(growth_mult=0.50, margin_delta=-0.03, wacc_delta=+0.010, tg=0.010),
    "Base": dict(growth_mult=1.00, margin_delta= 0.00, wacc_delta= 0.000, tg=0.020),
    "Bull": dict(growth_mult=1.25, margin_delta=+0.03, wacc_delta=-0.005, tg=0.028),
}

# Which multiple is trustworthy in each sub-sector.
# In payments, revenue may be reported GROSS (incl. interchange passed through
# to networks/issuers) or NET. Global Payments prints $10.2bn gross vs $7.7bn
# net -- a 32% gap -- so EV/Revenue ranks companies by accounting policy as
# much as by value. EBITDA is reported after those passthroughs and is the
# comparable denominator. FI software is the mirror image: recurring revenue
# is clean and comparable, while GAAP EBITDA is distorted by capitalised
# software and stock comp at very different rates.
PRIMARY_MULTIPLE = {
    "Merchant Payments & Transaction Processing": "ev_ebitda",
    "Financial-Institution Software":             "ev_revenue",
}

TV_SHARE_WARN = 0.75   # >75% of value in the terminal year => DCF is a TV bet


def wacc_for(row, sub_medians=None):
    """Crude but explicit WACC build-up. Small caps carry a size premium."""
    rf, erp = 0.043, 0.055
    beta = {"large": 1.05, "mid": 1.20, "small": 1.35, "micro": 1.50}.get(row["cap_tier"], 1.25)
    size_prem = {"large": 0.000, "mid": 0.006, "small": 0.015, "micro": 0.025}.get(row["cap_tier"], 0.01)
    coe = rf + beta * erp + size_prem
    # leverage-weighted, after-tax cost of debt
    nd, mc = row.get("net_debt"), row.get("market_cap")
    if nd and mc and nd > 0:
        w_d = min(nd / (nd + mc), 0.45)
        kd = 0.065 * (1 - TAX_RATE)
        return coe * (1 - w_d) + kd * w_d
    return coe


def project(row, growth, ebitda_margin, wacc, tg, years=YEARS, reinvest_rate=None):
    """Return (EV, equity_value, per_share, dataframe-ish list of rows)."""
    rev0 = row["revenue"]
    if not rev0 or rev0 <= 0:
        return None
    # reinvestment: trailing (capex - D&A + dNWC) proxied by capex intensity
    if reinvest_rate is None:
        cap = abs(row.get("capex") or 0)
        reinvest_rate = min(max(cap / rev0, 0.01), 0.12) if rev0 else 0.04

    rows, pv = [], 0.0
    rev = rev0
    # growth fades linearly toward terminal growth over the horizon
    for t in range(1, years + 1):
        g_t = growth + (tg - growth) * (t - 1) / max(years - 1, 1)
        rev = rev * (1 + g_t)
        ebitda = rev * ebitda_margin
        da = rev * 0.035
        ebit = ebitda - da
        nopat = ebit * (1 - TAX_RATE)
        fcff = nopat + da - rev * reinvest_rate
        disc = fcff / (1 + wacc) ** t
        pv += disc
        rows.append(dict(year=t, revenue=rev, growth=g_t, ebitda=ebitda,
                         fcff=fcff, pv=disc))
    if wacc <= tg:
        return None                      # guard: Gordon model undefined
    tv = rows[-1]["fcff"] * (1 + tg) / (wacc - tg)
    pv_tv = tv / (1 + wacc) ** years
    ev = pv + pv_tv
    equity = ev - (row.get("net_debt") or 0)
    sh = row.get("shares_out") or (row["market_cap"] / row["price"] if row.get("price") else None)
    ps = equity / sh if sh else None
    return dict(ev=ev, equity=equity, per_share=ps, pv_explicit=pv, pv_terminal=pv_tv,
                tv_share=pv_tv / ev if ev else None, rows=rows,
                reinvest_rate=reinvest_rate, wacc=wacc, tg=tg,
                growth=growth, margin=ebitda_margin)


def three_scenario(row, sub_median_margin=None):
    """Bear / Base / Bull anchored on the company's own trailing figures."""
    g0 = row.get("rev_growth")
    m0 = row.get("ebitda_margin")
    # anchor: if a company's own margin is unusable (negative/missing), fall back
    # to the sub-sector median -- and say so.
    anchor_note = []
    if m0 is None or m0 <= 0.02:
        m0 = sub_median_margin
        anchor_note.append("margin anchored to sub-sector median (own trailing margin unusable)")
    if g0 is None:
        g0 = 0.05
        anchor_note.append("growth defaulted to 5% (no usable history)")
    g0 = float(np.clip(g0, -0.10, 0.35))
    if m0 is None:
        return None, anchor_note + ["no usable margin anchor -- DCF not run"]

    w0 = wacc_for(row)
    out = {}
    for name, s in SCENARIOS.items():
        res = project(row,
                      growth=g0 * s["growth_mult"],
                      ebitda_margin=float(np.clip(m0 + s["margin_delta"], 0.02, 0.60)),
                      wacc=w0 + s["wacc_delta"], tg=s["tg"])
        if res:
            res["upside"] = (res["per_share"] / row["price"] - 1) if (res.get("per_share") and row.get("price")) else None
        out[name] = res
    out["_anchors"] = dict(growth=g0, margin=m0, wacc=w0, notes=anchor_note)
    return out, anchor_note


def sensitivity(row, axis1="growth", axis2="margin", n=5, sub_median_margin=None):
    """2-D grid of implied per-share value. Returns (row_labels, col_labels, grid)."""
    g0 = row.get("rev_growth") or 0.05
    m0 = row.get("ebitda_margin")
    if m0 is None or m0 <= 0.02:
        m0 = sub_median_margin
    if m0 is None:
        return None
    g0 = float(np.clip(g0, -0.10, 0.35))
    w0 = wacc_for(row)

    grids = {
        "growth":  np.linspace(max(g0 - 0.08, -0.05), g0 + 0.08, n),
        "margin":  np.linspace(max(m0 - 0.08, 0.03), min(m0 + 0.08, 0.60), n),
        "wacc":    np.linspace(w0 - 0.02, w0 + 0.02, n),
        "tg":      np.linspace(0.015, 0.035, n),
    }
    a1, a2 = grids[axis1], grids[axis2]
    grid = np.full((n, n), np.nan)
    for i, v1 in enumerate(a1):
        for j, v2 in enumerate(a2):
            kw = dict(growth=g0, ebitda_margin=m0, wacc=w0, tg=0.025)
            kw[{"growth": "growth", "margin": "ebitda_margin",
                "wacc": "wacc", "tg": "tg"}[axis1]] = v1
            kw[{"growth": "growth", "margin": "ebitda_margin",
                "wacc": "wacc", "tg": "tg"}[axis2]] = v2
            r = project(row, **kw)
            if r and r.get("per_share"):
                grid[i, j] = r["per_share"]
    return dict(axis1=axis1, axis2=axis2, a1=a1.tolist(), a2=a2.tolist(),
                grid=grid.tolist(), price=row.get("price"))


def reverse_dcf(row, sub_median_margin=None, solve_for="tg"):
    """Solve for the assumption that makes the DCF equal the CURRENT price.

    This is the most defensible output in the module. A forward DCF on free
    data produces a number nobody should trust; a reverse DCF produces a
    *falsifiable statement* -- "at $5.83 the market is assuming terminal
    growth of x%" -- which a reviewer can agree or disagree with on the
    merits of the business rather than on the modelling.
    """
    price = row.get("price")
    if not price:
        return None
    g0 = row.get("rev_growth") or 0.05
    m0 = row.get("ebitda_margin")
    if m0 is None or m0 <= 0.02:
        m0 = sub_median_margin
    if m0 is None:
        return None
    g0 = float(np.clip(g0, -0.10, 0.35))
    w0 = wacc_for(row)

    def ps_at(x):
        kw = dict(growth=g0, ebitda_margin=m0, wacc=w0, tg=0.02)
        if solve_for == "tg":     kw["tg"] = x
        elif solve_for == "wacc": kw["wacc"] = x
        elif solve_for == "growth": kw["growth"] = x
        elif solve_for == "margin": kw["ebitda_margin"] = x
        r = project(row, **kw)
        return r["per_share"] if r and r.get("per_share") else None

    lo, hi = {"tg": (-0.15, w0 - 0.005), "wacc": (0.04, 0.35),
              "growth": (-0.25, 0.50), "margin": (0.01, 0.65)}[solve_for]
    # value is monotonically increasing in tg/growth/margin, decreasing in wacc
    increasing = solve_for != "wacc"
    for _ in range(80):
        mid = (lo + hi) / 2
        v = ps_at(mid)
        if v is None:
            lo = mid; continue
        if (v < price) == increasing:
            lo = mid
        else:
            hi = mid
    sol = (lo + hi) / 2
    v = ps_at(sol)
    ok = v is not None and abs(v - price) / price < 0.05
    return dict(solve_for=solve_for, implied=sol, check_ps=v, converged=bool(ok),
                anchors=dict(growth=g0, margin=m0, wacc=w0))


def implied_from_comps(row, med, primary=None):
    """Cross-check: what the peer-median multiples imply for this name."""
    out = {}
    nd = row.get("net_debt") or 0
    sh = row.get("shares_out")
    suppressed = set(row.get("suppressed") or [])
    for metric, driver in (("ev_ebitda", "ebitda"), ("ev_revenue", "revenue")):
        if metric in suppressed:
            continue                     # e.g. gross/net revenue ambiguity
        mult = (med.get(metric) or {}).get("median")
        base = row.get(driver)
        if mult and base and base > 0 and sh:
            out[metric] = dict(multiple=mult, implied_ev=mult * base,
                               implied_ps=(mult * base - nd) / sh,
                               is_primary=(metric == primary))
    return out
