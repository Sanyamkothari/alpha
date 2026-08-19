# Brief for the coding agent

**Paste everything below the line into your agent running in `/Users/sanya/Projects/alpha`.**

---

## Task: produce a factual inventory of this project

You are producing a **ground-truth report** about this codebase and its database for an external advisor who has never seen the repository. The advisor has read `README.md` and a project summary, but has good reason to believe those documents describe intended capability rather than actual state.

### Rules — these matter more than completeness

1. **Run queries and commands. Do not read code and infer.** If you report a number, it must come from a query you actually executed. Paste the query alongside the result.
2. **Report absences as absences.** If a table, column, or feature does not exist, write `NOT PRESENT` and move on. Do not substitute the nearest similar thing without labelling it clearly.
3. **Never estimate.** If something cannot be determined, write `CANNOT DETERMINE` and state what would be needed.
4. **Distinguish "code exists" from "code runs" from "code has been used."** A module with 400 lines and no rows in its output table has not been used.
5. Do not fix anything. Do not refactor. This is a read-only survey.

---

## Part A — Data inventory

Query the SQLite database at `database/wq.db` (or wherever it actually lives — state the path you used).

**A1. Volume**

```sql
SELECT COUNT(*) FROM alphas;
```

Then, for every one of the 16 tables: table name and row count. A simple two-column table.

**A2. Territory count — the single most important number in this report**

Define territory as: `field × operator_family × horizon_band`, where horizon bands are short (1–10d), medium (11–63d), long (64d+).

Report:

- Number of **distinct territories** represented in the `alphas` table
- Distribution of alphas per territory: min, median, mean, max, and the count of territories holding more than 100 alphas
- The top 10 territories by alpha count

If `operator_family` or a horizon band is not directly stored, say so, then explain exactly how you derived them (e.g. from the AST, from the operator KB) and show the derivation.

**A3. Outcomes — how far down the funnel does the data actually go?**

Count alphas at each stage:

```
simulated          ____
passed BRAIN checks ____
passed plateau      ____
passed DSR          ____
passed subperiod    ____
promoted/shortlisted ____
marked submitted    ____
ACCEPTED by BRAIN   ____
REJECTED by BRAIN   ____
```

**Be very precise on the last two.** The README states submission is manual and outside this system. So:

- Is there any field anywhere recording whether BRAIN *accepted* a submitted alpha?
- Or does the data stop at "user pressed `s` to mark it submitted"?
- If acceptance is recorded, how does it get in — manual entry, an import script, an API fetch?
- How many rows actually have a non-null acceptance value?

**A4. Crowding data — is it historical or a snapshot?**

For `data_fields`, report the columns holding `user_count`, `alpha_count`, coverage.

Then answer precisely: **when the catalog is re-fetched, are previous values kept or overwritten?**

- Is there a revision/snapshot/history table, or an `as_of_date` column?
- How many distinct fetch dates exist in the data?
- For a single field, can you retrieve its user_count as it stood six months ago?

If the answer is "we only have current values," say so plainly. Do not soften it.

**A5. Time span and PnL**

- Earliest and latest `simulation_date` (or equivalent) in `alphas`
- Alphas per month over that span, as a simple list
- How many alphas have daily PnL vectors stored, and what is the typical vector length
- Is there any record of *when* a territory was first explored?

**A6. Crowding variation**

For the alphas actually in the database, report the distribution of the field-level `user_count`: min, 25th, 50th, 75th percentile, max.

Then state what share of alphas fall in the bottom quartile of crowding **across the whole catalog** (not just across mined fields). This tests whether the data covers a range of crowding levels or only the uncrowded end.

---

## Part B — Codebase ground truth

**B1. Built vs claimed.** For each of the following, report one of: `WORKING` (runs and has produced data), `CODE ONLY` (implemented but no evidence of use), `PARTIAL` (state what is missing), or `NOT PRESENT`. Give one line of evidence for each.

- AST compiler / validator
- Operator knowledge base (how many operators actually seeded?)
- BRAIN catalog fetch
- Batch simulation runner
- Single-field family constructor
- Composite constructor
- Genetic evolution engine
- Plateau filter
- Deflated Sharpe Ratio
- Subperiod stability
- PnL correlation gate
- Multi-armed bandit allocator
- Web console
- Desktop packaging
- LLM field triage

**B2. The reachable path.** Which of the above can actually be triggered by a normal user through the CLI or the UI, versus only by calling library code directly? List the CLI commands and UI actions that exist and work.

**B3. Known defects.** Two are already suspected — confirm or refute each:

- Are `numpy` and `scipy` imported by core services but absent from `backend/pyproject.toml` dependencies?
- Does `/api/system/modules` report modules as unimplemented that are in fact implemented? Paste its actual output.

Then list any other defects you find that would break a clean install on someone else's machine. **Do not fix them.** Just list them.

**B4. Test suite.** Run it. Report: total tests, passed, failed, skipped, wall-clock time. If anything fails, paste the failure names only.

**B5. Persistence and jobs.** Are background jobs in-process and lost on restart, or persisted? Is there a durable queue? What happens to a running simulation batch if the process dies?

---

## Part C — Two specific questions

**C1.** Does the codebase anywhere record, for a given alpha, the crowding of its field **as of the date it was simulated** — as opposed to the crowding today? Yes or no, with the evidence.

**C2.** Open the BRAIN web interface for any submitted alpha and look at the submission checks. Is there a correlation check against **production or platform alphas**, separate from self-correlation against the user's own alphas? Report the exact names of every submission check shown and its threshold. If you cannot access the BRAIN interface, write `NEEDS HUMAN` — do not guess.

---

## Part D — Output

Write the report to `docs/INVENTORY.md` in this structure:

```markdown
# Project Inventory — <date>

## Headline numbers
| | |
|---|---|
| Total alphas | |
| Distinct territories | |
| Median alphas per territory | |
| Submitted | |
| Acceptance outcomes recorded | |
| Historical crowding available | YES / NO |
| Date range of data | |

## A. Data inventory
...

## B. Codebase ground truth
...

## C. Specific questions
...

## D. Things I could not determine
<list, with what would be needed>
```

Lead with the headline table. Keep prose minimal — the reader wants numbers and evidence, not narrative.

**Two of these determine whether a planned research study is possible at all: `Distinct territories` and `Historical crowding available`. Get those exactly right, and if either is bad news, say so at the top rather than burying it.**
