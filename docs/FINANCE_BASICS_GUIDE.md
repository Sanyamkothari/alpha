# Finance Basics for Quantitative Research (Section 5.5)

**Official Reference:** [WorldQuant BRAIN Support — 5.5 Finance Basics (Article 12199778788887)](https://support.worldquantbrain.com/hc/en-us/articles/12199778788887--5-5-Finance-Basics)

---

## 1. Overview & Purpose

In quantitative modeling, mathematical algorithms operate on financial market structures. Understanding **Finance Basics** provides the conceptual foundation necessary to construct economically sound, risk-controlled alphas that generate pure excess return (**$\alpha$**) while insulating portfolios from broad market swings (**$\beta$**).

```
┌────────────────────────────────────────────────────────┐
│             EQUITY MARKET & FACTOR DYNAMICS            │
├──────────────────────────┬─────────────────────────────┤
│ Macro & Market Factors   │ Systematic Risk (Beta, β)   │
│ (Rates, Inflation, GDP)  │ Stripped via Neutralization │
├──────────────────────────┼─────────────────────────────┤
│ Company-Specific Signals │ Idiosyncratic Return (Alpha)│
│ (Earnings, Cash Flow)    │ Captured via Long/Short     │
└──────────────────────────┴─────────────────────────────┘
```

---

## 2. Stock Market Mechanics & Market Microstructure

### 2.1 Shares, Equities & Market Capitalization
* **Equity / Stock:** Represents fractional ownership in a publicly traded corporation.
* **Market Capitalization (`cap`):** The total dollar market value of a company's outstanding equity:
  $$\text{Market Cap} = \text{Current Stock Price} \times \text{Total Shares Outstanding}$$
* **Market Cap Universes:**
  * **Mega/Large Cap:** Liquid, low slippage, widely covered by analysts (e.g., `TOP1000`).
  * **Mid/Small Cap:** Higher idiosyncratic dispersion, higher potential alpha, lower liquidity (e.g., `TOP3000`).

### 2.2 Order Flow, Liquidity & Execution Drag
* **Bid-Ask Spread:** The difference between the highest price a buyer is willing to pay (bid) and the lowest price a seller will accept (ask).
* **Slippage & Market Impact:** Trading large sizes moves prices unfavorably against the trader.
* **Volume-Weighted Average Price (`vwap`):** Benchmark representing the true volume-weighted execution price over the trading day:
  $$\text{VWAP} = \frac{\sum (P_i \times V_i)}{\sum V_i}$$
* **BRAIN Takeaway:** Alphas with excessive turnover churn capital through the bid-ask spread, incurring fatal execution drag. Alphas must maintain low-to-moderate turnover ($5\% - 30\%$) and healthy margin ($> 10\text{ bps}$).

---

## 3. Mathematical Foundations of Financial Returns

### 3.1 Return Calculations
* **Simple Return ($R_t$):**
  $$R_t = \frac{P_t - P_{t-1}}{P_{t-1}} = \frac{P_t}{P_{t-1}} - 1$$
* **Logarithmic (Continuously Compounded) Return ($r_t$):**
  $$r_t = \ln\left(\frac{P_t}{P_{t-1}}\right) = \ln(P_t) - \ln(P_{t-1})$$
* **Total Return vs. Price Return:** Total return adjusts for corporate actions, cash dividends, and stock splits. On BRAIN, `returns` natively accounts for corporate actions.

---

## 4. Long / Short Portfolio Construction & Dollar Neutrality

WorldQuant BRAIN portfolios are constructed as **dollar-neutral, market-neutral equity long/short books**.

```
  LONG POSITIONS (Top Ranked Decile)      ──► Capital Allocated: +$5,000,000 (+50%)
  SHORT POSITIONS (Bottom Ranked Decile)  ──► Capital Allocated: -$5,000,000 (-50%)
───────────────────────────────────────────────────────────────────────────────────
  NET MARKET EXPOSURE (Long - Short)      ──► Net Capital: $0.00 (Dollar Neutral)
  GROSS BOOK SIZE (|Long| + |Short|)      ──► Total Capital: $10,000,000 (100%)
```

### 4.1 Long Positions (Underpriced Equities)
* Purchasing securities with positive predicted returns ($w_i > 0$).
* Profits when price rises: $\text{PnL} = w_i \times R_i$.

### 4.2 Short Positions (Overpriced Equities)
* Borrowing and selling securities with negative predicted returns ($w_i < 0$), aiming to buy them back cheaper.
* Profits when price falls: $\text{PnL} = -|w_i| \times R_i$.

### 4.3 Why Dollar Neutrality Matters
Because gross longs equal gross shorts, the portfolio is insulated against broad market crashes or bull runs. If the S&P 500 drops 20%, the short book gains approximately what the long book loses, isolating the pure relative outperformance of selected stocks.

---

## 5. Alpha vs. Beta: The Capital Asset Pricing Model (CAPM)

In modern quantitative finance, total asset return is decomposed into systematic factor exposure (**Beta**) and idiosyncratic skill (**Alpha**):

$$R_i - R_f = \alpha_i + \beta_i (R_m - R_f) + \epsilon_i$$

Where:
* $R_i$: Return of asset $i$.
* $R_f$: Risk-free rate (e.g. US Treasury yield).
* $R_m$: Broad market benchmark return (e.g. S&P 500).
* **$\beta_i$ (Beta):** Sensitivity to overall market movements. A stock with $\beta = 1.5$ moves $1.5\%$ for every $1\%$ move in the market.
* **$\alpha_i$ (Alpha):** The true excess return unexplained by market movements.
* $\epsilon_i$: Random zero-mean residual noise.

### The Objective on BRAIN:
Extract pure, persistent **$\alpha_i$** while constraining aggregate portfolio $\beta \approx 0$ via cross-sectional ranking and sector neutralization.

---

## 6. Risk, Diversification & Factor Decomposition

```
Total Portfolio Variance = Systematic Factor Variance + Idiosyncratic Risk / N
```

1. **Idiosyncratic Risk Reduction:** By holding hundreds of positions across the universe ($N \approx 1,000 - 3,000$), idiosyncratic noise cancels out, allowing the persistent alpha signal to compound smoothly.
2. **Common Risk Factors (Barra / Fama-French):**
   * **Size:** Small caps vs. Large caps.
   * **Value:** Low P/E / Book-to-Market vs. High growth multiples.
   * **Momentum:** Past 12-month winners vs. losers.
   * **Volatility:** High-beta speculative stocks vs. low-volatility defensive names.
3. **Subindustry Neutralization:** Applying `group_neutralize(signal, subindustry)` strips out industry-level factor exposure, preventing the portfolio from becoming an unintentional bet on oil, tech, or biotech cycles.

---

## 7. Key Financial Metrics Reference on BRAIN

| Metric | Formula | Target Threshold | Interpretation |
| :--- | :--- | :--- | :--- |
| **Sharpe Ratio** | $\frac{\mathbb{E}[R_p]}{\sigma(R_p)} \times \sqrt{252}$ | **$> 1.25$** ($> 1.50$ optimal) | Annualized risk-adjusted excess return. |
| **Fitness** | $\text{Sharpe} \times \sqrt{\frac{\|\text{Ret}\|}{\max(\text{Turnover}, 0.125)}}$ | **$\ge 1.00$** | Unified measure of profitability penalized for turnover. |
| **Turnover** | $\frac{1}{2} \sum \|w_{i, t} - w_{i, t-1}\|$ | **$5\% - 30\%$** | Daily percentage of capital rotated. |
| **Margin** | $\frac{\text{Annualized PnL}}{\text{Total Dollar Volume Traded}}$ | **$> 10\text{ bps}$** | PnL per dollar traded; measures buffer against transaction costs. |
| **Max Drawdown** | $\max_{t} \left(\frac{\text{Peak}_t - \text{Valley}_t}{\text{Peak}_t}\right)$ | **$< 20\%$** | Largest peak-to-trough equity drop. |

---

## 8. Alignment with the Local Research Engine

In this repository (`alpha`), all models adhere to these fundamental financial principles:
* **Dollar & Sector Neutral:** Every candidate formula uses `rank()` and `subindustry` factor neutralization.
* **Turnover Regulation:** Linear decay parameters ($4-16\text{d}$) and `trade_when` volatility gates maintain low turnover and high margin.
* **Risk Pruning:** Truncation caps individual stock weights to prevent idiosyncratic single-stock blowups.
