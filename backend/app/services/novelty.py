"""Subtree-frequency novelty prior service (C1).

Builds corpus-wide Inverse Document Frequency (IDF) tables over AST subtree structural hashes:
    IDF(s) = ln((N + 1) / (DF(s) + 1)) + 1.0

Scores candidate expressions by their subtree novelty to provide a pre-simulation ranking prior
(never a hard exclusion filter), prioritizing rare structural mechanisms over common clichés.
"""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.services.constructor import Candidate

log = structlog.get_logger("novelty")


@dataclass
class NoveltyScorer:
    """Corpus-level IDF scorer over structural subtree hashes."""

    total_alphas: int
    idf_table: dict[str, float]
    default_idf: float

    @classmethod
    def from_session(cls, db: Session) -> NoveltyScorer:
        """Construct IDF table from all alphas in database."""
        alphas = db.execute(select(Alpha.feature_json)).scalars().all()
        n_total = len(alphas)
        if n_total == 0:
            return cls(total_alphas=0, idf_table={}, default_idf=1.0)

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
        return cls(total_alphas=n_total, idf_table=idf, default_idf=default_idf)

    def score_subtree_hashes(self, subtree_hashes: Sequence[str]) -> float:
        """Compute the average IDF novelty score across a set of subtree hashes."""
        if not subtree_hashes:
            return self.default_idf
        scores = [self.idf_table.get(h, self.default_idf) for h in subtree_hashes]
        return float(sum(scores) / len(scores))

    def score_candidate(self, candidate: Candidate) -> float:
        """Compute novelty score for a candidate alpha."""
        hashes = (candidate.features or {}).get("subtree_hashes") or []
        return self.score_subtree_hashes(hashes)


def rank_candidates_by_novelty(
    candidates: list[Candidate],
    scorer: NoveltyScorer,
) -> list[Candidate]:
    """Sort candidate list descending by novelty score."""
    return sorted(candidates, key=lambda c: scorer.score_candidate(c), reverse=True)
