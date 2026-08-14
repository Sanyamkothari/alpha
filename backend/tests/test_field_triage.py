"""Field triage is the one place AI is used, so its failure modes matter.

Triage is an *optimisation*: a malformed or truncated model reply must degrade to
"no opinion" and let the deterministic heuristic carry on. It must never take
down a run, and it must never silently mark junk as usable.
"""

from __future__ import annotations

from app.services.field_triage import FieldVerdict, _parse


def test_parses_plain_json() -> None:
    text = (
        '{"fields": [{"field_code": "cfps_median", "usable": true, '
        '"frequency": "quarterly", "expected_sign": "positive", "why": "consensus estimate"}]}'
    )
    verdicts = _parse(text)
    assert verdicts == [
        FieldVerdict("cfps_median", True, "quarterly", "positive", "consensus estimate")
    ]


def test_parses_markdown_fenced_json() -> None:
    """Models wrap JSON in a fence constantly; that must not cost a batch."""
    text = '```json\n{"fields": [{"field_code": "x", "usable": false, "why": "a count"}]}\n```'
    verdicts = _parse(text)
    assert len(verdicts) == 1
    assert verdicts[0].usable is False


def test_unparseable_reply_yields_no_opinion(caplog) -> None:
    """A broken reply degrades to an empty verdict list, not an exception."""
    assert _parse("I'm sorry, I can't help with that.") == []
    assert _parse("") == []
    assert _parse("{truncated") == []


def test_missing_field_code_is_dropped() -> None:
    """A verdict we cannot attribute to a field is useless and must not be
    applied to some arbitrary row."""
    text = '{"fields": [{"usable": true}, {"field_code": "ok", "usable": true}]}'
    verdicts = _parse(text)
    assert [v.field_code for v in verdicts] == ["ok"]


def test_absent_keys_default_conservatively() -> None:
    """Missing 'usable' must read as NOT usable. Defaulting to True would let a
    lazy reply promote metadata into a 400-simulation batch."""
    verdicts = _parse('{"fields": [{"field_code": "x"}]}')
    assert verdicts[0].usable is False
    assert verdicts[0].frequency == "unknown"
    assert verdicts[0].expected_sign == "unknown"


def test_non_dict_payload_is_safe() -> None:
    assert _parse("[1, 2, 3]") == []
    assert _parse('"just a string"') == []
