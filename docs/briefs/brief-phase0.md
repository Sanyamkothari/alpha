# Brief for the coding agent — Phase 0

**Paste everything below the line into your agent running in `/Users/sanya/Projects/alpha`.**

---

## Context

This project is 37 days old. It has simulated 486 alphas, submitted 3, and recorded **zero** platform outcomes. The database cannot currently answer whether any submitted alpha was accepted, and it cannot answer what a field's crowding was on the day an alpha was simulated — the catalog fetch deletes and replaces.

Both of those gaps destroy data permanently every day they remain. This task closes them.

**Scope: instrumentation only.** You are making the system able to *record* evidence. You are not adding features, not improving alpha generation, not building anything user-facing.

## Rules

1. **Do the tasks in the order given.** Task 1 protects data that later tasks could destroy.
2. **Do not run `scripts/fetch_brain_catalog.py` at any point** until Task 3 is complete and tested. It currently deletes existing field rows, which would destroy the only crowding snapshot that exists.
3. Every schema change goes through an Alembic migration. No manual SQL against the live database.
4. Add tests for new behaviour. The suite currently has 176 passing tests in ~1.25s; keep it green and keep it fast.
5. If a task turns out to be larger than described or you find a reason it shouldn't be done as specified, **stop and report** rather than improvising a different design.
6. Report at the end: what you changed, what you could not do, and anything you found that a human needs to decide.

---

## Task 1 — Protect what exists

**1a. Back up the database before touching anything.**

```bash
cp database/wq.db database/wq.db.backup-$(date +%Y%m%d)
```

Also back up `database/pnl/` (369 `.npy` files). Report the total size.

**1b. Audit for secrets, then commit.**

There are 17 untracked files, including `subperiod.py`, `correlation.py`, `pnl_storage.py`, `allocator_bandit.py`, `composite_constructor.py`, `evolution.py`, and seven test files. These are not in version control and exist on one disk.

Before committing:

- Confirm `.gitignore` covers `.env`, any credential files, `database/*.db`, and `database/pnl/`
- Run `git status` and `git diff --cached` and confirm **no BRAIN credentials, API keys, or `.env` contents** are staged
- Confirm no large binaries are being committed (the 27.8 MB desktop build, `.npy` files)

Then commit the source files in logical groups with clear messages. **Report exactly what you committed and what you deliberately excluded.**

If a remote exists, push. If not, say so — the user needs to create one.

---

## Task 2 — Record platform outcomes

Right now the funnel ends when the user presses `s` in the console, which sets `status='submitted'`. Nothing records whether BRAIN then accepted or rejected the alpha. This is the single most important missing field in the schema.

**2a. First, investigate whether this can be automated.**

The project already has `scripts/import_brain_alphas.py`, which does authenticated GETs against the BRAIN API. Check whether any BRAIN GET endpoint exposes, for an alpha the user has submitted, its **post-submission status** — accepted, rejected, in review, or an equivalent.

Look at the API reference in `docs/`, at what `import_brain_alphas.py` already receives, and at the response shape of the alphas endpoint.

**Report what you find before building anything.** If the API exposes it, an automated sync is far better than manual entry. If it does not, build the manual path in 2b.

**Constraint: read-only.** The project's core invariant is that no code path submits an alpha. GET requests to read status are consistent with that. Do not add any POST to a submission endpoint.

**2b. Schema.**

Alembic migration adding to `alphas`:

```
platform_outcome    TEXT NULL     -- 'accepted' | 'rejected' | 'in_review' | NULL
outcome_date        DATE NULL
outcome_note        TEXT NULL     -- rejection reason if BRAIN gives one
outcome_source      TEXT NULL     -- 'manual' | 'api'
```

Also write outcome changes into `alpha_status_history` so there is an audit trail.

**2c. Entry path.**

- API endpoint to set an outcome for an alpha
- A console action on the submitted list — a keystroke, plus a small form for the note
- If 2a found an API source, a `scripts/sync_submission_outcomes.py` that fetches and updates, marking `outcome_source='api'`

**2d. Backfill the three existing submissions.**

Alphas #243, #267 and #2558 are marked submitted. Their true outcomes are only knowable by the user checking their BRAIN account.

Do **not** guess. Leave them NULL and print a clear note in your final report telling the user to look up those three and enter them.

---

## Task 3 — Make crowding history recoverable

Two separate changes. The second matters more.

**3a. Stop the catalog fetch from destroying history.**

`scripts/fetch_brain_catalog.py` lines ~109–115 execute a `DELETE` for the region/delay/universe before inserting. Every fetch erases the previous state.

Change to append-only with an `as_of_date`:

- Add `as_of_date` to `data_fields` (or a `data_field_snapshots` table if that is cleaner given how `data_fields` is read elsewhere — your call, but say which you chose and why)
- Fetch inserts a new dated revision rather than deleting
- All existing read paths must resolve to the **latest** revision by default, so current behaviour is unchanged
- Add a helper to fetch a field's values as of an arbitrary date

Existing rows carry the fetch dates already present in the data: `2026-08-03` (6,488 fields) and `2026-08-14` (95 fields).

**3b. Stamp every alpha with its fields' crowding at creation time.**

This is the important one. Even with 3a in place, an alpha created tomorrow has no durable link to what crowding looked like when it was created.

New table:

```
alpha_field_snapshot
  alpha_id        FK -> alphas
  field_id        FK -> data_fields
  user_count      INTEGER
  alpha_count     INTEGER
  coverage        FLOAT
  captured_at     DATETIME
  is_approximate  BOOLEAN   -- TRUE for backfilled rows
```

Write these rows at alpha creation, in whatever code path registers alphas (constructor, composite constructor, manual entry — find all of them). Fields come from the AST feature extraction that already runs; exclude group identifiers (`sector`, `industry`, `subindustry`, `market`, `cap`) as the inventory did.

**Backfill the existing 4,857 alphas** using the `2026-08-03` catalog values, with `is_approximate=TRUE`. The project is only five weeks old, so that snapshot is a reasonable proxy — but the flag must be there so any future analysis can exclude them.

---

## Task 4 — Fix the install-breakers

Three confirmed defects that would break a clean install on another machine.

**4a.** `numpy` and `scipy` are imported by `subperiod.py`, `correlation.py` and `pnl_storage.py` but absent from committed `backend/pyproject.toml`. They exist in the working tree uncommitted. Commit them with correct version constraints.

**4b.** `app/config.py` defaults the database to `~/.alpha-research/database/wq.db`, but the project database is at `<repo>/database/wq.db`, requiring `ALPHA_DATA_DIR` to be set manually. Make a fresh clone work without manual environment setup. Document whatever resolution order you choose.

**4c.** `/api/system/modules` returns hardcoded Stage-1 metadata reporting six working modules (`field-catalog`, `sim-runner`, `constructor`, `filter`, `allocator`, `report`) as `implemented: false`. Either derive the values from actual state or correct the constants. Do not leave it lying.

---

## Task 5 — Prove a clean install works

Not "read the code and conclude it should work." Actually do it:

```bash
git clone <this repo> /tmp/alpha-cleantest
cd /tmp/alpha-cleantest/backend
uv venv --python 3.11 .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
python -m alembic upgrade head
python -m app.seeds.load_operators
python -m pytest
python -m uvicorn app.main:app   # confirm it starts and the console loads
```

Report every manual step you had to take that isn't in the README. Those are exactly what will break the three external testers later.

**Do not run `fetch_brain_catalog` in the clean clone** — no credentials should be present there, and it would fail anyway.

---

## Explicitly out of scope

Do not build, even if it seems useful:

- Any product, billing, accounts, or multi-user feature
- The crowding map, exhaustion tracking, or any network layer
- New alpha constructors, operators, or filter techniques
- Wiring the composite or evolution engines into the CLI *(that is the next phase, not this one)*
- Refactoring working code
- Performance optimisation

If you finish early, improve test coverage on what you just built.

---

## Definition of done

- [ ] Database and PnL files backed up
- [ ] All source committed; no secrets or binaries staged; pushed if a remote exists
- [ ] Whether BRAIN's API exposes submission outcomes — answered with evidence
- [ ] Outcome fields exist, are writable from the console, and audit to `alpha_status_history`
- [ ] Catalog fetch no longer deletes; `as_of_date` in place; existing reads unchanged
- [ ] Every new alpha stamped with field crowding at creation; existing 4,857 backfilled and flagged approximate
- [ ] `numpy`/`scipy` committed; default DB path works from a clean clone; `/api/system/modules` accurate
- [ ] Clean-clone install verified end to end, with every undocumented manual step reported
- [ ] Test suite green, still under ~5 seconds

## Final report

Write to `docs/PHASE0.md`:

1. What changed, file by file
2. Migrations added and how to roll back
3. **Whether BRAIN's API can supply submission outcomes** — the most important finding in this task
4. Anything you could not do, and why
5. **Decisions needing the user**, especially: the true outcomes of alphas #243, #267 and #2558, which only they can look up
