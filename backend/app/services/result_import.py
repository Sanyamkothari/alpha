"""Module 7 — Simulation Result Importer.

Parse a pasted BRAIN result block (JSON, or a tolerant ``key: value`` text block)
into the hybrid ``alpha_metrics`` projection, keeping the raw payload as provenance
in ``simulation_imports.raw_payload``. Importing a result logs a status transition
(per the Phase-1 DoD). This NEVER contacts BRAIN — the human pastes the result.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field

import structlog
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus, ImportSource
from app.models.results import AlphaMetric, SimulationImport
from app.services.alpha_library import transition_status

# Canonical (normalized) metric key -> AlphaMetric float column.
_NUMERIC_COLS = {
    "sharpe": "sharpe",
    "fitness": "fitness",
    "turnover": "turnover",
    "returns": "returns",
    "return": "returns",
    "annualreturns": "returns",
    "margin": "margin_bps",
    "subuniversesharpe": "subuniverse_sharpe",
    "selfcorrelation": "self_correlation",
    "selfcorr": "self_correlation",
    "drawdown": "drawdown",
    "maxdrawdown": "drawdown",
}
_INT_COLS = {
    "longcount": "long_count",
    "long": "long_count",
    "shortcount": "short_count",
    "short": "short_count",
}
_PASS_TOKENS = {"PASS", "PASSED", "OK", "TRUE", "YES"}
_FAIL_TOKENS = {"FAIL", "FAILED", "ERROR", "FALSE", "NO"}
# Sentinel key wrapping a text-origin payload in raw_payload. Deliberately unlikely to
# collide with a real BRAIN metric key so replay can distinguish "this was pasted text"
# from "this was a dict whose only key happened to be raw_text".
RAW_TEXT_KEY = "__raw_text__"


log = structlog.get_logger("result_import")

@dataclass
class ParsedResult:
    columns: dict[str, float | int] = field(default_factory=dict)
    extra: dict = field(default_factory=dict)
    checks: list | None = None
    passed_all_checks: bool | None = None
    raw_payload: dict = field(default_factory=dict)


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def _coerce_number(value: object) -> tuple[float | None, bool]:
    """Return (number, was_percent). Percent applies only to ``"15%"``-style strings."""
    if isinstance(value, bool):
        return None, False
    if isinstance(value, (int, float)):
        # json.loads accepts the literals NaN/Infinity; a non-finite metric is not
        # usable and must never reach a metric column (it would corrupt hit scoring).
        f = float(value)
        return (f, False) if math.isfinite(f) else (None, False)
    text = str(value).strip()
    is_percent = text.endswith("%")
    cleaned = text.rstrip("%").replace(",", "").strip()
    match = re.search(r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?", cleaned)
    if match is None:
        return None, is_percent
    parsed = float(match.group())
    # A literal large enough to overflow to +/-inf (or a nan) is not a usable
    # metric — treat it as unparseable rather than letting inf reach int()/the DB.
    if not math.isfinite(parsed):
        return None, is_percent
    return parsed, is_percent


# BRAIN reports margin as a FRACTION of notional (0.000335 = 3.35 bps) while the
# column stores basis points. The API path converted it; the paste/CSV path did
# not, so the same result imported two ways differed by 10,000x — silently, in a
# column whose name asserts the unit.
#
# The two scales cannot overlap in practice: a fraction-scale margin is ~1e-4,
# and a basis-point margin below this threshold would mean under 0.01 bps, which
# is not a real alpha. Values already in bps therefore pass through untouched.
_MARGIN_FRACTION_CEILING = 0.01


def _margin_to_bps(value: float) -> float:
    """Normalise a margin to basis points, whichever scale it arrived in."""
    if value and abs(value) < _MARGIN_FRACTION_CEILING:
        return value * 10_000.0
    return value


def _coerce_int(value: object) -> int | None:
    """Parse an integer metric, avoiding a lossy float round-trip on big literals."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if math.isfinite(value) else None
    text = str(value).strip().replace(",", "")
    if re.fullmatch(r"-?\d+", text):
        return int(text)  # exact — no float precision loss
    num, _ = _coerce_number(value)
    return int(num) if num is not None else None


def _parse_kv_text(text: str) -> dict:
    payload: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if ":" in line:
            key, _, val = line.partition(":")
        elif "\t" in line:
            key, _, val = line.partition("\t")
        else:
            # Last whitespace-separated token is the value, the rest is the key.
            parts = line.rsplit(None, 1)
            if len(parts) != 2:
                continue
            key, val = parts
        key, val = key.strip(), val.strip()
        if key:
            payload[key] = val
    return payload


def _checks_pass(checks: list) -> bool | None:
    """True only if every RECOGNIZABLE check passed. Items lacking a known
    pass/fail token (e.g. PENDING, WARNING, missing result) are not counted, so
    an unscored check never silently flips the alpha to REJECTED."""
    if not checks:
        return None
    verdicts: list[bool] = []
    for c in checks:
        if not isinstance(c, dict):
            continue
        raw = c.get("result", c.get("status", c.get("value")))
        if raw is None:
            continue
        token = str(raw).upper()
        if token in _PASS_TOKENS:
            verdicts.append(True)
        elif token in _FAIL_TOKENS:
            verdicts.append(False)
        # else: unknown verdict -> not counted
    if not verdicts:
        return None
    return all(verdicts)


def parse_result_block(raw: str | dict) -> ParsedResult:
    if isinstance(raw, dict):
        payload: dict = raw
        raw_payload = raw
    else:
        text = str(raw).strip()
        payload = {}
        raw_payload = {RAW_TEXT_KEY: text}
        if text:
            try:
                obj = json.loads(text)
            except (ValueError, TypeError):
                obj = None
            if isinstance(obj, dict):
                payload = obj
                raw_payload = obj
            else:
                payload = _parse_kv_text(text)

    parsed = ParsedResult(raw_payload=raw_payload)
    for key, value in payload.items():
        nk = _norm_key(key)
        if nk == "checks" and isinstance(value, list):
            parsed.checks = value
            parsed.passed_all_checks = _checks_pass(value)
            # BRAIN reports the sub-universe Sharpe as the *value* of the
            # LOW_SUB_UNIVERSE_SHARPE check, not as a top-level key. Looking only for a
            # top-level key left the column populated on 4 rows out of 565, and the
            # missing figure is what made "does low turnover cost sub-universe Sharpe?"
            # unanswerable — a question that gates a whole line of work.
            for entry in value:
                if isinstance(entry, dict) and entry.get("name") == "LOW_SUB_UNIVERSE_SHARPE":
                    sub = entry.get("value")
                    if isinstance(sub, (int, float)):
                        parsed.columns.setdefault("subuniverse_sharpe", float(sub))
            continue
        if nk in ("passedallchecks", "passed"):
            parsed.passed_all_checks = (
                bool(value) if isinstance(value, bool) else (str(value).upper() in _PASS_TOKENS)
            )
            continue
        if nk in _INT_COLS:
            ival = _coerce_int(value)
            if ival is not None:
                parsed.columns[_INT_COLS[nk]] = ival
            continue
        if nk in _NUMERIC_COLS:
            num, is_percent = _coerce_number(value)
            if num is not None:
                num = num / 100.0 if is_percent else num
                if _NUMERIC_COLS[nk] == "margin_bps":
                    num = _margin_to_bps(num)
                parsed.columns[_NUMERIC_COLS[nk]] = num
            continue
        # Unrecognized but possibly useful — keep in the overflow JSON.
        parsed.extra[key] = value

    return parsed


@dataclass
class ImportResult:
    simulation_import: SimulationImport
    metrics: AlphaMetric
    parsed: ParsedResult
    new_status: str


def held_no_positions(parsed: ParsedResult) -> bool:
    """True when the simulation produced no book at all.

    Two families in this database (``fnd6_newqv1300_spcep12``,
    ``fnd6_newqv1300_xoptdq``) burned 44 simulations between them returning
    ``long_count == short_count == 0`` with every metric zero — the underlying field
    is empty for the configured region/delay/universe, so the expression evaluated to
    nothing on every day. Nothing checked, so the campaign kept feeding the same dead
    field more slots.

    An empty book is not a bad alpha, it is an absent one. It must not be recorded as
    ``rejected`` alongside alphas that genuinely traded and lost, because that
    silently pollutes every hit-rate and every trial count computed from status.
    """
    longs = parsed.columns.get("long_count")
    shorts = parsed.columns.get("short_count")
    if longs is None and shorts is None:
        return False
    return (longs or 0) == 0 and (shorts or 0) == 0


def _status_for(parsed: ParsedResult) -> str:
    if held_no_positions(parsed):
        # Not a verdict on the idea — there was nothing to judge.
        return AlphaStatus.ARCHIVED.value
    if parsed.passed_all_checks is True:
        return AlphaStatus.PASSED.value
    if parsed.passed_all_checks is False:
        return AlphaStatus.REJECTED.value
    return AlphaStatus.TESTING.value


def import_result(
    db: Session,
    alpha: Alpha,
    raw: str | dict,
    *,
    source: str = ImportSource.PASTE.value,
) -> ImportResult:
    parsed = parse_result_block(raw)

    # Demote any previous latest import for this alpha.
    db.execute(
        update(SimulationImport)
        .where(SimulationImport.alpha_id == alpha.id, SimulationImport.is_latest.is_(True))
        .values(is_latest=False)
    )

    sim = SimulationImport(
        alpha_id=alpha.id,
        source=source,
        raw_payload=parsed.raw_payload,
        is_latest=True,
    )
    db.add(sim)
    db.flush()

    metric = AlphaMetric(
        simulation_import_id=sim.id,
        alpha_id=alpha.id,
        extra=parsed.extra or None,
        checks=parsed.checks,
        passed_all_checks=parsed.passed_all_checks,
        **parsed.columns,
    )
    db.add(metric)
    db.flush()

    new_status = _status_for(parsed)
    sharpe = parsed.columns.get("sharpe")
    if held_no_positions(parsed):
        note = "result imported — EMPTY BOOK (0 long, 0 short); field likely unavailable here"
        log.warning(
            "empty_book_simulation",
            alpha_id=alpha.id,
            family=alpha.family_key,
            expression=alpha.expression[:120],
        )
    else:
        note = f"result imported (sharpe={sharpe})" if sharpe is not None else "result imported"
    transition_status(db, alpha, new_status, note=note)

    return ImportResult(simulation_import=sim, metrics=metric, parsed=parsed, new_status=new_status)
