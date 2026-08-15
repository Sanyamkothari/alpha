"""Module 6 — Alpha Library: validator-gated storage with exact dedup.

The validator is the hard gate: ``create_alpha`` validates first and refuses to
store anything that does not pass (raises ``InvalidAlphaError``). Stored alphas
carry their normalized form, complexity score, and feature breakdown. Exact dedup
is by ``expression_hash`` = SHA-256 of the normalized expression + the simulation
settings, so the same formula under different settings is a distinct alpha.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from dataclasses import asdict, dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.alphas import Alpha, AlphaFieldSnapshot, AlphaStatusHistory
from app.models.enums import AlphaStatus
from app.services.validation import validate_expression
from app.validator import Issue, ValidationResult


@dataclass
class AlphaSettings:
    region: str = "USA"
    universe: str = "TOP3000"
    delay: int = 1
    neutralization: str | None = None
    decay: int | None = None
    truncation: float | None = None


@dataclass
class CreateResult:
    alpha: Alpha
    created: bool  # False => an identical alpha already existed (dedup hit)
    validation: ValidationResult


class InvalidAlphaError(ValueError):
    """Raised when an expression fails validation — it is never stored."""

    def __init__(self, result: ValidationResult) -> None:
        super().__init__("expression failed validation")
        self.result = result


def expression_hash(normalized: str, s: AlphaSettings) -> str:
    signature = "|".join(
        [
            normalized,
            s.region,
            s.universe,
            f"delay={s.delay}",
            f"neut={s.neutralization or ''}",
            f"decay={'' if s.decay is None else s.decay}",
            f"trunc={'' if s.truncation is None else s.truncation}",
        ]
    )
    return hashlib.sha256(signature.encode("utf-8")).hexdigest()


def create_alpha(
    db: Session,
    expression: str,
    settings: AlphaSettings | None = None,
    *,
    family_key: str | None = None,
    grid: dict | None = None,
    parent_id: int | None = None,
    mutation_type: str | None = None,
    source: str = "user",
    comments: str | None = None,
    arm: str | None = None,
    campaign_task_id: int | None = None,
) -> CreateResult:
    settings = settings or AlphaSettings()
    result = validate_expression(
        db, expression, region=settings.region, delay=settings.delay, universe=settings.universe
    )
    if not result.valid:
        raise InvalidAlphaError(result)

    normalized = result.normalized_expression
    assert normalized is not None  # guaranteed when valid
    digest = expression_hash(normalized, settings)

    existing = db.execute(select(Alpha).where(Alpha.expression_hash == digest)).scalar_one_or_none()
    if existing is not None:
        return CreateResult(alpha=existing, created=False, validation=result)

    warnings = [asdict_issue(w) for w in result.warnings]
    alpha = Alpha(
        expression=expression,
        normalized_expression=normalized,
        expression_hash=digest,
        family_key=family_key,
        parent_id=parent_id,
        mutation_type=mutation_type,
        status=AlphaStatus.UNTESTED.value,
        source=source,
        region=settings.region,
        universe=settings.universe,
        delay=settings.delay,
        neutralization=settings.neutralization,
        decay=settings.decay,
        truncation=settings.truncation,
        is_valid=True,
        validation_warnings=warnings or None,
        complexity_score=result.complexity_score,
        arm=arm,
        campaign_task_id=campaign_task_id,
        # Grid coordinates ride INSIDE feature_json. plateau.load_surface skips
        # any alpha without feature_json["grid"]["window"], so writing them here
        # rather than patching after creation is what stops a caller silently
        # producing alphas the filter cannot see.
        feature_json=({**(result.features or {}), "grid": grid} if grid else result.features),
        comments=comments,
    )
    db.add(alpha)
    try:
        db.flush()
    except IntegrityError:
        # Lost a race on the unique expression_hash: another writer inserted the
        # identical alpha between our SELECT and INSERT. Roll back and return theirs
        # as a dedup hit instead of surfacing a 500.
        db.rollback()
        winner = db.execute(
            select(Alpha).where(Alpha.expression_hash == digest)
        ).scalar_one_or_none()
        if winner is not None:
            return CreateResult(alpha=winner, created=False, validation=result)
        raise
    db.add(
        AlphaStatusHistory(
            alpha_id=alpha.id,
            from_status=None,
            to_status=AlphaStatus.UNTESTED.value,
            note="created",
        )
    )
    # Stamp field crowding snapshot at creation time
    distinct_fields = (result.features or {}).get("distinct_fields")
    stamp_alpha_field_snapshots(db, alpha, distinct_fields)
    db.flush()
    return CreateResult(alpha=alpha, created=True, validation=result)


GROUP_FIELDS: frozenset[str] = frozenset({"sector", "industry", "subindustry", "market", "cap"})


def stamp_alpha_field_snapshots(
    db: Session,
    alpha: Alpha,
    field_codes: Iterable[str] | None = None,
    *,
    is_approximate: bool = False,
) -> list[AlphaFieldSnapshot]:
    """Record point-in-time crowding metrics for the alpha's underlying data fields."""
    from datetime import datetime
    from app.models.fields import DataField

    if field_codes is None:
        if alpha.feature_json and isinstance(alpha.feature_json, dict):
            raw_fields = alpha.feature_json.get("distinct_fields", [])
        else:
            raw_fields = []
        if not raw_fields and alpha.expression:
            try:
                from app.validator.ast_nodes import Field, children
                from app.validator.parser import parse

                def _walk(n):
                    r = []
                    if isinstance(n, Field):
                        r.append(n.name)
                    for c in children(n):
                        r.extend(_walk(c))
                    return r

                raw_fields = _walk(parse(alpha.expression))
            except Exception:
                raw_fields = []
        field_codes = raw_fields

    clean_codes = sorted({f for f in field_codes if f.lower() not in GROUP_FIELDS})
    if not clean_codes:
        return []

    snapshots: list[AlphaFieldSnapshot] = []
    for code in clean_codes:
        existing = db.execute(
            select(AlphaFieldSnapshot).where(
                AlphaFieldSnapshot.alpha_id == alpha.id,
                AlphaFieldSnapshot.field_code == code,
            )
        ).scalar_one_or_none()
        if existing is not None:
            continue

        df = db.execute(
            select(DataField).where(
                DataField.field_code == code,
                DataField.region == alpha.region,
                DataField.delay == alpha.delay,
                DataField.universe == alpha.universe,
            )
        ).scalar_one_or_none()

        if df is None:
            df = db.execute(
                select(DataField).where(DataField.field_code == code)
            ).scalars().first()

        snap = AlphaFieldSnapshot(
            alpha_id=alpha.id,
            field_code=code,
            field_id=df.id if df else None,
            user_count=df.user_count if df else None,
            alpha_count=df.alpha_count if df else None,
            coverage=df.coverage if df else None,
            captured_at=alpha.created_at or datetime.utcnow(),
            is_approximate=is_approximate,
        )
        db.add(snap)
        snapshots.append(snap)

    db.flush()
    return snapshots


def transition_status(
    db: Session, alpha: Alpha, to_status: str, *, note: str | None = None
) -> AlphaStatusHistory:
    """Move an alpha to a new lifecycle status and append an audit-trail row."""
    if to_status not in {s.value for s in AlphaStatus}:
        raise ValueError(f"unknown status {to_status!r}")
    previous = alpha.status
    alpha.status = to_status
    entry = AlphaStatusHistory(
        alpha_id=alpha.id, from_status=previous, to_status=to_status, note=note
    )
    db.add(entry)
    db.flush()
    return entry


def asdict_issue(issue: Issue) -> dict:
    """Issue dataclass -> JSON-friendly dict (span tuple becomes a list)."""
    return asdict(issue)
