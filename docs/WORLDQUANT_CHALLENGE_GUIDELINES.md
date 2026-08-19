# WorldQuant Challenge Guidelines & Scoring Mechanics

Official operating guidelines, participant rules, scoring mechanisms, and compliance standards for the WorldQuant Challenge / Alpha Building Competition on the WorldQuant BRAIN platform.

Reference: [WorldQuant BRAIN Support — What is the WorldQuant Challenge and how is it scored?](https://support.worldquantbrain.com/hc/en-us/articles/21210168520855-What-is-the-WorldQuant-Challenge-and-how-is-it-scored)

---

## 1. Overview & Participation

The **WorldQuant Challenge** is an ongoing, global competition hosted on the WorldQuant BRAIN platform. Participants research, build, and submit quantitative trading models ("alphas") to predict price movements across global financial instruments.

### 1.1 Eligibility Requirements
* **Age & Legal Capacity:** Must be eighteen (18) years of age or older and not prohibited from participating by any law, agreement, binding obligation, or policy.
* **Permissions:** Responsible for obtaining any permissions necessary to participate.
* **Account Registration:** Must register as a WorldQuant BRAIN user and receive platform approval.
* **Existing Users:** Existing users can compete by visiting the **Competitions** tab after logging into the WorldQuant BRAIN platform.
* **No Registration Fee:** There is zero fee to register or participate.

### 1.2 Account Integrity & Single-Account Policy
* **One Account per User:** Each participant should register only once.
* **No Account Sharing:** A participant's account or ID may not be shared under any circumstances.
* **Duplicate Accounts:** Strictly prohibited. Duplicate accounts are grounds for immediate suspension from the competition and permanent banning from the BRAIN platform.
* **Deactivation:** If duplicate accounts were accidentally created, email `support@worldquantbrain.com` immediately to request deactivation.

---

## 2. Alpha Construction & Scoring Rules

### 2.1 How the Challenge is Scored
Scoring is determined by the **quality, quantity, and diversification** of submitted alphas:

1. **Fitness & Sharpe Weighting:** Each submitted alpha is evaluated on statistical robustness, Sharpe ratio, and the platform's proprietary **Fitness** metric:
   $$\text{Fitness} = \text{Sharpe} \times \sqrt{\frac{|\text{Annualized Returns}|}{\max(\text{Turnover}, 0.125)}}$$
   * High returns and high Sharpe are rewarded.
   * Excessive turnover ($>70\%$) is heavily penalized via the denominator.
2. **Out-of-Sample (OS) Qualification:** Alphas only score challenge points once they pass In-Sample (IS) platform checks and are accepted into **Out-of-Sample (OS)** evaluation.
3. **Weekly OS Evaluation:** Scores are dynamic and evolve over time based on rolling weekly OS performance against live/fresh market data.
4. **Portfolio Correlation & Aggregation:** The scoring engine rewards portfolios of mutually uncorrelated alphas ($r < 0.70$). Submitting redundant or highly correlated signals yields diminishing returns.
5. **Daily Calculation Cycle:** Challenge scores update daily at **03:00 AM EST (08:00 UTC)**.

### 2.2 Progression Milestones & Tiers

| Level / Tier | Point Requirement | Platform Privileges & Status |
| :--- | :--- | :--- |
| **BRONZE** | `1,000` | Baseline environment, initial platform badges |
| **SILVER** | `5,000` | Extended operator library, intermediate datasets, consultant consideration |
| **GOLD** | **`10,000`** | **Consultant eligibility**, SuperAlpha access, international datasets, maximum concurrency |

---

## 3. Intellectual Property, Anti-Gaming & Compliance

### 3.1 Intellectual Property & Original Work
* **Original Work Warranty:** All submitted alphas must be your own original work. No part of an alpha may be subject to the rights of any third party.
* **Grant of Rights:** Submitting alphas grants WorldQuant the rights to the models as set forth in the User Agreement.

### 3.2 Prohibited Practices & Scoring Integrity
* **No Introduction of Noise:** Deliberate introduction of mathematical "noise" or useless complexity to subvert scoring systems is strictly prohibited and results in immediate disqualification and account termination.
* **Anti-Gaming / Anti-Cheating:** Any detected signs of gaming, formula sharing, or dishonest behavior will lead to termination of the account without notice.
* **Research Consultant Restrictions:** Existing WorldQuant Research Consultants are not permitted to participate in standard challenge competitions unless expressly authorized.

### 3.3 Platform Rights & Discretion
* WorldQuant reserves the right to modify the scoring algorithm, amend competition guidelines, cancel or terminate the competition, or disqualify participants at its sole discretion without compensation.
* Achieving a level (Bronze, Silver, Gold) does not automatically entitle participants to compensation or employment.

---

## 4. Consulting Opportunities

* **Eligibility:** Participants achieving **Silver** and **Gold** levels may be invited at WorldQuant's discretion to become paid **WorldQuant BRAIN Research Consultants**.
* **Conditions:** Consulting offers are non-guaranteed and contingent on passing background checks and signing a formal consulting agreement.

---

## 5. Local System Compliance Reference

For researchers using this repository (`alpha`), the system's hard invariants map directly to these official guidelines:

| Official Guideline / Constraint | Local Repository Implementation & Enforcement |
| :--- | :--- |
| **Manual Submissions Only** | Automated simulation only; final submission is **100% human-initiated** via the BRAIN web portal. Enforced by [Hard Invariant 1](../CLAUDE.md#L15) and `tests/test_brain_no_post.py`. |
| **Original Work / No Noise** | Alphas are generated via deterministic AST logic grounded in genuine economic mechanisms (no randomized noise or brute-force formula spam) ([Hard Invariant 2](../CLAUDE.md#L16)). |
| **Fitness & Turnover Optimization** | Built-in filters optimize the fitness formula ($\text{Sharpe} \times \sqrt{\text{Ret}/\text{Turnover}}$) by enforcing decay tuning and volume/liquidity gating to keep turnover low. |
| **Self-Correlation & Portfolio Quality** | Pre-submission gates verify pairwise correlation $r < 0.70$ and enforce Sharpe $> 1.25$ / Fitness $\ge 1.00$ to maintain out-of-sample stability ([GOLD_LEVEL_GUIDE.md](GOLD_LEVEL_GUIDE.md#L118-L146)). |
| **Single Account Safety** | All sync tools and local databases key to a single user profile without multi-account automation. |

---

*Official Reference: [support.worldquantbrain.com/hc/en-us/articles/21210168520855](https://support.worldquantbrain.com/hc/en-us/articles/21210168520855-What-is-the-WorldQuant-Challenge-and-how-is-it-scored)*
