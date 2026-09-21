# AI Verification Log

**Model used:** Claude Opus 5, via the Claude Code CLI
**Knowledge cutoff:** May 2026 · **Run date:** 20 September 2026
**Structural consequence:** every "recent" claim the model makes is *at least four
months stale by construction*. The pipeline is built on that assumption rather
than hoping otherwise.

## How verification works

The qualitative layer is not free text. Each company entry in
`research/ai_research.json` carries **machine-checkable claims** — either a
numeric assertion about a field the pipeline computes, or an assertion that a
named data-quality flag will fire. `src/verify.py` evaluates all of them on
every run and fails loudly.

Current result: **41 claims · 38 PASS · 3 FAIL.**

This matters because it converts "I checked the AI's output" from an assurance
into a reproducible artefact (`outputs/ai_verification.json`). Anyone can re-run
`python3 run.py` and see the same three failures.

---

## Errors caught, in order of severity

### 1. Misclassification — i3 Verticals is not a payments company *(most serious)*

**What the AI said.** When asked to assemble a payments peer set, the model
returned i3 Verticals (IIIV) as a merchant-acquiring comparable. That was its
business for most of the model's training data.

**How it was caught.** Not by intuition — by the pipeline. The initial data pull
showed revenue of **$213M against a remembered scale of roughly $385M**. A
~45% discrepancy in a company that had not reported a disaster is a
classification error, not a bad quarter. Pulling the filed business description
from the same API resolved it:

> *"i3 Verticals, Inc. provides enterprise software and services solutions to
> **public sector entities** in the United States and Canada... court systems,
> E-Filing..."*

**The correct information.** i3 Verticals divested its merchant services
business. It is now a government/public-sector software company. Its customers
are courts and municipalities, not merchants and not financial institutions.

**A second-order trap inside the same name.** FY2024 net income was **$113.3M on
$191.2M of revenue** — a 59% net margin, which is impossible for this business
model. That is the gain on sale of the divested unit. Any screen ranking on
trailing P/E would have scored IIIV as cheap on phantom earnings. The
`ONE_TIME_GAIN_SUSPECT` flag exists because of this name.

**Resolution.** Retained in the universe as a research finding, flagged
`BUSINESS_MODEL_DRIFT`, and **excluded from every sub-sector median** with a
written reason in `config/universe.py`. Excluded for a *business* reason, not a
data reason.

---

### 2. Stale ticker — MeridianLink was taken private

**What the AI said.** Listed MLNK as a live public comparable for the
FI-software set.

**How it was caught.** The ticker probe returned
`404 ... Quote not found for symbol: MLNK` before any modelling was done. This
is the direct argument for probing a universe empirically **before** designing
around it — a dead ticker discovered late in a build is expensive.

**The correct information.** MeridianLink was acquired by Centerbridge Partners
and no longer trades publicly.

**Resolution.** Removed from the universe. Replaced with Mitek Systems (MITK),
which was verified live before being added.

---

### 3. Outdated financial state — Marqeta's EBITDA turned positive *(claim MQ-1)*

**What the AI said.** `"Marqeta's EBITDA is negative"` — asserted with
`confidence: low`, which was itself correct self-assessment.

**How it was caught.** The verification harness, mechanically:

```
[FAIL] MQ-1  Marqeta's EBITDA is negative
       -> ebitda = 2.08e+07; claim asserted < 0
```

**The correct information.** Marqeta's **TTM EBITDA is +$20.8M**. The model was
not hallucinating; it was reporting the FY2025 annual figure (−$19M), which was
true when its training data ended. The company crossed into EBITDA-positive
territory in the intervening quarters.

**Why this one is instructive.** The error was invisible on annual data and only
appeared once the pipeline computed TTM from quarterly filings. An AI layer
checked against the *same* stale basis the AI used would have confirmed the
error rather than caught it. Verification only works when the data is fresher
than the model.

---

### 4. Directional error — Global Payments is shrinking, not growing *(claim GPN-3)*

**What the AI said.** `"GPN revenue is growing"`.

**How it was caught.** `rev_growth = -0.39%` — mild, but the *sign* is wrong,
and sign errors propagate into a DCF as a permanent growth assumption.

**The correct information.** Global Payments' revenue is approximately flat to
slightly declining. Compounding this, the company's revenue is reported on two
different bases across API endpoints ($10.2bn gross vs $7.7bn net of
interchange), so even the magnitude is basis-dependent.

---

### 5. Precision overreach — nCino's premium is not a premium *(claim NCNO-2)*

**What the AI said.** `"nCino trades at a premium EV/Revenue versus the
FI-software median"`.

**How it was caught.** `ev_revenue = 4.01` against a peer median of `4.13`. It
trades at a slight *discount*.

**Why it is worth logging despite being a near-miss.** This is the most common
and least visible LLM failure mode in financial work: a qualitatively plausible
statement ("the high-growth SaaS name is expensive") that is quantitatively
false. It reads as authoritative and it would survive a human skim. Only a
numeric check catches it. Logged rather than quietly dropped, because the
near-misses are the ones that reach a memo.

---

## What the verification layer does *not* cover

Stated plainly, because the limitation is the point:

- **Only ~40% of the qualitative content is machine-checkable.** Claims like
  *"Payoneer's earnings are levered to policy rates"* or *"Mitek's moat is a
  declining cheque annuity"* are the analytically valuable parts and cannot be
  falsified against yfinance. They are marked by `confidence` and are **excluded
  from the quantitative ranking** — they inform judgement, they do not score.
- **Passing a check is not truth.** A claim can pass because both the AI and the
  data source are wrong in the same direction. These checks catch *inconsistency*,
  not *accuracy*.
- **No primary-source reconciliation.** Nothing here has been checked against an
  actual 10-K or 10-Q. With three more days, the first thing to add is SEC EDGAR
  XBRL as an independent second source — see the roadmap slide.
