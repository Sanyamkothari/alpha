"""Subtree-frequency novelty prior service (C1).

Builds corpus-wide Inverse Document Frequency (IDF) tables over AST subtree structural hashes:
    IDF(s) = ln((N + 1) / (DF(s) + 1)) + 1.0

Scores candidate expressions by their subtree novelty to provide a pre-simulation ranking prior
(never a hard exclusion filter), prioritizing rare structural mechanisms over common clichés.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus

if TYPE_CHECKING:  # pragma: no cover - import cycle guard
    from app.services.constructor import Candidate

log = structlog.get_logger("novelty")

# Alphas that reached an outcome. These are the ones whose structure carries
# information about what the platform accepts.
OUTCOME_STATUSES: tuple[str, ...] = (
    AlphaStatus.PASSED.value,
    AlphaStatus.SUBMITTED.value,
)


@dataclass
class NoveltyScorer:
    """Corpus-level IDF scorer over structural subtree hashes."""

    total_alphas: int
    idf_table: dict[str, float]
    default_idf: float
    corpus: str = "outcome"  # "outcome" | "all" | "empty"

    @classmethod
    def from_session(
        cls,
        db: Session,
        *,
        statuses: Sequence[str] = OUTCOME_STATUSES,
    ) -> NoveltyScorer:
        """Build the IDF table from alphas that *got somewhere*.

        Scoping matters more than it looks. Over every row, the corpus is dominated
        by generated-and-never-simulated grid members, so IDF measures what our own
        constructor emits rather than what has ever worked — and it treats "already
        tried and failed" and "already succeeded" as the same evidence, when they
        point in opposite directions.

        Cold start falls back to the full corpus rather than scoring everything
        identically, and records which corpus was used so callers can say so.
        """
        rows = (
            db.execute(select(Alpha.feature_json).where(Alpha.status.in_(list(statuses))))
            .scalars()
            .all()
        )
        corpus = "outcome"
        if not rows:
            rows = db.execute(select(Alpha.feature_json)).scalars().all()
            corpus = "all" if rows else "empty"

        alphas = rows
        n_total = len(alphas)
        if n_total == 0:
            return cls(total_alphas=0, idf_table={}, default_idf=1.0, corpus="empty")

        df_counts: Counter[str] = Counter()
        for feat in alphas:
            if not feat or not isinstance(feat, dict):
                continue
            hashes = set(feat.get("subtree_hashes") or [])
            for h in hashes:
                df_counts[h] += 1

        idf: dict[str, float] = {}
        for h, count in df_counts.items():
            idf[h] = math.log((n_total + 1.0) / (count + 1.0)) + 1.0

        # Unseen subtree gets maximum IDF
        default_idf = math.log((n_total + 1.0) / 1.0) + 1.0
        return cls(total_alphas=n_total, idf_table=idf, default_idf=default_idf, corpus=corpus)

    def score_subtree_hashes(self, subtree_hashes: Sequence[str]) -> float:
        """Compute the average IDF novelty score across a set of subtree hashes."""
        if not subtree_hashes:
            return self.default_idf
        scores = [self.idf_table.get(h, self.default_idf) for h in subtree_hashes]
        return float(sum(scores) / len(scores))

    def score_candidate(self, candidate: "Candidate") -> float:
        """Compute novelty score for a candidate alpha."""
        hashes = (candidate.features or {}).get("subtree_hashes") or []
        return self.score_subtree_hashes(hashes)


def rank_surfaces_by_novelty(
    surfaces: list[list["Candidate"]],
    scorer: NoveltyScorer,
) -> list[list["Candidate"]]:
    """Order whole surfaces by mean novelty, keeping each surface contiguous.

    Ranking individual candidates would be wrong here, and quietly so. Callers cap
    what actually reaches BRAIN (``run_family --simulate N``), and that cap slices
    the emitted order. A novelty-sorted candidate list would hand them N points
    scattered across many surfaces — no *complete* (window x decay) surface, and
    therefore no plateau analysis, which is the highest-value test in the system.
    Surfaces are the unit that must survive the cap intact.
    """
    scored = [
        (
            sum(scorer.score_candidate(c) for c in surf) / len(surf) if surf else 0.0,
            idx,
            surf,
        )
        for idx, surf in enumerate(surfaces)
    ]
    # idx breaks ties so the ordering is deterministic.
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [surf for _, _, surf in scored]
