# IPO Scoring & Recommendation Engine — Phase 2 Ideation

> Ideation doc for discussion, not an approved spec. Nothing here is being
> built yet — this exists to give the next conversation something concrete
> to react to. No implementation should start from this doc without an
> explicit go-ahead and a proper `writing-plans` pass afterward.

## Context

Phase 1 (done, live) discovers every new SEBI public-issue filing and emails
you the raw list — no ranking, no opinion. You want Phase 2 to add a score:
which of the current IPOs actually look worth paying attention to, based on
company fundamentals, GMP, subscription demand, and (maybe) sentiment —
eventually rigorous enough to publish/share, not just a personal heuristic.

That's a materially bigger project than Phase 1: Phase 1 reads a public
listing page. Phase 2 needs to extract structured numbers out of inconsistent
PDFs, pull in at least one third-party data source with no official API, and
— because "share/publish" was the stated goal — validate that the resulting
score actually correlates with real outcomes before anyone sees it.

## What "profitable" means here

Target: **listing-day gain** — % change from issue price to Day 1 closing
price on NSE/BSE. Chosen over medium-term return (harder to source, drifts
away from "did I make money by lunchtime," which is what most people
actually mean by IPO investing) and over subscription success (that's an
*input* to the score below, not the outcome it should predict — using it as
both would be circular).

## The four parameters

| Parameter | What it measures | Proposed source | Status |
|---|---|---|---|
| **Fundamentals/Financials** | Revenue, PAT, EBITDA, debt, margins, growth trend — extracted from the DRHP/RHP PDFs the collector already downloads | **Upstox Fundamentals API** (your suggestion) | ⚠️ unverified — see below |
| **GMP** (Grey Market Premium) | Unofficial premium traders are currently paying above the issue price in the grey market — a informal but widely-watched demand signal | **parse.bot** configured against InvestorGain's GMP page (your suggestion) | ⚠️ unverified, and likely needs setup work — see below |
| **Subscription ratio** | How many times oversubscribed the issue is, split by category (retail/QIB/NII) — official demand data published by NSE/BSE during the bidding window | NSE/BSE bidding data (not yet sourced — needs the same verification pass as the others) | Not yet investigated |
| **Market sentiment** *(optional, lowest priority)* | Broad mood toward IPOs/markets generally, as opposed to demand for this specific issue | Not sourced — genuinely the weakest-defined parameter of the four, most likely to get cut | Deferred |

You confirmed: **use both GMP and subscription ratio as separate parameters**
(not one replacing the other), and **each parameter's value should come with
a one-line explanation in the email itself** — e.g.:

```
GMP: ₹42 (grey market premium — unofficial premium traders are
     currently paying above the issue price; not an exchange-verified figure)
Subscription: 38.4x (official oversubscription ratio across all
     investor categories as of the last bidding update)
```

This matters more than it looks: it's the cheapest possible defensibility
move for a published score — nobody can accuse you of hiding that GMP is
informal chatter if the email says so every time.

### On the three suggested sources — verification needed before committing

I tried to confirm all three before writing this doc and could **not**
verify any of them with confidence from the outside (a basic fetch of the
marketplace/docs pages didn't return real endpoint listings — both sites are
JS-heavy and don't render their actual catalogs to a simple page fetch). Two
specific things worth checking before this becomes a real plan, not because
either idea is wrong, but because the design details change a lot depending
on the answer:

1. **parse.bot** looks like a general "point this at any webpage, get
   structured data back via an API" tool/marketplace — not a pre-built
   "InvestorGain API" sitting in a catalog. If that's right, using it means
   *you* configure parse.bot against InvestorGain's GMP page yourself (their
   product, not a ready endpoint) — worth confirming this is actually how it
   works, what it costs past any free tier, and how stable the scrape is
   when InvestorGain changes their page layout (scrapers built on someone
   else's UI break silently — the collector's own `sebi_source.py` regex
   parser exists precisely because that kind of breakage is a real,
   recurring failure mode).
2. **Upstox's public developer API** has historically been a trading/market-
   data API (quotes, historical candles, orders, portfolio) — I could not
   confirm from outside whether it currently exposes a dedicated "IPO
   calendar" or "company fundamentals" product specifically. Worth 10 minutes
   in their developer console to confirm the exact endpoints, response
   shape, and whether fundamentals/IPO data needs a different tier/agreement
   than plain market data.

Neither of these is a reason not to use them — just don't lock the design to
exact endpoint shapes until one of us has actually seen a real response from
each.

**Bonus idea from "Upstox IPO API":** if it does give a clean official
upcoming-IPO calendar, that's independently useful *beyond* scoring — it
could cross-check SEBI's own listing (catch an IPO that's been announced but
whose DRHP SEBI hasn't posted yet, or confirm dates/sizes SEBI's filing pages
don't always state cleanly). Worth keeping even if the scoring project stalls.

## Recommended approach: validate before you publish

Three ways to sequence this; recommending the first:

**A. Historical-dataset-first (recommended).** Assemble a dataset of, say,
the last 1-2 years of IPOs — their DRHP/RHP financials, historical GMP where
available (some tracker sites publish GMP history), subscription ratios, and
actual listing-day gains. Fit and sanity-check weights against *that* data
— as a standalone script/notebook, decoupled from the live collector — before
building any live scraper. Only once the weights are validated do you wire
up live extraction (fundamentals from new PDFs, live GMP/subscription pulls)
to score *upcoming* IPOs with the same formula.
*Why this order:* you're building something you want to publish. A score
published before it's checked against reality is a credibility risk, not
just a technical shortcut. Front-loading the validation means the "extra"
work happens once, before launch, not as an apology after.

**B. Extraction-first.** Build the live fundamentals extractor now (natural
next step since the PDFs are already being downloaded), ship a score with
judgment-based weights (fundamentals 40%, GMP 25%, subscription 25%,
sentiment 10% — illustrative, not a recommendation) immediately, refine
weights later once enough real outcomes accumulate.
*Trade-off:* visible progress fastest, but you'd be publishing a score
before it's validated for however long "later" takes.

**C. Build all four pipelines in parallel, model last.** Not recommended —
doesn't solve the need for *historical* data (future IPOs alone can't
backtest a model you want to use *now*), and is the easiest of the three to
over-scope.

## What this actually requires, roughly

- **A financial-PDF extractor** for DRHP/RHP documents — this is the single
  hardest new technical piece. SEBI's PDFs are not uniformly structured;
  expect this to need real iteration, not a one-shot parser (if Upstox's
  Fundamentals API pans out, this whole piece might be skippable — another
  reason to verify it first).
- **A historical outcomes source** — actual listing-day prices for past
  IPOs. NSE/BSE publish this; needs its own small sourcing pass.
- **New storage** — today's schema (`companies`, `filings`) has nowhere to
  put any of this. Rough shape, not final: a `financial_metrics` table
  (per-filing extracted numbers), a `market_signals` table (GMP/subscription
  snapshots — these change over time before listing, so this is a
  time-series, not a single value per filing), and an `outcomes` table
  (actual listing-day result, once known, for both backtesting and future
  training data).
- **A fitting/validation method** — start as simple as a linear or logistic
  regression against listing-day gain, with the category breakdown as
  interpretable coefficients (matches "each gets a percent of a score" —
  don't reach for a black-box model here, the whole point is explainability).
- **A disclaimer, if this ever actually gets published** — "not financial
  advice," GMP's informal nature stated up front, and ideally a visible
  changelog of the weights so a published score isn't a moving, unexplained
  target.

## Other things you could do next (not scoring-related)

You asked to "ideate on what else" — a few smaller, more self-contained
ideas that don't require the scoring project to happen first:

- **Filter the "stale IPO" backfill noise.** Right now the first run against
  an empty DB (and, more subtly, any large gap between runs) discovers SEBI's
  *entire* recent history as "new." A simple fix: only alert on filings from
  the last N days, while still storing everything discovered.
- **Track subscription/GMP as a time series even before scoring exists.** A
  single new small table recording daily GMP + subscription snapshots per
  IPO (independent of the scoring model) turns every IPO the collector
  already tracks into free future training data, starting now instead of
  whenever the scoring project actually kicks off.
- **A second delivery channel.** Telegram/Slack webhook alongside email —
  useful if this ever becomes something for more than one person to watch.
- **Anchor investor allocation tracking.** SEBI/exchanges publish anchor
  investor lists (which institutions bought in pre-listing) — a real,
  official quality signal that's currently not tracked at all.
- **A minimal read-only dashboard** (even a static page regenerated on each
  cron run) — lower effort than a scoring model, and useful on its own for
  browsing what's been discovered so far.

## Historical datasets for the validate-first backtest (found 2026-09-13)

Checked these via web search + fetch, not from memory — links and what each
actually contains, most promising first.

1. **[rohanbeingsocial/ipo-radar](https://github.com/rohanbeingsocial/ipo-radar)
   — start here.** An already-built RHP scoring engine + 20-year dataset for
   NSE/BSE mainboard IPOs, self-updating daily via GitHub Actions, published
   weekly to Kaggle. Its dataset already has almost everything this project
   needs in one place: **ROE, ROCE, debt-to-equity, PAT/EBITDA margins,
   post-issue P/E** (fundamentals), **QIB/retail/anchor subscription %**
   (subscription ratio), **grey-market premium pre-listing** (GMP), and
   **listing-day gains + 20-year price paths** (the outcome variable) — all
   per IPO, all traced back to specific RHP page numbers. There's also a live
   companion app: https://ipo-tracker-woad.vercel.app/. This is close enough
   to "someone already built the thing you're validating against" that it's
   worth reading the repo's scoring methodology even if you don't use its
   data directly — 25 rules across 10 categories, deliberately explainable
   rather than a black box, which matches what you want.
   *Didn't confirm:* the exact Kaggle dataset URL (the repo describes weekly
   publication but I couldn't pin down the live listing) — check the GitHub
   repo's README/releases or the linked Kaggle profile directly.

2. **[Indian IPO Listing Gain History (2010-2025)](https://www.kaggle.com/datasets/skworld/indian-ipo-dataset)**
   — 611 IPOs, Jan 2010–Nov 2025, framed around predicting listing gain from
   pre-listing demand metrics. Good size, recent.

3. **[IPO Data India 2010-2026](https://www.kaggle.com/datasets/karanammithul/ipo-data-india-2010-2025)**
   — subscription details by investor category + listing gains + current
   market performance.

4. **[IPO Watch: Debuts, GMPs, Trajectories](https://www.kaggle.com/datasets/nikhilraj7700/ipo-watch-debuts-gmps-trajectories)**
   — explicitly tracks GMP *as a trajectory* (values over time pre-listing,
   not just a single snapshot) plus subscription ratios. Most useful if you
   want GMP momentum as a feature, not just its final pre-listing value.

5. Smaller/older, worth a glance: [IPO & NIFTY 2010-2021](https://www.kaggle.com/datasets/balabaskar/indian-ipo-dataset-2010-2021),
   [IPO Mainboard and SME Basic Details](https://www.kaggle.com/datasets/warcoder/ipo-mainboard-and-sme-basic-details-india),
   [All Indian IPOs (Moneycontrol)](https://www.kaggle.com/datasets/soumyadipghorai/all-ipo-stocks-of-moneycontrol).

**For live, ongoing data collection later** (not the backtest, but a
replacement for the earlier "configure parse.bot against InvestorGain"
idea): **[Apify's India IPO Tracker](https://apify.com/data_daemon/india-ipo-tracker)**
is a ready-made actor covering GMP, subscription (QIB/institutional/retail),
listing performance, and a full IPO calendar, for both current and
historical IPOs, via REST API/MCP/CLI — no free tier, priced at $3 per 1,000
IPO-report rows. Worth comparing against the DIY parse.bot route once you
know roughly how many rows/month this project would actually need.

**Official ground truth, if you want to cross-verify listing gains
independently of any third-party dataset:** NSE's own IPO tracker
(nseindia.com/ipo-tracker?type=gain_listing_date) lists past IPOs by
listing-date gain/loss — I couldn't confirm a CSV/API export from it (the
page timed out on fetch), so treat it as a manual cross-check source, not
an ingestion pipeline, unless you confirm otherwise.

## Open questions for next week

1. Which of the three sourcing questions above (parse.bot's actual shape,
   Upstox's actual endpoints, and where subscription-ratio data will come
   from) do you want to verify before or during the discussion?
2. Does "market sentiment" survive as a fourth parameter, or do fundamentals
   + GMP + subscription cover what you actually meant by it?
3. Approach A (validate-first) means no visible new email content for a
   while — is that an acceptable trade-off, or do you want a faster, rougher
   version live sooner even if the weights start as a guess (Approach B)?
4. How far back is "historical" for the backtest dataset — 1 year, 2, more?
   Older data is easier to find but Indian IPO market conditions (retail
   participation, SME board growth) have shifted enough that very old data
   may not generalize well.
