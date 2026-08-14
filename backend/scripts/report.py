"""Print the daily report.

python -m scripts.report              # to stdout
python -m scripts.report -o today.md  # to a file
"""

from __future__ import annotations

import argparse
import sys

from app.db import session_scope
from app.services.report import build


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--output", default=None)
    ap.add_argument("--region", default="USA")
    ap.add_argument("--delay", type=int, default=1)
    ap.add_argument("--universe", default="TOP3000")
    args = ap.parse_args()

    with session_scope() as db:
        text = build(db, region=args.region, delay=args.delay, universe=args.universe)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
