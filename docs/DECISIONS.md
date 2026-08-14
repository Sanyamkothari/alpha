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
