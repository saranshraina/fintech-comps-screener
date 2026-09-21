"""
Data-quality engine.

THE HANDLING POLICY (this is the part the exercise is really about)
-------------------------------------------------------------------
Free fundamental data is wrong in three different ways, and they need three
different responses. Collapsing them into "drop the row" destroys information.

  TIER 1 - FLAG, KEEP IN MEDIAN
      Value is real but noteworthy (derived EV, low analyst coverage).
      The number is usable; the reader just needs to know its pedigree.

  TIER 2 - FLAG, EXCLUDE *THIS METRIC* FROM THE MEDIAN, KEEP THE ROW
      The metric is mathematically defined but economically meaningless:
      a negative EV/EBITDA is not a "cheap" multiple, it is a category error,
      and letting it into a median corrupts the benchmark for every other
      company. But the same company's EV/Revenue is perfectly good.
      *** We exclude the METRIC, not the COMPANY. ***

  TIER 3 - EXCLUDE THE COMPANY, WITH A WRITTEN REASON
      Reserved for business-model mismatch, not for messy numbers.
      Only i3 Verticals qualifies (see config/universe.py).

We never silently impute. Where a value is estimated, it is tagged `derived`
by the fetch layer and surfaced in the provenance column.
"""

# metric-level suppression: flag -> metrics that must not enter a median
SUPPRESS = {
    "NEGATIVE_EBITDA":      ["ev_ebitda", "ebitda_margin"],
    "MISSING_EBITDA":       ["ev_ebitda", "ebitda_margin"],
    "NEGATIVE_EARNINGS":    ["pe"],
    "ONE_TIME_GAIN_SUSPECT":["pe"],
    "IMPLAUSIBLE_MARGIN":   ["ebitda_margin", "ev_ebitda"],
    "EXTREME_MULTIPLE":     ["pe", "ev_ebitda"],
    "GROWTH_OFF_DISTORTED_BASE": ["rev_growth", "rule_of_40"],
    "REVENUE_BASIS_AMBIGUOUS":   ["ev_revenue"],
}

SEVERITY = {
    "EV_DERIVED":            ("info",  "Enterprise value computed by us (mcap + debt - cash); API did not supply it."),
    "API_SUMMARY_DEGRADED":  ("warn",  "API summary endpoint returned no revenue/EBITDA; values recovered from filed statements."),
    "LOW_COVERAGE":          ("info",  "Thinly covered name; fewer analysts, higher risk of stale or unreconciled data."),
    "MISSING_EBITDA":        ("warn",  "No EBITDA available from any source."),
    "NEGATIVE_EBITDA":       ("warn",  "EBITDA is negative; EV/EBITDA is not a meaningful multiple."),
    "NEGATIVE_EARNINGS":     ("warn",  "Net income is negative; P/E is not a meaningful multiple."),
    "ONE_TIME_GAIN_SUSPECT": ("warn",  "Net margin implausibly high for this business model; earnings likely include a one-time gain, so trailing P/E understates the true multiple."),
    "IMPLAUSIBLE_MARGIN":    ("warn",  "EBITDA margin outside the plausible band for this business model."),
    "EXTREME_MULTIPLE":      ("warn",  "Multiple is a far outlier; likely a trough-earnings or definitional artifact rather than a valuation signal."),
    "API_MULTIPLE_DISAGREES":("warn",  "Our recomputed multiple differs from the API's precomputed one by >15%; we use ours and show both."),
    "STALE_FISCAL_PERIOD":   ("warn",  "Most recent fiscal period is over 15 months old."),
    "BUSINESS_MODEL_DRIFT":  ("block", "Company no longer operates the business model it was screened into."),
    "GROWTH_OFF_DISTORTED_BASE": ("warn",
        "Prior-year revenue was itself collapsed, so the growth rate measures recovery off a "
        "distorted base rather than underlying expansion. Multi-year trend used instead."),
    "REVENUE_BASIS_AMBIGUOUS": ("warn",
        "Filed-statement revenue and API summary revenue differ by >20%, which in payments "
        "almost always means one is GROSS (incl. interchange/network fees passed through to "
        "third parties) and the other NET. EV/Revenue is not comparable across bases."),
}

# plausible trailing EBITDA-margin bands by sub-sector
MARGIN_BAND = {
    "Merchant Payments & Transaction Processing": (-0.30, 0.60),
    "Financial-Institution Software":             (-0.50, 0.55),
}
# net margin above this implies a one-time gain for these business models
ONE_TIME_GAIN_NET_MARGIN = 0.35
REVENUE_BASIS_GAP = 0.20      # gross-vs-net presentation threshold
DISTORTED_BASE_RATIO = 0.60   # prior year < 60% of the year before it => V-shaped base
EXTREME_PE = 120.0
EXTREME_EV_EBITDA = 60.0


def assess(row, meta, raw):
    """Return (flags, suppressed_metrics) for one company row."""
    flags = []

    if meta.get("exclude_from_comps"):
        flags.append("BUSINESS_MODEL_DRIFT")
    if meta.get("coverage") == "low":
        flags.append("LOW_COVERAGE")

    src = raw.get("sources", {})
    if src.get("enterprise_value") == "derived":
        flags.append("EV_DERIVED")
    # The Fiserv case: the summary endpoint returned a market cap but no
    # revenue/EBITDA/industry at all, while the filed statements were complete.
    # Detect it on the *absence of the summary value*, not on the source tag,
    # so the check survives refactors of the resolution order.
    if raw.get("revenue_info") is None and row.get("revenue") is not None:
        flags.append("API_SUMMARY_DEGRADED")

    eb, ni, rev = row.get("ebitda"), row.get("net_income"), row.get("revenue")

    if eb is None:
        flags.append("MISSING_EBITDA")
    elif eb < 0:
        flags.append("NEGATIVE_EBITDA")

    if ni is not None and ni < 0:
        flags.append("NEGATIVE_EARNINGS")
    if ni is not None and rev and ni / rev > ONE_TIME_GAIN_NET_MARGIN:
        flags.append("ONE_TIME_GAIN_SUSPECT")

    m = row.get("ebitda_margin")
    lo, hi = MARGIN_BAND.get(meta["sub_sector"], (-1.0, 1.0))
    if m is not None and not (lo <= m <= hi):
        flags.append("IMPLAUSIBLE_MARGIN")

    if (row.get("pe") is not None and row["pe"] > EXTREME_PE) or \
       (row.get("ev_ebitda") is not None and row["ev_ebitda"] > EXTREME_EV_EBITDA):
        flags.append("EXTREME_MULTIPLE")

    # cross-check our arithmetic against the API's own precomputed multiple
    for ours, theirs in (("ev_ebitda", "ev_ebitda_api"), ("ev_revenue", "ev_rev_api")):
        a, b = row.get(ours), row.get(theirs)
        if a and b and b != 0 and abs(a - b) / abs(b) > 0.15:
            flags.append("API_MULTIPLE_DISAGREES")
            break

    # --- gross vs. net revenue presentation ------------------------------
    r_fy, r_info = raw.get("revenue_fy"), raw.get("revenue_info")
    if r_fy and r_info and abs(r_fy - r_info) / max(r_fy, r_info) > REVENUE_BASIS_GAP:
        flags.append("REVENUE_BASIS_AMBIGUOUS")

    # --- growth measured off a collapsed base ----------------------------
    hist = [v for _, v in raw.get("revenue_history", []) if v]
    if len(hist) >= 3 and hist[1] and hist[2]:
        if hist[1] / hist[2] < DISTORTED_BASE_RATIO:
            flags.append("GROWTH_OFF_DISTORTED_BASE")

    p = raw.get("revenue_period")
    if p:
        import datetime as dt
        try:
            age = (dt.date.today() - dt.date.fromisoformat(p[-10:])).days
            if age > 460:
                flags.append("STALE_FISCAL_PERIOD")
        except ValueError:
            pass

    flags = list(dict.fromkeys(flags))
    suppressed = sorted({m for f in flags for m in SUPPRESS.get(f, [])})
    return flags, suppressed


def worst_severity(flags):
    order = {"info": 0, "warn": 1, "block": 2}
    s = [SEVERITY.get(f, ("info", ""))[0] for f in flags]
    return max(s, key=lambda x: order[x]) if s else "clean"
