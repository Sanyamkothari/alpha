"""Out-of-sample decay report and filter calibration (F1).

Two questions, and the second is the one that matters:

1. How much did each live alpha decay out of sample?
2. **Does our filter predict that decay?** If DSR at promotion time does not track
   realised decay across our own alphas, the DSR threshold is decoration.

    python -m scripts.report_decay
    python -m scripts.report_decay --markdown > docs/decay.md
"""

from __future__ import annotations

import argparse

from app.db.session import session_scope
from app.services.feedback_loop import build_filter_calibration


def _fmt(value: float | None, spec: str = ".2f") -> str:
    return format(value, spec) if value is not None else "—"


def render(markdown: bool = False) -> str:
    lines: list[str] = []
    add = lines.append

    with session_scope() as db:
        cal = build_filter_calibration(db)

    add("# Out-of-sample decay & filter calibration")
    add("")

    if not cal.rows:
        add("No submitted alphas with production snapshots yet. This report becomes")
        add("informative once several alphas have been live for a quarter — which is the")
        add("reason to run the plumbing now rather than when the data would be useful.")
        return "\n".join(lines)

    add("| Alpha | IS Sharpe | OS Sharpe | Decay | DSR | Plateau | PBO | Neut. hold |")
    add("|---|---|---|---|---|---|---|---|")
    for r in sorted(cal.rows, key=lambda x: (x.decay_pct is None, -(x.decay_pct or 0))):
        add(
            f"| #{r.alpha_id} | {_fmt(r.backtest_sharpe)} | {_fmt(r.production_sharpe)} |"
            f" {_fmt(r.decay_pct, '.0%')} | {_fmt(r.dsr, '.3f')} | {_fmt(r.plateau_ratio)} |"
            f" {_fmt(r.family_pbo)} | {_fmt(r.neutralization_retention, '.0%')} |"
        )
    add("")

    add("## Does the filter predict decay?")
    add("")
    add(f"_{cal.note}_")
    add("")
    add("| Promotion-time statistic | Correlation with realised decay | Reads as |")
    add("|---|---|---|")
    for label, value in (
        ("DSR", cal.dsr_vs_decay_corr),
        ("Plateau ratio", cal.plateau_vs_decay_corr),
    ):
        if value is None:
            reading = "not enough paired observations"
        elif value <= -0.3:
            reading = "**working** — higher confidence, less decay"
        elif value >= 0.3:
            reading = "**inverted** — investigate before trusting this gate"
        else:
            reading = "no relationship detected"
        add(f"| {label} | {_fmt(value)} | {reading} |")
    add("")
    add("A working filter shows a *negative* correlation: statistics that were more")
    add("confident at promotion time should be followed by less realised decay. A value")
    add("near zero across enough alphas means the statistic is not earning its gate.")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--markdown", action="store_true", help="emit markdown (default)")
    ap.parse_args()
    print(render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
