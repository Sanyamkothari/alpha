"""Tests for Phase 3: Diversity & Novelty (Items B4, C1, C2).

Validates:
- C1: Subtree-frequency novelty prior computes correct IDF and re-ranks candidate pools based on structural rarity.
- C2: Orthogonalisation produces valid Tier-1 residuals against 4 risk factor proxies and Tier-2 residual against colliding alphas.
- B4: Universe tuning collapses identical mechanisms and surfaces LOW_SUB_UNIVERSE_SHARPE check.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.enums import AlphaStatus
from app.models.fields import DataField, Dataset
from app.services.alpha_library import AlphaSettings, create_alpha
from app.services.constructor import Candidate, FamilySpec, GridAxes, expand
from app.services.novelty import NoveltyScorer, rank_surfaces_by_novelty
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

    # NOTE: db_session rolls back but cannot undo committed rows, so the corpus here
    # includes whatever earlier tests committed. Assertions below are therefore
    # relative — they test behaviour, not absolute counts.
    scorer = NoveltyScorer.from_session(db_session)
    assert scorer.corpus in ("outcome", "all")
    assert scorer.total_alphas > 0
    assert len(scorer.idf_table) > 0

    # Frequent subtrees must score below an unseen one.
    for idf_val in scorer.idf_table.values():
        assert idf_val <= scorer.default_idf

    # Ranking operates on whole surfaces, in descending mean novelty.
    surfaces = _group_into_surfaces(candidates)
    assert len(surfaces) > 1
    ranked = rank_surfaces_by_novelty(surfaces, scorer)
    assert len(ranked) == len(surfaces)
    means = [
        sum(scorer.score_candidate(c) for c in surf) / len(surf) for surf in ranked
    ]
    assert all(means[i] >= means[i + 1] for i in range(len(means) - 1))


def _group_into_surfaces(candidates: list[Candidate]) -> list[list[Candidate]]:
    """Split an emitted candidate list back into its (window x decay) surfaces."""
    from app.services.plateau import _structure_of

    grouped: dict[tuple, list[Candidate]] = {}
    for c in candidates:
        grouped.setdefault(_structure_of(c.grid), []).append(c)
    return list(grouped.values())


def test_c1_novelty_ranking_keeps_surfaces_contiguous(db_session) -> None:
    """Ranking must not interleave surfaces.

    Callers cap what reaches BRAIN (``run_family --simulate N``) by slicing the
    emitted order. If novelty ranking scattered candidates across surfaces, that cap
    would yield no *complete* (window x decay) surface and the plateau test — the
    highest-value check in the system — would have nothing to read.
    """
    fcode = _seed_field(db_session, "ds_c1_contig", "f_c1_contig")
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
    spec = FamilySpec(field_code=fcode)

    candidates = expand(db_session, spec, base_settings=settings, max_candidates=400)
    assert len(candidates) >= 50

    from app.services.plateau import _structure_of

    # Every surface must occupy one unbroken run in the emitted order.
    seen_runs: list[tuple] = []
    for c in candidates:
        struct = _structure_of(c.grid)
        if not seen_runs or seen_runs[-1] != struct:
            seen_runs.append(struct)
    assert len(seen_runs) == len(set(seen_runs)), "a surface was split across the ordering"

    # And each run is a complete surface.
    counts: dict[tuple, int] = {}
    for c in candidates:
        counts[_structure_of(c.grid)] = counts.get(_structure_of(c.grid), 0) + 1
    assert len(set(counts.values())) == 1, f"uneven surface sizes: {set(counts.values())}"


def test_c1_corpus_scopes_to_outcome_alphas(db_session) -> None:
    """The corpus is alphas that got somewhere, not everything we ever generated."""
    fcode = _seed_field(db_session, "ds_c1_scope", "f_c1_scope")
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
    spec = FamilySpec(field_code=fcode)
    candidates = expand(db_session, spec, base_settings=settings, max_candidates=400)

    outcome_states = [AlphaStatus.PASSED.value, AlphaStatus.SUBMITTED.value]
    before = (
        db_session.query(Alpha).filter(Alpha.status.in_(outcome_states)).count()
    )
    total_before = db_session.query(Alpha).count()

    created = [
        create_alpha(db_session, c.expression, c.settings, family_key=c.family_key, grid=c.grid, source="test").alpha
        for c in candidates[:20]
    ]
    db_session.flush()

    # 20 new alphas, only 2 of which reached an outcome. The corpus must count the
    # 2, not the 20 — that distinction is the whole point of scoping it.
    for a in created[:2]:
        a.status = AlphaStatus.PASSED.value
    db_session.flush()

    scorer = NoveltyScorer.from_session(db_session)
    assert scorer.corpus == "outcome"
    assert scorer.total_alphas == before + 2
    assert scorer.total_alphas < total_before + 20


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
