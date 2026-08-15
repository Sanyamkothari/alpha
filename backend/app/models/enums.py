"""Domain enumerations.

Stored in the DB as TEXT + a CHECK constraint. TEXT+CHECK is portable
SQLite<->Postgres and avoids native-ENUM migration pain; ``FieldCategory`` is
*also* materialized as the ``categories`` lookup table.

Scope note: the hypothesis, template, critic-taxonomy, embedding and
experiment-ledger enums were removed with their modules (see STRATEGY.md).
Changing any value here that backs a CHECK constraint requires a migration.
"""

from __future__ import annotations

from enum import StrEnum


class AlphaStatus(StrEnum):
    UNTESTED = "untested"
    TESTING = "testing"
    PASSED = "passed"  # cleared every BRAIN check in simulation
    REJECTED = "rejected"
    IMPROVED = "improved"
    ARCHIVED = "archived"
    # Records that the HUMAN submitted this alpha on the platform. Nothing in
    # this codebase can submit — this is the operator telling the system what
    # they did, so hit-rate can distinguish "survived the filter" from
    # "actually put forward". See docs/DECISIONS.md.
    SUBMITTED = "submitted"


class PlatformOutcome(StrEnum):
    """Post-submission platform outcome from BRAIN review (derived from submission attempts)."""

    ACCEPTED = "accepted"
    SUBMITTED = "submitted"
    REJECTED = "rejected"
    PENDING = "pending"
    IN_REVIEW = "in_review"


class OutcomeSource(StrEnum):
    """How the platform outcome was recorded."""

    MANUAL = "manual"
    API = "api"


class SubmissionResult(StrEnum):
    """Result of a submission attempt."""

    SUBMITTED = "submitted"
    REJECTED = "rejected"


class ProductionStatus(StrEnum):
    """Lifecycle status of a submitted alpha in production."""

    IN_PRODUCTION = "in_production"
    DECOMMISSIONED = "decommissioned"


class SubmissionCheckName(StrEnum):
    """Known WorldQuant BRAIN simulation and submission checks."""

    SELF_CORRELATION = "SELF_CORRELATION"
    PROD_CORRELATION = "PROD_CORRELATION"
    LOW_SHARPE = "LOW_SHARPE"
    HIGH_TURNOVER = "HIGH_TURNOVER"
    LOW_TURNOVER = "LOW_TURNOVER"
    LOW_FITNESS = "LOW_FITNESS"
    CONCENTRATED_WEIGHT = "CONCENTRATED_WEIGHT"
    LOW_SUB_UNIVERSE_SHARPE = "LOW_SUB_UNIVERSE_SHARPE"
    MATCHES_COMPETITION = "MATCHES_COMPETITION"
    UNITS = "UNITS"
    LADDER_CHECK = "LADDER_CHECK"
    OTHER = "OTHER"


class FieldCategory(StrEnum):
    PRICE = "price"
    VOLUME = "volume"
    FUNDAMENTALS = "fundamentals"
    CASHFLOW = "cashflow"
    NEWS = "news"
    MACRO = "macro"
    ANALYST = "analyst"
    CORPORATE = "corporate"
    ALTERNATIVE = "alternative"
    OPTIONS = "options"
    RISK = "risk"


class FieldType(StrEnum):
    MATRIX = "MATRIX"
    VECTOR = "VECTOR"
    GROUP = "GROUP"


class OperatorCategory(StrEnum):
    ARITHMETIC = "arithmetic"
    TIME_SERIES = "time_series"
    CROSS_SECTION = "cross_section"
    GROUP = "group"
    LOGICAL = "logical"
    TRANSFORMATIONAL = "transformational"
    VECTOR = "vector"
    SPECIAL = "special"


class ArgType(StrEnum):
    MATRIX = "matrix"
    VECTOR = "vector"
    GROUP = "group"
    INT = "int"
    FLOAT = "float"
    BOOLEAN = "boolean"
    CONST = "const"


class ReturnType(StrEnum):
    MATRIX = "matrix"
    VECTOR = "vector"
    GROUP = "group"
    BOOLEAN = "boolean"


class MutationType(StrEnum):
    """How a variant differs from its seed. Backs ``alphas.mutation_type``.

    The grid constructor tags each emitted variant with the axis it varied, so a
    family's members can be grouped by what actually changed.
    """

    OPERATOR_SWAP = "operator_swap"
    PARAMETER_TUNE = "parameter_tune"  # window / decay stepping
    FIELD_SUBSTITUTION = "field_substitution"
    NEUTRALIZATION_DECAY = "neutralization_decay"
    COMPOSITION = "composition"


class PromptCategory(StrEnum):
    """Prompt roles that survive the pruned scope.

    Per STRATEGY.md Rule 3 the LLM proposes *ideas and slot choices*, never
    expression syntax — hence ``field_mechanism`` (read a field, propose an
    economic mechanism) rather than a hypothesis pipeline.
    """

    FIELD_MECHANISM = "field_mechanism"
    FORMULA_GENERATE = "formula_generate"
    FORMULA_REPAIR = "formula_repair"
    CLASSIFICATION = "classification"
    SUMMARY = "summary"
    GUARDRAIL = "guardrail"


class ModelTier(StrEnum):
    OPUS = "opus"
    SONNET = "sonnet"
    HAIKU = "haiku"
    RESERVE = "reserve"


class LLMRunStatus(StrEnum):
    OK = "ok"
    ERROR = "error"
    CACHED = "cached"


class ImportSource(StrEnum):
    PASTE = "paste"
    CSV = "csv"
    JSON = "json"
    BRAIN_API = "brain_api"
