"""
earnings_quality_tools.py — Tools for the Earnings Quality & Alpha Signals agent.

Fetches and computes metrics related to:
- GAAP vs non-GAAP earnings gaps
- Share-based compensation (SBC) analysis
- Short interest data
- Earnings quality (accruals, cash conversion)
- Off-balance-sheet items

Most data comes from SEC EDGAR (via sec_filings.py) and Yahoo Finance (via stock_data.py).
"""

import datetime
from typing import Optional

# Import our other tools for data fetching
from tools.sec_filings import get_specific_fact, get_recent_filings


def get_sbc_analysis(ticker: str) -> dict:
    """
    Analyze Share-Based Compensation (SBC) burden.

    Fetches SBC amounts from SEC EDGAR XBRL data and computes:
    - SBC as % of revenue
    - SBC as % of gross profit
    - SBC trend over 3 years
    - Flag level based on peer context

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with SBC analysis.
    """
    # Fetch SBC from SEC EDGAR
    sbc_data = get_specific_fact(ticker, "ShareBasedCompensation", "us-gaap")
    revenue_data = get_specific_fact(ticker, "Revenues", "us-gaap")

    # Also try RevenueFromContractWithCustomerExcludingAssessedTax (common alternative)
    if not revenue_data.get("values"):
        revenue_data = get_specific_fact(
            ticker,
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "us-gaap",
        )

    gross_profit_data = get_specific_fact(ticker, "GrossProfit", "us-gaap")

    # Extract annual values (form 10-K)
    sbc_values = [v for v in sbc_data.get("values", []) if v.get("form") == "10-K"][:3]
    revenue_values = [v for v in revenue_data.get("values", []) if v.get("form") == "10-K"][:3]
    gp_values = [v for v in gross_profit_data.get("values", []) if v.get("form") == "10-K"][:3]

    analyses = []
    for sbc in sbc_values:
        year = sbc.get("end_date", "")[:4]
        sbc_amount = sbc.get("value")

        # Find matching year revenue
        rev = next((r for r in revenue_values if r.get("end_date", "")[:4] == year), None)
        gp = next((g for g in gp_values if g.get("end_date", "")[:4] == year), None)

        sbc_pct_revenue = None
        sbc_pct_gross_profit = None

        if sbc_amount and rev and rev.get("value"):
            sbc_pct_revenue = round((sbc_amount / rev["value"]) * 100, 2)
        if sbc_amount and gp and gp.get("value"):
            sbc_pct_gross_profit = round((sbc_amount / gp["value"]) * 100, 2)

        analyses.append({
            "fiscal_year": year,
            "sbc_amount_usd": sbc_amount,
            "revenue_usd": rev["value"] if rev else None,
            "gross_profit_usd": gp["value"] if gp else None,
            "sbc_pct_of_revenue": sbc_pct_revenue,
            "sbc_pct_of_gross_profit": sbc_pct_gross_profit,
        })

    # Determine flag level based on most recent year
    flag = "🟢 Clean"
    if analyses:
        recent_sbc_pct = analyses[0].get("sbc_pct_of_revenue")
        if recent_sbc_pct is not None:
            if recent_sbc_pct > 15:
                flag = "🔴 Red Flag — SBC exceeds 15% of revenue"
            elif recent_sbc_pct > 8:
                flag = "🟡 Watch — SBC is elevated (>8% of revenue)"

    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "SEC EDGAR XBRL + computed",
        "sbc_by_year": analyses,
        "flag": flag,
        "note": (
            "SBC is excluded from non-GAAP EPS by most companies. "
            "High SBC as % of revenue reduces 'true' free cash flow "
            "and represents real economic dilution to shareholders."
        ),
    }


def get_gaap_vs_nongaap_gap(ticker: str) -> dict:
    """
    Estimate the GAAP vs non-GAAP earnings gap.

    Computes using publicly available GAAP metrics from SEC EDGAR.
    The agent will supplement this with web search for disclosed non-GAAP adjustments.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with GAAP income metrics and data for gap analysis.
    """
    # Fetch key GAAP metrics
    net_income = get_specific_fact(ticker, "NetIncomeLoss", "us-gaap")
    operating_income = get_specific_fact(ticker, "OperatingIncomeLoss", "us-gaap")
    sbc = get_specific_fact(ticker, "ShareBasedCompensation", "us-gaap")
    rd_expense = get_specific_fact(ticker, "ResearchAndDevelopmentExpense", "us-gaap")
    restructuring = get_specific_fact(
        ticker,
        "RestructuringCharges",
        "us-gaap",
    )
    depreciation = get_specific_fact(
        ticker,
        "DepreciationDepletionAndAmortization",
        "us-gaap",
    )

    # Get the most recent 4 quarters for each
    def recent_annual(data, n=3):
        return [v for v in data.get("values", []) if v.get("form") == "10-K"][:n]

    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "SEC EDGAR XBRL",
        "gaap_metrics": {
            "net_income": recent_annual(net_income),
            "operating_income": recent_annual(operating_income),
            "sbc_expense": recent_annual(sbc),
            "rd_expense": recent_annual(rd_expense),
            "restructuring_charges": recent_annual(restructuring),
            "depreciation_amortization": recent_annual(depreciation),
        },
        "agent_instruction": (
            "Use the web_search tool to find the company's disclosed non-GAAP reconciliation "
            "table from the most recent earnings press release or 10-K. "
            "Compare GAAP net income (above) with disclosed adjusted/non-GAAP EPS "
            "to compute the full adjustment gap. "
            "Flag any adjustment that has recurred for 3+ consecutive years as non-recurring in name only."
        ),
    }


def get_accruals_analysis(ticker: str) -> dict:
    """
    Compute earnings quality metrics: accruals ratio and cash conversion.

    Accruals Ratio = (Net Income - Operating Cash Flow) / Total Assets
    High positive accruals = potentially aggressive earnings recognition.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with accruals analysis.
    """
    # Fetch from SEC EDGAR
    net_income = get_specific_fact(ticker, "NetIncomeLoss", "us-gaap")
    op_cash_flow = get_specific_fact(
        ticker,
        "NetCashProvidedByUsedInOperatingActivities",
        "us-gaap",
    )
    total_assets = get_specific_fact(ticker, "Assets", "us-gaap")
    capex = get_specific_fact(
        ticker,
        "PaymentsToAcquirePropertyPlantAndEquipment",
        "us-gaap",
    )

    def recent_annual(data, n=3):
        return [v for v in data.get("values", []) if v.get("form") == "10-K"][:n]

    ni_vals = recent_annual(net_income)
    ocf_vals = recent_annual(op_cash_flow)
    assets_vals = recent_annual(total_assets)
    capex_vals = recent_annual(capex)

    accruals = []
    for ni in ni_vals:
        year = ni.get("end_date", "")[:4]
        ni_val = ni.get("value")

        ocf = next((o for o in ocf_vals if o.get("end_date", "")[:4] == year), None)
        assets = next((a for a in assets_vals if a.get("end_date", "")[:4] == year), None)
        capex_item = next((c for c in capex_vals if c.get("end_date", "")[:4] == year), None)

        accruals_ratio = None
        fcf = None
        cash_conversion = None

        if ni_val and ocf and ocf.get("value") and assets and assets.get("value"):
            accruals_ratio = round(
                (ni_val - ocf["value"]) / assets["value"] * 100, 2
            )

        if ocf and ocf.get("value") and capex_item and capex_item.get("value"):
            fcf = ocf["value"] - abs(capex_item["value"])
            if ni_val and ni_val != 0:
                cash_conversion = round(fcf / ni_val, 2)

        flag = "🟢 Clean"
        if accruals_ratio is not None:
            if accruals_ratio > 5:
                flag = "🔴 Red Flag — high accruals (earnings ahead of cash)"
            elif accruals_ratio > 2:
                flag = "🟡 Watch — elevated accruals"

        accruals.append({
            "fiscal_year": year,
            "net_income": ni_val,
            "operating_cash_flow": ocf["value"] if ocf else None,
            "total_assets": assets["value"] if assets else None,
            "capex": abs(capex_item["value"]) if capex_item else None,
            "free_cash_flow": fcf,
            "accruals_ratio_pct": accruals_ratio,
            "fcf_to_net_income": cash_conversion,
            "flag": flag,
        })

    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "SEC EDGAR XBRL + computed",
        "accruals_by_year": accruals,
        "note": (
            "Accruals ratio > 0 means net income exceeds cash from operations (earnings "
            "recognized but not yet collected). Persistently high accruals may indicate "
            "aggressive revenue recognition. FCF-to-net-income < 0.8 warrants scrutiny."
        ),
    }


def get_deferred_revenue_trend(ticker: str) -> dict:
    """
    Fetch deferred revenue trend — a quality indicator for subscription/SaaS businesses.

    Growing deferred revenue = cash collected ahead of recognition (good quality signal).
    Shrinking deferred revenue relative to revenue = potential pull-forward or churn risk.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with deferred revenue data.
    """
    deferred = get_specific_fact(ticker, "DeferredRevenueCurrent", "us-gaap")
    revenue = get_specific_fact(ticker, "Revenues", "us-gaap")

    def recent_annual(data, n=4):
        return [v for v in data.get("values", []) if v.get("form") == "10-K"][:n]

    deferred_vals = recent_annual(deferred)
    revenue_vals = recent_annual(revenue)

    results = []
    for d in deferred_vals:
        year = d.get("end_date", "")[:4]
        d_val = d.get("value")
        rev = next((r for r in revenue_vals if r.get("end_date", "")[:4] == year), None)

        deferred_pct = None
        if d_val and rev and rev.get("value"):
            deferred_pct = round((d_val / rev["value"]) * 100, 2)

        results.append({
            "fiscal_year": year,
            "deferred_revenue": d_val,
            "total_revenue": rev["value"] if rev else None,
            "deferred_as_pct_revenue": deferred_pct,
        })

    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "SEC EDGAR XBRL",
        "deferred_revenue_trend": results,
    }


def get_goodwill_analysis(ticker: str) -> dict:
    """
    Analyze goodwill as a proportion of total assets — impairment risk indicator.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with goodwill analysis and impairment risk flag.
    """
    goodwill = get_specific_fact(ticker, "Goodwill", "us-gaap")
    total_assets = get_specific_fact(ticker, "Assets", "us-gaap")
    intangibles = get_specific_fact(ticker, "IntangibleAssetsNetExcludingGoodwill", "us-gaap")

    def recent_annual(data, n=3):
        return [v for v in data.get("values", []) if v.get("form") == "10-K"][:n]

    gw_vals = recent_annual(goodwill)
    asset_vals = recent_annual(total_assets)
    intangible_vals = recent_annual(intangibles)

    results = []
    for gw in gw_vals:
        year = gw.get("end_date", "")[:4]
        gw_val = gw.get("value")
        assets = next((a for a in asset_vals if a.get("end_date", "")[:4] == year), None)
        intang = next((i for i in intangible_vals if i.get("end_date", "")[:4] == year), None)

        gw_pct = None
        total_intangible_pct = None

        if gw_val and assets and assets.get("value"):
            gw_pct = round((gw_val / assets["value"]) * 100, 2)

        combined = (gw_val or 0) + (intang["value"] if intang else 0)
        if combined and assets and assets.get("value"):
            total_intangible_pct = round((combined / assets["value"]) * 100, 2)

        flag = "🟢 Clean"
        if gw_pct is not None:
            if gw_pct > 50:
                flag = "🔴 Red Flag — goodwill > 50% of total assets; high impairment risk"
            elif gw_pct > 30:
                flag = "🟡 Watch — elevated goodwill concentration"

        results.append({
            "fiscal_year": year,
            "goodwill": gw_val,
            "intangibles_excl_goodwill": intang["value"] if intang else None,
            "total_assets": assets["value"] if assets else None,
            "goodwill_pct_assets": gw_pct,
            "total_intangibles_pct_assets": total_intangible_pct,
            "flag": flag,
        })

    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "SEC EDGAR XBRL",
        "goodwill_analysis": results,
        "note": (
            "High goodwill as % of assets indicates acquisitive growth strategy. "
            "Risk: if acquired businesses underperform, goodwill impairment charges "
            "can wipe out multiple years of reported profits in a single quarter."
        ),
    }


def get_debt_analysis(ticker: str) -> dict:
    """
    Fetch debt structure data for capital structure analysis.

    Args:
        ticker: Stock ticker symbol

    Returns:
        Dictionary with debt levels and coverage metrics.
    """
    long_term_debt = get_specific_fact(ticker, "LongTermDebt", "us-gaap")
    current_portion = get_specific_fact(
        ticker,
        "LongTermDebtCurrent",
        "us-gaap",
    )
    interest_expense = get_specific_fact(
        ticker,
        "InterestExpense",
        "us-gaap",
    )
    ebitda_proxy = get_specific_fact(
        ticker,
        "OperatingIncomeLoss",
        "us-gaap",
    )
    depreciation = get_specific_fact(
        ticker,
        "DepreciationDepletionAndAmortization",
        "us-gaap",
    )

    def recent_annual(data, n=3):
        return [v for v in data.get("values", []) if v.get("form") == "10-K"][:n]

    ltd_vals = recent_annual(long_term_debt)
    interest_vals = recent_annual(interest_expense)
    ebitda_vals = recent_annual(ebitda_proxy)
    da_vals = recent_annual(depreciation)

    results = []
    for ltd in ltd_vals:
        year = ltd.get("end_date", "")[:4]
        ltd_val = ltd.get("value")

        interest = next((i for i in interest_vals if i.get("end_date", "")[:4] == year), None)
        ebit = next((e for e in ebitda_vals if e.get("end_date", "")[:4] == year), None)
        da = next((d for d in da_vals if d.get("end_date", "")[:4] == year), None)

        ebitda = None
        interest_coverage = None

        if ebit and ebit.get("value") and da and da.get("value"):
            ebitda = ebit["value"] + da["value"]

        if ebitda and interest and interest.get("value") and interest["value"] != 0:
            interest_coverage = round(ebitda / abs(interest["value"]), 2)

        results.append({
            "fiscal_year": year,
            "long_term_debt": ltd_val,
            "interest_expense": interest["value"] if interest else None,
            "ebitda_approx": ebitda,
            "interest_coverage_ratio": interest_coverage,
        })

    return {
        "ticker": ticker,
        "fetched_date": datetime.date.today().isoformat(),
        "source": "SEC EDGAR XBRL + computed",
        "debt_analysis": results,
    }
