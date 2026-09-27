"""
Screening score and the pursue / pass recommendation.

CRITERIA (defined by us, and deliberately few)
----------------------------------------------
A screen should encode a thesis, not average everything together. Ours:

  VALUE      35%  Discount to the sub-sector median on the PRIMARY multiple
                  for that sub-sector (EV/EBITDA for payments, EV/Revenue for
                  FI software). Scored within sub-sector only -- cross-sector
                  cheapness is meaningless here.
  QUALITY    30%  Trailing EBITDA margin + FCF yield. Cash conversion is what
                  separates a cheap business from a cheap-looking one.
  GROWTH     20%  Revenue growth, using the distortion-corrected figure where
                  the one-year rate is measured off a collapsed base.
  RISK      -15%  Leverage, data-quality severity, and analyst coverage.

CONFIDENCE GATE (the part that actually matters)
-------------------------------------------------
Every score is multiplied by a data-confidence factor derived from the quality
flags on that name. A company cannot rank highly on numbers we do not trust.
This is what stops the screen recommending Open Lending, which on a naive
one-year-growth basis is the single fastest grower in the universe (+288%)
and is in fact a shrinking, loss-making business whose revenue line is an
accounting estimate.

Scores are percentile ranks WITHIN sub-sector, so the output is "cheap for a
payments company", never "cheap versus a software company".
"""
from __future__ import annotations
import numpy as np
import valuation as V

WEIGHTS = dict(value=0.35, quality=0.30, growth=0.20, risk=-0.15)

# MANDATE FILTER
# The brief is screening for "investment/M&A targets". Actionability is
# therefore a real criterion, not a cosmetic one: a $45bn mega-cap is not an
# acquirable target for most mandates, and it is also the most heavily covered
# name in the universe -- precisely where an AI screen adds least. We report
# BOTH the unconstrained ranking and the ranking inside the mandate, and we
# say which one drives the recommendation.
MANDATE_MAX_MARKET_CAP = 5e9

# Severity is DERIVED from the flags below, so penalising both double-counts.
# Only the blocking case is handled here; everything else is priced per flag.
SEVERITY_PENALTY = {"clean": 0.0, "info": 0.0, "warn": 0.0, "block": 1.0}

# Two kinds of data-quality problem, which earlier versions of this file wrongly
# priced the same:
#
#   PLUMBING  - the API failed to serve a value that the filed statements
#               contain. The fallback chain recovered it and the provenance
#               column proves where it came from. The NUMBER IS FINE. Charging
#               a large penalty here punishes a company for its data vendor's
#               outage, and it was suppressing Fiserv -- the highest raw score
#               in the universe -- for a problem the pipeline had already solved.
#
#   SUBSTANCE - the reported number does not mean what it appears to mean.
#               Growth measured off a written-down base, earnings inflated by a
#               one-time gain, revenue on an incomparable basis. No amount of
#               plumbing fixes these, because the figure itself is misleading.
#
# Only SUBSTANCE flags should meaningfully move confidence.
PLUMBING_PENALTY = {
    "EV_DERIVED": 0.02,
    "API_SUMMARY_DEGRADED": 0.03,
    "API_MULTIPLE_DISAGREES": 0.02,
}
SUBSTANCE_PENALTY = {
    "GROWTH_OFF_DISTORTED_BASE": 0.25,
    "ONE_TIME_GAIN_SUSPECT": 0.25,
    "STALE_FISCAL_PERIOD": 0.20,
    "REVENUE_BASIS_AMBIGUOUS": 0.15,
    "EXTREME_MULTIPLE": 0.15,
    "IMPLAUSIBLE_MARGIN": 0.15,
    "NEGATIVE_EBITDA": 0.10,
    "NEGATIVE_EARNINGS": 0.05,
    "LOW_COVERAGE": 0.05,
}
FLAG_PENALTY = {**PLUMBING_PENALTY, **SUBSTANCE_PENALTY}


def _pct(series):
    """Percentile rank 0-1 within the group; NaN-safe."""
    s = series.astype(float)
    return s.rank(pct=True, na_option="keep")


def confidence(row):
    """0-1 multiplier. Starts at 1 and is eroded by each data-quality flag.

    Plumbing flags cost almost nothing because the fallback chain recovered the
    value and provenance records the source. Substance flags cost real weight
    because the reported figure is misleading regardless of where it came from.
    """
    c = 1.0 - SEVERITY_PENALTY.get(row.get("severity"), 0.0)
    for f in (row.get("dq_flags") or []):
        c -= FLAG_PENALTY.get(f, 0.0)
    return float(np.clip(c, 0.0, 1.0))


def confidence_breakdown(row):
    """Explain a confidence score: (plumbing_cost, substance_cost, detail)."""
    plumb = sum(PLUMBING_PENALTY.get(f, 0.0) for f in (row.get("dq_flags") or []))
    subst = sum(SUBSTANCE_PENALTY.get(f, 0.0) for f in (row.get("dq_flags") or []))
    detail = [(f, "plumbing" if f in PLUMBING_PENALTY else
                  "substance" if f in SUBSTANCE_PENALTY else "unpriced",
               FLAG_PENALTY.get(f, 0.0))
              for f in (row.get("dq_flags") or [])]
    return plumb, subst, detail


def score(df, medians):
    d = df[~df.excluded].copy()
    d["confidence"] = d.apply(confidence, axis=1)

    parts = []
    for sub, g in d.groupby("sub_sector"):
        g = g.copy()
        prim = V.PRIMARY_MULTIPLE[sub]
        med = (medians[sub][prim] or {}).get("median")

        # --- VALUE: discount to peer median on the primary multiple ---------
        # suppressed => we do not have a usable primary multiple => no value score
        usable = ~g.suppressed.apply(lambda s: prim in s)
        g["primary_multiple"] = np.where(usable, g[prim], np.nan)
        g["discount_to_peer"] = np.where(
            usable & g[prim].notna() & bool(med),
            1.0 - g[prim] / (med or np.nan), np.nan)
        g["s_value"] = _pct(g["discount_to_peer"])

        # --- QUALITY: margin + cash conversion ------------------------------
        g["s_margin"] = _pct(g["ebitda_margin"])
        g["s_fcf"] = _pct(g["fcf_yield"])
        g["s_quality"] = g[["s_margin", "s_fcf"]].mean(axis=1, skipna=True)

        # --- GROWTH ----------------------------------------------------------
        g["s_growth"] = _pct(g["rev_growth"])

        # --- RISK: leverage + flag burden ------------------------------------
        lev = g["leverage"].clip(lower=0)
        g["s_leverage"] = _pct(lev)                      # higher leverage = worse
        g["s_flagburden"] = 1.0 - g["confidence"]
        g["s_risk"] = g[["s_leverage", "s_flagburden"]].mean(axis=1, skipna=True)

        raw = (WEIGHTS["value"] * g["s_value"].fillna(0.35)
               + WEIGHTS["quality"] * g["s_quality"].fillna(0.35)
               + WEIGHTS["growth"] * g["s_growth"].fillna(0.35)
               + WEIGHTS["risk"] * g["s_risk"].fillna(0.5))
        g["score_raw"] = raw
        g["score"] = raw * g["confidence"]
        parts.append(g)

    out = __import__("pandas").concat(parts)
    return out.sort_values("score", ascending=False)


def recommend(scored, mandate_cap=MANDATE_MAX_MARKET_CAP):
    """Return (pursue, passer, unconstrained_winner).

    PURSUE is the top confidence-adjusted score *within the mandate*.

    PASS is not the lowest score -- that would usually just be the smallest or
    most obviously broken company, which is not an interesting call. It is the
    name the DATA MOST FLATTERS: the largest gap between the raw screen score
    and the confidence-adjusted score. That is the definition of a value trap
    in this framework -- a company that looks best exactly where the numbers
    are least trustworthy.
    """
    s = scored.dropna(subset=["score"]).copy()
    unconstrained = s.iloc[0]

    inside = s[s["market_cap"] < mandate_cap]
    pursue = inside.iloc[0] if len(inside) else unconstrained

    s["flattery"] = s["score_raw"] - s["score"]
    passer = s.sort_values("flattery", ascending=False).iloc[0]
    return pursue, passer, unconstrained
