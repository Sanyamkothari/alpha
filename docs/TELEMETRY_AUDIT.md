# Telemetry Performance Audit (2026-08-20)

## Summary

The UI telemetry endpoints (`/api/ui/telemetry`, `/api/ui/verdicts`, `/api/ui/portfolio`) compute live statistics across families, surfaces, and simulation metrics.

### Measured Latencies (Empirical Baseline)
- **Database Size:** 6,377 alphas, 695 simulated distinct (740 `AlphaMetric` backtest runs), 390 PnL series files on disk.
- **Surface Evaluator (`evaluate`):** Scoped to confirmed submitted portfolio ($O(N)$ linear scaling vs $O(N^2)$ library scope). Full evaluation across candidate families completes in **~1.25s** (single thread, SQLite + numpy).
- **Portfolio & Queue UI:** < 25 ms.

### Evaluation of Caching Proposal
A proposal to add a 60-second TTL in-memory cache to the telemetry / verdict endpoints was evaluated and rejected:
1. **Honesty & Freshness:** The dashboard's purpose is reporting real-time progress and ground-truth metrics during active simulation runs and campaign sweeps. A 60s cache creates stale feedback loops during rapid iteration.
2. **Sub-second to Near-second Latency:** At current database scale, execution is well within acceptable interactive UI tolerance.
3. **True Bottleneck:** The primary source of latency in earlier audits was identified not as local database aggregation, but as synchronous network requests to BRAIN inside `ensure_alpha_pnl` on the request thread (addressed by offline PnL backfill).

### Revisit Threshold
- **Threshold for Action:** Revisit caching or query indexing if cold-cache evaluation time exceeds **3.0 seconds** or when library size exceeds 25,000 alphas.

