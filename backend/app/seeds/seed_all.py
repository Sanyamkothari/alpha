"""Run every seed loader in order. Idempotent.

Usage:  python -m app.seeds.seed_all
"""

from __future__ import annotations

from app.seeds import load_fields, load_lookups, load_operators


def run() -> dict[str, dict]:
    summary = {
        "lookups": load_lookups.run(),
        "operators": load_operators.run(),
        "fields": load_fields.run(),
    }
    return summary


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=2))
