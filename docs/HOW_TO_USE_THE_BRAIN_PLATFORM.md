# How to Use the BRAIN Platform (Section 3.5)

**Official Reference:** [WorldQuant BRAIN Support — 3.5 How to Use the BRAIN Platform (Article 12199858509719)](https://support.worldquantbrain.com/hc/en-us/articles/12199858509719--3-5-How-to-Use-the-Brain-Platform)

---

## 1. Introduction

The **WorldQuant BRAIN Platform** is a web-based research and simulation environment designed for quantitative researchers to develop, backtest, and evaluate predictive trading models (**"alphas"**). It provides direct access to thousands of institutional financial data fields, high-performance backtesting engines, and mathematical operator libraries.

```
┌────────────────────────┐      ┌────────────────────────┐      ┌────────────────────────┐
│  Data Fields Library   │ ───► │ Fast Expression Engine │ ───► │  Simulation & Metrics  │
│ (Price, Est, Fund, Alt)│      │  (Operators + Settings)│      │ (Sharpe, Fitness, PnL) │
└────────────────────────┘      └────────────────────────┘      └────────────────────────┘
```

---

## 2. Platform Core Architecture & Navigation

### 2.1 The Fast Expression Interface
The primary workspace on BRAIN is the **Fast Expression Editor**, where alphas are expressed as functional mathematical formulas evaluated cross-sectionally across stocks over historical time:
* **Syntax:** Nested functional language (e.g. `rank(ts_zscore(divide(ts_backfill(field, 120), cap), 63))`).
* **Case Sensitivity:** Operator names and data field names are lowercase.
* **Vectorized Execution:** Formulas automatically evaluate across all assets in the selected universe simultaneously for each trading day.

---

## 3. Simulation Settings & Configuration

Every alpha simulation requires configuring execution parameters that govern how the raw signal is transformed into portfolio weights and traded:

| Parameter | Recommended / Standard Setting | Description & Impact |
| :--- | :--- | :--- |
| **Instrument Universe** | `TOP3000` / `TOP1000` / `MINVOL1M` | The universe of equities traded. `TOP3000` captures broad US equities; `TOP1000` focuses on liquid large/mid caps. |
| **Region** | `USA` (Standard), `EUR`, `ASI`, `GLB` | Geographic market region. International markets unlock at Gold / Consultant level. |
| **Delay** | `Delay 1` (Standard) | Simulates trading on market close of day $t+1$ using data known at day $t$, preventing lookahead bias. |
| **Neutralization** | `SUBINDUSTRY` (Standard), `INDUSTRY`, `SECTOR`, `MARKET` | Strips out sector/industry factor risk to isolate pure stock-selection alpha. |
| **Truncation** | `0.08` to `0.10` ($8\%-10\%$) | Caps maximum portfolio weight assigned to any single stock to avoid idiosyncratic concentration risk. |
| **Decay** | `4` to `16` Days | Applies linear time-series smoothing over recent signals to dampen turnover and lower execution costs. |
| **Pasteurization** | `ON` | Clips extreme outlier values in raw data feeds to avoid noisy spikes. |
| **Unit Handling** | `NONE` | Standard normalization mode. |

---

## 4. Data Fields Library

The BRAIN platform hosts over 85,000 data fields organized across multiple domains:

### 4.1 Data Categories
1. **Price-Volume (PV):** `open`, `high`, `low`, `close`, `volume`, `vwap`, `returns`, `cap` (Market Capitalization).
2. **Fundamental Accounting:** Balance sheet (`assets`, `liabilities`, `debt`, `cash`), Income statement (`ebit`, `revenue`, `net_income`), Cash flow (`free_cash_flow`, `cash_flow_operating_activities`).
3. **Analyst Estimates (`anl4_*`):** Consensus earnings, forward EPS forecasts, price target revisions, revenue revisions.
4. **Sentiment & Alternative:** News sentiment, corporate filings, social sentiment, macro indicators.

### 4.2 Handling Sparse / Event Data
Fundamental and analyst data is reported periodically (quarterly or irregularly). To prevent lookahead bias and missing daily values, use `ts_backfill`:
```c
ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_cff_mean, 120)
```

---

## 5. Mathematical Operators Reference

Alphas are composed using four core families of operators:

### 5.1 Time-Series Operators (`ts_*`)
Operate along the time dimension for each stock independently:
* `ts_mean(x, d)`: Moving average over $d$ days.
* `ts_std_dev(x, d)`: Rolling standard deviation over $d$ days.
* `ts_zscore(x, d)`: Rolling z-score $(x - \text{mean}) / \text{std\_dev}$.
* `ts_rank(x, d)`: Rolling percentile rank against past $d$ days ($0.0 - 1.0$).
* `ts_delta(x, d)`: Change in value $x_t - x_{t-d}$.
* `ts_decay_linear(x, d)`: Linearly weighted moving average.
* `ts_backfill(x, d)`: Forwards the most recent non-NaN value up to $d$ days.

### 5.2 Cross-Sectional Operators
Operate across all stocks in the universe on a single trading day:
* `rank(x)`: Normalizes signal cross-sectionally to values evenly spaced in $[0.0, 1.0]$.
* `scale(x)`: Scales weights so $\sum |w_i| = 1$ (dollar neutral / unit book).
* `zscore(x)`: Cross-sectional standardization to zero mean and unit variance.

### 5.3 Group & Factor Operators
* `group_neutralize(x, group)`: Demeans signal within each industry or sector bucket.
* `group_rank(x, group)`: Cross-sectional rank computed within each group independently.

### 5.4 Arithmetic & Conditional Logic
* `divide(x, y)`, `multiply(x, y)`, `signed_power(x, p)`, `log(x)`
* `trade_when(entry_condition, signal, exit_condition)`: Executes signal only when condition is met.

---

## 6. Interpreting Simulation Output Metrics

When a simulation finishes, the platform produces a detailed performance report. Key metrics include:

| Metric | Minimum Passing Gate | Target / Optimal Range | Meaning & Significance |
| :--- | :--- | :--- | :--- |
| **Sharpe Ratio** | $> 1.25$ | **$> 1.50$** | Risk-adjusted return: Annualized Return / Annualized Volatility. |
| **Fitness** | $\ge 1.00$ | **$\ge 1.20$** | Quality metric: $\text{Sharpe} \times \sqrt{|\text{Ret}| / \max(\text{Turnover}, 0.125)}$. |
| **Turnover** | $< 70\%$ | **$5\% - 30\%$** | Average daily percentage of portfolio capital rebalanced. |
| **Margin** | $> 5\text{ bps}$ | **$> 15\text{ bps}$** | PnL per dollar traded ($\text{Return} / \text{Turnover}$), measuring resistance to slippage. |
| **Max Drawdown** | $< 25\%$ | **$< 15\%$** | Deepest peak-to-trough equity drop. |
| **Subperiod Returns** | Uniform | All years $>0$ | Consistency across market cycles without relying on single-year anomalies. |
| **Self-Correlation** | $< 0.70$ | **$< 0.50$** | Correlation against your existing submitted alphas to avoid duplicate penalties. |

---

## 7. The End-to-End Alpha Workflow

```
1. Hypothesis Generation ──► Identify economic thesis (e.g. Value, Quality, Revisions)
2. Expression Assembly   ──► Combine fundamental field with normalization & operators
3. Backtest Simulation   ──► Run on BRAIN with Delay 1, Universe TOP3000, Subindustry Neutral
4. Validation Screening  ──► Check Sharpe > 1.25, Fitness >= 1.00, Turnover < 30%, Corr < 0.70
5. Submission to OS      ──► Submit via BRAIN UI into Out-of-Sample evaluation for Challenge points
```

---

## 8. Integration with the Local Research Engine

This repository (`alpha`) automates steps 1 through 4:
* Generates AST expressions grounded in proven fundamental and technical paradigms.
* Simulates candidate formulas via the BRAIN API in `Delay 1`.
* Enforces rigorous local filters (Sharpe, Fitness, Subperiod stability, DSR, pairwise correlation $< 0.70$).
* Populates a curated candidate queue ready for quick human submission.
