"""Interactive CLI tool to record remembered past submission attempts and rejections.

Collects negative labels and rejection reasons from operator memory, tagging them
with is_recalled=TRUE to distinguish historical recollection from live platform syncs.

Usage:
    python -m scripts.record_past_attempts
    python -m scripts.record_past_attempts --alpha-id 243 --result rejected --check SELF_CORRELATION --detail "0.74 vs 0.70"
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from sqlalchemy import select

from app.db import session_scope
from app.models.alphas import (
    Alpha,
    AlphaStatusHistory,
    SubmissionAttempt,
    sync_alpha_platform_outcome,
)
from app.models.enums import SubmissionCheckName, SubmissionResult

CHECKS = [c.value for c in SubmissionCheckName]


def interactive_prompt() -> None:
    print("=" * 70)
    print(" RECORD PAST SUBMISSION ATTEMPTS & REJECTIONS (RECALLED FROM MEMORY)")
    print("=" * 70)
    print("These records provide negative labels for self-correlation & filter analysis.\n")

    with session_scope() as db:
        while True:
            query = input("Enter Alpha ID or formula search term (or 'q' to exit): ").strip()
            if not query or query.lower() == "q":
                break

            target_alpha = None
            if query.isdigit():
                target_alpha = db.get(Alpha, int(query))
            else:
                matches = db.execute(
                    select(Alpha).where(Alpha.expression.like(f"%{query}%")).limit(5)
                ).scalars().all()
                if not matches:
                    print(f"No alphas found matching {query!r}.\n")
                    continue
                if len(matches) == 1:
                    target_alpha = matches[0]
                else:
                    print("\nMultiple matches found:")
                    for idx, a in enumerate(matches, 1):
                        print(f"  [{idx}] #{a.id} ({a.family_key or 'no family'}): {a.expression[:60]}...")
                    sel = input("Select number [1-N]: ").strip()
                    if sel.isdigit() and 1 <= int(sel) <= len(matches):
                        target_alpha = matches[int(sel) - 1]
                    else:
                        print("Invalid selection.\n")
                        continue

            if not target_alpha:
                print("Alpha not found.\n")
                continue

            print(f"\nSelected Alpha #{target_alpha.id}:")
            print(f"  Expression: {target_alpha.expression}")
            print(f"  Settings:   {target_alpha.region} / {target_alpha.universe} / delay {target_alpha.delay} / {target_alpha.neutralization or 'NONE'} / decay {target_alpha.decay or 0}")
            print(f"  Status:     {target_alpha.status} (platform_outcome: {target_alpha.platform_outcome or 'none'})")

            # Prompt date
            raw_date = input("Attempt Date (YYYY-MM-DD or Enter for today): ").strip()
            if raw_date:
                try:
                    att_date = datetime.fromisoformat(raw_date)
                except ValueError:
                    att_date = datetime.utcnow()
            else:
                att_date = datetime.utcnow()

            # Prompt result
            res_input = input("Submission Result [1=rejected (default), 2=submitted]: ").strip()
            result_val = SubmissionResult.SUBMITTED.value if res_input in ("2", "submitted", "s") else SubmissionResult.REJECTED.value

            failed_check = None
            check_detail = None
            if result_val == SubmissionResult.REJECTED.value:
                print("\nKnown Failed Checks:")
                for i, c in enumerate(CHECKS, 1):
                    print(f"  [{i}] {c}")
                c_sel = input("Select Failed Check [1-N or name]: ").strip()
                if c_sel.isdigit() and 1 <= int(c_sel) <= len(CHECKS):
                    failed_check = CHECKS[int(c_sel) - 1]
                elif c_sel.upper() in CHECKS:
                    failed_check = c_sel.upper()
                else:
                    failed_check = SubmissionCheckName.OTHER.value

                check_detail = input("Check Detail / Threshold (e.g. '0.74 vs max 0.70', or Enter): ").strip() or None

            notes = input("Notes / Recollection Context: ").strip() or None

            # Create attempt
            att = SubmissionAttempt(
                alpha_id=target_alpha.id,
                attempted_at=att_date,
                result=result_val,
                failed_check=failed_check,
                check_detail=check_detail,
                notes=notes,
                is_recalled=True,
            )
            db.add(att)
            db.flush()

            sync_alpha_platform_outcome(db, target_alpha.id)

            hist_note = f"recalled_attempt:{result_val}" + (f": {failed_check}" if failed_check else "") + (f": {notes}" if notes else "")
            db.add(
                AlphaStatusHistory(
                    alpha_id=target_alpha.id,
                    from_status=target_alpha.status,
                    to_status=target_alpha.status,
                    note=hist_note,
                )
            )
            db.flush()

            print(f"✓ Recorded {result_val} attempt for Alpha #{target_alpha.id} (is_recalled=TRUE).\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--alpha-id", type=int, help="Target alpha ID")
    ap.add_argument("--result", choices=["submitted", "rejected"], default="rejected", help="Attempt result")
    ap.add_argument("--check", help="Failed check name (e.g. SELF_CORRELATION, PROD_CORRELATION, LOW_SHARPE)")
    ap.add_argument("--detail", help="Check detail (value vs threshold)")
    ap.add_argument("--notes", help="Notes or recollection context")
    ap.add_argument("--date", help="Date in YYYY-MM-DD format")

    args = ap.parse_args()
    if args.alpha_id:
        with session_scope() as db:
            alpha = db.get(Alpha, args.alpha_id)
            if not alpha:
                print(f"Error: Alpha #{args.alpha_id} not found", file=sys.stderr)
                return 1
            att_date = datetime.fromisoformat(args.date) if args.date else datetime.utcnow()
            att = SubmissionAttempt(
                alpha_id=alpha.id,
                attempted_at=att_date,
                result=args.result,
                failed_check=args.check,
                check_detail=args.detail,
                notes=args.notes,
                is_recalled=True,
            )
            db.add(att)
            db.flush()
            sync_alpha_platform_outcome(db, alpha.id)
            print(f"Recorded recalled attempt for #{alpha.id}: {args.result} (check={args.check})")
        return 0

    interactive_prompt()
    return 0


if __name__ == "__main__":
    sys.exit(main())
