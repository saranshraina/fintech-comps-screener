"""
Universe definition: Financial Technology, split into two sub-sectors.

SEGMENTATION RATIONALE
----------------------
We split fintech by *revenue mechanics*, not by SIC/GICS label, because the two
groups are underwritten and valued in fundamentally different ways:

  A) MERCHANT PAYMENTS & TRANSACTION PROCESSING
     Revenue = payment volume x take rate. Largely variable-cost, mature,
     EBITDA-positive, cash-generative, low revenue visibility (volume is
     macro/consumer-spend sensitive but re-prices instantly). Valued on
     EV/EBITDA and FCF yield.

  B) FINANCIAL-INSTITUTION SOFTWARE
     Revenue = multi-year recurring license/subscription contracts sold to
     banks and credit unions. High gross margin, high revenue visibility,
     heavy S&M reinvestment, frequently EBITDA-negative on a GAAP basis.
     Valued on EV/Revenue and Rule of 40.

WHY NOT USE THE API'S OWN LABELS: yfinance/Yahoo classifies PayPal as "Credit
Services", Priority Technology as "Software - Infrastructure", and CPI Card
Group as "Credit Services". Those labels cut across the economic distinction
that actually drives the multiple, so segmentation here is manual and
business-model-based. This is documented as a deliberate choice, not an
oversight.

CAP TIERS are assigned from live market cap at runtime, not hardcoded here.
"""

PAYMENTS = "Merchant Payments & Transaction Processing"
FI_SOFTWARE = "Financial-Institution Software"

UNIVERSE = [
    # ---------------- A) MERCHANT PAYMENTS & TRANSACTION PROCESSING ----------
    dict(ticker="PYPL", name="PayPal Holdings", sub_sector=PAYMENTS,
         thesis="Two-sided branded checkout + unbranded Braintree processing.",
         coverage="high"),
    dict(ticker="GPN", name="Global Payments", sub_sector=PAYMENTS,
         thesis="Merchant acquiring at scale; integrated/vertical software POS.",
         coverage="high"),
    dict(ticker="FISV", name="Fiserv", sub_sector=PAYMENTS,
         thesis="Merchant acquiring (Clover) + bank processing. Ticker moved to "
                "FI in 2023; FISV is the record that still carries data in this API.",
         coverage="high"),
    dict(ticker="EVTC", name="Evertec", sub_sector=PAYMENTS,
         thesis="Dominant payment processor in Puerto Rico / Latin America.",
         coverage="medium"),
    dict(ticker="PAYO", name="Payoneer Global", sub_sector=PAYMENTS,
         thesis="Cross-border B2B payouts for SMB marketplace sellers; earns "
                "float income on customer balances.",
         coverage="medium"),
    dict(ticker="MQ", name="Marqeta", sub_sector=PAYMENTS,
         thesis="Modern card-issuing API platform; heavy customer concentration.",
         coverage="medium"),
    dict(ticker="PRTH", name="Priority Technology Holdings", sub_sector=PAYMENTS,
         thesis="SMB acquiring + B2B payables + embedded 'Treasury/Passport' "
                "banking-as-a-service. Levered rollup.",
         coverage="low"),
    dict(ticker="PMTS", name="CPI Card Group", sub_sector=PAYMENTS,
         thesis="Physical payment-card manufacturing & personalization. "
                "Hardware economics inside a payments wrapper.",
         coverage="low"),

    # ---------------- B) FINANCIAL-INSTITUTION SOFTWARE ----------------------
    dict(ticker="JKHY", name="Jack Henry & Associates", sub_sector=FI_SOFTWARE,
         thesis="Core banking processing for community banks / credit unions.",
         coverage="high"),
    dict(ticker="QTWO", name="Q2 Holdings", sub_sector=FI_SOFTWARE,
         thesis="Digital banking front-end platform for regional FIs.",
         coverage="medium"),
    dict(ticker="NCNO", name="nCino", sub_sector=FI_SOFTWARE,
         thesis="Cloud loan-origination / onboarding built on Salesforce.",
         coverage="medium"),
    dict(ticker="ALKT", name="Alkami Technology", sub_sector=FI_SOFTWARE,
         thesis="Cloud-native digital banking for credit unions; GAAP-unprofitable "
                "growth compounder.",
         coverage="medium"),
    dict(ticker="MITK", name="Mitek Systems", sub_sector=FI_SOFTWARE,
         thesis="Mobile check deposit IP + identity verification sold to FIs.",
         coverage="low"),
    dict(ticker="LPRO", name="Open Lending", sub_sector=FI_SOFTWARE,
         thesis="Lenders Protection risk-analytics SaaS for credit-union auto "
                "lending; revenue carries insurance-like risk-sharing.",
         coverage="low"),

    # ---------------- FLAGGED: RECLASSIFIED / EXCLUDED ----------------------
    dict(ticker="IIIV", name="i3 Verticals", sub_sector=FI_SOFTWARE,
         thesis="ORIGINALLY SELECTED AS A PAYMENTS COMP. Primary-source check of "
                "the filed business description shows it now sells enterprise "
                "software to PUBLIC SECTOR entities (courts, e-filing, permitting) "
                "after divesting its merchant-services business. Neither a payments "
                "pure-play nor an FI-software vendor.",
         coverage="low",
         exclude_from_comps=True,
         exclusion_reason=(
             "Business-model drift: divested merchant services (revenue fell "
             "~$385M -> $213M); end market is government, not financial "
             "institutions. Trailing P/E is additionally distorted by a one-time "
             "gain on sale (FY2024 net income $113.3M on $191.2M revenue). "
             "Retained in the universe as a research finding; excluded from all "
             "sub-sector medians."),
         ),
]

def tickers(include_excluded=True):
    return [c["ticker"] for c in UNIVERSE
            if include_excluded or not c.get("exclude_from_comps")]

def meta(ticker):
    for c in UNIVERSE:
        if c["ticker"] == ticker:
            return c
    raise KeyError(ticker)

def cap_tier(market_cap):
    """Standard-ish US equity cap tiers, in USD."""
    if market_cap is None:
        return "unknown"
    if market_cap >= 10e9:  return "large"
    if market_cap >= 2e9:   return "mid"
    if market_cap >= 300e6: return "small"
    return "micro"
