"""
quant_tools.py — Technical indicators and statistical models.

Shared by both Quant Modeler agents (equity and macro).
Uses: pandas, numpy, scipy, statsmodels, ta (technical analysis)

No API keys required — all calculations are performed on data
already fetched by stock_data.py or macro_data.py.
"""

import math
import datetime
import statistics
from typing import Optional


# ── Technical Indicators ───────────────────────────────────────────────────────

def compute_rsi(prices: list, period: int = 14) -> Optional[float]:
    """
    Compute the Relative Strength Index (RSI).

    Formula: RSI = 100 - (100 / (1 + RS))
    where RS = average gain / average loss over the period.

    Args:
        prices: List of closing prices (chronological order, oldest first)
        period: Lookback period (default: 14)

    Returns:
        RSI value (0–100) or None if insufficient data.
    """
    if len(prices) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(1, len(prices)):
        change = prices[i] - prices[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0.0)
        else:
            gains.append(0.0)
            losses.append(abs(change))

    # Use only the last `period` changes
    recent_gains = gains[-period:]
    recent_losses = losses[-period:]

    avg_gain = sum(recent_gains) / period
    avg_loss = sum(recent_losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)


def compute_sma(prices: list, period: int) -> Optional[float]:
    """
    Compute Simple Moving Average (SMA).

    Args:
        prices: List of closing prices (chronological order)
        period: Lookback period (e.g., 20, 50, 200)

    Returns:
        SMA value or None if insufficient data.
    """
    if len(prices) < period:
        return None
    return round(sum(prices[-period:]) / period, 4)


def compute_ema(prices: list, period: int) -> Optional[float]:
    """
    Compute Exponential Moving Average (EMA).

    Args:
        prices: List of closing prices (chronological order)
        period: Lookback period

    Returns:
        EMA value or None if insufficient data.
    """
    if len(prices) < period:
        return None

    multiplier = 2 / (period + 1)
    ema = sum(prices[:period]) / period  # Seed with SMA

    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema

    return round(ema, 4)


def compute_macd(prices: list, fast: int = 12, slow: int = 26, signal: int = 9) -> dict:
    """
    Compute MACD (Moving Average Convergence Divergence).

    MACD line = 12-day EMA - 26-day EMA
    Signal line = 9-day EMA of MACD line
    Histogram = MACD line - Signal line

    Args:
        prices: List of closing prices (chronological order)
        fast: Fast EMA period (default: 12)
        slow: Slow EMA period (default: 26)
        signal: Signal line EMA period (default: 9)

    Returns:
        Dictionary with MACD line, signal line, histogram, and interpretation.
    """
    if len(prices) < slow + signal:
        return {"error": "Insufficient data", "required_minimum": slow + signal}

    # Phase 3C: O(n) incremental EMA — replaces the O(n²) approach that called
    # compute_ema(prices[:i+1], ...) in a loop (each call re-iterated the full array).
    k_fast = 2 / (fast + 1)
    k_slow = 2 / (slow + 1)

    # Seed both EMAs with the SMA of the first `slow` prices
    ema_fast = sum(prices[:slow]) / slow
    ema_slow = sum(prices[:slow]) / slow

    # Wind fast EMA forward — it has `slow - fast` extra periods to apply
    for price in prices[fast:slow]:
        ema_fast = (price - ema_fast) * k_fast + ema_fast

    # Collect MACD values from index `slow` onward (both EMAs now in sync)
    macd_values = []
    for price in prices[slow:]:
        ema_fast = (price - ema_fast) * k_fast + ema_fast
        ema_slow = (price - ema_slow) * k_slow + ema_slow
        macd_values.append(ema_fast - ema_slow)

    if len(macd_values) < signal:
        return {"error": "Insufficient MACD values for signal line"}

    current_macd = macd_values[-1]

    # Signal line = EMA of MACD values
    multiplier = 2 / (signal + 1)
    signal_line = sum(macd_values[:signal]) / signal
    for m in macd_values[signal:]:
        signal_line = (m - signal_line) * multiplier + signal_line

    histogram = current_macd - signal_line

    return {
        "macd_line": round(current_macd, 4),
        "signal_line": round(signal_line, 4),
        "histogram": round(histogram, 4),
        "crossover": "Bullish" if current_macd > signal_line else "Bearish",
        "momentum": "Strengthening" if histogram > 0 else "Weakening",
    }


def compute_bollinger_bands(prices: list, period: int = 20, num_std: float = 2.0) -> dict:
    """
    Compute Bollinger Bands.

    Middle Band = 20-day SMA
    Upper Band = Middle Band + (2 × std dev)
    Lower Band = Middle Band - (2 × std dev)

    Args:
        prices: List of closing prices (chronological order)
        period: SMA period (default: 20)
        num_std: Number of standard deviations (default: 2.0)

    Returns:
        Dictionary with upper, middle, lower bands and current price position.
    """
    if len(prices) < period:
        return {"error": "Insufficient data"}

    recent = prices[-period:]
    middle = sum(recent) / period
    variance = sum((p - middle) ** 2 for p in recent) / period
    std = math.sqrt(variance)

    upper = middle + (num_std * std)
    lower = middle - (num_std * std)
    current = prices[-1]

    bandwidth = (upper - lower) / middle * 100  # % of middle band
    pct_b = (current - lower) / (upper - lower) if (upper - lower) != 0 else 0.5

    return {
        "upper_band": round(upper, 4),
        "middle_band": round(middle, 4),
        "lower_band": round(lower, 4),
        "current_price": round(current, 4),
        "bandwidth_pct": round(bandwidth, 2),
        "percent_b": round(pct_b, 4),  # 0 = at lower, 1 = at upper, >1 = above upper
        "position": (
            "Above upper band (overbought)" if current > upper
            else "Below lower band (oversold)" if current < lower
            else "Within bands"
        ),
    }


def compute_atr(highs: list, lows: list, closes: list, period: int = 14) -> Optional[float]:
    """
    Compute Average True Range (ATR) — a volatility measure.

    True Range = max(High-Low, |High-PrevClose|, |Low-PrevClose|)
    ATR = 14-day average of True Range

    Args:
        highs: List of high prices
        lows: List of low prices
        closes: List of closing prices
        period: Lookback period (default: 14)

    Returns:
        ATR value or None if insufficient data.
    """
    if len(highs) < period + 1:
        return None

    true_ranges = []
    for i in range(1, len(closes)):
        tr = max(
            highs[i] - lows[i],
            abs(highs[i] - closes[i - 1]),
            abs(lows[i] - closes[i - 1]),
        )
        true_ranges.append(tr)

    return round(sum(true_ranges[-period:]) / period, 4)


# ── Volatility & Risk ──────────────────────────────────────────────────────────

def compute_historical_volatility(prices: list, annualize: bool = True) -> Optional[float]:
    """
    Compute historical (realized) volatility from daily returns.

    Args:
        prices: List of closing prices (chronological order)
        annualize: If True, annualize using sqrt(252) — assumes daily data.

    Returns:
        Volatility as a decimal (e.g., 0.25 = 25% annualized) or None.
    """
    if len(prices) < 2:
        return None

    log_returns = [
        math.log(prices[i] / prices[i - 1])
        for i in range(1, len(prices))
        if prices[i - 1] > 0
    ]

    if len(log_returns) < 2:
        return None

    mean = sum(log_returns) / len(log_returns)
    variance = sum((r - mean) ** 2 for r in log_returns) / (len(log_returns) - 1)
    daily_vol = math.sqrt(variance)

    if annualize:
        return round(daily_vol * math.sqrt(252), 4)
    return round(daily_vol, 6)


def compute_max_drawdown(prices: list) -> dict:
    """
    Compute maximum drawdown: the largest peak-to-trough decline.

    Args:
        prices: List of closing prices (chronological order)

    Returns:
        Dictionary with max drawdown %, peak date index, and trough date index.
    """
    if len(prices) < 2:
        return {"max_drawdown_pct": None}

    peak = prices[0]
    peak_idx = 0
    max_drawdown = 0.0
    trough_idx = 0

    for i, price in enumerate(prices):
        if price > peak:
            peak = price
            peak_idx = i
        drawdown = (price - peak) / peak
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            trough_idx = i

    return {
        "max_drawdown_pct": round(max_drawdown * 100, 2),
        "peak_index": peak_idx,
        "trough_index": trough_idx,
        "note": "Negative value indicates drawdown (e.g., -30.5 means 30.5% decline from peak)",
    }


def compute_var(returns: list, confidence: float = 0.95) -> Optional[float]:
    """
    Compute Value at Risk (VaR) using the historical simulation method.

    Args:
        returns: List of daily returns as decimals (e.g., [-0.02, 0.01, ...])
        confidence: Confidence level (e.g., 0.95 for 95% VaR)

    Returns:
        VaR as a decimal (e.g., -0.025 = 2.5% loss threshold) or None.
    """
    if len(returns) < 20:
        return None

    sorted_returns = sorted(returns)
    index = int((1 - confidence) * len(sorted_returns))
    return round(sorted_returns[index], 4)


# ── Statistical Models ─────────────────────────────────────────────────────────

def compute_beta(asset_returns: list, benchmark_returns: list) -> dict:
    """
    Compute beta using Ordinary Least Squares (OLS) regression.

    Beta = Cov(asset, benchmark) / Var(benchmark)

    Args:
        asset_returns: List of asset daily returns
        benchmark_returns: List of benchmark daily returns (same length)

    Returns:
        Dictionary with beta, alpha (Jensen's), R-squared, and standard error.
    """
    if len(asset_returns) != len(benchmark_returns):
        return {"error": "Return series must be the same length"}
    if len(asset_returns) < 30:
        return {"error": "Fewer than 30 observations — low confidence result", "n": len(asset_returns)}

    n = len(asset_returns)
    mean_x = sum(benchmark_returns) / n
    mean_y = sum(asset_returns) / n

    cov_xy = sum((benchmark_returns[i] - mean_x) * (asset_returns[i] - mean_y) for i in range(n)) / (n - 1)
    var_x = sum((benchmark_returns[i] - mean_x) ** 2 for i in range(n)) / (n - 1)

    if var_x == 0:
        return {"error": "Zero variance in benchmark — cannot compute beta"}

    beta = cov_xy / var_x
    alpha = mean_y - beta * mean_x

    # R-squared
    y_hat = [alpha + beta * x for x in benchmark_returns]
    ss_res = sum((asset_returns[i] - y_hat[i]) ** 2 for i in range(n))
    ss_tot = sum((asset_returns[i] - mean_y) ** 2 for i in range(n))
    r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0

    # Standard error of beta
    s_squared = ss_res / (n - 2)
    se_beta = math.sqrt(s_squared / ((n - 1) * var_x)) if var_x != 0 else None

    return {
        "beta": round(beta, 4),
        "alpha_annualized": round(alpha * 252, 4),
        "r_squared": round(r_squared, 4),
        "standard_error": round(se_beta, 4) if se_beta else None,
        "n_observations": n,
        "interpretation": (
            f"For every 1% move in the benchmark, the asset historically moves {round(beta, 2)}%. "
            f"The regression explains {round(r_squared * 100, 1)}% of return variance (R²)."
        ),
    }


def compute_correlation(series_a: list, series_b: list) -> dict:
    """
    Compute Pearson correlation coefficient between two return series.

    Args:
        series_a: First return series
        series_b: Second return series (same length)

    Returns:
        Dictionary with correlation, R-squared, and interpretation.
    """
    if len(series_a) != len(series_b) or len(series_a) < 10:
        return {"error": "Series must be same length and at least 10 observations"}

    n = len(series_a)
    mean_a = sum(series_a) / n
    mean_b = sum(series_b) / n

    cov = sum((series_a[i] - mean_a) * (series_b[i] - mean_b) for i in range(n)) / (n - 1)
    std_a = math.sqrt(sum((x - mean_a) ** 2 for x in series_a) / (n - 1))
    std_b = math.sqrt(sum((x - mean_b) ** 2 for x in series_b) / (n - 1))

    if std_a == 0 or std_b == 0:
        return {"error": "Zero standard deviation in one series"}

    corr = cov / (std_a * std_b)

    return {
        "correlation": round(corr, 4),
        "r_squared": round(corr ** 2, 4),
        "n_observations": n,
        "interpretation": (
            "Strong positive" if corr > 0.7
            else "Moderate positive" if corr > 0.3
            else "Weak/no correlation" if corr > -0.3
            else "Moderate negative" if corr > -0.7
            else "Strong negative"
        ),
    }


def compute_skewness_kurtosis(returns: list) -> dict:
    """
    Compute skewness and excess kurtosis of return distribution.
    Used to assess if returns are normally distributed.

    Args:
        returns: List of returns

    Returns:
        Dictionary with skewness, kurtosis, and normality flag.
    """
    if len(returns) < 20:
        return {"error": "Need at least 20 observations"}

    n = len(returns)
    mean = sum(returns) / n
    variance = sum((r - mean) ** 2 for r in returns) / n
    std = math.sqrt(variance)

    if std == 0:
        return {"error": "Zero standard deviation"}

    # Skewness
    skew = sum(((r - mean) / std) ** 3 for r in returns) / n

    # Excess kurtosis (normal distribution has kurtosis = 3, excess = 0)
    kurt = sum(((r - mean) / std) ** 4 for r in returns) / n - 3

    return {
        "skewness": round(skew, 4),
        "excess_kurtosis": round(kurt, 4),
        "n_observations": n,
        "normality_flag": (
            "Approximately normal" if abs(skew) < 0.5 and abs(kurt) < 1
            else "Moderately non-normal — fat tails or asymmetry present"
            if abs(skew) < 1 and abs(kurt) < 3
            else "Highly non-normal — significant tail risk or skew"
        ),
        "interpretation": (
            f"Skew = {round(skew, 2)} ({'left-tail heavy (more big losses)' if skew < -0.5 else 'right-tail heavy (more big gains)' if skew > 0.5 else 'near-symmetric'}). "
            f"Excess kurtosis = {round(kurt, 2)} ({'fat tails — extreme events more likely than normal distribution predicts' if kurt > 1 else 'thin tails — extreme events less likely than normal'})."
        ),
    }


# ── Econometric Tools (for Macro Quant Modeler) ────────────────────────────────

def simple_linear_regression(x: list, y: list, x_label: str = "X", y_label: str = "Y") -> dict:
    """
    Run a simple OLS linear regression: Y = alpha + beta * X + error

    Args:
        x: Independent variable values
        y: Dependent variable values
        x_label: Label for the independent variable (for interpretation)
        y_label: Label for the dependent variable (for interpretation)

    Returns:
        Dictionary with regression coefficients, R², p-value, and interpretation.
    """
    if len(x) != len(y):
        return {"error": "X and Y must be the same length"}
    if len(x) < 10:
        return {"error": "Need at least 10 observations for regression"}

    n = len(x)
    mean_x = sum(x) / n
    mean_y = sum(y) / n

    ss_xy = sum((x[i] - mean_x) * (y[i] - mean_y) for i in range(n))
    ss_xx = sum((x[i] - mean_x) ** 2 for i in range(n))

    if ss_xx == 0:
        return {"error": "No variance in X — regression not possible"}

    beta = ss_xy / ss_xx
    alpha = mean_y - beta * mean_x

    # Residuals and R²
    y_hat = [alpha + beta * xi for xi in x]
    residuals = [y[i] - y_hat[i] for i in range(n)]
    ss_res = sum(r ** 2 for r in residuals)
    ss_tot = sum((y[i] - mean_y) ** 2 for i in range(n))
    r_squared = 1 - (ss_res / ss_tot) if ss_tot != 0 else 0

    # Standard error and t-statistic for beta
    se_squared = ss_res / (n - 2)
    se_beta = math.sqrt(se_squared / ss_xx)
    t_stat = beta / se_beta if se_beta != 0 else None

    # Approximate p-value using t-distribution (two-tailed, df = n-2)
    # Using a simple approximation for the p-value
    p_value_approx = None
    if t_stat is not None and n > 2:
        df = n - 2
        # Approximate p-value: if |t| > 2, p < 0.05 (rough rule of thumb)
        abs_t = abs(t_stat)
        if abs_t > 3.0:
            p_value_approx = "< 0.01 (highly significant)"
        elif abs_t > 2.0:
            p_value_approx = "< 0.05 (significant)"
        elif abs_t > 1.65:
            p_value_approx = "< 0.10 (marginally significant)"
        else:
            p_value_approx = "> 0.10 (not statistically significant)"

    return {
        "alpha_intercept": round(alpha, 6),
        "beta_slope": round(beta, 6),
        "r_squared": round(r_squared, 4),
        "n_observations": n,
        "t_statistic": round(t_stat, 4) if t_stat else None,
        "p_value_approx": p_value_approx,
        "standard_error_beta": round(se_beta, 6),
        "interpretation": (
            f"For each 1-unit increase in {x_label}, {y_label} changes by {round(beta, 4)} units. "
            f"R² = {round(r_squared * 100, 1)}% — the model explains {round(r_squared * 100, 1)}% "
            f"of variation in {y_label}. {p_value_approx or 'Significance not computed.'}"
        ),
        "warning": "Low confidence — fewer than 30 observations" if n < 30 else None,
    }


def compute_yield_spread(short_rate: float, long_rate: float) -> dict:
    """
    Compute yield curve spread and its signal.

    Args:
        short_rate: Short-term rate (e.g., 3-month or 2-year yield)
        long_rate: Long-term rate (e.g., 10-year yield)

    Returns:
        Dictionary with spread value and recession signal interpretation.
    """
    spread_bps = round((long_rate - short_rate) * 100, 1)

    signal = (
        "Deeply inverted — historically high recession probability (Estrella-Mishkin model)"
        if spread_bps < -100
        else "Inverted — elevated recession risk"
        if spread_bps < 0
        else "Flat — transitioning; elevated but not alarming risk"
        if spread_bps < 50
        else "Positive slope — normal; growth-positive environment"
    )

    return {
        "short_rate": short_rate,
        "long_rate": long_rate,
        "spread_bps": spread_bps,
        "inverted": spread_bps < 0,
        "signal": signal,
    }


def compute_z_score(current_value: float, historical_values: list) -> dict:
    """
    Compute the z-score of a current value vs its historical distribution.
    Used to assess how extreme a reading is relative to history.

    Args:
        current_value: Current reading of an indicator
        historical_values: Historical readings (30+ observations recommended)

    Returns:
        Dictionary with z-score, percentile approximation, and interpretation.
    """
    if len(historical_values) < 10:
        return {"error": "Need at least 10 historical observations"}

    mean = sum(historical_values) / len(historical_values)
    variance = sum((x - mean) ** 2 for x in historical_values) / len(historical_values)
    std = math.sqrt(variance)

    if std == 0:
        return {"z_score": 0, "error": "No variance in historical data"}

    z = (current_value - mean) / std

    # Approximate percentile from z-score
    abs_z = abs(z)
    if abs_z > 3.0:
        pct_label = "Extreme (>99th or <1st percentile)"
    elif abs_z > 2.0:
        pct_label = "Very elevated (>97.5th or <2.5th percentile)"
    elif abs_z > 1.5:
        pct_label = "Elevated (>93rd or <7th percentile)"
    elif abs_z > 1.0:
        pct_label = "Above/below average (>84th or <16th percentile)"
    else:
        pct_label = "Near historical average"

    return {
        "current_value": current_value,
        "historical_mean": round(mean, 4),
        "historical_std": round(std, 4),
        "z_score": round(z, 2),
        "percentile_label": pct_label,
        "n_observations": len(historical_values),
        "interpretation": (
            f"Current value of {current_value} is {abs(round(z, 2))} standard deviations "
            f"{'above' if z > 0 else 'below'} the historical mean of {round(mean, 4)}. "
            f"{pct_label}."
        ),
    }
