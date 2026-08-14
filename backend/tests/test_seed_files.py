"""Validate the on-disk seed files match the loader contract."""

from __future__ import annotations

import json

import yaml

from app.config import FIELDS_DIR, OPERATORS_DIR
from app.models.enums import ArgType, FieldCategory, FieldType, OperatorCategory

OP_KEYS = {"name", "category", "definition", "arguments", "returns", "examples"}


def test_operators_yaml_is_valid() -> None:
    data = yaml.safe_load((OPERATORS_DIR / "operators.yaml").read_text(encoding="utf-8"))
    assert isinstance(data, list) and len(data) >= 90
    names = [o["name"] for o in data]
    assert len(names) == len(set(names)), "duplicate operator names"
    cats = {e.value for e in OperatorCategory}
    argtypes = {e.value for e in ArgType}
    for op in data:
        assert OP_KEYS <= set(op), f"{op.get('name')} missing keys"
        assert op["category"] in cats, f"{op['name']} bad category {op['category']}"
        for arg in op["arguments"]:
            assert arg["type"] in argtypes, f"{op['name']} bad arg type {arg['type']}"
    # A GROUP-consuming operator must exist (validator depends on it).
    assert "group_neutralize" in names


def test_fields_json_is_valid() -> None:
    payload = json.loads(
        (FIELDS_DIR / "usa_top3000_delay1_sample.json").read_text(encoding="utf-8")
    )
    rows = payload["results"]
    assert payload["count"] == len(rows)
    cats = {e.value for e in FieldCategory}
    types = {e.value for e in FieldType}
    codes = [r["field_code"] for r in rows]
    assert len(codes) == len(set(codes)), "duplicate field_code"
    for r in rows:
        assert r["category"] in cats
        assert r["type"] in types
    # Must include GROUP fields so group_* operators can validate.
    assert any(r["type"] == "GROUP" for r in rows)
    assert "close" in codes and "operating_income" in codes
