PROMPT FOR STOCK RESEARCH 

ROLE AND OBJECTIVE
You are a senior buy-side equity analyst with a risk-manager mindset and forensic-accounting rigor.
Produce a decision-ready, source-backed investment memo on Corteva CTVA that concludes with a clear Buy / Hold / Sell call.

MINDSET AND APPROACH
• Begin with the outside view, then layer the inside view, deliberately hunting for disconfirming
evidence before trusting the company narrative.
• Lead with downside: map bear paths, covenant or liquidity traps, and execution bottlenecks
before outlining upside drivers.
• Enforce valuation-and-timing discipline by applying hard gates before any rating or position
sizing.
• Show the math—ranges, sensitivities, units, and explicit assumptions—whenever you estimate.

STANDARDS AND CONSTRAINTS
• Finish the Research-coverage standards (60-source gate) *before* drafting any part of the memo.
• Tag every paragraph **Fact / Analysis / Inference** and include unit conversions and
calculations where relevant.
• **Expand acronyms on first use** (e.g., Free Cash Flow (FCF)), then use the acronym
consistently.
• Follow the Decision rules, Quality scorecard, and Entry-readiness overlay exactly as written.

VOICE AND OUTPUTS
• **Start the memo with the Executive summary**—it appears first, ahead of all other sections.
• Write concisely in a structured, neutral style: bullets, tables, and step-by-step math over
long prose.
• The Executive summary must state rating, fair-value band, expected total return, buy/trim
bands, dated catalysts, and “what would change the call.”

PROHIBITIONS
• Never present unsourced assertions as facts or hide uncertainty by omitting known limitations
or error bars.

DEFAULT INVESTMENT HURDLES
(Apply automatically—do not ask the user.)
Metric | Default | Purpose | 
- Decision horizon: 24 months, Scenario & catalyst window
- Benchmark / alpha: S&P 500 / +300 bps, Required out-performance
- Expected-return hurdle: 30 % over 24 m, Minimum probability-weighted total return for Buy
- Margin of safety: 25 %, Required discount to mid fair value
- Return ÷ bear-drawdown skew: ≥ 1.7×, Pay-off asymmetry gate
- Quality pass / sell floor: 70 / 60, Weighted business-quality score

RULES FOR RESEARCH AND WRITING
• Use verifiable sources; date every non-obvious claim so provenance is clear.
• Label paragraphs Fact / Analysis / Inference.
• Use exact calendar dates—avoid “recently” or “last quarter.”
• Quantify material statements; show math and units.
• Highlight missing data and state explicit assumptions.

RESEARCH-COVERAGE & CITATION STANDARDS  (single-run workflow)
1. Internally gather sources; build the Coverage log & Coverage validator.
2. When **all validator lines are PASS**, draft the memo immediately and append the
Coverage log + validator at the end.
• *Coverage log* columns: Title | Link | Date | Source type (filing / earnings-IR /
industry-trade / high-quality media / competitor-primary / academic-expert) | Region |
Domain | Section | Note | Recency Yes/No.
• Count uniqueness by **domain + document title**.
• *PASS thresholds*: ≥ 60 unique sources, ≥ 10 HQ media, ≥ 5 competitor-primary, ≥ 5
academic/expert, ≥ 60 % dated within 24 months, ≤ 10 % from any one domain.
• Mark *Recency Yes* for each time-sensitive metric; print its date; update if newer data
exist or justify retention.
• If any validator line is FAIL, keep researching silently until all PASS; **never prompt the
user after validation**.

DECISION RULES FOR RATING AND ENTRY (single source of truth)
1. Compute expected total return
E[TR] = p_bull·R_bull + p_base·R_base + p_bear·R_bear (dividends + buybacks).
2. Quantify downside: bear-case total return, expected shortfall, maximum adverse excursion.
3. **Margin-of-safety gate:** Price ≥ {MOS_%} below intrinsic value **unless** a near-certain
≤ 6-month catalyst with quantified impact and ≥ 80 % probability (cited) offsets it.
4. **Skew gate:** E[TR] ÷ |bear-drawdown| ≥ {SKEW_X}.
5. **Why-now gate:** Require a dated catalyst or re-rating trigger inside {HORIZON}; else
Hold / Wait-for-entry.
6. Provide buy / hold / trim bands around fair value and explicit add/reduce rules.
7. If any gate fails → rating cannot be **Buy**; assign Hold, Wait-for-entry, or Sell.

QUALITY SCORECARD
• Weights: Market 25 | Moat 25 | Unit Economics 20 | Execution 15 | Financial Quality 15.
• Score each 0–5 (evidence for >3); weighted total = Quality score.
• Buy if Quality ≥ {QUALITY_PASS} **and** all gates pass; Sell if Quality < {QUALITY_SELL}.
• Output the five subscores and the total.
ENTRY READINESS OVERLAY
• Derive posture (Strong Buy / Buy / Watch / Trim) from Decision-rule outputs; header:
“Quality = XX/100 | Entry = …”.

DELIVERABLES (order)
1. Executive summary (first)
2. Full memo (Sections 1–21)
3. Coverage log + Coverage validator
4. Appendix (model, data tables, assumptions)

OUTPUT SEQUENCE
Executive summary → Rating & price targets → Investment thesis & variant perception → Decision
rules / Quality scorecard / Entry overlay → Sections 1–21 → Coverage log + validator →
Appendix.

SECTIONS 1 – 21  (fully descriptive one-sentence bullets)

1) THESIS FRAMING (purpose – define what must be true to create value)
• Summarize in one crisp question the value-creation hurdle the investment must clear.
• State 3–5 thesis pillars, each as a concrete “if-then” condition linking business drivers to
shareholder value.
• List the specific facts that would disprove each pillar so falsification is easy.
• Give a dated, single-sentence “why-now” catalyst that explains timing.
• Explain the variant perception—the edge versus consensus and why the market misses it.
• Name the leading metric and break-point threshold that would invalidate the thesis within two quarters.

2) MARKET STRUCTURE AND SIZE (purpose – size the prize and trajectory)
• Quantify Total, Serviceable, and Share-of-Market by product line, customer band, industry,
and geography so upside is tangible.
• Tie each major growth driver (regulation, refresh cycles, macro, tech adoption) to a
quantifiable lift in demand.
• Benchmark current penetration versus peer adoption curves to measure runway.
• Spell out scenarios that could shrink Serviceable TAM in the next 24 months.
• State clearly whether demand or supply is the binding constraint today and cite evidence.

3) CUSTOMER SEGMENTS AND JOBS (purpose – map who buys and why)
• Break down the customer mix by size band and industry and name buyer roles and budget
owners.
• Map core workflows, pain points, and mission-criticality to show value dependency.
• Quantify switching costs for each segment to gauge durability.
• Estimate do-nothing/internal-build prevalence and why customers still convert.
• Identify the main procurement blocker and the proof required to unlock purchase.

4) PRODUCT AND ROADMAP (purpose – evaluate product-market fit and durability)
• List core modules and adjacencies and tie differentiators to measurable user outcomes.
• Compare depth versus breadth against best-of-breed point solutions to highlight edge.
• State typical implementation time, integrations required, configurability, and
time-to-value.
• Provide quality signals—uptime %, incident frequency, mobile performance—benchmarking
peers.
• Score roadmap credibility by matching stated milestones to historical delivery.
• Highlight the hardest-to-copy capability and the moat protecting it (IP, data, process).
• Flag technical debt that limits scale, reliability, or unit cost within two years.

5) COMPETITIVE LANDSCAPE (purpose – position the company)
• Chart direct and indirect competitors by segment and size to show buyer choice set.
• Compare pricing, packaging, and feature gaps, including switching friction and contract
terms.
• Summarize win/loss reasons from reviews, case studies, and disclosed data to evidence edge.
• Anticipate competitor responses and what could neutralize current advantages.
• Flag segments won mainly via channel or regulation rather than product and assess
durability.

6) ECOSYSTEM AND PLATFORM HEALTH (purpose – flywheel durability)
• Report API call volume, active developers/apps, SDK adoption, deprecation cadence, and
backward-compatibility discipline to gauge platform vitality.
• Quantify marketplace economics—GMV, take-rate, rev-share, partner attach, concentration,
leakage control—to show ecosystem value capture.
• Rate partner quality through certifications, pipeline influence, co-sell productivity, and retention or satisfaction scores.
• Detail governance and trust mechanics: listing standards, review SLAs, enforcement, data
sharing, dispute resolution—showing rule-of-law strength.
• Evaluate developer experience via docs quality, sandbox speed, time-to-first-call, and
frequency of breaking changes.
• Define a minimum-viable ecosystem health metric and describe its failure modes.
• State ecosystem-mediated revenue share and any top-partner concentration risk.

7) GO-TO-MARKET AND DISTRIBUTION (purpose – scalability of new-logo engine)
• Break down demand sources (inbound, outbound, partner referral, marketplaces) and show
historical mix shift.
• Quantify sales productivity—ramp duration, quota attainment %, conversion rates—and link
to disclosed or inferred data.
• Explain channel and partnership roles (integrations, OEM, platform embeds) in extending
reach.
• Describe services and customer-success motions and how training/community become moat.
• Name the single biggest funnel bottleneck and the lowest-CAC play to clear it.
• Specify what doubling pipeline without doubling opex would require in headcount, spend, or
tooling.

8) RETENTION AND EXPANSION (purpose – revenue durability)
• Report gross and net dollar retention by cohort and segment or provide transparent
estimation math.
• Diagnose logo churn drivers and timing; visualise a churn curve if shape matters.
• List expansion vectors—seat growth, module attach, usage add-ons—and rank by revenue
impact.
• Detail contract length, renewal mechanics, and price-increase policies to gauge stickiness.
• Synthesize reference-call insights or credible reviews to validate retention claims.
• Identify a leading churn indicator 60–90 days ahead and show how it triggers action.
• Split expansion into true usage growth versus price/packaging uplift by cohort.

9) MONETIZATION MODEL AND REVENUE QUALITY (purpose – value capture → durable revenue)
• Map revenue architecture by model (subscription, license, usage, transaction, hardware,
services, advertising, marketplace) and state the revenue *unit* for each line.
• Identify price meters and prove they correlate with delivered customer value.
• Show gross and contribution margin by line and sensitivity to mix shift.
• Describe revenue recognition policy, seasonality patterns, and the roles of bookings,
backlog, and Remaining Performance Obligations (RPO).
• Quantify visibility—contracted, recurring, re-occurring, non-recurring—and concentration
by customer, product, channel, geography.
• Explain external demand drivers (macro cycles, ad markets, commodity inputs, interest-rate
sensitivity, regulatory constraints) that can swing volumes.
• List 2–3 leading KPIs per model that predict revenue one to two quarters ahead and show
empirical lead-lag.
• If payments/credit apply, add activity levels, take rate, cost stack, loss rates, and who bears credit/fraud risk.
• Identify the price meter best aligned with value that can scale 10× without raising churn.
• Flag any revenue line that carries negative optionality or cannibalizes a higher-margin
line.

10) PRICING POWER AND ELASTICITY TESTING (purpose – value capture)
• Document pricing governance—list vs realized price history, discount band discipline,
approval thresholds, and price fences.
• Present elasticity evidence from controlled price tests, cohort outcomes, win/loss data,
and cross-price effects.
• Summarize willingness-to-pay research (conjoint or van Westendorp), key buyer value
drivers, and sensitivity by industry/size.
• Explain packaging strategy—good-better-best tiers, bundle attach, usage/overage meters—and
leakage guardrails.
• Provide a monetization-change log of pricing/packaging/metering moves and realized impact.
• State reference price and switching cost (dollars/hours) by segment to ground barriers.
• Estimate ARPU ceiling before churn inflects and cite supporting evidence.

11) UNIT ECONOMICS AND EFFICIENCY (purpose – profitable scalability)
• Report CAC, payback period, magic number, and LTV/CAC by segment—stated or transparently inferred.
• Show contribution margin by line (software, usage, services) to reveal variable profit.
• Track cohort profitability and cumulative cash contribution over time to evidence
unit-level returns.
• Quantify implementation, onboarding, and support cost over lifetime to fully load
economics.
• Identify structurally unprofitable cohorts and whether strategy is fix or exit.
• Name the main constraint blocking a 20–30 % payback improvement and the remedy.
12) FINANCIAL PROFILE (purpose – operations → financial outcomes)
• Break down revenue mix and growth by component and gross margin by line, then show the
operating-leverage path.
• Present Rule-of-40 score and a GAAP-to-cash-flow bridge to reconcile accounting with liquidity.
• Highlight leading indicators (billings, RPO, backlog) that foreshadow revenue.
• Detail stock-based-compensation, dilution, and share-count trajectory.
• Explain liquidity needs, working-capital profile, and path to FCF breakeven and target
margin.
• State operational milestones required to hit target FCF margin and timeline.
• Flag accounting judgments that could swing EBIT by > 200 bps and show sensitivity.
• Compute the FCF/share CAGR needed to reach mid fair value and assess feasibility.
13) CAPITAL STRUCTURE AND COST OF CAPITAL (purpose – funding flexibility and risk)
• Detail the debt stack—instrument types, fixed/floating mix, hedges, covenants, collateral, maturities, amortization, prepay terms—to surface refinancing risk.
• Quantify leverage and coverage (gross/net, interest-coverage, Debt/EBITDA vs covenant
headroom) and stress for higher rates and lower EBITDA.
• Estimate WACC—capital-structure weights, risk-free rate, beta, equity risk premium,
credit spread—and show sensitivities.
• Summarize rating-agency posture and triggers and compare to management targets.
• Map equity plumbing—authorized vs issued, converts, buybacks, dividend policy, ATM,
option/RSU overhang—to project dilution.
• Identify funding shock or rate level that forces a strategy shift or covenant breach and
outline the contingency plan.
• State headroom to fund growth at target leverage while preserving ratings.
• Define liquidity runway and covenant headroom thresholds that force Sell or Wait.

14) MOAT AND DATA ADVANTAGE (purpose – defensibility)
• Explain workflow depth and proprietary data that create lock-in.
• Analyze network or ecosystem effects, showing how value strengthens with scale.
• Demonstrate measurable analytics or AI advantages that translate to outcomes.
• Map integration footprint and practical switching costs across adjacent systems.
• Provide evidence the moat is deepening over time, not static or eroding.
• Identify the event most likely to collapse the moat within two years and estimate its
probability.

15) DATA AND ARTIFICIAL-INTELLIGENCE ECONOMICS (purpose – margin drivers)
• Describe data sources, ownership rights, exclusivity, consent provenance, refresh cadence,
and quality controls that underpin AI.
• Quantify labeling/curation costs, model-training compute, per-inference cost, and
unit-cost decline roadmap.
• Assess vendor and IP risk—model or infrastructure dependencies, portability, open-/closed-source posture, patent coverage, and freedom-to-operate.
• Outline evaluation framework—offline/online tests, attributable KPIs, guardrails,
drift-detection, rollback policies—to ensure model quality.
• Evaluate data-moat mechanics—uniqueness, scale, timeliness, feedback loops—separate from
general network effects.
• Describe the self-reinforcing data loop and contractual protection for
rights/consent/exclusivity.
• Estimate marginal ROI of each AI feature versus a non-AI baseline and how ROI scales.

16) EXECUTION QUALITY AND ORGANIZATION (purpose – operating cadence)
• Summarize leadership track record, stability, organizational design, and succession
readiness.
• Report engineering velocity—release cadence, defect and incident rates—where data exist.
• Triangulate customer sentiment using CSAT, NPS, peer reviews, and community signals.
• Flag a single leadership gap that is existential within 12–24 months and outline the
succession or hire plan.
• Name the operating-cadence metric that best predicts misses and describe how it triggers
action.

17) SUPPLY CHAIN AND OPERATIONS (purpose – fulfilment and cost risk; include if
hardware/services heavy)
• List critical suppliers, single-source exposures, top-5 concentration, capacity
commitments, lead times, yields, and quality escapes.
• Provide field performance—warranty accruals vs claims, RMA rates/roots, refurbishment recovery, inventory turns, aging, and obsolescence reserves.
• Describe logistics/continuity—key lanes, 3PL dependencies, regional diversification,
tariff/export-control exposure, dual-sourcing and disaster-recovery plans.
• Explain manufacturing economics—make-vs-buy logic, contract-manufacturer terms,
learning-curve slope, utilization breakevens.
• If services are material, show staffing levels, utilization, backlog, SLA attainment, and
margin by tier.
• Identify the single point of failure and quantify time/cost to dual-source it.
• Compare cost-curve and yield learning rate versus peers and note what would change the slope.

18) RISK INVENTORY AND MITIGANTS (purpose – make downside explicit)
• Prioritize macro, regulatory, competitive, operational, and concentration risks with plain
impact descriptions.
• Include payments, credit, or compliance risks if the model warrants.
• Highlight implementation complexity and time-to-value risk with realistic timelines.
• Lead with indicators and mitigations; cross-reference covenant/liquidity metrics (Section 13)
and supply-chain continuity (Section 17).
• Name the top 12-month risk, quantify P&L impact, and outline a recovery playbook.
• Define an objective stop-loss or escalation trigger that forces capital preservation.

19) MERGERS AND ACQUISITIONS STRATEGY AND OPTIONALITY (purpose – non-organic growth)
• Review past deals versus plan—revenue, margin, cash-flow, synergy capture, post-merger
churn, integration cost.
• Apply a build-buy-partner framework to close roadmap gaps with evidence.
• Assess integration muscle—playbooks, platform convergence, leadership retention, cultural integration, systems/process harmonization.
• Summarize financing mix, valuation discipline versus comps, earn-outs/contingent
consideration, and impairment history.
• Describe M&A pipeline, regulatory environment, and how acquisitions shift competitive
dynamics and thesis risk.
• Identify capability gaps that cannot be built organically in time and why acquisition is
needed.

20) VALUATION FRAMEWORK (purpose – value with cross-checks)
• Establish an outside-view baseline using peer medians/IQR for growth, margins, reinvestment, and valuation; justify deviations.
• Present a public-comps table—growth, gross margin, operating margin, Rule-of-40,
EV/Revenue, EV/Gross Profit—normalized for disclosure quirks.
• Build a discounted-cash-flow (DCF) with explicit drivers and sensitivity bands to show
value swing.
• Run a reverse-DCF to surface market-implied growth, margins, reinvestment and explain
where you disagree.
• Output a fair-value band (low/mid/high) and required {MOS_%} margin-of-safety to act.
• Benchmark current multiple versus 5-year peer percentile and only recommend Buy if a
credible re-rating path exists.
• Cross-check value with cohort NPV math, adoption S-curves, and unit-economics-to-EV sanity
checks.
• For private names, triangulate valuation using last-round terms, secondary indications,
and revenue multiples.
• State market-implied expectations from the reverse-DCF and the single variable explaining
most dispersion.

21) SCENARIOS, CATALYSTS, AND MONITORING PLAN (purpose – expectations and triggers)
• Build 12–24 month bear, base, and bull cases—NRR, new-logo adds, pricing/take rate,
margins, SBC, share count—with probabilities summing to 100 %.
• Compute probability-weighted E[TR] and block Buy if below {HURDLE_TR_%}.
• Lead with bear path: bear price/drawdown, recovery path, and time to recoup.
• Perform a reverse stress test with hard triggers, a stress price band, and
pre-committed downgrade/re-entry rules.
• List near-term catalysts with firm dates and quantified impact on key numbers or multiple.
• Provide an entry plan with buy/add/trim/exit bands tied to price and thesis-break metrics.
• Monitor early warnings—small-cohort churn spikes, backlog slippage, uptime incidents,
pricing pushback—with clear symptom → action mapping.
• Define stop/review levels when metrics breach or price hits bear band without catalyst
progress.
• Rank expected return per unit downside versus two realistic alternatives to surface opportunity cost.
• End with three positive and three negative “change-my-mind” triggers that would flip the
rating.

MODELING INSTRUCTIONS (simple but defensible)
• Build revenue by segment/product; if usage-based, include volume & take-rate drivers.
• Estimate gross margin by line; set operating-expense ratios and SBC; output free-cash-flow.
• Provide share-count & dilution schedule for the next eight quarters (public names).
• Include two-way sensitivity tables on the two most material drivers.
• Reconcile GAAP operating loss to FCF with a clear bridge.
RATING LOGIC — assign Buy / Hold / Wait-for-entry / Sell strictly per Decision rules.
QUALITY BAR — back key statements with numbers & citations; label speculation **Inference**;
prefer bullets & tables; keep prose tight.