# What is the WorldQuant Challenge and How Is It Scored?

**Official Reference:** [WorldQuant BRAIN Support Article 21210168520855](https://support.worldquantbrain.com/hc/en-us/articles/21210168520855-What-is-the-WorldQuant-Challenge-and-how-is-it-scored)

---

## 1. What is the WorldQuant Challenge?

The **WorldQuant Challenge** is an ongoing, global quantitative finance competition hosted on the **WorldQuant BRAIN** platform. It provides participants (students, researchers, data scientists, and engineers) with the opportunity to build predictive mathematical models known as **"alphas"** using historical market data and proprietary financial operators.

### Key Objectives:
* **Skill Building:** Learn and apply quantitative modeling, factor investing, and risk management techniques.
* **Global Leaderboard:** Benchmark performance against quants worldwide.
* **Consulting Opportunities:** High-performing participants who achieve specific score thresholds (Silver and Gold levels) are eligible for paid **WorldQuant BRAIN Research Consultant** opportunities.

---

## 2. How Is It Scored?

Scoring in the WorldQuant Challenge evaluates both the **quality** and **quantity** of alphas submitted to the platform.

### 2.1 The Fitness Formula
Every individual alpha simulated and submitted is scored on its statistical quality, Sharpe ratio, and a proprietary **Fitness** metric:

$$\text{Fitness} = \text{Sharpe} \times \sqrt{\frac{|\text{Annualized Returns}|}{\max(\text{Turnover}, 0.125)}}$$

#### Mechanics of the Formula:
* **Sharpe Ratio Incentive:** Rewards alphas with high risk-adjusted return profiles (typically requiring $> 1.25$ In-Sample).
* **Return Scaling:** Rewards higher annualized absolute returns.
* **Turnover Penalty:** The denominator heavily penalizes excessive turnover. Strategies with high churn/turnover (> 70%) suffer significant fitness degradation.
* **Turnover Floor ($0.125$):** Prevents division-by-zero or artificial inflation for ultra-low turnover signals.

---

### 2.2 Out-of-Sample (OS) Qualification & Dynamic Scoring

1. **In-Sample (IS) vs. Out-of-Sample (OS):**
   * Alphas are initially tested against historical In-Sample data.
   * Only alphas passing all platform submission checks (Sharpe, Fitness, Turnover, Subperiod checks, Self-Correlation) advance to **Out-of-Sample (OS)** testing.
   * **Only Out-of-Sample alphas generate and accumulate Challenge points.**

2. **Weekly Performance Adjustments:**
   * Scores are dynamic. As live market data arrives each week, the Out-of-Sample performance of your active alpha pool is evaluated.
   * Alphas performing well in live conditions increase or maintain your score; degrading alphas stop accumulating points.

3. **Portfolio Diversification & Correlation:**
   * The scoring engine rewards a basket of **uncorrelated alphas** ($r < 0.70$).
   * Submitting duplicate or highly correlated signals into the same market space yields diminishing score returns.

4. **Daily Score Refresh:**
   * Leaderboard rankings and total accumulated points are calculated and refreshed **daily at 03:00 AM EST (08:00 UTC)**.

---

## 3. Challenge Levels & Progression

Participants progress through three primary milestone tiers based on cumulative challenge score:

| Level | Required Points | Platform Unlocks & Opportunities |
| :--- | :--- | :--- |
| **BRONZE** | **1,000 Points** | Core operator access, basic data feeds, competition badges |
| **SILVER** | **5,000 Points** | Advanced operators, expanded datasets, **initial consultant consideration** |
| **GOLD** | **10,000 Points** | **Full Research Consultant eligibility**, SuperAlpha access, international datasets (EUR/ASI), maximum concurrency |

---

## 4. Summary Checklist for Submitting High-Scoring Alphas

To maximize point accumulation while adhering to platform standards:

- [x] **Target High Sharpe:** Aim for In-Sample Sharpe $> 1.50$ across multi-year backtests.
- [x] **Control Turnover:** Keep daily turnover between $1\% - 30\%$ by tuning decay windows or applying conditional entry (`trade_when`).
- [x] **Ensure Low Self-Correlation:** Check pairwise correlation against your existing submitted alphas ($r < 0.70$).
- [x] **Pass Subperiod Checks:** Verify the alpha demonstrates consistent profitability across all sub-regimes (no single-year windfall profits).
- [x] **Respect Daily Limits:** Submit 1–2 high-conviction alphas per 24-hour cycle to pace towards the daily progression ceiling.

---

*For further support and platform inquiries, contact `support@worldquantbrain.com`.*
