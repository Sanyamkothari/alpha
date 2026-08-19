# WorldQuant BRAIN Gold Level & Alpha Expansion Guide

This document is the authoritative operating manual for reaching **Gold Level (10,000 Challenge Points)** on WorldQuant BRAIN, unlocking consultant-tier privileges, higher simulation concurrency, and entry into paid research programs.

---

## 1. Challenge Point System & Gold Level Mechanics

### 1.1 Progression Milestones

| Level / Tier | Point Requirement | Platform Unlocks & Privileges |
| :--- | :--- | :--- |
| **BRONZE** | `0 – 1,000` | Baseline tutorial environment, 3 concurrent simulations, USA region |
| **SILVER** | `1,000 – 5,000` | Extended operator library, intermediate datasets, community access |
| **GOLD** | **`10,000`** | **Consultant eligibility**, SuperAlpha access, international regions (`EUR`, `ASI`, `GLB`), higher concurrency |

```
Progression Formula:
  Remaining Points = 10,000 - Current_Leaderboard_Score
  Required Submissions = ceil(Remaining_Points / 2,000) to ceil(Remaining_Points / 1,000)
```

### 1.2 Point Economy & Daily Caps
* **Daily Progression Cap:** The platform enforces a strict ceiling of **2,000 Challenge Points per calendar day** (Reset occurs daily at **3:00 AM EST / 08:00 UTC**).
* **Point Yield per Alpha:** An alpha clearing all In-Sample checks and accepted into Out-of-Sample (`Stage: OS`, `Status: ACTIVE`) contributes between **1,000 and 2,000 points** depending on:
  * **In-Sample Sharpe Ratio:** ($> 1.25$ required, $> 1.50$ yields maximum points)
  * **Fitness Score:** ($\ge 1.00$ required)
  * **Quality Grade:** (`AVERAGE`, `GOOD`, `EXCELLENT`)
* **Optimal Submission Pace:** Submitting **1 to 2 verified alphas per 24-hour cycle over ~5 consecutive days** safely captures the daily 2,000-point ceiling without exhausting research bandwidth in a single day.

---

## 2. The 4 Frameworks for Expanding Alpha Ideas

A single proven fundamental anomaly or economic thesis can be systematically expanded into multiple submittable, mutually uncorrelated alphas using 4 distinct levers:

```
                                  EXPANDING ALPHA IDEAS
                                            │
        ┌───────────────────┬───────────────┴───────────────┬───────────────────┐
        ▼                   ▼                               ▼                   ▼
 1. Cross-Universe   2. Neutralization              3. Non-Linear        4. Synergy /
    Exploration         Diversification                Transforms           Regime Filters
 (TOP1000, MINVOL1M) (SECTOR, group_neutralize)     (signed_power, log)  (trade_when)
```

---

### Lever 1: 🪐 Cross-Universe Exploration
* **Mechanism:** Changing the universe alters the constituent assets, liquidity characteristics, and capitalization weights.
* **Target Universes:**
  * `TOP3000`: Broad US Equities (~3,000 liquid instruments).
  * `TOP1000`: Large & Mid Cap Equities (~1,000 highly liquid instruments).
  * `MINVOL1M`: Minimum $1M daily trading volume equities.
* **Why BRAIN Accepts It:** Alphas running on different universes generate different weight vectors and PnL curves, qualifying as distinct, submittable alphas with independent challenge points.
* **Robustness Rule:** A signal that performs well across multiple universes demonstrates structural robustness rather than small-cap artifact fitting.

---

### Lever 2: ⚖️ Neutralization Diversification
* **Mechanism:** Strips out unwanted systematic risk factors to isolate pure stock-selection alpha.
* **Settings-Level Neutralization:**
  * `SUBINDUSTRY`: Neutralizes granular sub-industry factor trends (default).
  * `INDUSTRY`: Captures intra-industry dispersion while stripping industry momentum.
  * `SECTOR`: Neutralizes broad 11-sector risk, capturing wider cross-industry opportunities.
  * `MARKET`: Dollar-neutral broad market benchmark.
* **Operator-Level Factor Neutralization:**
  * `group_neutralize(signal, group)`: Neutralizes against external groupings (e.g. Size, Beta, Momentum buckets).
  * `regression_neut(signal, factor)`: Orthogonalizes alpha weights against a continuous risk factor.

---

### Lever 3: 📐 Non-Linear Position Distributions (`signed_power`, `rank`)
* **Mechanism:** Modifies the conviction curve and capital concentration across the asset universe.
* **Key Operators & Transformations:**
  1. **Standard Linear Rank:**
     ```c
     rank(signal)
     ```
     Assigns uniform weight steps across all stocks.
  2. **Non-Linear Power Conviction:**
     ```c
     signed_power(rank(signal) - 0.5, 2.0)
     ```
     Concentrates capital heavily into extreme high-conviction tails while compressing noise in the median 80% of assets.
  3. **Time-Series Volatility Normalization:**
     ```c
     ts_rank(signal, 63)
     ```
     Ranks each stock's current metric against its own historical distribution.
* **Safety Gate:** Ensure `CONCENTRATED_WEIGHT < 0.10` to avoid triggering the single-asset concentration failure gate.

---

### Lever 4: ⚗️ Synergistic Conditional Execution (`trade_when`)
* **Mechanism:** Conditions trade entry and position holding on favorable market volatility, liquidity, or volume regimes.
* **Formula Syntax:**
  ```c
  trade_when(Condition_Entry, Alpha_Signal, Condition_Exit)
  ```
* **Production Patterns:**
  * **Volume Surge Entry:**
    ```c
    trade_when(volume > ts_mean(volume, 20), Alpha_Signal, -1)
    ```
    Executes the fundamental signal only when trading volume confirms market liquidity, reducing execution drag and lowering turnover by 30%–50%.
  * **Volatility Regime Gating:**
    ```c
    trade_when(ts_std_dev(returns, 20) < ts_mean(ts_std_dev(returns, 20), 120), Alpha_Signal, -1)
    ```
    Trades only during low-to-moderate volatility regimes, suppressing drawdown risk.
* **Drawdown Diversification:** Complementary alphas compensate for drawdowns in other strategies, creating a robust, multi-strategy portfolio.

---

## 3. Live Submitted Portfolio Ground Truth

The engine enforces an empirical Pearson correlation gate ($r < 0.70$) across all active submissions:

| # | Local DB ID | BRAIN ID | Expression | Lookback & Decay | Sharpe | Fitness | Turnover | Status |
| :- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **1** | ``#243`` | `zqNXMEZE` | `rank(ts_zscore(divide(ts_backfill(liabilities,120),cap),5))` | Window 5d, Decay 4 | **1.91** | **1.00** | **58.25%** | **SUBMITTED (OS)** |
| **2** | ``#2558`` | `N1bkwYGw` | `rank(ts_zscore(divide(ts_backfill(max_reported_pretax_profit_quarterly_estimate,120),cap),63))` | Window 63d, Decay 0 | **1.71** | **1.01** | **22.81%** | **SUBMITTED (OS)** |
| **3** | ``#4102`` | `9qpOZjMq` | `rank(ts_zscore(divide(ts_backfill(anl4_fs_detail_estimates_basic_qf_delay1_v4_nd_cfps_high,120),cap),63))` | Window 63d, Decay 0 | **1.68** | **1.05** | **23.77%** | **SUBMITTED (OS)** |
| **4** | ``#5177`` | `xANpg6OW` | `rank(-ts_zscore(divide(ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_cff_mean,120),cap),63))` | Window 63d, Decay 8 | **1.48** | **1.01** | **9.70%** | **SUBMITTED (OS)** |
| **5** | ``#2569`` | `j26KNdKo` | `rank(ts_zscore(divide(ts_backfill(max_reported_pretax_profit_quarterly_estimate,120),cap),252))` | Window 252d, Decay 16 | **1.43** | **1.02** | **4.47%** | **SUBMITTED (OS)** |
| **6** | ``#5178`` | `RRmwqE5b` | `rank(ts_zscore(divide(ts_backfill(anl4_fs_detail_estimate_1qf_v4_nd_sh_equity_mean,120),cap),63))` | Window 63d, Decay 4 | **1.30** | **1.03** | **14.02%** | **SUBMITTED (OS)** |
| **7** | ``#5179`` | `blQmY7br` | `rank(ts_zscore(divide(ts_backfill(ebit,120),cap),63))` | Window 63d, Decay 8 | **1.35** | **1.05** | **10.59%** | **SUBMITTED (OS)** |
| **8** | ``#5180`` | `LLG0Y2p9` | `rank(ts_zscore(divide(ts_backfill(anl4_afv4_eps_mean,120),close),252))` | Window 252d, Decay 16 | **1.77** | **1.43** | **4.55%** | **SUBMITTED (OS)** |
| **9** | ``#5188`` | `QP7Znjbg` | `rank(ts_zscore(divide(ts_backfill(adj_net_income_avg,120),cap),126))` | Window 126d, Decay 8 | **1.63** | **1.19** | **7.35%** | **SUBMITTED (OS)** |
| **10** | ``#5189`` | `6XlmjjjG` | `rank(ts_zscore(divide(ts_backfill(actual_cashflow_per_share_value_quarterly,120),close),63))` | Window 63d, Decay 8 | **1.49** | **1.03** | **10.00%** | **SUBMITTED (OS)** |

### 3.1 Exact 10x10 Pairwise Empirical Correlation Matrix
*(Computed over 1,236 common historical trading days cached in ``database/pnl/``):*

```
             zqNXMEZE  N1bkwYGw  9qpOZjMq  xANpg6OW  j26KNdKo  RRmwqE5b  blQmY7br  LLG0Y2p9  QP7Znjbg  6XlmjjjG
zqNXMEZE       1.0000    0.1640    0.4789    0.0447    0.0210    0.6512   -0.0451    0.3484    0.1861    0.0506
N1bkwYGw       0.1640    1.0000    0.4948    0.5247    0.6484    0.2921    0.6690    0.4062    0.6512    0.5841
9qpOZjMq       0.4789    0.4948    1.0000    0.3799    0.3443    0.6687    0.4342    0.6091    0.5833    0.4458
xANpg6OW       0.0447    0.5247    0.3799    1.0000    0.4294    0.1517    0.6502    0.3525    0.5032    0.4450
j26KNdKo       0.0210    0.6484    0.3443    0.4294    1.0000    0.1712    0.5841    0.5081    0.6571    0.4790
RRmwqE5b       0.6512    0.2921    0.6687    0.1517    0.1712    1.0000    0.0909    0.6539    0.4522    0.2733
blQmY7br      -0.0451    0.6690    0.4342    0.6502    0.5841    0.0909    1.0000    0.3889    0.6758    0.5895
LLG0Y2p9       0.3484    0.4062    0.6091    0.3525    0.5081    0.6539    0.3889    1.0000    0.6776    0.4577
QP7Znjbg       0.1861    0.6512    0.5833    0.5032    0.6571    0.4522    0.6758    0.6776    1.0000    0.6094
6XlmjjjG       0.0506    0.5841    0.4458    0.4450    0.4790    0.2733    0.5895    0.4577    0.6094    1.0000
```
* **Every single pair is $< 0.70$** (Max pairwise correlation across all 45 pairs: **`0.6776`**).

---

## 4. API Endpoints for Tracking Points & Progression

| Query / Goal | API Endpoint | Key Fields in Response |
| :--- | :--- | :--- |
| **Cumulative Score & Level** | `GET /users/self/competitions` | `results[0].leaderboard.score`, `leaderboard.level`, `progress.score.remaining` |
| **Active Challenge Alphas** | `GET /competitions/challenge/alphas` | `count`, `results[].grade`, `results[].stage` (`OS`), `results[].status` (`ACTIVE`) |
| **Milestone Badges** | `GET /users/self/achievements` | Complete 13-achievement unlock timestamps and consultant progression |
| **Daily Activity Telemetry** | `GET /users/self/activities/simulations` | `yesterday.value`, `current.value`, `total.value`, `records` daily counts |

---

## 5. Daily Submission Operating Protocol

```
 Daily Reset (3:00 AM EST)
          │
          ▼
 [Step 1: Automated Prep]
 Local engine simulates candidates, checks 7 platform gates,
 and verifies pairwise correlation < 0.55 vs all submitted alphas.
          │
          ▼
 [Step 2: Pre-Verified Queue]
 1–2 winning candidate IDs prepared in your BRAIN account.
          │
          ▼
 [Step 3: 1-Click Human Submission]
 Operator opens BRAIN UI → Selects candidate → Clicks "Submit" (5 seconds).
          │
          ▼
 [Step 4: Score Verification]
 Leaderboard updates +2,000 points towards 10,000 Gold Level.
```
