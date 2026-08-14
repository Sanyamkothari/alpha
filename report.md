# Alpha research — daily report

**625 alphas · 295 simulated · 2 clearing every BRAIN check**

## Promotion shortlist

| # | Sharpe | Fitness | neighbours | expression |
|---|---|---|---|---|
| 1 | 1.91 | 1.00 | 1.66 | `rank(ts_zscore(divide(ts_backfill(liabilities,120),cap),5))` |
| 2 | 1.82 | 1.01 | 1.56 | `rank(ts_zscore(divide(ts_backfill(liabilities,120),cap),5))` |

Review, correlation-check, and **submit manually**. Nothing here has been sent.

## Cleared BRAIN's checks but was NOT promoted

_none_

## Plateau surfaces (Sharpe by window x decay)

A broad ridge is a mechanism; an isolated high cell is luck.

### `liabilities/cap`
```
  structure: ts=ts_zscore cs=rank group=None neutralization=INDUSTRY
  decay\win        5      10      22      63     126     252
          0     1.98    1.66    1.23    0.99    0.74    0.44
          4     1.82    1.41    0.97    0.83    0.61    0.36
          8     1.56    1.13    0.79    0.72    0.53    0.30
         16     1.20    0.85    0.66    0.65    0.47    0.25
```

## Dataset hit-rate and crowding

| dataset | fields | avg users/field | tried | passed | hit-rate |
|---|---|---|---|---|---|
| `fundamental6` | 886 | 759 | 48 | 2 | 4.2% |
| `univ1` | 6 | 8 | 0 | 0 | — |
| `news12` | 875 | 109 | 0 | 0 | — |
| `fundamental2` | 766 | 131 | 0 | 0 | — |
| `model16` | 24 | 255 | 0 | 0 | — |
| `news18` | 121 | 289 | 0 | 0 | — |
| `analyst4` | 1324 | 356 | 0 | 0 | — |
| `pv13` | 165 | 459 | 0 | 0 | — |
| `option9` | 74 | 595 | 0 | 0 | — |
| `model51` | 16 | 1,300 | 0 | 0 | — |
| `socialmedia12` | 18 | 1,908 | 0 | 0 | — |
| `socialmedia8` | 4 | 2,179 | 0 | 0 | — |

## Allocator — next families

Diversity-capped: no dataset may take more than 20% of the batch, because
concentrating produces correlated alphas that BRAIN rejects.

| field | dataset | why |
|---|---|---|
| `news_open_vol` | `news12` | news12: 180 users, coverage 0.44, dataset unexplored |
| `debt_repayment_year_three` | `fundamental2` | fundamental2: 0 users, coverage 0.56, dataset unexplored |
| `fscore_bfl_total` | `model16` | model16: 30 users, coverage 0.37, dataset unexplored |
| `mean_entity_relevance_score` | `news18` | news18: 36 users, coverage 1.00, dataset unexplored |
| `max_reported_pretax_profit_quarterly_estimate` | `analyst4` | analyst4: 0 users, coverage 0.45, dataset unexplored |
| `single_sector_pureplay_company_count` | `pv13` | pv13: 42 users, coverage 0.35, dataset unexplored |

```
python -m scripts.run_family --field news_open_vol --denominator cap --simulate 48
```

---
*Simulation is automated. Submission is not — no alpha leaves this machine.*