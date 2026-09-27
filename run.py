#!/usr/bin/env python3
"""Entry point: fetch -> comps -> quality -> valuation -> score -> outputs/"""
import sys, os, json, argparse
sys.path[:0] = [os.path.join(os.path.dirname(__file__), d) for d in ("src", "config")]
import pandas as pd
import universe as U, fetch, comps, valuation as V, verify as VF, score as SC

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="bypass the disk cache")
    a = ap.parse_args()

    print("=" * 78); print("STEP 1  FETCH"); print("=" * 78)
    raw = fetch.fetch_all(U.tickers(), use_cache=not a.refresh)

    print("\n" + "=" * 78); print("STEP 2  COMPS TABLE"); print("=" * 78)
    df = comps.build_rows(raw)
    os.makedirs("outputs", exist_ok=True)
    df.drop(columns=["provenance"]).to_csv("outputs/comps_table.csv", index=False)

    pd.set_option("display.width", 250, "display.max_columns", 50)
    show = ["ticker", "cap_tier", "market_cap", "revenue", "ebitda",
            "ev_revenue", "ev_ebitda", "pe", "ebitda_margin", "rev_growth", "severity"]
    for sub, g in df.groupby("sub_sector"):
        print(f"\n--- {sub} ---")
        d = g[show].copy()
        for c in ("market_cap", "revenue", "ebitda"):
            d[c] = (d[c] / 1e6).round(0)
        for c in ("ev_revenue", "ev_ebitda", "pe"):
            d[c] = d[c].round(1)
        for c in ("ebitda_margin", "rev_growth"):
            d[c] = (d[c] * 100).round(1)
        print(d.to_string(index=False))

    print("\n" + "=" * 78); print("STEP 3  SUB-SECTOR MEDIANS"); print("=" * 78)
    med = comps.medians(df)
    for sub, st in med.items():
        print(f"\n{sub}")
        for m, s in st.items():
            v = "  n/a" if s["median"] is None else f"{s['median']:7.2f}"
            thin = "  << THIN SAMPLE" if 0 < s["n_used"] < 5 else ""
            print(f"   {m:<14} median {v}   (n={s['n_used']}/{s['n_total']})"
                  + (f"  dropped: {','.join(s['excluded_tickers'])}" if s["excluded_tickers"] else "")
                  + thin)
    json.dump(med, open("outputs/medians.json", "w"), indent=1)

    print("\n" + "=" * 78); print("STEP 4  DATA-QUALITY REPORT"); print("=" * 78)
    qr = comps.quality_report(df)
    for q in qr:
        print(f"\n[{q['severity'].upper():<5}] {q['ticker']:<6} {q['company']}")
        for f, d in zip(q["dq_flags"], q["detail"]):
            print(f"         - {f}: {d}")
        if q["suppressed"]:
            print(f"         -> suppressed from medians: {', '.join(q['suppressed'])}")
    json.dump(qr, open("outputs/quality_report.json", "w"), indent=1)
    print("\n" + "=" * 78); print("STEP 5  3-SCENARIO DCF"); print("=" * 78)
    val = {}
    for _, r in df[~df.excluded].iterrows():
        row = r.to_dict()
        smm = (med[row["sub_sector"]]["ebitda_margin"] or {}).get("median")
        sc, notes = V.three_scenario(row, sub_median_margin=smm)
        if not sc:
            print(f"\n{row['ticker']:<6} DCF not run: {'; '.join(notes)}"); continue
        a = sc["_anchors"]
        print(f"\n{row['ticker']:<6} {row['company']:<30} px ${row['price']:.2f}"
              f"   [anchors g={a['growth']*100:.1f}% m={a['margin']*100:.1f}% WACC={a['wacc']*100:.1f}%]")
        if a["notes"]:
            for n_ in a["notes"]:
                print(f"         ! {n_}")
        for name in ("Bear", "Base", "Bull"):
            s_ = sc.get(name)
            if not s_:
                print(f"         {name:<5} n/a"); continue
            up = s_["upside"]
            print(f"         {name:<5} ${s_['per_share']:8.2f}  "
                  f"{'+' if up and up>0 else ''}{up*100 if up is not None else float('nan'):6.1f}%   "
                  f"TV={s_['tv_share']*100:4.0f}% of EV")
        # terminal-value dominance check
        tvs = [s_["tv_share"] for s_ in (sc.get("Base"),) if s_]
        if tvs and tvs[0] > V.TV_SHARE_WARN:
            print(f"         ! {tvs[0]*100:.0f}% of DCF value sits in the terminal year "
                  f"-- this is a terminal-value bet, not a cash-flow valuation")

        prim = V.PRIMARY_MULTIPLE.get(row["sub_sector"])
        cc = V.implied_from_comps(row, med[row["sub_sector"]], primary=prim)
        for k, v in cc.items():
            tag = "  <- PRIMARY for this sub-sector" if v["is_primary"] else "  (secondary)"
            print(f"         comps-implied via {k:<11} ${v['implied_ps']:8.2f}"
                  f"  (peer median {v['multiple']:.2f}x){tag}")
        for k in ("ev_ebitda", "ev_revenue"):
            if k not in cc and k in (row.get("suppressed") or []):
                print(f"         comps-implied via {k:<11} suppressed (data-quality flag)")

        rv = V.reverse_dcf(row, sub_median_margin=smm, solve_for="tg")
        if rv and rv["converged"]:
            print(f"         REVERSE DCF: at ${row['price']:.2f} the market implies terminal "
                  f"growth of {rv['implied']*100:+.1f}%  (vs 2.0% base assumption)")
        elif rv:
            print(f"         REVERSE DCF: no solution in range -- price is outside what "
                  f"terminal growth alone can explain (implied tg <= {rv['implied']*100:+.1f}%)")

        # reconciliation: DCF vs the primary comps multiple
        base_ps = sc["Base"]["per_share"] if sc.get("Base") else None
        prim_ps = (cc.get(prim) or {}).get("implied_ps")
        if base_ps and prim_ps:
            spread = base_ps / prim_ps - 1
            if abs(spread) > 0.50:
                print(f"         ! DCF base (${base_ps:.2f}) and primary comps (${prim_ps:.2f}) "
                      f"disagree by {spread*100:+.0f}% -- treat the DCF as low-confidence here")

        val[row["ticker"]] = dict(
            scenarios={k: {kk: vv for kk, vv in s_.items() if kk != "rows"}
                       for k, s_ in sc.items() if k != "_anchors" and s_},
            anchors=a, comps_implied=cc, price=row["price"], reverse_dcf=rv,
            primary_multiple=prim)
    json.dump(val, open("outputs/valuation.json", "w"), indent=1, default=float)

    print("\n" + "=" * 78); print("STEP 6  AI-LAYER VERIFICATION"); print("=" * 78)
    vres = VF.verify(df)
    summ = VF.summarise(vres)
    print("  " + "  ".join(f"{k}={v}" for k, v in sorted(summ.items())))
    fails = [r for r in vres if r["status"] in ("FAIL", "UNTESTABLE")]
    if fails:
        print("\n  --- AI claims that did NOT survive contact with the data ---")
        for r in fails:
            print(f"  [{r['status']:<10}] {r['id']:<8} {r['text']}")
            print(f"               -> {r['detail']}")
    json.dump(vres, open("outputs/ai_verification.json", "w"), indent=1, default=float)

    print("\n" + "=" * 78); print("STEP 7  SCREEN & RECOMMENDATION"); print("=" * 78)
    sc_df = SC.score(df, med)
    cols = ["ticker", "sub_sector", "cap_tier", "primary_multiple", "discount_to_peer",
            "s_value", "s_quality", "s_growth", "s_risk", "confidence", "score_raw", "score"]
    for sub, g in sc_df.groupby("sub_sector"):
        print(f"\n--- {sub}  (primary multiple: {V.PRIMARY_MULTIPLE[sub]}) ---")
        t = g[cols].drop(columns=["sub_sector"]).copy()
        for c in ("discount_to_peer", "s_value", "s_quality", "s_growth", "s_risk",
                  "confidence", "score_raw", "score"):
            t[c] = t[c].astype(float).round(2)
        t["primary_multiple"] = t["primary_multiple"].astype(float).round(2)
        print(t.to_string(index=False))
    sc_df[cols].to_csv("outputs/screen_scores.csv", index=False)

    pursue, passer, uncon = SC.recommend(sc_df)
    print("\n" + "-" * 78)
    print(f"Unconstrained top score : {uncon.ticker} ({uncon.company}, "
          f"${uncon.market_cap/1e9:.2f}bn) score {uncon.score:.3f}")
    if uncon.ticker == pursue.ticker:
        print(f"  -> Also the top name inside the mandate "
              f"(< ${SC.MANDATE_MAX_MARKET_CAP/1e9:.0f}bn), so the filter is not binding "
              f"on this run.")
    else:
        print(f"  -> NOT the recommendation. The mandate is investment/M&A targets;")
        print(f"     at ${uncon.market_cap/1e9:.0f}bn it is neither actionable nor "
              f"under-researched,")
        print(f"     which is exactly where an AI screen adds the least value.")
    print(f"\nPURSUE : {pursue.ticker}  {pursue.company}  (${pursue.market_cap/1e9:.2f}bn, {pursue.cap_tier}-cap)")
    print(f"         score {pursue.score:.3f} = raw {pursue.score_raw:.3f} x confidence {pursue.confidence:.2f}")
    print(f"         value {pursue.s_value:.2f} | quality {pursue.s_quality:.2f} | "
          f"growth {pursue.s_growth:.2f} | risk {pursue.s_risk:.2f}")
    print(f"         {pursue.primary_multiple:.2f}x vs peer median -> "
          f"{pursue.discount_to_peer*100:+.1f}% discount")
    if pursue.discount_to_peer is not None and abs(pursue.discount_to_peer) < 0.05:
        print(f"         ! LOW CONVICTION: a {pursue.discount_to_peer*100:+.1f}% discount is "
              f"inside the noise of this dataset.")
        print(f"           The screen is not finding meaningful value in this sub-sector "
              f"right now;")
        print(f"           it is ranking names that are all priced within a few percent "
              f"of each other.")
    pl, sb, det = SC.confidence_breakdown(pursue)
    print(f"         confidence {pursue.confidence:.2f} = 1.00 - {pl:.2f} plumbing "
          f"- {sb:.2f} substance")
    print(f"\nPASS   : {passer.ticker}  {passer.company}  (${passer.market_cap/1e9:.2f}bn)")
    print(f"         raw screen score {passer.score_raw:.3f} -> confidence-adjusted {passer.score:.3f}")
    print(f"         the data flatters this name by {passer.score_raw - passer.score:.3f} "
          f"-- the largest gap in the universe")
    print(f"         value percentile {passer.s_value:.2f} (screens CHEAP) but confidence only {passer.confidence:.2f}")
    print(f"         flags: {', '.join(passer.dq_flags) or 'none'}")
    json.dump(dict(pursue=pursue.ticker, passer=passer.ticker,
                   unconstrained=uncon.ticker), open("outputs/recommendation.json", "w"), indent=1)

    df.to_pickle("outputs/_df.pkl")
    sc_df.to_pickle("outputs/_scored.pkl")
    print(f"\nWrote outputs/comps_table.csv, medians.json, quality_report.json, valuation.json")

if __name__ == "__main__":
    main()
