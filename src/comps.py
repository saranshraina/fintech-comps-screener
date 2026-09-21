"""Build the comparable-companies table, sub-sector medians, and a data-quality report."""
from __future__ import annotations
import pandas as pd, numpy as np
import universe as U
import quality as Q

METRICS = ["ev_revenue", "ev_ebitda", "pe", "ebitda_margin", "rev_growth",
           "rule_of_40", "fcf_yield"]


def _cagr(hist):
    """1-year revenue growth from the filed history."""
    vals = [v for _, v in hist if v]
    if len(vals) < 2 or not vals[1]:
        return None
    return vals[0] / vals[1] - 1.0


def _cagr_multi(hist):
    """Annualised growth over the full available history.

    Used where the single-year rate is measured off a collapsed base (e.g.
    Open Lending: FY24 revenue fell to $24m on a profit-share re-estimate, so
    FY25/FY24 prints +288% while the 3-year trend is sharply negative).
    """
    vals = [v for _, v in hist if v]
    if len(vals) < 3 or not vals[-1] or vals[-1] <= 0 or vals[0] <= 0:
        return None
    yrs = len(vals) - 1
    return (vals[0] / vals[-1]) ** (1 / yrs) - 1.0


def build_rows(raw_all):
    rows = []
    for c in U.UNIVERSE:
        t = c["ticker"]
        raw = raw_all[t]
        f = raw["fields"]
        mc, ev = f.get("market_cap"), f.get("enterprise_value")
        rev, eb, ni = f.get("revenue"), f.get("ebitda"), f.get("net_income")
        fcf = f.get("fcf")

        r = dict(
            ticker=t, company=c["name"], sub_sector=c["sub_sector"],
            coverage=c["coverage"], thesis=c["thesis"],
            price=f.get("price"), market_cap=mc, enterprise_value=ev,
            shares_out=f.get("shares_out") or ((mc / f["price"]) if (mc and f.get("price")) else None),
            capex=f.get("capex"),
            revenue=rev, ebitda=eb, net_income=ni, fcf=fcf,
            total_debt=f.get("total_debt"), cash=f.get("cash"),
            cap_tier=U.cap_tier(mc),
            # --- we RECOMPUTE every multiple; API versions kept for cross-check
            ev_revenue=(ev / rev) if (ev and rev and rev > 0) else None,
            ev_ebitda=(ev / eb) if (ev and eb and eb > 0) else None,
            pe=(mc / ni) if (mc and ni and ni > 0) else None,
            ebitda_margin=(eb / rev) if (eb is not None and rev and rev > 0) else None,
            net_margin=(ni / rev) if (ni is not None and rev and rev > 0) else None,
            fcf_yield=(fcf / mc) if (fcf is not None and mc and mc > 0) else None,
            rev_growth=_cagr(raw.get("revenue_history", [])),
            rev_cagr_multi=_cagr_multi(raw.get("revenue_history", [])),
            revenue_fy=raw.get("revenue_fy"), revenue_info=raw.get("revenue_info"),
            revenue_basis_gap=(
                abs(raw["revenue_fy"] - raw["revenue_info"]) / max(raw["revenue_fy"], raw["revenue_info"])
                if raw.get("revenue_fy") and raw.get("revenue_info") else None),
            ev_ebitda_api=f.get("ev_ebitda_api"), ev_rev_api=f.get("ev_rev_api"),
            pe_api=f.get("pe_api"),
            net_debt=((f.get("total_debt") or 0) - (f.get("cash") or 0))
                     if f.get("total_debt") is not None else None,
        )
        # Rule of 40 -- the standard software screen: growth% + margin%
        if r["rev_growth"] is not None and r["ebitda_margin"] is not None:
            r["rule_of_40"] = 100 * (r["rev_growth"] + r["ebitda_margin"])
        else:
            r["rule_of_40"] = None
        r["leverage"] = (r["net_debt"] / eb) if (r["net_debt"] is not None and eb and eb > 0) else None

        flags, suppressed = Q.assess(r, c, raw)
        if "GROWTH_OFF_DISTORTED_BASE" in flags and r["rev_cagr_multi"] is not None:
            r["rev_growth_reported"] = r["rev_growth"]
            r["rev_growth"] = r["rev_cagr_multi"]          # trend, not the optical bounce
            suppressed = [m for m in suppressed if m not in ("rev_growth", "rule_of_40")]
            if r["ebitda_margin"] is not None:
                r["rule_of_40"] = 100 * (r["rev_growth"] + r["ebitda_margin"])
        r["dq_flags"] = flags
        r["suppressed"] = suppressed
        r["severity"] = Q.worst_severity(flags)
        r["excluded"] = bool(c.get("exclude_from_comps"))
        r["exclusion_reason"] = c.get("exclusion_reason")
        r["provenance"] = raw.get("sources", {})
        rows.append(r)
    return pd.DataFrame(rows)


def medians(df):
    """Sub-sector medians, honouring metric-level suppression and exclusions."""
    out = {}
    for sub, g in df[~df.excluded].groupby("sub_sector"):
        stats = {}
        for m in METRICS:
            usable = g[~g.suppressed.apply(lambda s, m=m: m in s)][m].dropna()
            stats[m] = dict(
                median=float(usable.median()) if len(usable) else None,
                mean=float(usable.mean()) if len(usable) else None,
                n_used=int(len(usable)),
                n_total=int(len(g)),
                excluded_tickers=sorted(
                    g[g.suppressed.apply(lambda s, m=m: m in s) | g[m].isna()].ticker.tolist()),
            )
        out[sub] = stats
    return out


def quality_report(df):
    lines = []
    for _, r in df.iterrows():
        fl = r["dq_flags"]
        if not fl:
            continue
        lines.append(dict(
            ticker=r.ticker, company=r.company, severity=r.severity,
            dq_flags=fl, suppressed=r.suppressed,
            detail=[Q.SEVERITY.get(f, ("", "?"))[1] for f in fl],
        ))
    order = {"block": 0, "warn": 1, "info": 2}
    return sorted(lines, key=lambda x: order.get(x["severity"], 3))
