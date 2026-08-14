"""Run LLM field triage over a dataset (or the datasets the allocator favours).

    python -m scripts.triage_fields --dataset fundamental2
    python -m scripts.triage_fields --dataset news12 --limit 40

Needs LLM_PROVIDER + an API key in .env. Fires once per dataset, not per alpha.
"""

from __future__ import annotations

import argparse
import sys

from app.config import settings
from app.db import session_scope
from app.services.field_triage import triage_dataset


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--region", default="USA")
    ap.add_argument("--delay", type=int, default=1)
    ap.add_argument("--universe", default="TOP3000")
    args = ap.parse_args()

    if settings.llm_provider == "fake":
        print("LLM_PROVIDER is 'fake' — set it to openrouter/anthropic in .env first.")
        return 1

    with session_scope() as db:
        verdicts = triage_dataset(
            db,
            args.dataset,
            region=args.region,
            delay=args.delay,
            universe=args.universe,
            limit=args.limit,
        )

    usable = [v for v in verdicts if v.usable]
    print(f"\ntriaged {len(verdicts)} fields — {len(usable)} usable\n")
    for v in usable[:20]:
        print(f"  USABLE  {v.field_code}")
        print(f"          {v.frequency}, expect {v.expected_sign} — {v.why}")
    rejected = [v for v in verdicts if not v.usable]
    if rejected:
        print(f"\n  rejected {len(rejected)}, e.g.:")
        for v in rejected[:5]:
            print(f"    {v.field_code} — {v.why}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
