# Decision records

## D1 — Simulation is automated; submission is not (2026-08-04)

**Status:** accepted, reverses a prior project invariant.

### What changed

The project previously held a hard rule that **no code may issue a non-GET
request to BRAIN**, enforced by `BrainReadOnlyClient` (GET-only surface, verb
assertion), the `brain_fetch_log CHECK(http_method='GET')` constraint, and
`tests/test_brain_no_post.py` in CI. `docs/BRAIN_API.md` §3 documented the
simulation endpoint explicitly as "read only so the validator understands valid
syntax — never auto-submit."

That rule is now narrowed: **`POST /simulations` on the user's own account is
permitted. Submission remains prohibited and has no code path.**

### Why

The rule conflated two different actions under one word. *Submitting* an alpha
is an irreversible act with platform and reputational consequences, and it
should always be a human decision. *Simulating* an alpha is a backtest — the
platform provides the endpoint for exactly this purpose, and running one is the
research equivalent of pressing "run" on your own experiment.

Holding both to the same standard had a measurable cost. In the project's entire
history it produced **51 simulated alphas, 0 of which passed** — every one a
price-volume transform, because hand-running backtests caps throughput at a
level where you can only afford to test the obvious. Finding good alphas is a
search problem, and the search was starved. See STRATEGY.md §1.

### The boundary that now holds

| Action | Automated? | Enforcement |
|---|---|---|
| `GET` catalog/metrics reads | yes | rate-limited client |
| `POST /simulations` (own account) | yes | concurrency cap, backoff, `Retry-After` |
| Submitting an alpha | **never** | no code path exists |

### Obligations this creates

1. **The simulation client must be polite.** Concurrency cap, exponential
   backoff, honor `Retry-After`. A research tool that hammers a shared platform
   is a different and worse thing than one that queues considerately.
2. **`tests/test_brain_no_post.py` must be retargeted, not deleted.** It
   currently forbids all non-GET verbs and will fail the moment the simulation
   client lands. It must be rewritten to assert the boundary that actually
   holds: no submission endpoint, no trade/submit write path, and `POST` allowed
   *only* from the simulation client module. A guardrail that gets deleted the
   first time it fires was never a guardrail.
3. **`brain_fetch_log`'s GET-only CHECK stays.** It guards the *fetcher*, which
   is still genuinely read-only. Simulations get their own logging.
4. **`/api/system/banner` must stay accurate.** It now says simulation is
   automated and submission is manual. It previously claimed the tool was
   "read-only with respect to the platform", which would have become false.

### What would reverse this

If BRAIN's terms, rate limits, or a platform communication indicate that
automated simulation is unwelcome, the client goes back to human-initiated
batches. The queue architecture supports that without a rewrite — it is a
concurrency setting of 0 and a manual trigger.


## D2 — Explicit Campaign Resumption (2026-08-16)

**Status:** accepted (resolves review finding F3).

### What changed

Campaign resumption on server boot is no longer triggered automatically on a background daemon thread by default. It is gated behind the configuration setting `AUTO_RESUME_CAMPAIGNS` (default: `false`).

### Why

Auto-resuming queued or interrupted campaigns on server start or during `uvicorn --reload` dev cycles silently consumed the account's simulation quota without operator confirmation. Resuming campaigns now requires an explicit operator action via the UI/API or explicitly setting `AUTO_RESUME_CAMPAIGNS=true` in the environment.


## D3 — Campaign Task Failure Isolation & Forfeiture Policy (2026-08-16)

**Status:** accepted (resolves review finding F4).

### What changed

If an individual campaign task fails (e.g. invalid field code or simulation error), the exception is caught, recorded in `CampaignTask.error`, and the task status is marked as `failed`. The campaign runner proceeds to subsequent tasks in the campaign without stalling the loop.

### Why & Budget Policy

A single malformed field or transient simulation failure must not stall an entire overnight campaign. The failed task's simulation budget is forfeited (not reallocated) to preserve the predetermined multi-armed sampling balance across territories without introducing unbounded retries.


## D4 — Platform Outcome 3-State Lifecycle Machine (2026-08-16)

**Status:** accepted (resolves review finding F6).

### What changed

`sync_alpha_platform_outcome` derives `platform_outcome` using a strict 3-state lifecycle:
1. **`submitted`**: Local operator recorded a submission attempt with `result = "submitted"`. The alpha has been put forward on BRAIN, but platform review has not confirmed approval.
2. **`accepted`**: Platform review API (`sync_submission_outcomes.py`) confirms platform status is `ACCEPTED`/`APPROVED` or in stage `PROD`/`OS`.
3. **`rejected`**: Local submission failed or platform review rejected the alpha.
4. **`pending`**: Unresolved submission attempt recorded.

### Why

Previously, recording a local submission attempt with `result = "submitted"` immediately marked `platform_outcome = "accepted"`. This conflated "put forward by operator" with "accepted and paid by platform", undermining Phase 1's goal of measuring the true empirical acceptance rate.


