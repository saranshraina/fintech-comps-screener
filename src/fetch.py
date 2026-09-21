"""
Data acquisition layer with multi-source fallback and provenance tracking.

WHY THIS IS NOT A ONE-LINER
---------------------------
yfinance exposes several backend endpoints that fail INDEPENDENTLY. During
development, Fiserv (FISV, ~$25B market cap) returned a valid marketCap from
the `.info` summary endpoint but NO revenue, NO EBITDA and NO industry --
while `.income_stmt` returned complete, correct financials for the same name.
A naive `info["totalRevenue"]` pipeline silently drops a mega-cap.

So every field is resolved through an ordered chain of sources, and we record
WHICH source produced each value. Provenance is a first-class output: an
analyst reviewing the comps table can see which numbers came from the API's
summary blob, which were derived from filed statements, and which were
computed by us.

SOURCE PRECEDENCE (best -> worst)
  1. "statement"  - derived from filed income statement / balance sheet / CF
  2. "info"       - Yahoo's precomputed summary field
  3. "derived"    - computed by us from other resolved fields
  4. None         - genuinely unavailable -> flagged downstream, never guessed
"""
from __future__ import annotations
import json, os, time, warnings, datetime as dt
import yfinance as yf

warnings.filterwarnings("ignore")

CACHE_DIR = os.path.join(os.path.dirname(__file__), "..", "data", "cache")
os.makedirs(CACHE_DIR, exist_ok=True)


# --------------------------------------------------------------------------
# low-level helpers
# --------------------------------------------------------------------------
def _num(x):
    """Coerce to float, mapping NaN/inf/None/blank to None."""
    if x is None:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _row(df, *names):
    """First matching row label in a yfinance statement frame -> most recent value."""
    if df is None or getattr(df, "empty", True):
        return None, None
    for n in names:
        if n in df.index:
            s = df.loc[n].dropna()
            if len(s):
                col = s.index[0]                      # most recent period
                period = str(col)[:10] if col is not None else None
                return _num(s.iloc[0]), period
    return None, None


def _series(df, *names, n=4):
    """Row as an ordered list of (period, value), most recent first."""
    if df is None or getattr(df, "empty", True):
        return []
    for nm in names:
        if nm in df.index:
            s = df.loc[nm].dropna()
            return [(str(i)[:10], _num(v)) for i, v in list(s.items())[:n]]
    return []


def _ttm(qdf, *names, min_q=4):
    """Trailing-twelve-month sum from the quarterly statement.

    Comps convention is TTM. Mixing a fiscal-year figure (what `.income_stmt`
    returns) against a TTM figure (what `.info` returns) silently compares
    different periods -- for Global Payments that gap was 32%. We therefore
    build TTM ourselves from quarterly filings whenever 4 quarters exist.
    """
    if qdf is None or getattr(qdf, "empty", True):
        return None, None
    for n in names:
        if n in qdf.index:
            s_ = qdf.loc[n].dropna()
            if len(s_) >= min_q:
                q = s_.iloc[:min_q]
                return float(sum(_num(v) or 0 for v in q)), f"{str(q.index[-1])[:10]}..{str(q.index[0])[:10]}"
    return None, None


class Resolved(dict):
    """dict of field -> value, with a parallel dict of field -> source tag."""
    def __init__(self):
        super().__init__()
        self.src = {}

    def put(self, field, value, source):
        """Set field only if not already set with a better source."""
        v = _num(value)
        if v is None:
            return False
        if field in self and self[field] is not None:
            return False
        self[field] = v
        self.src[field] = source
        return True


# --------------------------------------------------------------------------
# main fetch
# --------------------------------------------------------------------------
def fetch_one(ticker, use_cache=True, max_age_hours=12):
    """Return a dict of raw+resolved fundamentals for one ticker."""
    path = os.path.join(CACHE_DIR, f"{ticker}.json")
    if use_cache and os.path.exists(path):
        age = (time.time() - os.path.getmtime(path)) / 3600
        if age < max_age_hours:
            with open(path) as fh:
                return json.load(fh)

    out = {
        "ticker": ticker,
        "fetched_at": dt.datetime.now().isoformat(timespec="seconds"),
        "errors": [],
        "endpoints_ok": {},
    }

    tk = yf.Ticker(ticker)

    # --- endpoint 1: summary blob -----------------------------------------
    info = {}
    try:
        info = tk.info or {}
        out["endpoints_ok"]["info"] = bool(info.get("marketCap") or info.get("shortName"))
    except Exception as e:
        out["errors"].append(f"info: {type(e).__name__}: {e}")
        out["endpoints_ok"]["info"] = False

    # --- endpoint 2/3/4: filed statements ---------------------------------
    inc = bal = cfs = qinc = qcfs = None
    for attr, key in (("income_stmt", "income"), ("balance_sheet", "balance"),
                      ("cashflow", "cashflow"),
                      ("quarterly_income_stmt", "q_income"),
                      ("quarterly_cashflow", "q_cashflow")):
        try:
            df = getattr(tk, attr)
            ok = df is not None and not df.empty
            out["endpoints_ok"][key] = bool(ok)
            if key == "income":      inc = df
            elif key == "balance":   bal = df
            elif key == "cashflow":  cfs = df
            elif key == "q_income":  qinc = df
            else:                    qcfs = df
        except Exception as e:
            out["errors"].append(f"{attr}: {type(e).__name__}: {e}")
            out["endpoints_ok"][key] = False

    r = Resolved()

    # ---- identity ---------------------------------------------------------
    out["name_api"] = info.get("shortName") or info.get("longName")
    out["industry_api"] = info.get("industry")
    out["summary_api"] = info.get("longBusinessSummary")
    out["currency"] = info.get("currency")

    # ---- price & share count ---------------------------------------------
    r.put("price", info.get("currentPrice") or info.get("regularMarketPrice")
                   or info.get("previousClose"), "info")
    r.put("shares_out", info.get("sharesOutstanding"), "info")
    r.put("market_cap", info.get("marketCap"), "info")
    # derive market cap if the summary blob withheld it
    if "market_cap" not in r and "price" in r and "shares_out" in r:
        r.put("market_cap", r["price"] * r["shares_out"], "derived")

    # ---- income statement: statements FIRST, summary blob as fallback -----
    rev_ttm, ttm_p = _ttm(qinc, "Total Revenue", "Operating Revenue")
    rev_fy,  rev_p = _row(inc, "Total Revenue", "Operating Revenue")
    r.put("revenue", rev_ttm, "ttm_quarterly")
    r.put("revenue", rev_fy, "statement_fy")
    r.put("revenue", info.get("totalRevenue"), "info")
    out["revenue_period"] = ttm_p or rev_p
    # keep both bases so we can detect gross-vs-net presentation differences
    out["revenue_fy"] = rev_fy
    out["revenue_info"] = _num(info.get("totalRevenue"))
    out["revenue_ttm"] = rev_ttm

    eb_ttm, _ = _ttm(qinc, "EBITDA", "Normalized EBITDA")
    eb_fy,  _ = _row(inc, "EBITDA", "Normalized EBITDA")
    r.put("ebitda", eb_ttm, "ttm_quarterly")
    r.put("ebitda", eb_fy, "statement_fy")
    r.put("ebitda", info.get("ebitda"), "info")

    ebit_ttm, _ = _ttm(qinc, "EBIT", "Operating Income")
    ebit_fy,  _ = _row(inc, "EBIT", "Operating Income")
    r.put("ebit", ebit_ttm, "ttm_quarterly")
    r.put("ebit", ebit_fy, "statement_fy")

    ni_ttm, _ = _ttm(qinc, "Net Income", "Net Income Common Stockholders")
    ni_fy,  _ = _row(inc, "Net Income", "Net Income Common Stockholders")
    r.put("net_income", ni_ttm, "ttm_quarterly")
    r.put("net_income", ni_fy, "statement_fy")
    r.put("net_income", info.get("netIncomeToCommon"), "info")

    gp, _ = _row(inc, "Gross Profit")
    r.put("gross_profit", gp, "statement")

    # EBITDA is frequently absent for smaller names -> reconstruct EBIT + D&A
    if "ebitda" not in r:
        da, _ = _row(cfs, "Depreciation And Amortization",
                     "Depreciation Amortization Depletion")
        if r.get("ebit") is not None and da is not None:
            r.put("ebitda", r["ebit"] + da, "derived")

    # ---- balance sheet ----------------------------------------------------
    debt, _ = _row(bal, "Total Debt")
    r.put("total_debt", debt, "statement")
    r.put("total_debt", info.get("totalDebt"), "info")

    cash, _ = _row(bal, "Cash And Cash Equivalents",
                   "Cash Cash Equivalents And Short Term Investments")
    r.put("cash", cash, "statement")
    r.put("cash", info.get("totalCash"), "info")

    # ---- cash flow --------------------------------------------------------
    o1, _ = _ttm(qcfs, "Operating Cash Flow", "Total Cash From Operating Activities")
    o2, _ = _row(cfs, "Operating Cash Flow", "Total Cash From Operating Activities")
    r.put("op_cash_flow", o1, "ttm_quarterly"); r.put("op_cash_flow", o2, "statement_fy")
    c1, _ = _ttm(qcfs, "Capital Expenditure"); c2, _ = _row(cfs, "Capital Expenditure")
    r.put("capex", c1, "ttm_quarterly"); r.put("capex", c2, "statement_fy")
    f1, _ = _ttm(qcfs, "Free Cash Flow"); f2, _ = _row(cfs, "Free Cash Flow")
    r.put("fcf", f1, "ttm_quarterly"); r.put("fcf", f2, "statement_fy")
    if r.get("fcf") is None and r.get("op_cash_flow") is not None and r.get("capex") is not None:
        r.put("fcf", r["op_cash_flow"] + r["capex"], "derived")   # capex is negative

    # ---- enterprise value -------------------------------------------------
    r.put("enterprise_value", info.get("enterpriseValue"), "info")
    if "enterprise_value" not in r and r.get("market_cap") is not None:
        ev = r["market_cap"] + (r.get("total_debt") or 0) - (r.get("cash") or 0)
        r.put("enterprise_value", ev, "derived")

    # ---- multiples: we recompute rather than trust the API's versions -----
    r.put("pe_api", info.get("trailingPE"), "info")
    r.put("pe_fwd_api", info.get("forwardPE"), "info")
    r.put("ev_ebitda_api", info.get("enterpriseToEbitda"), "info")
    r.put("ev_rev_api", info.get("enterpriseToRevenue"), "info")

    # ---- growth history ---------------------------------------------------
    out["revenue_history"] = _series(inc, "Total Revenue", "Operating Revenue")
    out["ebitda_history"] = _series(inc, "EBITDA", "Normalized EBITDA")

    out["fields"] = dict(r)
    out["sources"] = r.src
    with open(path, "w") as fh:
        json.dump(out, fh, indent=1)
    return out


def fetch_all(tickers, use_cache=True, verbose=True):
    res = {}
    for t in tickers:
        if verbose:
            print(f"  fetching {t:<6}", end="", flush=True)
        d = fetch_one(t, use_cache=use_cache)
        res[t] = d
        if verbose:
            got = sum(1 for k in ("revenue", "ebitda", "market_cap", "net_income")
                      if d["fields"].get(k) is not None)
            bad = [k for k, v in d["endpoints_ok"].items() if not v]
            print(f" core {got}/4" + (f"  DEAD:{','.join(bad)}" if bad else ""))
    return res
