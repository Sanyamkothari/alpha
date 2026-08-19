# WorldQuant BRAIN Platform Knowledge Base & Research Reference

An exhaustive technical guide compiling platform mechanics, submission gates, operator mathematics, failure diagnoses, and alpha expansion strategies from the WorldQuant BRAIN platform and official support archives.

---

## 1. The 8 BRAIN Submission Gates & Failure Diagnostics

Every alpha submitted to WorldQuant BRAIN is automatically evaluated against **8 strict quantitative gates**. An alpha must pass all 8 checks simultaneously to be accepted into Out-of-Sample (OS) testing and score challenge points.

```
                                    THE 8 SUBMISSION GATES
                                              │
         ┌───────────────────┬────────────────┼───────────────────┬───────────────────┐
         ▼                   ▼                ▼                   ▼                   ▼
    1. Sharpe           2. Fitness       3. Turnover        4. Subperiod        5. Weights
   (SR > 1.25)        (Fit >= 1.00)     (1% < TO < 70%)    (Uniform Profit)    (Max W < 0.10)
         │                   │                │                   │                   │
         └───────────────────┴────────────────┼───────────────────┴───────────────────┘
                                              ▼
                             ┌─────────────────────────────────┐
                             │  6. Self-Correlation (r < 0.70) │
                             │  7. Prod-Correlation (Unique)   │
                             │  8. Data Coverage (No NaNs)     │
                             └─────────────────────────────────┘
```

---

### Gate 1: Sharpe Ratio Check (`SHARPE_CHECK`)
* **Threshold:** $\text{Sharpe} > 1.25$ In-Sample (Target: $> 1.50$ for maximum point yield).
* **Failure Cause:** High return volatility or insufficient signal persistence.
* **Remedies & Fixes:**
  * Increase lookback window (e.g., extend from 5d to 20d, 63d, or 252d).
  * Add factor neutralization: `group_neutralize(signal, subindustry)` to strip out sector noise.
  * Combine with a secondary quality factor (e.g., operating cash flow or low debt).

---

### Gate 2: Fitness Metric Check (`FITNESS_CHECK`)
* **Threshold:** $\text{Fitness} \ge 1.00$ (Target: $\ge 1.20$).
* **Formula:**
  $$\text{Fitness} = \text{Sharpe} \times \sqrt{\frac{|\text{Annualized Returns}|}{\max(\text{Turnover}, 0.125)}}$$
* **Failure Cause:** Excessive day-to-day position rebalancing (churn) that destroys risk-adjusted returns after estimated transaction friction.
* **Remedies & Fixes:**
  * Increase the simulation `decay` parameter (e.g., `decay = 4` to `16`).
  * Apply `ts_decay_linear(signal, d)` inside the formula.
  * Use fundamental quarterly data forward-filled with `ts_backfill(FIELD, 120)` rather than high-frequency noisy price deltas.

---

### Gate 3: Turnover Constraint (`TURNOVER_CHECK`)
* **Threshold:** $1\% < \text{Turnover} < 70\%$ (Target: $5\% - 30\%$).
* **Failure Cause:**
  * **Too High ($> 70\%$):** Signal responds too fast to daily price changes, creating huge slippage.
  * **Too Low ($< 1\%$):** Static buy-and-hold portfolio that does not trade or rebalance.
* **Remedies & Fixes:**
  * For High Turnover: Apply decay smoothing or condition trading on volatility filters:
    ```c
    trade_when(volume > ts_mean(volume, 20), signal, -1)
    ```
  * For Low Turnover: Reduce decay or incorporate intermediate lookbacks ($20\text{d}-63\text{d}$).

---

### Gate 4: Subperiod Consistency Check (`SUBPERIOD_CHECK`)
* **Threshold:** Uniform profitability across all backtest calendar years (no individual year with significant negative Sharpe or severe drawdown).
* **Failure Cause:** Overfitting to a specific market crisis (e.g. 2020 COVID rally or 2022 rate hikes) where 90% of profits were made in a single 6-month window.
* **Remedies & Fixes:**
  * Avoid narrow, curve-fitted lookback windows (e.g., using `37` instead of standard intervals `20`, `63`, `120`, `252`).
  * Run **plateau tests** to verify performance is stable across adjacent lookbacks ($w \pm 20\%$).

---

### Gate 5: Weight Concentration Check (`CONCENTRATED_WEIGHT`)
* **Threshold:** Maximum single-asset portfolio weight $< 0.10$ ($10\%$).
* **Failure Cause:** Overly restrictive boolean filters that isolate only 2–5 stocks, forcing 20–50% of capital into a single instrument.
* **Remedies & Fixes:**
  * Always apply `rank(signal)` across the full universe before allocating weights.
  * Avoid hard boolean masks (`if_else`) that discard $99\%$ of the universe.
  * Set simulation truncation to `0.08` or `0.10`.

---

### Gate 6: Self-Correlation Check (`SELF_CORRELATION`)
* **Threshold:** Pairwise Pearson correlation $r < 0.70$ against all alphas previously submitted by the user.
* **Failure Cause:** Generating multiple variations of the exact same field and operator shape.
* **Remedies & Fixes:**
  * **Change Universe:** Switch between `TOP3000`, `TOP1000`, and `MINVOL1M`.
  * **Change Neutralization:** Switch between `SUBINDUSTRY`, `INDUSTRY`, and `SECTOR`.
  * **Non-Linear Transform:** Apply `signed_power(rank(signal) - 0.5, 2.0)` to concentrate weights into extreme deciles.
  * **Invert / Orthogonalize:** Test negative momentum or regress out the primary factor.

---

### Gate 7: Production Correlation (`PROD_CORRELATION`)
* **Threshold:** Low correlation against WorldQuant's global production alpha library.
* **Failure Cause:** Submitting generic standard anomalies (e.g., standard 12-month momentum `ts_delta(close, 252)`) that are already heavily traded by hundreds of researchers.
* **Remedies & Fixes:**
  * Use proprietary analyst estimate fields (`anl4_*`) and specialized balance sheet metrics.
  * Build composite multi-factor signals (Value + Quality + Sentiment).

---

### Gate 8: Data Coverage & Missing Value Check (`LACK_DATA`)
* **Threshold:** Alpha must generate valid weights for at least $95\%$ of trading days.
* **Failure Cause:** Data fields containing NaNs or missing quarterly values.
* **Remedies & Fixes:**
  * Always backfill sparse fundamental/analyst fields:
    ```c
    ts_backfill(FIELD_NAME, 120)
    ```

---

## 2. Fast Expression Operator Taxonomy & Mathematical Definitions

### 2.1 Time-Series Operators

| Operator | Mathematical Definition | Key Use Case |
| :--- | :--- | :--- |
| `ts_mean(x, d)` | $\frac{1}{d} \sum_{i=0}^{d-1} x_{t-i}$ | Rolling baseline / trend center. |
| `ts_std_dev(x, d)` | $\sqrt{\frac{1}{d-1} \sum_{i=0}^{d-1} (x_{t-i} - \mu)^2}$ | Rolling volatility measurement. |
| `ts_zscore(x, d)` | $\frac{x_t - \text{ts\_mean}(x, d)}{\text{ts\_std\_dev}(x, d)}$ | Standardized anomaly detection. |
| `ts_rank(x, d)` | $\frac{\text{Rank of } x_t \text{ in } \{x_{t-d+1} \dots x_t\}}{d}$ | Time-series percentile position ($0.0-1.0$). |
| `ts_decay_linear(x, d)` | $\frac{\sum_{i=0}^{d-1} (d-i) x_{t-i}}{\sum_{i=1}^d i}$ | Linearly weighted moving average; turnover suppressor. |
| `ts_delta(x, d)` | $x_t - x_{t-d}$ | Price or estimate change momentum. |
| `ts_corr(x, y, d)` | $\frac{\text{Cov}(x, y)}{\sigma_x \sigma_y}$ over $d$ days | Rolling comovement between two variables. |
| `ts_regression(y, x, d, lag, rettype)` | Fits $y = \alpha + \beta x + \epsilon$ over $d$ days | Extracts residual $\epsilon$ (`rettype=0`) or slope $\beta$ (`rettype=1`). |

---

### 2.2 Cross-Sectional & Factor Neutralization Operators

| Operator | Syntax & Definition | Function & Advantage |
| :--- | :--- | :--- |
| `rank(x)` | Maps $N$ stocks to uniform ranks $[0.0, 1.0]$ | Eliminates magnitude outliers; ensures symmetric capital. |
| `scale(x)` | $w_i = \frac{x_i}{\sum \|x_j\|}$ | Scales raw weights to unit dollar-neutral book. |
| `group_neutralize(x, group)` | $x_i - \mu_{\text{group}(i)}$ | Strips out industry/sector momentum and macro factor shocks. |
| `group_rank(x, group)` | Cross-sectional rank computed strictly within each group | Discovers top performers relative to direct competitors. |

---

### 2.3 Non-Linear & Conditional Operators

| Operator | Mathematical Syntax | Practical Purpose |
| :--- | :--- | :--- |
| `signed_power(x, p)` | $\text{sign}(x) \times \|x\|^p$ | Modifies conviction curve; concentrates capital in high-conviction tails when $p > 1.0$. |
| `trade_when(cond, signal, default)` | Returns `signal` when `cond == true`, else `default` | Filters execution based on volume, volatility, or event triggers. |
| `divide(x, y)` | $x / y$ (handles $y=0 \to \text{NaN}$) | Fundamental normalization (e.g. `divide(ebit, cap)`). |
| `log(x)` | $\ln(x)$ for $x > 0$ | Compresses skewed positive financial metrics (e.g. Market Cap, Volume). |

---

## 3. The 4 Proven Alpha Expansion Levers

When a researcher identifies a single proven fundamental anomaly, they can expand it into multiple submittable, mutually uncorrelated ($r < 0.70$) alphas using 4 distinct levers:

```
                                  EXPANDING ALPHA IDEAS
                                            │
         ┌───────────────────┬───────────────┴───────────────┬───────────────────┐
         ▼                   ▼                               ▼                   ▼
  1. Universe Switch  2. Neutralization              3. Non-Linear        4. Regime Gating
  (TOP3000, TOP1000)  (SUBINDUSTRY, SECTOR)          (signed_power)       (trade_when)
```

1. **Universe Switching:** A fundamental signal running on `TOP3000` exhibits different constituents and liquidity dynamics on `TOP1000` or `MINVOL1M`, producing an uncorrelated PnL stream.
2. **Neutralization Diversification:** Changing neutralization from `SUBINDUSTRY` to `SECTOR` captures cross-industry allocation while removing broad market risk.
3. **Non-Linear Shaping:** Applying `signed_power(rank(x) - 0.5, 2.0)` concentrates weights in the top 10% and bottom 10% tails, creating an uncorrelated return distribution.
4. **Regime Gating:** Using `trade_when(volume > ts_mean(volume, 20), signal, -1)` reduces turnover by 30%–50% and isolates high-liquidity execution days.

---

## 4. Summary Guide for Local Research Architecture

| Component | Repository Implementation |
| :--- | :--- |
| **Simulation Engine** | Automated execution via `backend/app/simulation` in `Delay 1` and `SUBINDUSTRY` neutralization. |
| **Statistical Gating** | Pre-submission verification of Sharpe $> 1.25$, Fitness $\ge 1.00$, Subperiod uniformity, and DSR. |
| **Correlation Gating** | Automated pairwise Pearson correlation calculation ($r < 0.70$) across all submitted alphas cached in `database/pnl/`. |
| **Submission Protocol** | Strict manual 1-click human execution in BRAIN UI to preserve platform compliance ([Hard Invariant 1](../CLAUDE.md#L15)). |
