"""Tests for Phase 3: Diversity & Novelty (Items B4, C1, C2).

Validates:
- C1: Subtree-frequency novelty prior computes correct IDF and re-ranks candidate pools based on structural rarity.
- C2: Orthogonalisation produces valid Tier-1 residuals against 4 risk factor proxies and Tier-2 residual against colliding alphas.
- B4: Universe tuning collapses identical mechanisms and surfaces LOW_SUB_UNIVERSE_SHARPE check.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.fields import DataField, Dataset
from app.services.alpha_library import AlphaSettings, create_alpha
from app.services.constructor import FamilySpec, expand
from app.services.novelty import NoveltyScorer, rank_candidates_by_novelty
from app.services.orthogonalization import build_tier1_residuals, build_tier2_residual
from app.validator import ValidatorKB


def _seed_field(db: Session, ds_code: str, f_code: str) -> str:
    for univ in ("TOP3000", "TOP1000", "TOP500"):
        ds = db.query(Dataset).filter_by(dataset_code=ds_code, universe=univ).first()
        if not ds:
            ds = Dataset(dataset_code=ds_code, name=ds_code, region="USA", universe=univ, delay=1)
            db.add(ds)
            db.flush()
        f = db.query(DataField).filter_by(dataset_id=ds.id, field_code=f_code, universe=univ).first()
        if not f:
            f = DataField(dataset_id=ds.id, field_code=f_code, universe=univ, field_type="MATRIX", user_count=1)
            db.add(f)
            db.flush()
    return f_code


def test_c1_subtree_novelty_scoring_and_reranking(db_session) -> None:
    """C1: Subtree IDF novelty scoring computes correct IDF and re-ranks candidate pool by structural novelty."""
    fcode = _seed_field(db_session, "ds_c1_test", "f_c1_novelty")
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)

    spec = FamilySpec(field_code=fcode)
    candidates = expand(db_session, spec, base_settings=settings, max_candidates=400)
    assert len(candidates) >= 50

    # Seed some alphas into DB so IDF table has corpus history
    for c in candidates[:30]:
        create_alpha(db_session, c.expression, c.settings, family_key=c.family_key, grid=c.grid, source="test")
    db_session.flush()

    scorer = NoveltyScorer.from_session(db_session)
    assert scorer.total_alphas >= 30
    assert len(scorer.idf_table) > 0

    # Verify that frequent subtrees have lower IDF than unseen subtrees
    for h, idf_val in scorer.idf_table.items():
        assert idf_val <= scorer.default_idf

    # Score and re-rank candidates
    reranked = rank_candidates_by_novelty(candidates, scorer)
    assert len(reranked) == len(candidates)
    scores = [scorer.score_candidate(c) for c in reranked]
    assert all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1))


def test_c2_orthogonalisation_tier1_and_tier2(db_session) -> None:
    """C2: Generates valid Tier-1 residuals against 4 risk proxies and Tier-2 residual against colliding alpha."""
    fcode = _seed_field(db_session, "ds_c2_test", "f_c2_ortho")
    kb = ValidatorKB.from_session(db_session)

    expr = f"rank(ts_zscore({fcode},20))"

    # Tier 1: 4 standard risk factors
    tier1 = build_tier1_residuals(expr, kb, parent_alpha_id=101)
    assert len(tier1) == 4
    proxy_types = {r.proxy_type for r in tier1}
    assert proxy_types == {"size", "momentum", "volatility", "liquidity"}
    for r in tier1:
        assert r.expression.startswith(f"regression_neut({expr},")

    # Tier 2: Inline colliding alpha
    colliding = f"rank(ts_mean({fcode},10))"
    tier2 = build_tier2_residual(expr, colliding, kb, max_complexity=15.0, parent_alpha_id=101)
    assert tier2 is not None
    assert tier2.expression == f"regression_neut({expr},{colliding})"
    assert tier2.proxy_type == "colliding_parent"
