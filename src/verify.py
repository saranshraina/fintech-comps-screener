"""
Automated verification of the AI qualitative layer.

The point of this module: an LLM summary is an *assertion*, and assertions
should be testable. Every qualitative entry in research/ai_research.json
carries machine-checkable claims -- either a numeric assertion about a field
the pipeline computed, or an assertion that a specific data-quality flag will
fire. This module evaluates them and reports every failure.

That turns "I checked the AI's work" from a claim into an artefact.
"""
from __future__ import annotations
import json, os, operator

OPS = {">": operator.gt, "<": operator.lt, ">=": operator.ge,
       "<=": operator.le, "==": operator.eq, "!=": operator.ne}

HERE = os.path.dirname(__file__)
RESEARCH = os.path.join(HERE, "..", "research", "ai_research.json")


def load():
    with open(RESEARCH) as fh:
        return json.load(fh)


def verify(df):
    res = load()
    rows = {r["ticker"]: r for _, r in df.iterrows()}
    results = []
    for tk, entry in res.items():
        if tk == "_meta":
            continue
        row = rows.get(tk)
        for c in entry.get("checkable_claims", []):
            rec = dict(ticker=tk, id=c["id"], text=c["text"])
            if row is None:
                rec.update(status="NO_DATA", detail="ticker absent from comps table")
                results.append(rec); continue

            if "flag_expected" in c:
                fired = c["flag_expected"] in (row.get("dq_flags") or [])
                rec.update(status="PASS" if fired else "FAIL",
                           detail=f"expected flag {c['flag_expected']} "
                                  f"{'fired' if fired else 'did NOT fire'}",
                           actual=list(row.get("dq_flags") or []))
            else:
                actual = row.get(c["field"])
                if actual is None:
                    rec.update(status="UNTESTABLE", actual=None,
                               detail=f"{c['field']} unavailable in pulled data")
                else:
                    ok = OPS[c["op"]](actual, c["value"])
                    rec.update(status="PASS" if ok else "FAIL", actual=float(actual),
                               detail=f"{c['field']} = {actual:,.4g}; claim asserted "
                                      f"{c['op']} {c['value']:,.4g}")
            results.append(rec)
    return results


def summarise(results):
    from collections import Counter
    return Counter(r["status"] for r in results)
