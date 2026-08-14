"""Golden corpus for the Phase 1 AST validator (the keystone hard gate).

The KB is built from the real seed files (102 operators, 122 fields) so the corpus
exercises the actual Operator KB + Field DB, hermetically (no DB dependency). A
separate test covers the ``ValidatorKB.from_session`` wiring.
"""

from __future__ import annotations

import json

import pytest
import yaml
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session

from app.config import FIELDS_DIR, OPERATORS_DIR
from app.models import Base
from app.models.fields import DataField, Dataset
from app.models.operators import Operator, OperatorArgument
from app.validator import ValidatorKB, validate
from app.validator.kb import ArgSpec, OpSpec


def _kb_from_seed_files() -> ValidatorKB:
    ops_raw = yaml.safe_load((OPERATORS_DIR / "operators.yaml").read_text(encoding="utf-8"))
    operators: dict[str, OpSpec] = {}
    for item in ops_raw:
        args = tuple(
            ArgSpec(
                name=str(a.get("name", "")),
                position=int(a.get("position", 0)),
                arg_type=str(a.get("type", "matrix")).lower(),
                required=bool(a.get("required", True)),
                is_window=bool(a.get("window", False)),
                min_value=a.get("min"),
                max_value=a.get("max"),
            )
            for a in sorted(item.get("arguments", []) or [], key=lambda a: a.get("position", 0))
        )
        operators[item["name"]] = OpSpec(
            name=item["name"],
            returns=str(item.get("returns", "matrix")).lower(),
            category=str(item.get("category", "")).lower(),
            scope=item.get("scope"),
            args=args,
        )
    fields_raw = json.loads((FIELDS_DIR / "usa_top3000_delay1_sample.json").read_text("utf-8"))
    field_types = {
        r["field_code"]: str(r.get("type", "MATRIX")).upper() for r in fields_raw["results"]
    }
    return ValidatorKB.from_specs(operators, field_types, region="USA", delay=1, universe="TOP3000")


@pytest.fixture(scope="module")
def kb() -> ValidatorKB:
    return _kb_from_seed_files()


# --------------------------------------------------------------- valid corpus

VALID = [
    "rank(close)",
    "ts_mean(close, 20)",
    "ts_zscore(volume, 60)",
    "group_neutralize(rank(ts_delta(close, 5)), sector)",
    "group_mean(returns, 1, sector)",  # scalar constant fills the matrix weight slot
    "subtract(returns, group_mean(returns, 1, sector))",
    "ts_delta(close, 5)",
    "vwap / close",
    "close - open",
    "(high - low) / (volume * close)",
    "rank(close / open)",
    "close + 2 * open",
    "-ts_zscore(returns, 22) / cap",
]


@pytest.mark.parametrize("expr", VALID)
def test_valid_expressions_pass(kb: ValidatorKB, expr: str) -> None:
    result = validate(expr, kb)
    assert result.valid, f"expected valid, got errors: {result.error_codes} -> {result.errors}"
    assert result.normalized_expression
    assert result.features is not None
    assert result.complexity_score is not None


# ------------------------------------------------------------- invalid corpus

# The three PLAN.md §4 golden cases + structural/semantic rejections.
INVALID = [
    ("ts_rsi(close, 14)", "unknown_operator"),  # PLAN golden #1 (genuinely absent op)
    (
        "rank(free_cashflow_growth)",
        "unknown_field",
    ),  # PLAN golden #2 (free_cashflow exists; growth variant absent)
    ("group_neutralize(rank(close), returns)", "non_group_arg"),  # PLAN golden #3
    ("group_neutralize(rank(close), 5)", "non_group_arg"),
    ("ts_mean(close, 1)", "window_out_of_band"),
    ("ts_mean(close, 600)", "window_out_of_band"),
    ("ts_mean(close)", "missing_arg"),
    ("ts_mean(close, 20, 30)", "arity"),
    ("ts_mean(close, sector)", "arg_type"),  # GROUP field in an int-window slot
    ("rank(close))", "parse_error"),
    ("rank(close", "parse_error"),
    ("", "parse_error"),
    ("close +", "parse_error"),
    ("/ open", "parse_error"),
    ("(close + open", "parse_error"),
]


@pytest.mark.parametrize("expr,code", INVALID)
def test_invalid_expressions_rejected(kb: ValidatorKB, expr: str, code: str) -> None:
    result = validate(expr, kb)
    assert not result.valid, f"expected invalid: {expr!r}"
    assert code in result.error_codes, f"{expr!r}: expected {code}, got {result.error_codes}"
    assert result.normalized_expression is None
    assert result.features is None


def test_infix_precedence_and_desugaring(kb: ValidatorKB) -> None:
    # Precedence: * before +
    res1 = validate("close + open * volume", kb)
    assert res1.valid
    assert res1.normalized_expression == "add(close,multiply(open,volume))"

    # Precedence: * before + when first
    res2 = validate("close * open + volume", kb)
    assert res2.valid
    assert res2.normalized_expression == "add(multiply(close,open),volume)"

    # Left-associativity: a - b - c => (a - b) - c
    res3 = validate("close - open - volume", kb)
    assert res3.valid
    assert res3.normalized_expression == "subtract(subtract(close,open),volume)"

    # Parentheses override precedence
    res4 = validate("(close + open) / volume", kb)
    assert res4.valid
    assert res4.normalized_expression == "divide(add(close,open),volume)"


def test_unknown_operator_does_not_mask_nested_errors(kb: ValidatorKB) -> None:
    # An outer unknown op must not stop the walk from also flagging an inner bad field.
    result = validate("ts_rsi(rank(bogus_field), 14)", kb)
    assert not result.valid
    assert "unknown_operator" in result.error_codes
    assert "unknown_field" in result.error_codes


def test_normalized_form_is_whitespace_stable(kb: ValidatorKB) -> None:
    a = validate("group_neutralize(  rank( ts_delta(close,5) ) , sector )", kb)
    b = validate("group_neutralize(rank(ts_delta(close, 5)), sector)", kb)
    assert a.valid and b.valid
    assert a.normalized_expression == b.normalized_expression
    assert a.normalized_expression == "group_neutralize(rank(ts_delta(close,5)),sector)"


def test_features_capture_depth_and_windows(kb: ValidatorKB) -> None:
    result = validate("group_neutralize(rank(ts_delta(close, 5)), sector)", kb)
    assert result.valid
    f = result.features
    assert f is not None
    assert f["depth"] == 4  # group_neutralize > rank > ts_delta > close
    assert f["operator_count"] == 3
    assert f["time_windows"] == [5]
    assert "close" in f["distinct_fields"] and "sector" in f["distinct_fields"]
    assert result.complexity_score > 0


def test_structural_hash_is_field_agnostic(kb: ValidatorKB) -> None:
    # Same tree shape over different MATRIX fields + same window band => same hash.
    a = validate("rank(ts_delta(close, 5))", kb)
    b = validate("rank(ts_delta(returns, 5))", kb)
    assert a.valid and b.valid
    assert a.structural_hash == b.structural_hash
    assert a.normalized_expression != b.normalized_expression  # exact hash still distinguishes
    # A different shape (or a GROUP field where a MATRIX sat) must differ.
    c = validate("rank(close)", kb)
    assert c.structural_hash != a.structural_hash


def test_scope_emits_warning_not_error() -> None:
    kb = ValidatorKB.from_specs(
        {
            "fancy_op": OpSpec(
                name="fancy_op",
                returns="matrix",
                category="special",
                scope="LEVEL2",
                args=(ArgSpec("x", 0, "matrix", True, False, None, None),),
            )
        },
        {"close": "MATRIX"},
    )
    result = validate("fancy_op(close)", kb)
    assert result.valid  # scope is a warning, never a hard failure
    assert any(w.code == "scope" for w in result.warnings)


def test_optional_argument_must_be_keyword_not_positional() -> None:
    # BRAIN's convention: required inputs are positional, OPTIONAL parameters are keyword-only
    # (winsorize(x, std=4), never winsorize(x, 4)). The validator must mirror that or it passes
    # expressions BRAIN rejects — the first live run hit exactly this ("Invalid number of inputs:
    # 2, should be exactly 1" on winsorize(x, 3)).
    kb = ValidatorKB.from_specs(
        {
            "winsorize": OpSpec(
                name="winsorize",
                returns="matrix",
                category="transformational",
                scope=None,
                args=(
                    ArgSpec("x", 0, "matrix", True, False, None, None),
                    ArgSpec("std", 1, "float", False, False, 1.0, 10.0),
                ),
            )
        },
        {"close": "MATRIX"},
    )
    assert validate("winsorize(close, std=3)", kb).valid  # keyword form: accepted
    assert validate("winsorize(close)", kb).valid  # optional omitted: accepted
    bad = validate("winsorize(close, 3)", kb)  # positional optional: rejected
    assert not bad.valid
    assert "positional_optional" in bad.error_codes


# ------------------------------------------------------- from_session wiring


@pytest.fixture()
def session_kb() -> ValidatorKB:
    eng = create_engine("sqlite:///:memory:", future=True)

    @event.listens_for(eng, "connect")
    def _fk(dbapi_conn, _rec):  # type: ignore[no-untyped-def]
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    with Session(eng) as s:
        op = Operator(name="ts_mean", category="time_series", definition="avg", returns="matrix")
        op.arguments = [
            OperatorArgument(name="x", position=0, arg_type="matrix", required=True),
            OperatorArgument(
                name="d",
                position=1,
                arg_type="int",
                required=True,
                is_window=True,
                min_value=2,
                max_value=512,
            ),
        ]
        s.add(op)
        ds = Dataset(dataset_code="pv1", name="Price/Volume", region="USA")
        s.add(ds)
        s.flush()
        s.add(
            DataField(field_code="close", dataset_id=ds.id, category="price", field_type="MATRIX")
        )
        s.commit()
        kb = ValidatorKB.from_session(s, region="USA", delay=1, universe="TOP3000")
    eng.dispose()
    return kb


def test_from_session_builds_usable_kb(session_kb: ValidatorKB) -> None:
    assert session_kb.operator("ts_mean") is not None
    assert session_kb.field_type("close") == "MATRIX"
    assert validate("ts_mean(close, 20)", session_kb).valid
    assert not validate("ts_mean(close, 1)", session_kb).valid  # below the window band
