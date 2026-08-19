# WorldQuant BRAIN — Platform & API reference

> Extracted from the original research notes (§1–6). Everything after §6 described
> modules that no longer exist and was deleted with them — see STRATEGY.md.
>
> **Sourcing caveat, unchanged and important:** WorldQuant publishes no open REST
> API reference. Nearly every endpoint shape below comes from community
> reverse-engineering. Treat it as "very likely correct, confirm against your own
> authenticated session" — not a vendor contract. Facts tagged UNVERIFIED are
> guesses until the first authenticated run captures the real shapes into config.

# WorldQuant BRAIN — Real Platform & API Research (for a READ-ONLY fetcher)

Scope reminder: this report grounds the design of a **read-only, offline research assistant**. Everything below about `/simulations` and submission is documented **only so the formula validator understands valid syntax** — the tool must never auto-submit. Items I could not confirm from a primary/official source are tagged **UNVERIFIED**.

A persistent caveat on sourcing: WorldQuant does **not** publish an open, official REST API reference. The platform's own API docs live behind login at `platform.worldquantbrain.com/learn/documentation`. Almost all endpoint-shape facts below come from **community reverse-engineering** (GitHub wrappers, DeepWiki, member notes). Treat them as "very likely correct but confirm against your own authenticated session," not as vendor-guaranteed contracts.

---

# ✅ VERIFIED against a live session — 2026-08-04

Everything in this section was observed directly against account `SK11953`
(`level: "NONE"`, `permissions: ["TUTORIAL"]`). It **supersedes** any conflicting
claim in the community-sourced sections below.

## Auth

| Fact | Value |
|---|---|
| Endpoint | `POST /authentication` with HTTP **Basic** auth |
| Success | **201** |
| Carrier | **Cookie** `t=<JWT>`, `Domain=api.worldquantbrain.com`, HttpOnly/Secure/SameSite=None — *not* a Bearer header |
| Token TTL | **14400 s (4 h)** — the community ~4 h guess is correct |
| Response | `{"user":{"id":...},"token":{"expiry":14400.0},"permissions":[...]}` |
| Rate Limits | Bursting > 5 req/s returns **`429 API rate limit exceeded`**; client must back off 1–2 s |

`httpx.Client` persists the cookie automatically; no manual header handling.

## Reads (verified 200 on a TUTORIAL account)

| Endpoint | Result & Payload Details |
|---|---|
| `GET /users/self` | User profile: `id`, `email`, `firstName`, `lastName`, `fullName`, `gender`, `dateCreated` |
| `GET /users/self/competitions` | User challenge status: leaderboard rank, tier (`BRONZE`/`SILVER`/`GOLD`), current score, remaining points to next tier, and alpha count |
| `GET /users/self/achievements` | Complete list of 13 gamification milestones, unlock timestamps, and consultant unlock criteria |
| `GET /users/self/activities` | Activity channel catalog: `referrals`, `simulations`, `submissions` |
| `GET /users/self/activities/simulations` | Daily simulation counts: `current` (month), `yesterday`, `total`, `ytd`, and daily time-series records |
| `GET /users/self/activities/submissions` | Daily submission counts: `current`, `yesterday`, `total`, `ytd`, and submission time-series records |
| `GET /users/self/agreements` | List of signed platform agreements, terms of service, and privacy policies |
| `GET /users/self/teams` | Team memberships: `{count, results[]}` |
| `GET /users/self/alphas?limit=&status=&stage=` | Paginated user alphas: `{count: 557, results[]}`, filterable by `stage` (`IS`/`OS`) and `status` (`UNSUBMITTED`/`ACTIVE`) |
| `GET /competitions` | Global competitions catalog: `{count: 279, results[]}` (e.g. `challenge`, `IQC2026S3`, `GAC2026`, `PAC2026`) |
| `GET /competitions/{competition_id}` | Competition details, rules, status (`ACCEPTED`/`EXCLUDED`), scoring model |
| `GET /competitions/{competition_id}/alphas` | List of user alphas actively counting towards that competition |
| `GET /events` | Platform events and research webinars catalog: `{count: 5, results[]}` |
| `GET /events/{event_id}` | Event details, `recording` URLs, `register` links, timezone, description, venue |
| `GET /users/self/tags` | User tags management: `{count, results[]}` |
| `GET /data-categories` | Full 7-category taxonomy with `valueScore`, `datasetCount`, `fieldCount`, `alphaCount`, and `userCount` |
| `GET /data-sets?region&delay&universe&instrumentType` | **14 datasets** for USA/TOP3000/delay1 |
| `GET /data-sets/{dataset_id}` | Detailed dataset metadata, descriptions, coverage, and linked **`researchPapers[]`** |
| `GET /data-fields?…&limit=` | **`count: 4367`** — the real catalog with schema: `{id, description, dataset, category, subcategory, type}` |
| `GET /data-fields/{field_id}` | Individual field definition, matrix type, coverage, and user statistics |
| `GET /operators` | **66 operators** — flat JSON array of `{name, category, scope, definition, description, level}` |
| `GET /alphas/{alpha_id}` | Complete alpha definition with `stage`, `status`, `dateSubmitted`, `is` (in-sample metrics + checks), and `os` blocks |
| `GET /alphas/{alpha_id}/recordsets/daily-pnl` | Date-aligned daily PnL series: `{"schema": ..., "records": [["YYYY-MM-DD", pnl_val], ...]}`. **Semantics Note:** BRAIN daily-pnl recordsets can be cumulative PnL curves or daily returns. The engine auto-detects cumulative curves (lag-1 autocorrelation > 0.99) and differences them at the storage boundary (`save_pnl(..., series_kind="auto")`) to recover stationary daily returns $\Delta P_t$. Standing Sharpe reconciliation (`verify_pnl_reconciliation`) recomputes $\text{SR} = \frac{\mu}{\sigma}\sqrt{252}$ and ensures $|\text{SR}_{\text{recomputed}} - \text{SR}_{\text{reported}}| \le 0.10$. Any unreconciled series fails closed and blocks promotion. |
| `GET /alphas/{alpha_id}/recordsets/turnover` | Date-aligned daily Turnover series: `{"schema": ..., "records": [["YYYY-MM-DD", turnover_pct], ...]}` |
| `GET /users/self/consultant` | **403 Forbidden** on Tutorial accounts (unlocks consultant contract status and payout management) |
| `GET /alphas/{alpha_id}/correlations/prod` | **403 Forbidden** on Tutorial accounts (production pool correlation is restricted to higher consultant tiers) |

## Simulation — works on a TUTORIAL account

`GET /simulations` → **405** (`Method "GET" not allowed`). POST-only, as documented.

1. `POST /simulations` with the §3 body → **201**, **empty body**, and a
   **`Location`** header holding an absolute URL.
2. Poll `GET {Location}`:
   - **while running** → `{"progress": <float>}` — and *nothing else*. There is
     **no `status` key during progress**; the notes' `status == "COMPLETE"`
     condition never appears mid-flight.
   - **when finished** → `{id, type, settings, regular, status, alpha}`.
   - **Terminal condition: poll until the `alpha` key exists.** Do not wait on
     `status`.
3. `GET /alphas/{alpha_id}` → metrics under **`is`**.

Observed wall-clock for one trivial alpha: **≳60 s**, under 120 s.

## The real submission bars — from `is.checks[]`

No longer guesswork. Each entry is `{name, result, limit, value}`:

| Check | Limit | Direction |
|---|---|---|
| `LOW_SHARPE` | **1.25** | Sharpe must exceed |
| `LOW_FITNESS` | **1.0** | Fitness must exceed |
| `LOW_TURNOVER` | **0.01** | floor |
| `HIGH_TURNOVER` | **0.7** | ceiling |
| `LOW_SUB_UNIVERSE_SHARPE` | **0.433 x own Sharpe** (measured, see below) | must exceed |
| `CONCENTRATED_WEIGHT` | — | pass/fail, no numeric limit |
| `MATCHES_COMPETITION` | — | pass/fail |
| `SELF_CORRELATION` | — | **returns `PENDING`** |

> **`LOW_SUB_UNIVERSE_SHARPE` is a RATIO, not a constant — RESOLVED 2026-08-19.**
>
> Measured across **393 stored check payloads** on our own account:
>
> ```
> limit = 0.433 x the alpha's OWN Sharpe     (sd 0.016; 93% of rows within 0.02)
> ```
>
> The `0.01` this table carried for two weeks came from a single TUTORIAL-account alpha
> whose Sharpe was near zero — so its derived limit was near zero too. It was never a
> platform constant; it was one point on a line.
>
> **The consequence is the part that matters, and it is counter-intuitive.** Because the
> bar scales with your own Sharpe, *a stronger signal does not help you pass this check*.
> It is a robustness test: the alpha must hold up on the smaller universe in proportion
> to how well it does on the full one. An entire 16-simulation family
> (`single_sector_pureplay_company_count/cap`) was completed before anyone noticed all 16
> points failed here, precisely because the check was assumed to be a low fixed bar.
>
> BRAIN reports the sub-universe Sharpe as the **`value` of the check entry**, not as a
> top-level key. Reading only the top-level key left `alpha_metrics.subuniverse_sharpe`
> populated on 4 rows out of 565. Fixed in `result_import`; history recovered by
> `scripts/backfill_subuniverse_sharpe.py`.
>
> `FilterConfig.subuniverse_ratio` models it. It **warns and never rejects** — the ratio
> is measured from our own results, not published, and being wrong in the rejecting
> direction would silently discard good alphas.

`is` metrics: `pnl, bookSize, longCount, shortCount, turnover, returns, drawdown,
margin, sharpe, fitness, startDate`. Backtest starts **2019-01-01**.

**`margin` is a fraction, not bps.** Observed `0.000335` = 3.35 bps → multiply by
10,000 for the bps figure the UI shows.

## Account-level access — what `level: NONE` / `TUTORIAL` actually gets

Probed directly on 2026-08-04. **Readable is not the same as simulatable**, and
that distinction costs real work if you assume otherwise.

| Scope | `GET /data-sets` | `POST /simulations` |
|---|---|---|
| USA / delay 1 | 14 datasets, **4,367 fields** | ✅ works |
| USA / delay 0 | 11 datasets, **2,121 fields** | ❌ `400 {"settings":{"delay":["Delay 0 is not available."]}}` |
| EUR / GLB / ASI / CHN / AMR | **0 datasets**, every universe tried | — (nothing to run) |

Two things this settles:

* **Universe does not partition the field catalog.** TOP3000, TOP1000, TOP500,
  TOP200 and TOPSP500 all return the identical 4,367 fields at delay 1. Only
  *delay* changes the catalogue.
* **Delay 0 is visible but gated.** Its catalogue is fully readable and, tellingly,
  ~18x less crowded (avg 27 users/field vs 493 at delay 1; `fundamental2` shows 3
  vs 131). That is a genuine opportunity locked behind account level, not an
  oversight. `BrainClient.config_available()` preflights this so a family is never
  generated against a config that cannot be run.

Non-USA regions returned 0 datasets for every universe spelling tried, so this is
a permission gate rather than a naming error.

## ⚠️ Consequence for the strategy

**`SELF_CORRELATION` comes back `PENDING`** — BRAIN computes it at submission
time, not at simulation time. The correlation gate in STRATEGY.md §4 Rule 5
therefore **cannot** use BRAIN's own self-correlation number to filter
candidates. It has to compute a local proxy (structural-AST + signal-level
similarity against the accepted portfolio). Designing stage 4 around a field that
is only populated after the decision has already been made would not work.

---

## 1) Authentication

**Verified flow (HTTP Basic → session cookie):**
- Endpoint: `POST https://api.worldquantbrain.com/authentication`
- The client sends **HTTP Basic auth** (your BRAIN email + password). The canonical community pattern is a `requests.Session` with `session.auth = (email, password)` and a single `session.post('https://api.worldquantbrain.com/authentication')`. On success the server returns the user/permissions JSON **and sets a session cookie (a JWT)** on the session; every subsequent GET reuses that cookie automatically via the `Session` object. (Source: WQ-Brain `main.py`, wqb `WQBSession`.) [WQ-Brain](https://github.com/RussellDash332/WQ-Brain) · [wqb on PyPI](https://pypi.org/project/wqb/)
- A successful auth returns **HTTP 201** (per `wqb.auth_request()`). [wqb](https://pypi.org/project/wqb/)

**Biometric / "persona" step (verified, real):**
- If the auth response JSON contains an **`inquiry`** field, the account requires a **biometric/identity verification** step. The flow: the user opens `{auth_url}/persona?inquiry={inquiry_id}` in a browser, completes verification, then the client re-POSTs `{auth_url}/persona` with the returned JSON. (Source: worldquant-miner DeepWiki + WQ-Brain.) [DeepWiki: WorldQuant Brain API](https://deepwiki.com/zhutoutoutousan/worldquant-miner/5.1-worldquant-brain-api)
- Practical implication: a fully-headless login is not always possible — some accounts force an interactive biometric step. A read-only client should **assume occasional interactive re-auth**.

**Token expiry / refresh (partially UNVERIFIED):**
- The session JWT is short-lived (community reports cluster around a **few hours**, commonly cited ~4h, but the **exact TTL is UNVERIFIED**). There is **no documented refresh-token endpoint**; the standard practice is simply to **re-run Basic auth** when a request starts returning `401`. [wqb](https://pypi.org/project/wqb/)
- One secondary source (worldquant-miner DeepWiki) describes an alternative `Authorization: Basic … → Authorization: Bearer {jwt}` header pattern instead of cookies. Both ultimately carry the same JWT; the **cookie-via-Session approach is the simplest and most widely used**. The Bearer-header detail is **UNVERIFIED** as the official mechanism.

**What a read-only client must hold:** one authenticated `requests.Session` (cookie jar with the JWT), a stored email/password (or a one-time interactive login), logic to detect `401`/expired session and re-auth, and handling for the optional `inquiry`/persona branch.

---

## 2) Read-only endpoints relevant to us

All under base `https://api.worldquantbrain.com`. Shapes below are community-reverse-engineered; confirm against your session.

**Data fields — `GET /data-fields`** (the one you already identified). Query params: `instrumentType=EQUITY`, `region` (e.g. `USA`), `delay` (0 or 1), `universe` (e.g. `TOP3000`), `dataset.id=={id}` (filter to a dataset), `limit`, `offset` (pagination; community code commonly pages at `limit=50` or `100`). Response is a paginated object with a `count` and a `results[]` array; each field carries roughly: `id`, `description`, `dataset` (id/name), `category`/`subcategory`, `region`, `delay`, `universe`, `type` (e.g. `MATRIX` vs `VECTOR`/`GROUP`), and coverage/usage stats (`coverage`, `userCount`, `alphaCount`). (Sources: wqb `search_fields`/`search_fields_limited`, q3yi/worldquant `crawl.py`, xiegengcai DeepWiki.) [wqb](https://pypi.org/project/wqb/) · [DeepWiki: Configuration & Dataset Mgmt](https://deepwiki.com/xiegengcai/world-quant-brain/7-configuration-and-dataset-management) · [q3yi/worldquant](https://github.com/q3yi/worldquant)
- Note the **field `type`** distinction (MATRIX vs VECTOR vs GROUP) is load-bearing for the formula validator — many operators only accept matrix fields. The exact total (~4367) is region/delay/universe-dependent, so your "~4367 fields" figure is **only valid for one (region, delay, universe) tuple** and should be treated as a per-config count.

**Datasets — `GET /data-sets`** (community: `search_datasets` / `locate_dataset(dataset_id)`), same `region`/`delay`/`universe` params; returns dataset id, name, category, field count, coverage. Example dataset ids: `pv1` (price/volume), `fundamental6`, `analyst…`, `news…`. [wqb](https://pypi.org/project/wqb/)

**Operators — `GET /operators`** (community: `search_operators()`); returns a flat list where each operator has at least `name`, `category` (arithmetic / time-series / cross-sectional / group / logical / transformational / vector), and typically `definition`, `description`, and a `scope`/`level` (some operators unlock only at higher BRAIN levels — Expert/Master/Grandmaster). This single endpoint is the backbone of your **Operator Knowledge Base (Module 2)**. [wqb](https://pypi.org/project/wqb/) · [BRAIN operators](https://platform.worldquantbrain.com/learn/operators)

**Universes / regions / delays:** there is **no clean public "list regions" endpoint** in the community wrappers; these are effectively an enumerated config you pass as params. Verified value sets: regions include `USA` (open to all) and, at consultant level, `CHN, EUR, ASI, GLB, JPN, KOR, TWN, HKG, AMR`; delays `0` and `1`; universes `TOP3000, TOP1000, TOP500, TOP200` (and `TOPSP500` appears in some configs). [Medium: Simulation Settings](https://medium.com/@mapongo/worldquant-brain-how-to-apply-the-simulation-environment-settings-9dc232831bb6)

**Simulation RESULTS & Portfolio Reading (read-only):**
- `GET /alphas/{alpha_id}` — full alpha record including:
  - `stage`: `"IS"` (In-Sample / Simulated) or `"OS"` (Out-of-Sample / Submitted & active in paper trading).
  - `status`: `"UNSUBMITTED"` (not submitted), `"ACTIVE"` (submitted and running), or `"REJECTED"`.
  - `dateSubmitted`: ISO-8601 timestamp string (e.g. `"2026-08-15T17:15:47-04:00"`) when submitted, or `null` if unsubmitted.
  - `regular.code`: the alpha formula expression string.
  - `is`: In-Sample metrics (`sharpe`, `fitness`, `turnover`, `returns`, `margin`, `drawdown`, `longCount`, `shortCount`, and `checks[]`).
  - `os`: Out-of-Sample paper trading performance block (populated once submitted into OS).
- `GET /users/self/alphas` (a.k.a. `filter_alphas`) — paginated list of the user's alphas.
  - Parameters: `status` (`UNSUBMITTED`, `ACTIVE`, `SUBMITTED`), `stage` (`IS`, `OS`), `limit`, `offset`, `order` (`-dateCreated`, `-dateSubmitted`, `-is.sharpe`).
  - Response schema: `{count: int, next: str|null, previous: str|null, results: [...]}`.
- `GET /alphas/{id}/recordsets/daily-pnl` — Historical date-aligned PnL time series:
  - Response schema: `{"schema": {"name": "daily-pnl", ...}, "records": [["YYYY-MM-DD", float_pnl], ...]}`.
  - Used directly by our Pearson correlation engine to compute pairwise self-correlation.
- `GET /alphas/{id}/correlations/self` — Live self-correlation metrics calculated by BRAIN against your existing account submissions.

**Challenge Progress & Leaderboard Tracking:**
- `GET /users/self/competitions` — Live challenge progress tracking:
  ```json
  {
    "results": [{
      "id": "challenge",
      "name": "Challenge",
      "status": "ACCEPTED",
      "leaderboard": {
        "user": "SK11953",
        "rank": 25711,
        "level": "BRONZE",
        "score": 2000.0,
        "alphas": 2
      },
      "progress": {
        "level": "SILVER",
        "score": {"bottom": 1000.0, "top": 5000.0, "remaining": 3000.0}
      }
    }]
  }
  ```

**Data Catalog Taxonomy & Value Scoring:**
- `GET /data-categories` — 7 top-level data categories with vendor value scores:
  - `model` (Value Score: **7.0**, 40 fields, highest vendor value)
  - `option` (Value Score: **6.0**, 138 fields)
  - `analyst` (Value Score: **5.0**, 1,374 fields)
  - `fundamental` (Value Score: **3.0**, 1,758 fields)
  - `news` (Value Score: **3.0**, 1,021 fields)
  - `pv` / Price Volume (Value Score: **2.0**, 202 fields)
  - `socialmedia` (Value Score: **2.0**, 22 fields)

**Simulation & Submission Telemetry:**
- `GET /users/self/activities/simulations` — Daily simulation velocity & volume:
  ```json
  {
    "yesterday": {"value": 95, "start": "2026-08-14", "end": "2026-08-14"},
    "current": {"value": 371, "start": "2026-08-01", "end": "2026-08-14"},
    "total": {"value": 571, "start": "2026-06-30", "end": "2026-08-14"},
    "ytd": {"value": 571, "start": "2026-01-01", "end": "2026-08-14"},
    "records": {"schema": ..., "records": [["2026-08-01", 12], ...]}
  }
  ```
- `GET /users/self/activities/submissions` — Daily submission velocity & volume.

**Dataset Academic Theory & Linked Research Papers:**
- `GET /data-sets/{dataset_id}`:
  ```json
  {
    "id": "model16",
    "name": "Fundamental Scores",
    "researchPapers": [
      {
        "type": "research",
        "title": "Research Paper 01: The Momentum of News",
        "url": "https://support.worldquantbrain.com/hc/en-us/community/posts/13954113156503-Research-Paper-01-The-Momentum-of-News"
      }
    ]
  }
  ```

**Historical Turnover Recordset:**
- `GET /alphas/{alpha_id}/recordsets/turnover`:
  ```json
  {
    "schema": {
      "name": "turnover",
      "properties": [
        {"name": "date", "type": "date"},
        {"name": "turnover", "type": "percent"}
      ]
    },
    "records": [
      ["2019-01-02", 0.8214],
      ["2019-01-03", 0.5671]
    ]
  }
  ```

**Platform Events & Webinars Catalog:**
- `GET /events` & `GET /events/{event_id}`:
  ```json
  {
    "id": "VNbY5El",
    "title": "English | IQC 2026 | Global Research Webinar (5/7)",
    "type": "ONLINE",
    "category": "RESEARCH",
    "language": "English",
    "description": "Global quantitative research methodology and alpha formulation session.",
    "recording": "https://...",
    "register": "https://..."
  }
  ```

**Competition Alpha Portfolios:**
- `GET /competitions/{competition_id}/alphas`:
  - Returns array of submitted alphas actively graded in any competition (`challenge`, `IQC2026S3`, `GAC2026`, `PAC2026`).


**Gamification Achievements & Consultant Milestones:**
- `GET /users/self/achievements` — Returns 13 platform achievements and progression tracks:
  - `SIMULATION_20` / `SIMULATION_100`: Simulation volume milestones.
  - `SUBMIT_1` / `SUBMIT_10`: Submission count milestones.
  - `ALPHA_PERF_GOOD` / `ALPHA_PERF_EXCELLENT` / `ALPHA_PERF_SPECTACULAR`: Quality & performance gates.
  - `TUTORIAL`: Completed tutorial and achieved **GOLD level** (10k points).
  - `SUPER_ALPHA`: Unlocked consultant access to SuperAlpha.
  - `CONSULTANT_SUBMIT`: Submitted first consultant alpha.
  - `CONSULTANT_TUTORIAL`: Completed Consultant tutorial.
  - `OSMOSIS_PIONEER`: Earned Osmosis Rank on Consultant Leaderboard (100k points in 3 scopes).
  - `PYTHON_ALPHA_SUBMIT`: Submitted first Python Alpha as a consultant.

---

## 3) Simulation request format

> Note: this section originally read "read ONLY to validate syntax — never
> auto-submit". That conflated two different things. *Simulation* (backtesting on
> your own account) is now automated; *submission* is not and never will be.
> See docs/DECISIONS.md.

We document this purely so the **Formula Generator (Module 4)** and validator produce syntactically valid expressions.

**Submit (do not implement as automation):** `POST https://api.worldquantbrain.com/simulations` with the JSON body:
```json
{
  "type": "REGULAR",
  "regular": "<alpha expression string>",
  "settings": {
    "instrumentType": "EQUITY",
    "region": "USA",
    "universe": "TOP3000",
    "delay": 1,
    "decay": 0,
    "neutralization": "SUBINDUSTRY",
    "truncation": 0.08,
    "pasteurization": "ON",
    "unitHandling": "VERIFY",
    "nanHandling": "OFF",
    "language": "FASTEXPR",
    "visualization": false
  }
}
```
Response returns a **`Location`** header pointing at the progress resource; the client polls `GET {Location}` until the JSON contains an `alpha` field / `status == "COMPLETE"` (community polls every ~10s). (Source: WQ-Brain `main.py`, worldquant-miner DeepWiki.) [DeepWiki](https://deepwiki.com/zhutoutoutousan/worldquant-miner/5.1-worldquant-brain-api) · [WQ-Brain](https://github.com/RussellDash332/WQ-Brain)

**Expression language ("FASTEXPR") rules the validator must enforce:**
- An alpha is a single expression (or `;`-separated statements with intermediate variable assignments) that evaluates to a per-stock vector; the final value is the portfolio weight signal.
- Function-call syntax `op(arg, ...)`, nested; numeric literals; field identifiers must exist for the chosen `(region, delay, universe)`.
- **Operator/field type compatibility** is the main validity axis: time-series ops (`ts_rank, ts_mean, ts_std_dev, ts_corr, ts_delta, ts_decay_linear, ts_regression, ts_quantile, ts_scale, ts_covariance`) take a matrix field + an integer **lookback window**; cross-sectional ops (`rank, zscore, normalize, winsorize, quantile`) take a vector; group ops (`group_rank, group_zscore, group_neutralize, group_scale`) require a **group field** (e.g. sector/industry/subindustry) as an argument; `trade_when`/`if_else`/`bucket` have fixed arities. Wrong arity, wrong arg type (e.g. passing a group field where a matrix is expected), or unknown field/operator are the structural invalidities. [BRAIN operators](https://platform.worldquantbrain.com/learn/operators)

**Settings semantics (so the importer/critic interpret them):**
- `neutralization`: `NONE | MARKET | SECTOR | INDUSTRY | SUBINDUSTRY` (and group variants) — controls what risk is zeroed out.
- `decay`: linear decay over N days (raising it **lowers turnover**).
- `truncation`: caps single-stock weight (e.g. 0.08 = 8%) — addresses **weight-concentration** failures.
- `delay`: 1 = use prior-day data (default), 0 = same-day (more restricted).
- `pasteurization`/`nanHandling`/`unitHandling`: data-cleaning toggles. [Medium: Simulation Settings](https://medium.com/@mapongo/worldquant-brain-how-to-apply-the-simulation-environment-settings-9dc232831bb6)

**Common validation errors** (for the critic to recognize): unknown/unavailable data field for the region/delay; operator arity/type mismatch; operator not unlocked at the user's level; expression returns all-NaN / zero exposure; lookback window too large for history. (Specific error strings are **UNVERIFIED** — capture them empirically from the user's session.)

---

## 4) Rate limits / ToS for a polite read-only fetcher

- **No published numeric rate limit.** WorldQuant's ToS prohibits abusive automated access; the platform is known to throttle. Treat limits as unknown-and-strict. **UNVERIFIED** exact numbers.
- Community wrappers report the server returns standard throttling/`429`-style responses and that **simulation concurrency is capped (wqb allows 1–10 concurrent)** — a strong hint the server enforces concurrency limits. [wqb](https://pypi.org/project/wqb/)
- Polite-client requirements to bake in: **serial-by-default GETs** with a small delay; honor `Retry-After` if present and otherwise **exponential backoff with jitter** on 429/5xx; **single auth session reused** (don't re-login per request); **aggressive local caching** of slow-changing data (fields/operators/datasets change rarely — cache for days); a configurable **requests-per-minute throttle**; identify a clear non-spoofed User-Agent. Because field lists are large (~4k per config) and static, fetch once and cache, not per-run.
- ToS-critical design rule (aligns with your hard constraints): **GET-only**; never call `POST /simulations`, the persona/submit, or check endpoints in an automated loop; the human drives all testing/submission in the browser.

---

## 5) Existing community wrappers (learn API shape only; ToS caution)

These are useful **only as documentation of the API surface**. Several explicitly automate submission/mining, which **conflicts with our constraints** — we mine them for endpoint/payload shapes, we do not adopt their auto-submit behavior.

- **`wqb` (PyPI)** — the most informative. Clean `WQBSession` with `search_fields`, `search_datasets`, `search_operators`, `locate_alpha`, `filter_alphas`, `simulate`/`concurrent_simulate` (concurrency 1–10), `check`/`concurrent_check`, `patch_properties`. Reveals param names (`region, delay, universe, limit, offset, order`) and that **submit is "not fully implemented"**. Best reference for the read-only endpoints. [wqb](https://pypi.org/project/wqb/)
- **`RussellDash332/WQ-Brain`** — minimal, readable `requests.Session` auth (incl. the `inquiry`/persona biometric branch), the `/simulations` payload, and `Location`-header polling. Best reference for auth + simulation shape. [WQ-Brain](https://github.com/RussellDash332/WQ-Brain)
- **`pyworldquant` (PyPI)** — auto-submit helper; shows settings dict and submission flow. (Auto-submit = against our constraints; reference only.) [pyworldquant](https://pypi.org/project/pyworldquant/)
- **`zhutoutoutousan/worldquant-miner`** + its **DeepWiki** — documents auth/persona, `/simulations`, payload keys; good narrative of the API. [worldquant-miner](https://github.com/zhutoutoutousan/worldquant-miner) · [DeepWiki](https://deepwiki.com/zhutoutoutousan/worldquant-miner/5.1-worldquant-brain-api)
- **`xiegengcai/world-quant-brain` (DeepWiki)** — dataset config, field search (`dataset.id==…`, offset/limit=100), and **self-correlation** analysis (Local vs Server check, 0.7 threshold). [DeepWiki: Self-Correlation](https://deepwiki.com/xiegengcai/world-quant-brain/4.1-self-correlation-analysis)
- **`q3yi/worldquant`** — `crawl.py` for crawling fields by `--type MATRIX --dataset_id …` into SQLite (mirrors your Field Database module 1). [q3yi/worldquant](https://github.com/q3yi/worldquant)

ToS caution to surface in docs: most of these are submission/mining bots. We cite them for shape only and must not replicate automated submission, rate-limit evasion, or multi-account behavior.

---

## 6) Metrics — definitions and "passing" thresholds

Definitions below are verified against member-facing docs; **exact numeric thresholds are region/level/delay-dependent and several are community-reported, not official** — flagged accordingly.

- **Sharpe** — risk-adjusted return: `Sharpe = mean(daily PnL) / std(daily PnL) × √250` (annualized, 250 trading days). Higher is better. [Tymen wiki](https://wiki.untymen.com/%E7%BB%8F%E6%B5%8E%E5%AD%A6/WorldQuant/a/)
- **Returns** — annualized PnL relative to invested capital. BRAIN sims are long-short, dollar-neutral, so invested capital ≈ half the book size; "returns" is annualized return on that. [Tymen wiki](https://wiki.untymen.com/%E7%BB%8F%E6%B5%8E%E5%AD%A6/WorldQuant/a/)
- **Turnover** — fraction of capital traded per day = `Value Traded / Value Held`. Lower = lower transaction cost. [alexisdpc](https://github.com/alexisdpc/WorldQuant-alpha-trading)
- **Margin** — PnL per dollar traded = `total PnL / total traded`, **expressed in basis points (bps / ‱), not %**. (e.g. "5 bps" = $0.0005 profit per $1 traded.) Critic must not confuse bps with %. [Tymen wiki](https://wiki.untymen.com/%E7%BB%8F%E6%B5%8E%E5%AD%A6/WorldQuant/a/)
- **Fitness** — canonical official form: **`Fitness = Sharpe × sqrt( |Returns| / max(Turnover, 0.125) )`**. Rewards high Sharpe + high returns + low turnover. (Note: one source renders it as `Sharpe × sqrt(|Returns|) / max(Turnover,0.125)` — that is a **markdown-rendering ambiguity**; the division-inside-sqrt form is the one BRAIN uses.) [alexisdpc](https://github.com/alexisdpc/WorldQuant-alpha-trading) · [jglazar notes](https://github.com/jglazar/notes/blob/main/quant_interview/worldquant_seminar.md)
- **Sub-universe Sharpe** — the alpha's Sharpe recomputed on a smaller/sub universe; must remain reasonably high (the **threshold scales with sub-universe size**). Guards against alphas that only work on illiquid names. (Threshold formula UNVERIFIED.) [jglazar notes](https://github.com/jglazar/notes/blob/main/quant_interview/submitted_alphas.md)
- **Self-Correlation** — correlation of this alpha's **daily PnL curve** (not weights) against the user's already-submitted/OS alphas, over a **~2-year window**. **Must be < 0.7** to submit; if ≥ 0.7 it fails (and production correlation will too). An alpha above the line can still pass if it **improves Sharpe by ≥ ~10%** vs the correlated one. [DeepWiki: Self-Correlation](https://deepwiki.com/xiegengcai/world-quant-brain/4.1-self-correlation-analysis) · [jglazar notes](https://github.com/jglazar/notes/blob/main/quant_interview/worldquant_seminar.md)

**Commonly-reported "passing" gate for USA/TOP3000 (community, treat as defaults, region-dependent → confirm with user):**
- Sharpe **> 1.25** (in-sample)
- Fitness **≥ 1.0**
- Turnover within a band — roughly **1%–70%** acceptable, with **< 30%** strongly preferred for cost reasons
- **Weight-concentration** check (no single stock / handful of names dominating) — managed via `truncation`
- **Sub-universe Sharpe** test passes
- **Self-correlation < 0.7**
- IS/OS sub-period consistency ("ladder"/2-year robustness)
Sources: [jglazar notes](https://github.com/jglazar/notes/blob/main/quant_interview/worldquant_seminar.md) · [alexisdpc](https://github.com/alexisdpc/WorldQuant-alpha-trading). **The exact numeric thresholds are not officially published per-region — flag as UNVERIFIED and let the user confirm from their own check results.**

---

## 7) Verified API Behaviors

### `GET /alphas/{id}/recordsets/daily-pnl` — Shape and Sharpe Reconciliation
- **Status:** **VERIFIED** (2026-08-16, tested against local database snapshot `database/wq.db` and 369 PnL series in `database/pnl/` via `scripts/verify_pnl_reconciliation.py`).
- **Recordset Format:** Returns a two-column array `[date, pnl_value]`.
- **Series Property:** Non-cumulative, discrete daily dollar PnL (fluctuating positive and negative). For example, Alpha #257 begins with `[27721, 21939, 63990, 34602, 30617, 54748, 17238, -3954, -23985, 11115]`.
- **Annualization & Reconciliation:**
  - Across all 355 stored PnL vectors with reported metrics, 100% (355/355) reconcile with BRAIN's reported in-sample Sharpe ratio within the $\pm 0.05$ standing tolerance.
  - Linear regression of recomputed Sharpe on reported Sharpe yields:
    $$\text{recomputed\_sharpe} = 1.003473 \times \text{reported\_sharpe} - 0.000582 \quad (R^2 = 0.999908)$$
  - **Residual Explanation:** BRAIN annualizes Sharpe using 250 trading days/year ($\times \sqrt{250}$), while the internal `subperiod.py` pipeline uses 252 trading days/year ($\times \sqrt{252}$). The theoretical ratio $\sqrt{252/250} = 1.003992$ accounts for the entire systematic slope and median difference ($\approx 0.0065$). Minor remaining differences (max $\Delta = 0.045$) stem from day-count differences and floating point precision.


---

