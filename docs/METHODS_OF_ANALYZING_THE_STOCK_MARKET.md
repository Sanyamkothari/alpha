# Methods of Analyzing the Stock Market (Section 2.5)

**Official Reference:** [WorldQuant BRAIN Support — 2.5 Methods of Analyzing the Stock Market (Article 12199923438231)](https://support.worldquantbrain.com/hc/en-us/articles/12199923438231--2-5-Methods-of-Analyzing-the-Stock-Market)

---

## 1. Overview

In quantitative finance and on the **WorldQuant BRAIN** platform, alpha design begins with understanding the core analytical disciplines used to evaluate financial markets. Market participants and quantitative researchers primarily use **Fundamental Analysis**, **Technical Analysis**, and their synthesis through **Quantitative Modeling** to generate predictive trading signals.

```
                              MARKET ANALYSIS DISCIPLINES
                                           │
         ┌─────────────────────────────────┼─────────────────────────────────┐
         ▼                                 ▼                                 ▼
  1. FUNDAMENTAL                    2. TECHNICAL                      3. QUANTITATIVE
     ANALYSIS                          ANALYSIS                          SYNTHESIS
  Intrinsic Value & Accounting      Price Action & Volume Trends      Systematic Multi-Factor Models
  (Financial Statements, Earnings)  (Momentum, Mean Reversion)        (AST Expressions & Cross-Sectional)
```

---

## 2. Fundamental Analysis

### 2.1 Core Principle
Fundamental analysis evaluates a security by examining its **intrinsic value** based on underlying economic, financial, and operational factors. If intrinsic value exceeds current market price, the asset is undervalued (long signal); if market price exceeds intrinsic value, it is overvalued (short signal).

### 2.2 Key Fundamental Data Categories on BRAIN
* **Income Statement Metrics:** Operating revenue, EBIT/EBITDA, gross profit, net income, operating margins.
* **Balance Sheet Metrics:** Total assets, current liabilities, short/long-term debt, shareholder equity, book value, cash & equivalents.
* **Cash Flow Dynamics:** Operating cash flow (CFO), cash flow from financing (CFF), free cash flow (FCF), capital expenditures (CapEx).
* **Analyst Estimates & Revisions (`anl4_*`):** Consensus earnings estimates, EPS revisions, revenue forecasts, price target adjustments.

### 2.3 Mathematical Representation in Fast Expressions
Fundamental data is reported periodically (quarterly/annually) and requires forward filling to prevent stale or lookahead artifacts:

```c
// Value Signal: Pretax profit normalized by market capitalization
rank(ts_zscore(divide(ts_backfill(max_reported_pretax_profit_quarterly_estimate, 120), cap), 63))

// Quality & Solvency Signal: Operating cash flow to total liabilities
rank(ts_zscore(divide(ts_backfill(cash_flow_operating_activities, 120), ts_backfill(liabilities, 120)), 252))
```

---

## 3. Technical Analysis

### 3.1 Core Principle
Technical analysis forecasts price directions through the statistical study of **past market data**, primarily price movements (OHLC: Open, High, Low, Close) and trading volume. Rather than measuring intrinsic value, technical analysis exploits market psychology, liquidity imbalances, and trend inertia.

### 3.2 Primary Technical Paradigms on BRAIN
* **Momentum & Trend:** Assets exhibiting strong past performance tend to persist over intermediate horizons (1–6 months).
  * Example operators: `ts_delta(close, 20)`, `ts_mean(returns, 63)`
* **Mean Reversion:** Short-term extreme departures from moving averages tend to revert to baseline.
  * Example operators: `-ts_zscore(close, 5)`, `rank(-ts_rank(close, 10))`
* **Volume & Volatility Confirmation:** Price moves supported by abnormal volume or expanding volatility indicate institutional conviction.
  * Example operators: `volume / ts_mean(volume, 20)`, `ts_std_dev(returns, 20)`

---

## 4. Quantitative Synthesis & Multi-Factor Modeling

Modern quantitative strategies on WorldQuant BRAIN synthesize fundamental valuation signals with technical filters to maximize Sharpe and minimize turnover:

### 4.1 Hybrid Alpha Patterns
1. **Regime-Conditioned Execution (`trade_when`):**
   Execute fundamental valuation models only when liquidity or volume confirms institutional interest:
   ```c
   trade_when(volume > ts_mean(volume, 20), rank(divide(ts_backfill(ebit, 120), cap)), -1)
   ```
2. **Momentum-Confirmed Value:**
   Long stocks that are fundamentally cheap *and* exhibiting emerging price momentum, avoiding "value traps":
   ```c
   rank(divide(ts_backfill(free_cash_flow, 120), cap)) + 0.5 * rank(ts_delta(close, 20))
   ```
3. **Cross-Sectional Neutralization:**
   Strip out broader market or sector bias to isolate pure stock-selection alpha:
   ```c
   group_neutralize(rank(ts_zscore(divide(ts_backfill(anl4_afv4_eps_mean, 120), close), 252)), subindustry)
   ```

---

## 5. Comparison Matrix

| Dimension | Fundamental Analysis | Technical Analysis | Quantitative Synthesis (BRAIN) |
| :--- | :--- | :--- | :--- |
| **Primary Data** | Financial statements, earnings, analyst estimates | Price (OHLC), trading volume, order flow | Multi-modal: Fundamentals, Technicals, Alternative datasets |
| **Holding Horizon** | Medium to Long (Weeks to Months) | Short to Medium (Days to Weeks) | Multi-horizon (1d to 252d lookbacks) |
| **Turnover Profile** | Low ($1\% - 15\%$) | High ($40\% - 90\%$) | Controlled via decay ($5\% - 30\%$) |
| **Key Risk** | Value traps, delayed market recognition | False breakouts, high transaction drag | Overfitting, data mining bias |
| **BRAIN Operators** | `ts_backfill`, `divide`, `group_neutralize` | `ts_delta`, `ts_zscore`, `ts_std_dev` | `trade_when`, `signed_power`, `rank` |

---

## 6. Implementation in the Local Alpha Engine

In this repository (`alpha`), these market analysis disciplines are systematically operationalized:

1. **Fundamental Value Scans:** The generator leverages accounting and analyst estimate fields (`fields/anl4_*`, balance sheet, cash flows) with 63d/252d lookbacks.
2. **Turnover Control via Decay:** Raw fundamental signals use `ts_decay_linear` or `decay` parameters ($4-16$) to stabilize weights and satisfy BRAIN turnover constraints.
3. **Statistical Validation Gates:** Candidate signals are screened across In-Sample Sharpe, Fitness, Subperiod stability, and pairwise correlation ($r < 0.70$) before promotion to the human submission queue.
