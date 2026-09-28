# AI-Assisted Company Screening & Valuation — Fintech

A prototype that screens 15 public fintech companies across two sub-sectors,
builds comparable-company tables, runs a scenario DCF, layers on AI-generated
qualitative research, **and automatically verifies that research against the
data it pulled.**

```bash
pip install -r requirements.txt
python3 run.py            # full pipeline, prints every step
streamlit run app.py      # interactive demo
```

---

## The short version

| | |
|---|---|
| **Sub-sectors** | Merchant Payments & Transaction Processing (8) · Financial-Institution Software (7) |
| **Small / under-covered names** | PRTH, PMTS, MITK, LPRO, IIIV — 5 of 15 |
| **Recommendation — PURSUE** | **NCNO** (nCino, $2.00bn) — *low conviction, see below* |
| **Recommendation — PASS** | **LPRO** (Open Lending, $0.37bn) — high conviction |
| **AI claims auto-verified** | 41 · **36 pass / 5 fail** |
| **Data as of** | 28 September 2026 |

Two headline findings:

**1. The name that screens cheapest in the universe is the one we pass on.**
Open Lending has the best value percentile of any company here (1.00). The
data-confidence layer drops it to last place.

**2. Nothing in this universe is meaningfully cheap right now, and the tool says
so.** The winning name trades at a **+1.0% discount** to its peer median, which
is inside the noise of this dataset. FI-software multiples cluster between
3.17× and 4.10×. The screen flags its own output as low-conviction rather than
dressing up noise as a recommendation.

---

## 1. Segmentation, and why it is manual

The split is by **revenue mechanics**, not by industry label:

- **Merchant Payments** — revenue = volume × take rate. Mature, EBITDA-positive,
  cash-generative, macro-sensitive. Valued on **EV/EBITDA**.
- **FI Software** — revenue = multi-year recurring contracts sold to banks and
  credit unions. High gross margin, high visibility, heavy reinvestment,
  frequently GAAP-unprofitable. Valued on **EV/Revenue**.

The API's own labels are unusable for this: yfinance classifies PayPal as
"Credit Services", Priority Technology as "Software - Infrastructure", and CPI
Card Group as "Credit Services". Those cut straight across the economic
distinction that drives the multiple.

**Why the primary multiple differs by sub-sector.** In payments, revenue may be
reported **gross** (including interchange passed through to networks and
issuers) or **net**. Global Payments prints **$10.2bn gross vs $7.7bn net** — a
32% gap. EV/Revenue therefore ranks payments companies partly by accounting
policy. EBITDA sits below those passthroughs and is comparable. FI software is
the mirror image: recurring revenue is clean, while GAAP EBITDA is distorted by
capitalised software and stock comp at very different rates.

---

## 2. Messy data: what broke, and the policy for handling it

Four things broke. All four were found by the tool, not assumed.

| # | What | Response |
|---|---|---|
| 1 | **Fiserv** — summary endpoint returned a $25bn market cap but **no revenue, no EBITDA, no industry**; filed statements were complete | Multi-source fallback chain; value recovered, provenance recorded |
| 2 | **MeridianLink** — ticker 404s, company was taken private | Caught by empirical probe *before* modelling; replaced with a verified live name |
| 3 | **i3 Verticals** — divested merchant services, now sells software to **courts and municipalities** | Excluded from medians with a written reason |
| 4 | **Open Lending** — reports **+288% revenue growth** | Growth measured off a collapsed base; substituted the multi-year trend of **−19.6%** |

### Two kinds of bad data

An early version of the confidence model charged Fiserv the same penalty as Open
Lending. That was wrong, and correcting it changed the recommendation:

- **Plumbing** (costs ~0.02) — the API failed to serve a value the filed
  statements contain. The fallback recovered it, provenance proves the source.
  **The number is fine.**
- **Substance** (costs 0.15–0.25) — the reported figure does not mean what it
  appears to mean. No amount of plumbing fixes it. **The number lies.**

### The three-tier policy

| Tier | Response | Example |
|---|---|---|
| 1 | Flag, keep in the median | `EV_DERIVED` — value is real, pedigree noted |
| 2 | Flag, drop **that metric** from the median, keep the row | `NEGATIVE_EBITDA` — EV/EBITDA is meaningless, EV/Revenue is fine |
| 3 | Exclude the company, with a written reason | `BUSINESS_MODEL_DRIFT` — i3 Verticals |

**We exclude the metric, not the company.** A negative-EBITDA name still
contributes a perfectly good EV/Revenue. Dropping the whole row throws away
data. And Tier 3 is reserved for *business-model* mismatch — never for numbers
being inconvenient.

Nothing is silently imputed. Every field records its source
(`ttm_quarterly` → `statement_fy` → `info` → `derived`) and the provenance
table is a visible output.

### A methodology bug the tool caught in itself

An early run flagged `API_MULTIPLE_DISAGREES` on **9 of 15 names**. A flag
firing on 60% of the universe is miscalibrated or hiding something. It was
hiding something: statement revenue is **fiscal year**, the API's summary
revenue is **TTM**, and the pipeline was comparing across periods. Rebuilding
TTM from quarterly filings dropped the flag to near zero — and changed real
conclusions, including Marqeta's EBITDA sign.

---

## 3. Valuation: a reverse DCF, because the forward DCF was not credible

The first 3-scenario DCF produced a **+584% bull case for Priority Technology**.
That is not analysis. Two things were wrong: the scenario levers stacked
multiplicatively, and — more importantly — a 9.5% WACC with 2.5% terminal
growth implies a terminal value around **14.6× FCFF** when the market pays
**6–8× EBITDA** for these businesses.

So the model was inverted. The **reverse DCF** solves for the assumption that
makes the DCF equal today's price:

| | Implied terminal growth at current price |
|---|---|
| PayPal | **−2.2%** |
| Fiserv | **−1.6%** |
| Priority Technology | **−3.1%** |
| Evertec | +1.7% |
| Payoneer | **+8.7%** |
| Marqeta | **no solution** — the price cannot be explained by terminal growth at all |

That is a *falsifiable statement* a reviewer can argue with on the merits of the
business, rather than a point estimate nobody should trust. The market is
pricing legacy payment processors for **perpetual decline**.

The tool also flags its own unreliability: when the DCF base case diverges from
the primary comps multiple by more than 50%, it says so, and when more than 75%
of value sits in the terminal year it labels the output a terminal-value bet.

---

## 4. The AI layer, and how it is checked

Qualitative research is generated per company (business model, value driver,
key risk) — but every entry also carries **machine-checkable claims**, either a
numeric assertion about a computed field or an assertion that a named
data-quality flag will fire. `src/verify.py` evaluates all 41 on every run.

**Result: 36 pass, 5 fail.** The failures are real and reproducible:

- **`MQ-1`** — the model asserted Marqeta's EBITDA is negative. It was, on
  FY2025. **TTM EBITDA is +$20.8M.** The model was reporting correctly as of
  its May 2026 cutoff; the company crossed over afterwards. *Verification only
  works when the data is fresher than the model.*
- **`GPN-3`** — asserted Global Payments' revenue is growing. It is **−0.4%**.
  A sign error that would have propagated into a DCF as a permanent assumption.
- **`NCNO-2` and `ALKT-3`** — asserted nCino and Alkami trade at premiums.
  Both trade at discounts (3.73× and 3.58× against a 3.77× median).
  Qualitatively plausible, quantitatively false, and they would survive a human
  skim.
- **`PRTH-4`** — asserted Priority trades below the payments peer median. It did
  on 20 September (6.72× vs 7.26×); by the 26th the stock had risen 33% and it
  did not (7.48× vs 7.48×).

Three of the five failures appeared only on the refresh. `ALKT-3` and `PRTH-4`
did not fail because the model was wrong — they failed because **the market
moved beneath them.** The harness cannot distinguish "the AI was mistaken" from
"the world changed", and that limitation is stated rather than hidden.

Full write-up, including the two larger errors caught before modelling began
(i3 Verticals, MeridianLink): **[`research/verification_log.md`](research/verification_log.md)**.

**What this does not cover:** roughly 60% of the qualitative content — the
analytically valuable part, like *"Payoneer's earnings are levered to policy
rates"* — is not machine-checkable. Those claims carry a `confidence` field and
are **excluded from the quantitative ranking**. A passing check also is not
truth: it catches *inconsistency*, not *accuracy*. Nothing has been reconciled
against an actual 10-K.

---

## 5. Ranking logic and the recommendation

Score = `0.35 × value + 0.30 × quality + 0.20 × growth − 0.15 × risk`,
percentile-ranked **within sub-sector**, then multiplied by a **data-confidence
factor** derived from the quality flags on that name.

The confidence gate is the part that matters. A company cannot rank highly on
numbers we do not trust.

### PURSUE — nCino (NCNO, $2.00bn) — low conviction

Score 0.517 = raw 0.517 × confidence **1.00**. The best-rounded name inside the
mandate (value 0.67, quality 0.75, growth 0.67) and the **only company in the
universe carrying zero data-quality flags**. Cloud loan origination and
onboarding sold to banks on multi-year contracts.

**Why the conviction is low, stated plainly.** The discount to peers is
**+1.0%**, which is noise, not signal. And the reverse DCF says the current
price already assumes **+7.4% terminal growth**. It ranks first because it is
clean and balanced, not because it is cheap. The tool prints a
`LOW CONVICTION` warning on exactly this condition.

### PASS — Open Lending (LPRO, $0.37bn)

Raw score 0.387 → confidence-adjusted **0.135**, the largest gap in the
universe. It has the **best value percentile of any company here (1.00)** and
finishes last. Revenue is substantially an *accounting estimate* — a
profit-share receivable on insured auto loans that has been revised down hard
enough to produce negative revenue quarters. Its headline +288% growth is
recovery off that collapsed base; the real multi-year trend is −19.6%.

### Note on the unconstrained winner

The top unconstrained score is **Fiserv (0.572)** — a 13% discount, 38.9% EBITDA
margin, the highest raw score in the universe. The `< $5bn` mandate filter
excludes it at $24.7bn. That filter is an explicit criterion, not an oversight,
and both rankings are always reported.

### Robustness: the weights are arbitrary, so they were attacked

The obvious challenge to any scoring model is *"you chose those weights."* So
the model was stress-tested rather than defended:

| Test | Result |
|---|---|
| 36 plausible weight combinations | **Same winner in 33 of 36** |
| Drop any single criterion entirely | Same winner in 5 of 6 |
| Remove the confidence gate | **Recommendation changes** — the gate does real work |
| Move the mandate cap ($1bn–$1tn) | Same winner |

The ranking is **robust to the weights** and **sensitive to price**.

### The single biggest reason the recommendation could be wrong

**The mandate and the opportunity are in different places.**

The only genuinely cheap business in this universe is Fiserv — 13% discount,
38.9% margins, highest raw score — and our own `< $5bn` filter excludes it. So
the recommendation is the best-ranked name in a sub-sector where *nothing* is
mispriced, while the actual opportunity sits outside the constraint.

If this call is wrong, it is because **the question as posed was answered
instead of the honest thing being said**: on this data, today, the right action
is to widen the universe rather than force a pick from fifteen names whose
multiples span 3.17× to 4.10×.

**A second, structural reason.** Everything the screen measures is *trailing*. A
27% trailing EBITDA margin on a structurally shrinking revenue base looks
identical to 27% on a stable one. The tool cannot distinguish duration of cash
flows, which is the thing that actually determines value.

### Price sensitivity, demonstrated

This was built on 20 September and refreshed on the 28th. In eight days:

| | 20 Sep | 28 Sep |
|---|---|---|
| Mitek | $16.73 | $17.66 (**+5.6%**) |
| nCino | $21.05 | $18.90 (**−10.2%**) |
| FI-software median EV/Revenue | 4.13× | 3.77× |
| **Recommendation** | **MITK** | **NCNO** |

Mitek did not get worse — it got *more expensive*, and its 13% discount went to
zero. This is a property of relative-multiple screening, not a defect being
confessed: **a comps ranking is a snapshot.** It is dated deliberately and
reproducible on demand.

---

## 6. Repo layout

```
config/universe.py     15 companies, sub-sector, thesis, coverage, exclusions
src/fetch.py           multi-source fallback + TTM + provenance + disk cache
src/quality.py         flag definitions, severity, metric-level suppression
src/comps.py           comps table, sub-sector medians, quality report
src/valuation.py       3-scenario DCF, sensitivity, reverse DCF, comps cross-check
src/verify.py          automated verification of the AI layer
src/score.py           screening score, confidence gate, recommendation
research/              AI research + verification log
run.py                 full pipeline, 7 printed steps
app.py                 Streamlit demo
```

---

## 7. With three more days

1. **SEC EDGAR XBRL as an independent second source.** Every material figure
   reconciled against the filing, with a variance report. This removes the
   single largest weakness — everything currently rests on one free API.
2. **Segment-level data.** Priority's Treasury business and Mitek's identity
   business are the actual theses, and consolidated figures hide both.
3. **Rate sensitivity as a first-class input.** Payoneer and Priority both earn
   float income on customer balances; neither is modelled as rate-exposed today,
   and for Payoneer that is most of the story.
4. **Retrieval-grounded AI layer with citations.** Replace recalled knowledge
   with retrieved filings, so every qualitative claim carries a source and the
   staleness window collapses from four months to zero.
5. **Backtest the screen.** Run it on data as of 12 months ago and measure
   whether the confidence gate actually improved forward returns. Right now the
   gate is principled but unvalidated.

---

## Deck and walkthrough

- **`deck.html`** — 15-slide presentation deck. Open in a browser; arrow keys or
  the section tabs to navigate; Cmd-P exports all slides to PDF.
- **`TUTORIAL.html`** — a ten-section walkthrough of the pipeline: what each step
  does in plain English, the code behind it, and commands to run.
