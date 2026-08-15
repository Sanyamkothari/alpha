# Phase 0 Completion Report: Instrumentation & Data Preservation

**Execution Date:** August 15, 2026  
**Status:** COMPLETE & VERIFIED  
**Target Repository:** `Sanyamkothari/alpha` (Commit: `741af4e`)

---

## 1. Executive Summary

Phase 0 resolves the two critical data loss vectors in the platform:
1. **Unrecorded Platform Outcomes:** Alphas submitted to WorldQuant BRAIN previously had no post-submission outcome recording. We implemented database columns, status audit history, REST API endpoints, web console UI modal forms with keyboard shortcut (`o`), and an automated platform sync script (`scripts/sync_submission_outcomes.py`).
2. **Ephemeral Crowding Metrics:** Catalog fetches previously replaced `data_fields` and erased history. We introduced an append-only `data_field_snapshots` table, backfilled past catalog versions (`2026-08-03` and `2026-08-14`), added creation-time point-in-time crowding stamping via `alpha_field_snapshot` (with idempotent backfill across all 4,857 alphas), and updated `fetch_brain_catalog.py` to preserve history indefinitely.
3. **Clean Installation & Runtime Reliability:** Resolved package dependencies (`numpy>=1.26`, `scipy>=1.12`), implemented CWD-independent path resolution for both dev clones and PyInstaller frozen desktop bundles, verified `alembic upgrade`/`downgrade`, and verified 100% test passes (183/183) on fresh clones and empty databases.

---

## 2. Task 1: Data Preservation & Secret Audit

### 2.1 Database & PnL Backup
Before executing any schema alterations or code changes, full backups were taken:
- **SQLite Database Backup:** Checked in WAL via `PRAGMA wal_checkpoint(TRUNCATE)` and backed up using online backup API to `database/wq.db.backup-20260815` (10 MB).
- **PnL Array Backup:** Copied 369 `.npy` files to `database/pnl.backup-20260815/` (12 MB).

### 2.2 Table Row Count Verification
All 16 original tables were preserved with zero row loss. Two new tables were added:

| Table Name | Pre-Migration Rows | Post-Migration Rows | Status |
| :--- | :--- | :--- | :--- |
| `alembic_version` | 1 | 1 | Preserved |
| `alphas` | 4,857 | 4,857 | Preserved |
| `alpha_metrics` | 531 | 531 | Preserved |
| `alpha_status_history` | 5,468 | 5,468 | Preserved |
| `brain_fetch_log` | 0 | 0 | Preserved |
| `categories` | 11 | 11 | Preserved |
| `data_fields` | 6,583 | 6,583 | Preserved |
| `datasets` | 33 | 33 | Preserved |
| `field_tags` | 0 | 0 | Preserved |
| `llm_runs` | 64 | 64 | Preserved |
| `operator_arguments` | 213 | 213 | Preserved |
| `operator_compatibility` | 479 | 479 | Preserved |
| `operator_examples` | 133 | 133 | Preserved |
| `operators` | 105 | 105 | Preserved |
| `simulation_imports` | 531 | 531 | Preserved |
| `tags` | 0 | 0 | Preserved |
| `data_field_snapshots` | *N/A* | **6,583** | **New Table (Append-Only)** |
| `alpha_field_snapshot` | *N/A* | **4,868** | **New Table (Point-in-Time)** |

### 2.3 Secret Audit & Git Protection
- `.gitignore` was updated to explicitly ignore `.env*`, `database/*.db*`, `database/*.db.backup-*`, `database/jobs.json`, `database/pnl/`, `database/pnl.backup-*/`, `.coverage`, `dist/`, `build/`.
- Working tree was audited for API keys/tokens before committing.
- Commits pushed to `origin/main`:
  - `7b3c7c2`: chore: backup database and pnl before migration
  - `3bf0d48`: chore: add database backups and local artifacts to gitignore
  - `b8827fb`: feat: commit engine implementation modules
  - `a361ab9`: test: add test suite covering stages 0 through 6
  - `04f563b`: feat(phase0): add platform outcomes, crowding history snapshots, and clean install fixes
  - `741af4e`: fix(test): make bandit test deterministic with seed

---

## 3. Task 2: Platform Outcomes Recording

### 3.1 Live BRAIN API Investigation

Authenticated probe against live BRAIN API endpoint `GET /alphas/{brain_id}` for submitted alphas:

#### Alpha #243 (BRAIN ID: `gJ8ANpYK`)
```json
{
  "id": "gJ8ANpYK",
  "type": "REGULAR",
  "settings": {
    "instrumentType": "EQUITY",
    "region": "USA",
    "universe": "TOP3000",
    "delay": 1,
    "decay": 0,
    "neutralization": "NONE",
    "truncation": 0.08,
    "pasteurization": "ON",
    "unitHandling": "VERIFY",
    "nanHandling": "OFF",
    "language": "FASTEXPR",
    "visualization": false
  },
  "regular": "rank(divide(close, ts_mean(close, 20)))",
  "category": "Price-Volume",
  "grade": null,
  "stage": "IS",
  "status": "UNSUBMITTED",
  "tags": [],
  "is_submitted": null,
  "dateSubmitted": null,
  "origin": "PLATFORM",
  "favorite": false,
  "hidden": false,
  "color": null,
  "author": "sanyam.kothari16@gmail.com",
  "dateCreated": "2026-08-04T12:54:19.000+00:00",
  "dateModified": "2026-08-04T12:54:19.000+00:00"
}
```

#### Alpha #267 (BRAIN ID: `d5ZXMp2X`)
```json
{
  "id": "d5ZXMp2X",
  "type": "REGULAR",
  "settings": {
    "instrumentType": "EQUITY",
    "region": "USA",
    "universe": "TOP3000",
    "delay": 1,
    "decay": 0,
    "neutralization": "NONE",
    "truncation": 0.08,
    "pasteurization": "ON",
    "unitHandling": "VERIFY",
    "nanHandling": "OFF",
    "language": "FASTEXPR",
    "visualization": false
  },
  "regular": "rank(divide(open, close))",
  "category": "Price-Volume",
  "grade": null,
  "stage": "IS",
  "status": "UNSUBMITTED",
  "tags": [],
  "is_submitted": null,
  "dateSubmitted": null,
  "origin": "PLATFORM",
  "favorite": false,
  "hidden": false,
  "color": null,
  "author": "sanyam.kothari16@gmail.com",
  "dateCreated": "2026-08-04T13:12:45.000+00:00",
  "dateModified": "2026-08-04T13:12:45.000+00:00"
}
```

#### Alpha #2558 (BRAIN ID: `YPvX66Aq`)
```json
{
  "id": "YPvX66Aq",
  "type": "REGULAR",
  "settings": {
    "instrumentType": "EQUITY",
    "region": "USA",
    "universe": "TOP3000",
    "delay": 1,
    "decay": 4,
    "neutralization": "SUBINDUSTRY",
    "truncation": 0.08,
    "pasteurization": "ON",
    "unitHandling": "VERIFY",
    "nanHandling": "OFF",
    "language": "FASTEXPR",
    "visualization": false
  },
  "regular": "group_neutralize(rank(ts_rank(operating_income, 252)) + rank(ts_rank(operating_cashflow_reported_value, 252)) - rank(debt), subindustry)",
  "category": "Fundamental",
  "grade": null,
  "stage": "IS",
  "status": "UNSUBMITTED",
  "tags": [],
  "is_submitted": null,
  "dateSubmitted": null,
  "origin": "PLATFORM",
  "favorite": false,
  "hidden": false,
  "color": null,
  "author": "sanyam.kothari16@gmail.com",
  "dateCreated": "2026-08-05T09:41:22.000+00:00",
  "dateModified": "2026-08-05T09:41:22.000+00:00"
}
```

#### Analysis & Findings:
- Queries to `/users/self/alphas?status=SUBMITTED`, `status=ACCEPTED`, `status=REJECTED`, `status=ACTIVE` all returned 0 results.
- Alphas generated via API simulations are marked `"origin": "PLATFORM"`, `"status": "UNSUBMITTED"`, and `"dateSubmitted": null`.
- When an operator submits an alpha via the web UI or platform review process, the platform outcome must be captured either via manual review entry or via authenticated periodic status sync.

### 3.2 Schema Migration (`c8e1f2a3b4c5`)
Added to `alphas` table:
- `platform_outcome`: VARCHAR(16) NULL with CHECK constraint (`accepted`, `rejected`, `in_review`)
- `outcome_date`: DATE NULL
- `outcome_note`: TEXT NULL
- `outcome_source`: VARCHAR(16) NULL with CHECK constraint (`manual`, `api`)

### 3.3 Entry Paths Implemented
1. **REST API Endpoint:** `POST /api/alphas/{alpha_id}/outcome`
   - Updates `platform_outcome`, `outcome_date`, `outcome_note`, `outcome_source`.
   - Appends an audit-trail entry to `alpha_status_history` (`outcome:<status>: <note>`).
2. **Web Console UI:**
   - Portfolio view displays current outcome badges (`accepted` green, `rejected` red with reason tooltip, `in review` yellow).
   - "Record outcome" button and modal dialog for recording outcomes.
   - Keyboard shortcut `o` opens the outcome modal directly for the selected alpha.
3. **CLI Sync Script:** `scripts/sync_submission_outcomes.py`
   - Queries BRAIN API for submitted alphas and records status updates if detected.

### 3.4 Open Action Item for User
The 3 submitted alphas (#243, #267, #2558) currently have `platform_outcome = NULL`.
- **Alpha #243** (`gJ8ANpYK`): `rank(divide(close, ts_mean(close, 20)))`
- **Alpha #267** (`d5ZXMp2X`): `rank(divide(open, close))`
- **Alpha #2558** (`YPvX66Aq`): `group_neutralize(rank(ts_rank(operating_income, 252)) + rank(ts_rank(operating_cashflow_reported_value, 252)) - rank(debt), subindustry)`

**Action:** Look up their status on your WorldQuant BRAIN web portal and record their outcomes in the console (press `o` on the Portfolio tab) or via API:
```bash
curl -X POST http://localhost:8000/api/alphas/243/outcome \
  -H "Content-Type: application/json" \
  -d '{"platform_outcome": "rejected", "outcome_date": "2026-08-15", "outcome_note": "self-correlation", "outcome_source": "manual"}'
```

---

## 4. Task 3: Crowding History Recovery

### 4.1 `data_field_snapshots` (Append-Only Field History)
- Schema:
  - `field_code`: VARCHAR(128) NOT NULL
  - `dataset_id`: INTEGER ForeignKey(`datasets.id`)
  - `category`: VARCHAR(32) NOT NULL
  - `field_type`: VARCHAR(16) NOT NULL
  - `coverage`: FLOAT
  - `user_count`: INTEGER
  - `alpha_count`: INTEGER
  - `delay`: INTEGER NOT NULL
  - `region`: VARCHAR(16) NOT NULL
  - `universe`: VARCHAR(32) NOT NULL
  - `as_of_date`: DATE NOT NULL
  - Unique Constraint: `(field_code, region, delay, universe, as_of_date)`
- **Backfill Results:**
  - `2026-08-03`: 6,488 field snapshots
  - `2026-08-14`: 95 field snapshots
  - Total: **6,583 historical snapshots**
- **Catalog Fetcher:** `scripts/fetch_brain_catalog.py` updated to write current state to `data_fields` AND append dated snapshot rows to `data_field_snapshots` without deleting past dates.
- **Point-in-Time Service:** `app/services/field_crowding.py` provides `get_field_crowding(db, field_code, as_of_date, ...)` to query historical snapshots.

### 4.2 `alpha_field_snapshot` (Point-in-Time Alpha Stamping)
- Schema:
  - `alpha_id`: INTEGER NOT NULL ForeignKey(`alphas.id`)
  - `field_code`: VARCHAR(128) NOT NULL (durable key)
  - `field_id`: INTEGER ForeignKey(`data_fields.id`) (nullable FK)
  - `user_count`: INTEGER
  - `alpha_count`: INTEGER
  - `coverage`: FLOAT
  - `captured_at`: DATETIME NOT NULL
  - `is_approximate`: BOOLEAN NOT NULL DEFAULT 0
  - Unique Constraint: `(alpha_id, field_code)`
- **Backfill Results:**
  - 4,868 snapshot records stamped across 4,857 existing alphas with `is_approximate = TRUE`.
  - Group identifiers (`sector`, `industry`, `subindustry`, `market`, `cap`) are excluded.
- **Runtime Stamping:** `create_alpha()` in `app/services/alpha_library.py` and `scripts/import_brain_alphas.py` automatically stamp `alpha_field_snapshot` upon creation with exact crowding metrics.

---

## 5. Task 4: Clean Install Fixes

1. **Dependency Pinning (`pyproject.toml`):**
   - Added `numpy>=1.26` and `scipy>=1.12`.
2. **Dynamic Database Path Resolution (`app/config.py`):**
   - Standard clone/dev environment: Resolves to `<repo_root>/database/wq.db` automatically without requiring manual `ALPHA_DATA_DIR` setup.
   - Frozen desktop environment (`sys.frozen = True`): Resolves to `~/.alpha_research/database/wq.db`.
   - Verified with unit tests in `tests/test_config_paths.py`.
3. **Module Metadata Alignment (`app/routers/system.py`):**
   - Updated `/api/system/modules` to report all 10 engine modules as `implemented: True` and `current_stage: 6`.

---

## 6. Task 5: Verification & Validation

### 6.1 Clean Clone Verification (`/tmp/alpha-cleantest`)
1. Cloned repository into `/tmp/alpha-cleantest`.
2. Created virtual environment and installed editable package:
   ```bash
   uv venv
   uv pip install -e ".[dev]"
   ```
3. Executed migrations:
   ```bash
   alembic upgrade head
   ```
4. Seeded operator KB:
   ```bash
   python -m app.seeds.load_operators
   # operators_seeded: inserted=102, total=102, arguments=213
   ```
5. Executed full test suite:
   ```bash
   pytest
   # 183 passed in 1.48s
   ```
6. Verified API endpoints against an empty database:
   - `GET /api/system/banner` -> 200 OK
   - `GET /api/system/modules` -> 200 OK (all 10 implemented)
   - `GET /api/ui/portfolio` -> 200 OK (`{"portfolio": []}`)
   - `GET /` -> 200 OK (Web console HTML renders)

### 6.2 Alembic Migration Rollback Verification
Tested migration rollback and re-application:
```bash
alembic downgrade b7c1d2e3f4a5
alembic upgrade head
```
Rollbacks and upgrades completed with 0 errors.

---

## 7. Migrations Reference & Rollback Instructions

### Migrations List
- `f0be3fc3cdad`: baseline: alpha-generation core schema
- `b7c1d2e3f4a5`: add the 'submitted' alpha status
- `c8e1f2a3b4c5`: add platform outcome fields to alphas
- `d9f2a3b4c5e6`: add crowding history tables (`data_field_snapshots`, `alpha_field_snapshot`)

### Rollback Commands
To revert Phase 0 migrations back to the baseline:
```bash
cd backend
.venv/bin/alembic downgrade b7c1d2e3f4a5
```
To re-apply Phase 0 migrations:
```bash
.venv/bin/alembic upgrade head
```
