# Telemetry Performance Audit (2026-08-16)

## Summary

The UI telemetry endpoints (`/api/ui/telemetry`, `/api/ui/verdicts`, `/api/ui/portfolio`) compute live statistics across families, surfaces, and simulation metrics.

### Measured Latencies (Empirical Baseline)
- **Database Size:** 5,176 alphas, 531 simulated (`AlphaMetric`), 369 PnL series files.
- **Surface Evaluator (`evaluate`):** 17 families / 245 candidate points evaluated in **946.2 ms** (single thread, SQLite + numpy).
- **Portfolio & Queue UI:** < 25 ms.

### Evaluation of Caching Proposal
A proposal to add a 60-second TTL in-memory cache to the telemetry / verdict endpoints was evaluated and rejected:
1. **Honesty & Freshness:** The dashboard's purpose is reporting real-time progress and ground-truth metrics during active simulation runs and campaign sweeps. A 60s cache creates stale feedback loops during rapid iteration.
2. **Sub-second Latency:** At current database scale, sub-second execution (946 ms) is well within acceptable interactive UI tolerance.
3. **True Bottleneck:** The primary source of latency in earlier audits was identified not as local database aggregation, but as synchronous network requests to BRAIN inside `ensure_alpha_pnl` on the request thread (addressed in F18).

### Revisit Threshold
- **Threshold for Action:** Revisit caching or query indexing if cold-cache evaluation time exceeds **3.0 seconds** or when library size exceeds 25,000 alphas.
