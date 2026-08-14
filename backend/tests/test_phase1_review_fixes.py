"""Regression tests locking in the Phase 1 adversarial-review fixes (M1-M5, L1-L6).

Pure-function level (validator + result importer), no DB — a small hand-built KB
mirrors the real operator signatures the review used.
"""

from __future__ import annotations

from app.services.result_import import _checks_pass, _coerce_int, parse_result_block
from app.validator import ValidatorKB, validate
from app.validator.kb import ArgSpec, OpSpec


def _kb() -> ValidatorKB:
    def op(name, *args, returns="matrix"):  # type: ignore[no-untyped-def]
        return OpSpec(name=name, returns=returns, category="x", scope=None, args=tuple(args))

    m = lambda n, p: ArgSpec(n, p, "matrix", True, False, None, None)  # noqa: E731
    w = lambda n, p: ArgSpec(n, p, "int", True, True, 2, 512)  # noqa: E731
    g = lambda n, p: ArgSpec(n, p, "group", True, False, None, None)  # noqa: E731
    operators = {
        "ts_mean": op("ts_mean", m("x", 0), w("d", 1)),
        "add": op("add", m("x", 0), m("y", 1)),
        "group_mean": op("group_mean", m("x", 0), m("weight", 1), g("group", 2)),
        "rank": op("rank", m("x", 0)),
    }
    fields = {"close": "MATRIX", "returns": "MATRIX", "sector": "GROUP"}
    return ValidatorKB.from_specs(operators, fields)


KB = _kb()


# M1 — keyword-passed window args are seen by feature extraction + structural hash
def test_keyword_window_is_extracted_and_bucketed() -> None:
    kw5 = validate("ts_mean(close, d=5)", KB)
    kw252 = validate("ts_mean(close, d=252)", KB)
    assert kw5.valid and kw252.valid
    # M1: the keyword window is now seen by feature extraction (was [] / turnover 0)
    assert kw5.features["time_windows"] == [5]
    assert kw5.features["shortest_window"] == 5
    assert kw5.features["expected_turnover_proxy"] > 0
    # M1: different keyword windows no longer collapse to one structural hash
    assert kw5.structural_hash != kw252.structural_hash


# M2 — reordered keyword args dedup as one alpha
def test_kwarg_order_is_canonical() -> None:
    a = validate("group_mean(returns, weight=1, group=sector)", KB)
    b = validate("group_mean(returns, group=sector, weight=1)", KB)
    assert a.valid and b.valid
    assert a.normalized_expression == b.normalized_expression
    assert a.structural_hash == b.structural_hash


# M3 — double-fill and duplicate kwargs are rejected
def test_positional_and_keyword_same_slot_rejected() -> None:
    r = validate("ts_mean(close, 20, d=30)", KB)
    assert not r.valid
    assert "duplicate_arg" in r.error_codes


def test_duplicate_keyword_rejected() -> None:
    r = validate("ts_mean(close, d=20, d=30)", KB)
    assert not r.valid
    assert "duplicate_kwarg" in r.error_codes


# M4 — pathological input degrades to a clean parse error, never a crash
def test_deep_nesting_is_a_parse_error_not_a_crash() -> None:
    expr = "rank(" * 1000 + "close" + ")" * 1000
    r = validate(expr, KB)
    assert not r.valid
    assert "parse_error" in r.error_codes


def test_giant_numeric_literal_is_a_parse_error() -> None:
    r = validate("add(close, " + "9" * 400 + ")", KB)
    assert not r.valid
    assert "parse_error" in r.error_codes


# L1 — numerically equal constants dedup to one hash
def test_int_and_float_constants_canonicalize() -> None:
    assert validate("add(close, 5)", KB).normalized_expression == "add(close,5)"
    assert validate("add(close, 5.0)", KB).normalized_expression == "add(close,5)"
    assert validate("add(close, -0.0)", KB).normalized_expression == "add(close,0)"


# M5 — overflowing metric is skipped, does not crash the import
def test_overflowing_metric_is_skipped_not_crashed() -> None:
    parsed = parse_result_block({"sharpe": "1e999", "longCount": "1e999", "turnover": 0.1})
    assert "sharpe" not in parsed.columns
    assert "long_count" not in parsed.columns
    assert parsed.columns["turnover"] == 0.1


# Non-finite numeric VALUES (not strings) are skipped too — json.loads accepts the
# NaN/Infinity literals, and a NaN metric must never reach a column (it would score a HIT).
def test_non_finite_numeric_value_is_skipped() -> None:
    parsed = parse_result_block({"sharpe": float("nan"), "fitness": float("inf"), "turnover": 0.2})
    assert "sharpe" not in parsed.columns
    assert "fitness" not in parsed.columns
    assert parsed.columns["turnover"] == 0.2


# L4 — an unscored check does not flip the alpha to REJECTED
def test_unknown_check_verdict_is_not_a_failure() -> None:
    assert _checks_pass([{"name": "X", "result": "PENDING"}]) is None
    assert (
        _checks_pass([{"name": "X", "result": "PASS"}, {"name": "Y", "result": "PENDING"}]) is True
    )
    assert _checks_pass([{"name": "X", "result": "PASS"}, {"name": "Y", "result": "FAIL"}]) is False
    assert _checks_pass([{"name": "X"}]) is None  # missing result


# L6 — big integer counts keep exact precision (no float round-trip)
def test_large_integer_precision_preserved() -> None:
    assert _coerce_int("9007199254740993") == 9007199254740993  # 2**53 + 1


def test_margin_normalised_to_bps_from_either_scale() -> None:
    """BRAIN sends margin as a fraction; the column stores basis points.

    The API path used to convert and the paste path did not, so the same result
    imported two ways differed by 10,000x — in a column whose name asserts the
    unit. One rule now covers both.
    """
    from app.services.result_import import parse_result_block

    # Fraction, as BRAIN's `is` block reports it.
    assert parse_result_block({"margin": 0.000335}).columns["margin_bps"] == 3.35

    # Already basis points (e.g. a hand-typed value) — must pass through.
    assert parse_result_block({"margin": 3.35}).columns["margin_bps"] == 3.35

    # Zero stays zero rather than being scaled.
    assert parse_result_block({"margin": 0}).columns["margin_bps"] == 0


def test_normalize_is_block_does_not_double_convert() -> None:
    """The client must not pre-scale, or the parser would scale it again."""
    from app.services.brain.client import normalize_is_block
    from app.services.result_import import parse_result_block

    once = parse_result_block(normalize_is_block({"margin": 0.000335}))
    assert once.columns["margin_bps"] == 3.35
