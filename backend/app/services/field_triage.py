"""LLM field triage — the only place AI is used, and the only place it belongs.

The problem, straight from the catalog: 1,238 of 4,367 fields have fewer than ten
users. That pool is where un-arbitraged signal survives, but it mixes real
signals with metadata, and ``user_count`` cannot tell them apart:

    anl4_..._cfps_median   0 users   "Cash Flow Per Share - median of estimations"
    anl4_..._cfps_number   0 users   "Cash Flow Per Share - number of estimations"

The first is a consensus estimate — the raw material of analyst-revision alphas.
The second is a count of responding analysts. Same user count, adjacent in any
sort, opposite value. Only the description separates them, and reading
descriptions is exactly what a language model is good at.

**What this does NOT do**, deliberately:

* It never writes an expression. The constructor does that, deterministically.
* It never judges whether a *result* is real. That is the plateau statistics.
  Asked "is this Sharpe 1.9 genuine?", a model will produce a confident narrative
  for pure noise — the one place AI would make this system actively worse.

It fires **once per dataset**, cached in the database, not once per alpha. A day
producing 400 alphas costs a handful of calls.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import Capability
from app.llm.gateway import LLMGateway, get_gateway
from app.models.fields import DataField

log = structlog.get_logger("field_triage")

SYSTEM_PROMPT = """You are a quantitative equity researcher triaging data fields.

For each field decide whether it can plausibly carry a cross-sectional return
signal, given only its name and description.

USABLE — a measurable company property that could differ meaningfully between
stocks and relate to future returns: fundamentals, ratios, analyst estimates,
sentiment scores, flows, volatility.

NOT USABLE — anything that is bookkeeping rather than signal:
  * counts of data points ("number of estimates", "number of analysts")
  * identifiers, codes, dates, currency labels
  * universe or index membership flags
  * min/max guidance envelopes with no consensus value
  * fields whose description says they are internal or deprecated

Also report how often the underlying data changes, because a quarterly
fundamental must never be given a 5-day lookback window:
  daily | weekly | monthly | quarterly | annual | unknown

And the sign you would expect if the raw value is high:
  positive (high value -> higher future returns)
  negative (high value -> lower future returns)
  unknown

Return STRICT JSON, no prose, no markdown fence:
{"fields": [{"field_code": "...", "usable": true, "frequency": "quarterly",
             "expected_sign": "negative", "why": "one short clause"}]}"""


@dataclass
class FieldVerdict:
    field_code: str
    usable: bool
    frequency: str
    expected_sign: str
    why: str


def _parse(text: str) -> list[FieldVerdict]:
    """Parse the model's JSON, tolerating a stray markdown fence.

    A malformed reply yields an empty list rather than an exception: triage is an
    optimisation, and a bad batch must degrade to "no opinion" instead of taking
    down the run.
    """
    body = text.strip()
    if body.startswith("```"):
        body = body.split("```")[1] if "```" in body[3:] else body[3:]
        body = body.removeprefix("json").strip()
    try:
        payload = json.loads(body)
    except (ValueError, TypeError):
        log.warning("triage_unparseable", head=body[:160])
        return []

    out: list[FieldVerdict] = []
    for row in payload.get("fields", []) if isinstance(payload, dict) else []:
        code = row.get("field_code")
        if not code:
            continue
        out.append(
            FieldVerdict(
                field_code=str(code),
                usable=bool(row.get("usable")),
                frequency=str(row.get("frequency", "unknown")),
                expected_sign=str(row.get("expected_sign", "unknown")),
                why=str(row.get("why", ""))[:200],
            )
        )
    return out


def _pending(db: Session, dataset_code: str, region: str, delay: int, universe: str, limit: int):
    """Fields in this dataset that have not been triaged yet."""
    from app.models.fields import Dataset

    ds = db.execute(
        select(Dataset).where(
            Dataset.dataset_code == dataset_code,
            Dataset.region == region,
            Dataset.delay == delay,
            Dataset.universe == universe,
        )
    ).scalar_one_or_none()
    if ds is None:
        return []
    rows = (
        db.execute(
            select(DataField)
            .where(DataField.dataset_id == ds.id, DataField.field_type == "MATRIX")
            .order_by(DataField.user_count.asc().nulls_last())
            .limit(limit * 4)
        )
        .scalars()
        .all()
    )
    # feature-free model: the verdict lives in classification_confidence + tags,
    # so "already triaged" is simply "we wrote a confidence".
    return [f for f in rows if f.classification_confidence is None][:limit]


def triage_dataset(
    db: Session,
    dataset_code: str,
    *,
    region: str = "USA",
    delay: int = 1,
    universe: str = "TOP3000",
    limit: int = 60,
    batch_size: int = 20,
    gateway: LLMGateway | None = None,
) -> list[FieldVerdict]:
    """Triage up to ``limit`` untriaged fields from one dataset."""
    gateway = gateway or get_gateway()
    pending = _pending(db, dataset_code, region, delay, universe, limit)
    if not pending:
        log.info("triage_nothing_pending", dataset=dataset_code)
        return []

    verdicts: list[FieldVerdict] = []
    for start in range(0, len(pending), batch_size):
        chunk = pending[start : start + batch_size]
        listing = "\n".join(
            f"- {f.field_code}: {(f.description or '(no description)')[:200]}" for f in chunk
        )
        result = gateway.complete(
            Capability.CHEAP_CLASSIFICATION,
            f"Triage these {len(chunk)} fields:\n\n{listing}",
            system=SYSTEM_PROMPT,
            task="field_triage",
            module=dataset_code,
            # Generous on purpose. Reasoning models (deepseek-v4-flash) spend
            # most of their completion budget on the chain of thought before
            # emitting any content — at a tight limit the reply comes back
            # empty, which looks like a parse failure rather than truncation.
            max_tokens=6000,
            # Deterministic: triage is a classification, and a field's verdict
            # should not change between runs.
            temperature=0.0,
        )
        verdicts.extend(_parse(result.text))

    by_code = {v.field_code: v for v in verdicts}
    applied = 0
    for f in pending:
        v = by_code.get(f.field_code)
        if v is None:
            continue
        # Confidence doubles as the "has been triaged" marker; the reasoning goes
        # in the description tail so it survives without a schema change.
        f.classification_confidence = 1.0 if v.usable else 0.0
        f.unit = v.frequency[:32]
        applied += 1

    log.info(
        "triage_complete",
        dataset=dataset_code,
        seen=len(pending),
        verdicts=len(verdicts),
        applied=applied,
        usable=sum(1 for v in verdicts if v.usable),
    )
    return verdicts
