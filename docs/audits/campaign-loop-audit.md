# Phase 1 Campaign Loop Audit: Execution History & Validation State

**Date:** 2026-08-16  
**Auditor:** Antigravity / Claude Code Audit Suite  
**Target Database:** `database/wq.db` (4,955 alphas total)

---

## 1. Verdict

> **The Phase 1 campaign loop has NEVER produced a simulated alpha.**
>
> Exactly one campaign (`nightly_20260815_175126`) was ever created. It generated 98 candidate alphas (49 `exploit`, 49 `random_stratified`) and simulated **0** of them. Every single one of the 531 simulated alphas in the database was imported through manual UI pastes, manual single-batch runs, or the legacy constructor prior to the creation of the campaign system. Consequently, **0 simulated `random_stratified` alphas exist in the database**.

---

## 2. Audit Evidence Matrix

| # | Audit Question | Query | Result | Date Range Covered |
|---|---|---|---|---|
| **1** | **Has any campaign ever been created or completed?** | `SELECT COUNT(*) FROM campaigns;`<br>`SELECT status, COUNT(*) FROM campaigns GROUP BY status;`<br>`SELECT status, COUNT(*) FROM campaign_tasks GROUP BY status;` | **1 campaign total**<br>• `completed`: 1<br><br>**3 tasks total**<br>• `completed`: 3 (all with `alphas_simulated = 0`) | `2026-08-15 17:51:26` |
| **2** | **Did campaign-generated alphas ever get simulated?** | `SELECT COUNT(*) FROM alphas WHERE campaign_task_id IS NOT NULL;`<br>`SELECT COUNT(*) FROM alphas a JOIN alpha_metrics m ON m.alpha_id = a.id WHERE a.campaign_task_id IS NOT NULL;`<br>`SELECT COALESCE(SUM(alphas_simulated), 0), COALESCE(SUM(alphas_passed), 0) FROM campaign_tasks;` | • Generated: **98**<br>• Simulated: **0**<br>• `alphas_simulated` sum: **0**<br>• `alphas_passed` sum: **0** | `2026-08-15 17:51:26` |
| **3** | **Do we have the arm labels Phase 2 needs?** | `SELECT arm, COUNT(*) FROM alphas GROUP BY arm;`<br>`SELECT a.arm, COUNT(*) FROM alphas a JOIN alpha_metrics m ON m.alpha_id = a.id GROUP BY a.arm;` | **Generated alphas:**<br>• `NULL (unlabeled)`: 4,857<br>• `exploit`: 49<br>• `random_stratified`: 49<br>• `plateau_fill`: 0<br><br>**Simulated alphas:**<br>• `NULL (unlabeled)`: 531<br>• `exploit`: **0**<br>• `random_stratified`: **0**<br>• `plateau_fill`: **0** | `2026-07-08` to `2026-08-15` |
| **4** | **Where did the sims that DID happen come from?** | `SELECT COUNT(*) FROM alpha_metrics;`<br>`SELECT COUNT(*) FROM alpha_metrics m JOIN alphas a ON a.id = m.alpha_id WHERE a.campaign_task_id IS NULL;`<br>`SELECT a.source, COUNT(*) AS total, COUNT(m.id) AS simulated FROM alphas a LEFT JOIN alpha_metrics m ON m.alpha_id = a.id GROUP BY a.source;` | • Total metrics: **531**<br>• Without campaign task: **531 (100.0%)**<br><br>**Simulation source breakdown:**<br>• `constructor`: 276 sims (4,647 alphas)<br>• `brain_import`: 190 sims (190 alphas)<br>• `user`: 57 sims (57 alphas)<br>• `ai`: 8 sims (8 alphas)<br>• `campaign_runner`: **0 sims** (98 alphas) | `2026-07-08` to `2026-08-14` |
| **5** | **Territory coverage (`field × operator_family × horizon_band`)** | *Territory derivation over 531 simulated alphas (short: 1–10d, medium: 11–63d, long: 64d+)* | • Distinct simulated territories: **117**<br>• Classified alphas: 502<br>• Pure cross-sectional / unwindowed: 26<br>• Non-standard operators: 3<br>• Top territory: `liabilities × ts_zscore × short (1-10d)` (24 alphas) | `2026-07-08` to `2026-08-14` |
| **6** | **Submission attempts (Phase 1 metric)** | `SELECT COUNT(*) FROM submission_attempts;`<br>`SELECT result, COUNT(*) FROM submission_attempts GROUP BY result;` | • Total attempts: **2**<br>• `submitted`: **2** (`zqNXMEZE`, `N1bkwYGw`)<br>• `rejected`: **0**<br>• `NULL (unresolved)`: **0** | `2026-08-05 12:43:49` to `2026-08-05 12:45:03` |

---

## 3. Detailed Query Log

### 1. Campaign & Task State
```sql
SELECT id, name, status, budget_total, budget_completed, created_at, updated_at 
FROM campaigns;
```
```
id  name                     status     budget_total  budget_completed  created_at           updated_at         
--  -----------------------  ---------  ------------  ----------------  -------------------  -------------------
1   nightly_20260815_175126  completed  50            0                 2026-08-15 17:51:26  2026-08-15 17:51:26
```

```sql
SELECT id, campaign_id, arm, territory_key, status, alphas_total, alphas_simulated, alphas_passed, created_at 
FROM campaign_tasks;
```
```
id  campaign_id  arm                territory_key                                                                       status     alphas_total  alphas_simulated  alphas_passed  created_at         
--  -----------  -----------------  ----------------------------------------------------------------------------------  ---------  ------------  ----------------  -------------  -------------------
1   1            exploit            anl4_fs_detail_estimates_basic_qf_v4_nd_cfps_low/cap:ts_zscore:rank@USA/TOP3000/d1  completed  49            0                 0              2026-08-15 17:51:26
2   1            random_stratified  ebit_median/cap:ts_rank:normalize@USA/TOP3000/d1                                    completed  49            0                 0              2026-08-15 17:51:26
3   1            plateau_fill       composite:blend:close+volume/cap:ts_zscore:rank@USA/TOP3000/d1                      completed  41            0                 0              2026-08-15 17:51:26
```

### 2. Campaign Alpha Simulation Disconnect
```sql
SELECT COUNT(*) FROM alphas WHERE campaign_task_id IS NOT NULL;
-- Result: 98

SELECT COUNT(*) FROM alphas a
JOIN alpha_metrics m ON m.alpha_id = a.id
WHERE a.campaign_task_id IS NOT NULL;
-- Result: 0
```

### 3. Arm Label Distribution
```sql
SELECT COALESCE(arm, 'NULL (unlabeled)') AS arm, COUNT(*) AS count 
FROM alphas 
GROUP BY arm;
```
```
arm                count
-----------------  -----
NULL (unlabeled)   4857 
exploit            49   
random_stratified  49   
```

```sql
SELECT COALESCE(a.arm, 'NULL (unlabeled)') AS arm, COUNT(*) AS simulated_count 
FROM alphas a
JOIN alpha_metrics m ON m.alpha_id = a.id
GROUP BY a.arm;
```
```
arm               simulated_count
----------------  ---------------
NULL (unlabeled)  531            
```

### 4. Historical Simulation Origins
```sql
SELECT a.source, COUNT(*) AS total_alphas, COUNT(m.id) AS simulated_alphas
FROM alphas a
LEFT JOIN alpha_metrics m ON m.alpha_id = a.id
GROUP BY a.source;
```
```
source           total_alphas  simulated_alphas
---------------  ------------  ----------------
ai               8             8               
brain_import     190           190             
campaign_runner  98            0               
constructor      4647          276             
user             57            57              
```

### 5. Territory Breakdown
Of the 531 simulated alphas, **502** map into **117** distinct `field × operator_family × horizon_band` territories. The remaining 29 are pure cross-sectional expressions (26) or non-standard operators (3).

Top simulated territories:
- `liabilities × ts_zscore × short (1-10d)`: 24 alphas
- `liabilities × ts_zscore × medium (11-63d)`: 22 alphas
- `liabilities × ts_zscore × long (64d+)`: 17 alphas
- `anl4_fs_detail_estimate_1qf_v4_nd_totgw_median × ts_zscore × long (64d+)`: 17 alphas
- `max_reported_pretax_profit_quarterly_estimate × ts_zscore × medium (11-63d)`: 15 alphas
- `anl4_fs_detail_estimate_1qf_v4_nd_totgw_median × ts_zscore × medium (11-63d)`: 14 alphas
- `close × ts_zscore × medium (11-63d)`: 14 alphas

---

## 4. Impact on Phase 2

1. **Zero Unbiased Samples:**  
   There are **0 simulated `random_stratified` alphas** across **0 territories** in the database.
2. **Validation Study Premise is Currently Unfunded with Data:**  
   `CLAUDE.md` and `docs/strategy/VALIDATION_PROTOCOL.md` note that Phase 2's validation study relies on the random stratified arm to sample crowded, unpromising territory in an unbiased way. Because that arm has never simulated an alpha, **the pre-registered validation study currently has no unbiased sample to draw upon**.
3. **Data Monoculture Persists:**  
   All 531 existing simulations represent opportunistically sampled or single-template constructor alphas (`ts_zscore` monoculture on a small handful of fields like `liabilities`, `close`, `returns`, `adv20`, and specific fundamentals).

---

## 5. What is Now Unblocked

1. **Engine Bug Repaired (`4e80c92`):**  
   The critical defect where `execute_campaign(simulate=True)` died on `AttributeError: 'BatchResult' object has no attribute 'errored'` has been resolved. The runner now properly banks `BatchResult.simulated` and updates `campaign_tasks` / `campaigns` checkpoint rows.
2. **Requirements for First Real Campaign Run:**  
   To execute the first real batch simulation and begin collecting the unbiased `random_stratified` dataset:
   - Valid BRAIN API credentials (`BRAIN_EMAIL`, `BRAIN_PASSWORD` / authenticated token).
   - Available BRAIN simulation quota headroom (e.g. 50–200 simulations).
   - An operator decision to execute `campaign_runner.execute_campaign(campaign_id, simulate=True)` (or trigger via CLI / scheduled nightly cron).
