"""Streamlit demo. Run: streamlit run app.py"""
import sys, os, json
sys.path[:0] = [os.path.join(os.path.dirname(__file__), d) for d in ("src", "config")]
import streamlit as st, pandas as pd, numpy as np
import universe as U, fetch, comps, valuation as V, verify as VF, score as SC


def heat(df_, ref):
    """Red->green shading without matplotlib.

    pandas' background_gradient requires matplotlib, which is not a declared
    dependency here; calling it crashed this tab. Shading is relative to the
    CURRENT SHARE PRICE, which is more useful than shading relative to the
    grid's own min/max -- the reader wants to see where the assumption set
    stops justifying the price, not which cell happens to be largest.
    """
    def css(v):
        if v != v or not ref:
            return ""
        r = v / ref - 1.0
        r = max(min(r, 1.0), -1.0)
        if r >= 0:
            a = 0.12 + 0.55 * r
            return f"background-color: rgba(34,150,83,{a:.2f})"
        a = 0.12 + 0.55 * (-r)
        return f"background-color: rgba(200,60,50,{a:.2f})"
    return df_.style.format("${:,.2f}").map(css)

st.set_page_config(page_title="Fintech Comps Screener", layout="wide")

@st.cache_data(show_spinner="Pulling fundamentals…")
def load(refresh=False):
    raw = fetch.fetch_all(U.tickers(), use_cache=not refresh, verbose=False)
    df = comps.build_rows(raw)
    med = comps.medians(df)
    return raw, df, med

st.title("AI-Assisted Company Screening — Fintech")
st.caption("Merchant Payments & Transaction Processing  vs.  Financial-Institution Software")

if st.sidebar.button("Refresh from API"):
    st.cache_data.clear()
raw, df, med = load()
scored = SC.score(df, med)
pursue, passer, uncon = SC.recommend(scored)

tabs = st.tabs(["Recommendation", "Comps", "Data quality", "Valuation", "AI layer"])

# ---------------------------------------------------------------- RECOMMEND
with tabs[0]:
    c1, c2 = st.columns(2)
    with c1:
        st.subheader(f"PURSUE — {pursue.ticker}")
        st.markdown(f"**{pursue.company}** · ${pursue.market_cap/1e9:.2f}bn · {pursue.cap_tier}-cap")
        st.metric("Confidence-adjusted score", f"{pursue.score:.3f}",
                  f"{pursue.discount_to_peer*100:+.0f}% vs peer median")
        st.write(pd.Series({"value": pursue.s_value, "quality": pursue.s_quality,
                            "growth": pursue.s_growth, "risk": pursue.s_risk,
                            "confidence": pursue.confidence}).round(2))
    with c2:
        st.subheader(f"PASS — {passer.ticker}")
        st.markdown(f"**{passer.company}** · ${passer.market_cap/1e9:.2f}bn")
        st.metric("Raw → confidence-adjusted", f"{passer.score_raw:.2f} → {passer.score:.2f}",
                  f"-{passer.score_raw-passer.score:.2f} flattery", delta_color="inverse")
        st.write("Flags: " + ", ".join(passer.dq_flags))
        st.info("Screens as the cheapest name in the universe "
                f"(value percentile {passer.s_value:.2f}); confidence {passer.confidence:.2f} "
                "drops it to last.")
    st.divider()
    st.caption(f"Unconstrained top score is {uncon.ticker} (${uncon.market_cap/1e9:.0f}bn) — "
               "excluded from the recommendation by the mandate filter "
               f"(market cap < ${SC.MANDATE_MAX_MARKET_CAP/1e9:.0f}bn).")
    st.dataframe(scored[["ticker", "company", "sub_sector", "cap_tier", "primary_multiple",
                         "discount_to_peer", "s_value", "s_quality", "s_growth",
                         "s_risk", "confidence", "score"]].round(3), width="stretch")

# --------------------------------------------------------------------- COMPS
with tabs[1]:
    for sub, g in df.groupby("sub_sector"):
        st.subheader(sub)
        st.caption(f"Primary multiple: **{V.PRIMARY_MULTIPLE[sub]}**")
        show = g[["ticker", "company", "cap_tier", "market_cap", "enterprise_value",
                  "revenue", "ebitda", "ev_revenue", "ev_ebitda", "pe",
                  "ebitda_margin", "rev_growth", "fcf_yield", "severity", "excluded"]].copy()
        for c in ("market_cap", "enterprise_value", "revenue", "ebitda"):
            show[c] = (show[c] / 1e6).round(0)
        st.dataframe(show.round(3), width="stretch")
        m = med.get(sub, {})
        st.write("**Medians** " + " · ".join(
            f"{k}: {(v['median'] if v['median'] is not None else float('nan')):.2f} (n={v['n_used']}/{v['n_total']})"
            for k, v in m.items()))

# -------------------------------------------------------------- DATA QUALITY
with tabs[2]:
    st.subheader("Handling policy")
    st.markdown("""
| Tier | Response | Example |
|---|---|---|
| 1 | Flag, keep in median | `EV_DERIVED` — value is real, pedigree noted |
| 2 | Flag, drop **that metric** from the median, keep the row | `NEGATIVE_EBITDA` — EV/EBITDA is meaningless, EV/Revenue is fine |
| 3 | Exclude the company, with a written reason | `BUSINESS_MODEL_DRIFT` — i3 Verticals |

**We exclude the metric, not the company.** Dropping a whole row because one
field is unusable throws away good data.
""")
    for q in comps.quality_report(df):
        with st.expander(f"[{q['severity'].upper()}] {q['ticker']} — {q['company']}"):
            for f, d in zip(q["dq_flags"], q["detail"]):
                st.markdown(f"- **{f}** — {d}")
            if q["suppressed"]:
                st.warning("Suppressed from medians: " + ", ".join(q["suppressed"]))
    st.subheader("Provenance")
    prov = pd.DataFrame({t: raw[t]["sources"] for t in raw}).T
    st.dataframe(prov[[c for c in ("revenue", "ebitda", "net_income", "market_cap",
                                   "enterprise_value", "fcf") if c in prov.columns]],
                 width="stretch")

# ----------------------------------------------------------------- VALUATION
with tabs[3]:
    tk = st.selectbox("Company", [t for t in df[~df.excluded].ticker])
    row = df[df.ticker == tk].iloc[0].to_dict()
    smm = (med[row["sub_sector"]]["ebitda_margin"] or {}).get("median")
    sc, notes = V.three_scenario(row, sub_median_margin=smm)
    if sc:
        a = sc["_anchors"]
        st.caption(f"Anchors — growth {a['growth']*100:.1f}% · margin {a['margin']*100:.1f}% "
                   f"· WACC {a['wacc']*100:.1f}% · price ${row['price']:.2f}")
        cols = st.columns(3)
        for col, name in zip(cols, ("Bear", "Base", "Bull")):
            s_ = sc.get(name)
            if s_:
                col.metric(name, f"${s_['per_share']:.2f}",
                           f"{s_['upside']*100:+.0f}%" if s_.get("upside") else "")
                col.caption(f"TV = {s_['tv_share']*100:.0f}% of EV")
        rv = V.reverse_dcf(row, sub_median_margin=smm, solve_for="tg")
        if rv and rv["converged"]:
            st.success(f"**Reverse DCF** — at \\${row['price']:.2f} the market is implying "
                       f"terminal growth of **{rv['implied']*100:+.1f}%**.")
        else:
            st.warning("**Reverse DCF** — no solution: the price cannot be explained by "
                       "terminal growth alone.")
        st.subheader("Sensitivity — implied value per share")
        ax1 = st.selectbox("Rows", ["growth", "margin", "wacc", "tg"], 0)
        ax2 = st.selectbox("Columns", ["margin", "growth", "wacc", "tg"], 0)
        if ax1 != ax2:
            sens = V.sensitivity(row, ax1, ax2, sub_median_margin=smm)
            if sens:
                g = pd.DataFrame(sens["grid"],
                                 index=[f"{v*100:.1f}%" for v in sens["a1"]],
                                 columns=[f"{v*100:.1f}%" for v in sens["a2"]])
                st.dataframe(heat(g, row["price"]), width="stretch")
                st.caption(f"Rows = {ax1}, columns = {ax2}. Shaded against the current "
                           f"price of ${row['price']:.2f} — green = assumption set "
                           f"supports a value above today's price.")

# ------------------------------------------------------------------ AI LAYER
with tabs[4]:
    res = VF.load()
    vres = VF.verify(df)
    summ = VF.summarise(vres)
    st.metric("Claims verified", sum(summ.values()),
              f"{summ.get('FAIL',0)} failed", delta_color="inverse")
    st.subheader("Claims that did not survive contact with the data")
    for r in [x for x in vres if x["status"] == "FAIL"]:
        st.error(f"**{r['id']}** — {r['text']}\n\n→ {r['detail']}")
    st.subheader("Qualitative layer")
    tk2 = st.selectbox("Company ", [t for t in df.ticker])
    e = res.get(tk2, {})
    if e:
        st.markdown(f"**Business model** — {e.get('business_model','')}")
        st.markdown(f"**Value driver** — {e.get('value_driver','')}")
        st.markdown(f"**Key risk** — {e.get('key_risk','')}")
        st.caption(f"Model confidence: {e.get('confidence','?')}")
    st.divider()
    st.caption(res["_meta"]["honesty_note"])
