"""Targeted regression and unit tests for the 10 findings from REVIEW.md."""

from __future__ import annotations

import math
import zlib
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.models import Base
from app.models.alphas import Alpha, SubmissionAttempt
from app.models.enums import AlphaStatus, ImportSource
from app.models.results import AlphaMetric, SimulationImport
from app.services.constructor import STANDARD_DECAYS, STANDARD_WINDOWS
from app.services.correlation import (
    check_portfolio_empirical_correlation,
)
from app.services.plateau import (
    FALLBACK_DECAY_LADDER,
    FALLBACK_WINDOW_LADDER,
    _select_representatives,
    check_portfolio_correlation,
    evaluate,
)
from app.services.pnl_storage import PnLStore
from app.services.report import build as build_report


def _seed_for(*parts: object) -> int:
    """Deterministic seed across processes (unlike hash() on tuples containing str)."""
    return zlib.crc32("|".join(str(p) for p in parts).encode()) % (2**31)


_STRUCTURE = {"ts": "ts_zscore", "cs": "rank", "group": None, "truncation": 0.08}


def _point_passed(
    db: Session,
    family: str,
    window: int,
    decay: int,
    sharpe: float,
    *,
    passes: bool = True,
    pnl_store: PnLStore | None = None,
    n_days: int = 1236,
    structural_hash: str | None = None,
    status: str = "passed",
    neut: str = "SUBINDUSTRY",
    cs: str = "rank",
) -> Alpha:
    """Insert one simulated grid point with realistic 'passed' status and reconciled PnL."""
    fcode = family.replace("/", "_").replace("@", "_").replace(":", "_")
    grid = dict(ts="ts_zscore", cs=cs, group=None, truncation=0.08, window=window, decay=decay, neutralization=neut, field=fcode)
    shash = structural_hash or f"shash-{fcode}-{window > 30}"
    alpha = Alpha(
        expression=f"rank(ts_zscore({fcode},{window}))",
        normalized_expression=f"rank(ts_zscore({fcode},{window}))",
        expression_hash=f"{family}-{window}-{decay}",
        family_key=family,
        status=status,
        source="constructor",
        region="USA",
        universe="TOP3000",
        delay=1,
        neutralization="SUBINDUSTRY",
        decay=decay,
        truncation=0.08,
        is_valid=True,
        generation=0,
        feature_json={"structural_hash": shash, "grid": grid},
    )
    db.add(alpha)
    db.flush()

    si = SimulationImport(alpha_id=alpha.id, source=ImportSource.BRAIN_API.value, raw_payload={})
    db.add(si)
    db.flush()

    db.add(
        AlphaMetric(
            simulation_import_id=si.id,
            alpha_id=alpha.id,
            sharpe=sharpe,
            fitness=1.5,
            turnover=0.2,
            returns=0.15,
            passed_all_checks=passes,
        )
    )
    db.flush()

    if pnl_store is not None:
        dates = [f"d_{i:04d}" for i in range(n_days)]
        daily_sharpe = sharpe / math.sqrt(252)
        daily_vol = 0.01
        rng = np.random.default_rng(_seed_for(family, window, decay, alpha.id))
        pnl = rng.normal(daily_sharpe * daily_vol, daily_vol, n_days)
        std = float(np.std(pnl, ddof=1))
        if std > 0:
            pnl = pnl - np.mean(pnl) + daily_sharpe * std
        pnl_store.save_pnl(alpha.id, dates, pnl)

    return alpha


def test_measured_correlation_beats_structural_proxy(db_session: Session, tmp_path: Path) -> None:
    """Test 1: Candidate and portfolio alpha share structural_hash, but independent PnL clears empirical gate."""
    store = PnLStore(tmp_path / "pnl")
    fam = "close_test1/cap@USA/TOP3000/d1"

    port_alpha = _point_passed(
        db_session,
        fam,
        20,
        4,
        2.0,
        pnl_store=store,
        structural_hash="shared_hash_1",
        status=AlphaStatus.SUBMITTED.value,
    )
    db_session.add(SubmissionAttempt(alpha_id=port_alpha.id, result="submitted"))
    db_session.flush()

    cand_alpha = _point_passed(
        db_session,
        "close_test1_alt/cap@USA/TOP3000/d1",
        40,
        6,
        2.1,
        pnl_store=store,
        structural_hash="shared_hash_1",
        status=AlphaStatus.TESTING.value,
    )

    is_corr, reason, max_corr = check_portfolio_empirical_correlation(
        db_session, cand_alpha.id, pnl_store=store, portfolio=[port_alpha]
    )
    assert not is_corr, f"Expected measured independent PnL to clear gate, got {reason}"
    assert reason is None


def test_structural_proxy_still_applies_to_unmeasured_alphas(
    db_session: Session, tmp_path: Path
) -> None:
    """Test 2: Same setup, but portfolio alpha has NO stored PnL -> fallback proxy catches collision."""
    store = PnLStore(tmp_path / "pnl")
    fam = "close_test2/cap@USA/TOP3000/d1"

    # port_alpha has NO PnL in store
    port_alpha = _point_passed(
        db_session,
        fam,
        20,
        4,
        2.0,
        pnl_store=None,
        structural_hash="shared_hash_2",
        status=AlphaStatus.SUBMITTED.value,
    )
    db_session.add(SubmissionAttempt(alpha_id=port_alpha.id, result="submitted"))
    db_session.flush()

    cand_alpha = _point_passed(
        db_session,
        fam,
        40,
        6,
        2.1,
        pnl_store=store,
        structural_hash="shared_hash_2",
        status=AlphaStatus.TESTING.value,
    )

    is_corr, reason, _ = check_portfolio_empirical_correlation(
        db_session, cand_alpha.id, pnl_store=store, portfolio=[port_alpha]
    )
    assert is_corr is True, "Expected unmeasured portfolio alpha to trigger structural proxy collision"
    assert "structural correlation collision" in (reason or "")


def test_proxy_runs_only_on_the_unmeasured_subset(db_session: Session, tmp_path: Path) -> None:
    """Test 3: Two portfolio alphas, one measured-and-clean, one unmeasured-and-colliding -> names the unmeasured one."""
    store = PnLStore(tmp_path / "pnl")
    fam = "close_test3/cap@USA/TOP3000/d1"

    # Clean portfolio alpha with PnL
    clean_alpha = _point_passed(
        db_session,
        "clean_fam/cap@USA/TOP3000/d1",
        20,
        4,
        1.8,
        pnl_store=store,
        structural_hash="other_hash",
        status=AlphaStatus.SUBMITTED.value,
    )
    db_session.add(SubmissionAttempt(alpha_id=clean_alpha.id, result="submitted"))

    # Colliding unmeasured alpha without PnL
    colliding_alpha = _point_passed(
        db_session,
        fam,
        20,
        4,
        2.0,
        pnl_store=None,
        structural_hash="shared_hash_3",
        status=AlphaStatus.SUBMITTED.value,
    )
    db_session.add(SubmissionAttempt(alpha_id=colliding_alpha.id, result="submitted"))
    db_session.flush()

    cand_alpha = _point_passed(
        db_session,
        fam,
        40,
        6,
        2.1,
        pnl_store=store,
        structural_hash="shared_hash_3",
        status=AlphaStatus.TESTING.value,
    )

    is_corr, reason, _ = check_portfolio_empirical_correlation(
        db_session, cand_alpha.id, pnl_store=store, portfolio=[clean_alpha, colliding_alpha]
    )
    assert is_corr is True
    assert f"#{colliding_alpha.id}" in (reason or "")


def test_passed_siblings_do_not_block_each_other(db_session: Session) -> None:
    """Test 4: 49-point family with status='passed' and shared hash -> check_portfolio_correlation returns False for all."""
    fam = "close_siblings/cap@USA/TOP3000/d1"
    alphas = []
    for w in STANDARD_WINDOWS:
        for d in STANDARD_DECAYS:
            a = _point_passed(
                db_session,
                fam,
                w,
                d,
                2.0,
                structural_hash="family_hash_common",
                status=AlphaStatus.PASSED.value,
            )
            alphas.append(a)
    db_session.flush()

    for a in alphas:
        is_blocked, reason = check_portfolio_correlation(db_session, a.id, portfolio=alphas)
        assert not is_blocked, f"Alpha #{a.id} was blocked by passed sibling: {reason}"


def test_submitted_sibling_still_blocks(db_session: Session) -> None:
    """Test 5: One sibling flipped to 'submitted' -> other siblings are blocked."""
    fam = "close_sub_sib/cap@USA/TOP3000/d1"
    alphas = []
    for w in [5, 10]:
        for d in [0, 1]:
            a = _point_passed(
                db_session,
                fam,
                w,
                d,
                2.0,
                structural_hash="family_hash_sub",
                status=AlphaStatus.PASSED.value,
            )
            alphas.append(a)
    db_session.flush()

    # Mark the first one submitted
    alphas[0].status = AlphaStatus.SUBMITTED.value
    db_session.add(SubmissionAttempt(alpha_id=alphas[0].id, result="submitted"))
    db_session.flush()

    for a in alphas[1:]:
        is_blocked, reason = check_portfolio_correlation(db_session, a.id, portfolio=alphas)
        assert is_blocked is True, f"Alpha #{a.id} should be blocked by submitted sibling #{alphas[0].id}"
        assert f"submitted alpha #{alphas[0].id}" in (reason or "")


def test_collision_message_names_portfolio_not_submitted(db_session: Session) -> None:
    """Test 6: Colliding alpha is 'passed' (not submitted) -> message says 'portfolio alpha', not 'submitted alpha'."""
    fam1 = "fieldA_base/cap:ts_zscore@USA/TOP3000/d1"
    fam2 = "fieldA_base/cap:ts_rank@USA/TOP3000/d1"

    port_alpha = _point_passed(
        db_session,
        fam2,
        20,
        4,
        2.0,
        structural_hash="hash_six",
        status=AlphaStatus.PASSED.value,
    )
    cand_alpha = _point_passed(
        db_session,
        fam1,
        40,
        6,
        2.1,
        structural_hash="hash_six",
        status=AlphaStatus.TESTING.value,
    )

    is_blocked, reason = check_portfolio_correlation(db_session, cand_alpha.id, portfolio=[port_alpha])
    assert is_blocked is True
    assert "portfolio alpha #" in (reason or "")
    assert "submitted alpha #" not in (reason or "")


def test_family_promotes_one_representative_per_skeleton(db_session: Session, tmp_path: Path) -> None:
    """Test 7: Full family with PnL -> promoted count equals distinct structural hashes, demoted twins carry redundant_with."""
    store = PnLStore(tmp_path / "pnl")
    fam = "close_dedup/cap@USA/TOP3000/d1"

    # Create 49 points with 4 distinct structural hashes (fast vs slow, low vs high decay)
    dates = [f"d_{i:04d}" for i in range(1236)]
    daily_vol = 0.01
    for w in STANDARD_WINDOWS:
        for d in STANDARD_DECAYS:
            shash = f"hash_w_{w > 30}_d_{d > 4}"
            neut = "SUBINDUSTRY" if w > 30 else "INDUSTRY"
            cs = "rank" if d > 4 else "zscore"
            # Give points high Sharpe clearing EVT haircut bar for 49 trials (2.19)
            sharpe = 2.50 + (0.1 if w == 40 and d == 4 else 0.0)
            a = _point_passed(db_session, fam, w, d, sharpe, pnl_store=None, structural_hash=shash, neut=neut, cs=cs)
            # Create perfectly stationary daily PnL with exact matching Sharpe so all 49 clear reconciliation and stability
            daily_sharpe = sharpe / math.sqrt(252)
            rng = np.random.default_rng(_seed_for(w, d, a.id))
            half = 1236 // 2
            n1 = rng.normal(0, daily_vol, half)
            n1 = (n1 - np.mean(n1)) / float(np.std(n1, ddof=1)) * daily_vol
            n2 = rng.normal(0, daily_vol, 1236 - half)
            n2 = (n2 - np.mean(n2)) / float(np.std(n2, ddof=1)) * daily_vol
            noise = np.concatenate([n1, n2])
            pnl = (daily_sharpe * daily_vol) + noise
            store.save_pnl(a.id, dates, pnl)
    db_session.flush()

    verdicts = evaluate(db_session, fam, pnl_store=store)
    promoted = [v for v in verdicts if v.promoted]
    # Distinct structural hashes = 4 (True/False for w>30 x True/False for d>4)
    assert len(promoted) == 4, f"Expected 4 promoted representatives, got {len(promoted)}"

    demoted = [v for v in verdicts if not v.promoted and v.clears_bar]
    assert len(demoted) == 49 - 4
    for d in demoted:
        assert d.redundant_with is not None
        assert any(f"redundant with #{d.redundant_with}" in r for r in d.reasons)


def test_representative_selection_is_deterministic(db_session: Session) -> None:
    """Test 8: Two members with identical Sharpe and ratio -> lower alpha_id wins deterministically."""
    from app.services.plateau import SurfacePoint, Verdict

    p1 = SurfacePoint(10, "expr1", 20, 4, 2.0, 1.5, 0.2, True, ("ts", "cs", None, "NEUT", 0.08))
    p2 = SurfacePoint(20, "expr2", 40, 4, 2.0, 1.5, 0.2, True, ("ts", "cs", None, "NEUT", 0.08))

    v1 = Verdict(alpha_id=10, expression="expr1", sharpe=2.0, fitness=1.5, neighbour_median_sharpe=2.0,
                 ridge_score=2.0, plateau_ratio=0.8, is_plateau=True, neighbours_simulated=4, neighbours_possible=4,
                 clears_bar=True, haircut_bar=1.2, is_correlated=False, correlation_collision=None, promoted=True, family_size=49)
    v2 = Verdict(alpha_id=20, expression="expr2", sharpe=2.0, fitness=1.5, neighbour_median_sharpe=2.0,
                 ridge_score=2.0, plateau_ratio=0.8, is_plateau=True, neighbours_simulated=4, neighbours_possible=4,
                 clears_bar=True, haircut_bar=1.2, is_correlated=False, correlation_collision=None, promoted=True, family_size=49)

    verdicts = [v2, v1]  # pass in reverse order
    _select_representatives(db_session, verdicts, [p1, p2])

    assert v1.promoted is True, "Alpha 10 should be the keeper (lower alpha_id)"
    assert v2.promoted is False, "Alpha 20 should be demoted"
    assert v2.redundant_with == 10


def test_surfaces_axes_cover_every_emitted_cell(client: TestClient, db_session: Session) -> None:
    """Test 9: 49-cell family -> GET /api/ui/surfaces returns 7x7 axes covering 49/49 cells."""
    fam = "axes_test_fam/cap@USA/TOP3000/d1"
    for w in STANDARD_WINDOWS:
        for d in STANDARD_DECAYS:
            _point_passed(db_session, fam, w, d, 1.8)
    db_session.commit()

    resp = client.get("/api/ui/surfaces", params={"family": fam})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["windows"]) == 7
    assert len(data["decays"]) == 7
    assert data["windows"] == list(STANDARD_WINDOWS)
    assert data["decays"] == list(STANDARD_DECAYS)

    cells = data["surfaces"][0]["cells"]
    assert len(cells) == 49
    for w in data["windows"]:
        for d in data["decays"]:
            assert f"{w}:{d}" in cells


def test_surfaces_axes_fall_back_when_surface_empty(client: TestClient) -> None:
    """Test 10: Unknown family -> axes equal fallback ladders, surfaces == []."""
    resp = client.get("/api/ui/surfaces", params={"family": "nonexistent_family/cap@USA/TOP3000/d1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["windows"] == list(FALLBACK_WINDOW_LADDER)
    assert data["decays"] == list(FALLBACK_DECAY_LADDER)
    assert data["surfaces"] == []


def test_summary_counts_distinct_alphas(client: TestClient, db_session: Session) -> None:
    """Test 11: Importing two metric results for one alpha -> counts.simulated matches distinct alphas."""
    before_resp = client.get("/api/ui/summary")
    before_sim = before_resp.json()["counts"]["simulated"]

    fam = "distinct_count_fam/cap@USA/TOP3000/d1"
    alpha = _point_passed(db_session, fam, 20, 4, 1.9)

    # Add second import for the same alpha
    si2 = SimulationImport(alpha_id=alpha.id, source=ImportSource.PASTE.value, raw_payload={})
    db_session.add(si2)
    db_session.flush()
    db_session.add(
        AlphaMetric(
            simulation_import_id=si2.id,
            alpha_id=alpha.id,
            sharpe=2.0,
            fitness=1.6,
            turnover=0.18,
            passed_all_checks=True,
        )
    )
    db_session.commit()

    resp = client.get("/api/ui/summary")
    assert resp.status_code == 200
    counts = resp.json()["counts"]
    # Simulated count must advance by 1 distinct Alpha.id despite 2 AlphaMetric rows added
    metric_rows = db_session.query(AlphaMetric).filter(AlphaMetric.alpha_id == alpha.id).count()
    assert metric_rows == 2
    assert counts["simulated"] == before_sim + 1


def test_summary_reports_plateau_count(client: TestClient, db_session: Session) -> None:
    """Test 12: Family with a known ridge -> counts.plateau reflects the number of is_plateau verdicts."""
    before_resp = client.get("/api/ui/summary")
    before_plateau = before_resp.json()["counts"]["plateau"]

    fam = "plateau_count_fam/cap@USA/TOP3000/d1"
    for w in STANDARD_WINDOWS:
        for d in STANDARD_DECAYS:
            _point_passed(db_session, fam, w, d, 2.2)
    db_session.commit()

    resp = client.get("/api/ui/summary")
    assert resp.status_code == 200
    counts = resp.json()["counts"]
    assert counts["plateau"] == before_plateau + 49


def test_report_empty_catalog_advises_seeding(tmp_path: Path) -> None:
    """Test 13: report.build() with no DataField rows -> output advises seeding instead of 'Everything has been tried'."""
    eng = create_engine(f"sqlite:///{tmp_path / 'empty_catalog.db'}", future=True)
    Base.metadata.create_all(eng)
    with Session(eng) as s:
        report_text = build_report(s)
        assert "No field catalog loaded" in report_text
        assert "python -m app.seeds.seed_all" in report_text
        assert "Everything has been tried" not in report_text


def test_favicon_returns_no_content(client: TestClient) -> None:
    """Test 14: GET /favicon.ico -> 204 No Content."""
    resp = client.get("/favicon.ico")
    assert resp.status_code == 204


# --------------------------------------------------------------------------------------
# Correlation gate must act on magnitude, not sign (CODE_REVIEW.md H1)
# --------------------------------------------------------------------------------------


def _mirror_pair(
    db_session: Session, tmp_path: Path, *, rho_sign: float = -1.0
) -> tuple[int, int, PnLStore]:
    """A submitted alpha plus a candidate whose PnL is (rho_sign x) the same series.

    Same length, same dates, so the pair is fully *measured* — the structural proxy
    never runs and the verdict is the empirical gate's alone. Different families and
    hashes so no sibling or skeleton rule can mask the result.
    """
    store = PnLStore(tmp_path / "pnl_mirror")
    submitted = _point_passed(
        db_session, "sub_m/cap@USA/TOP3000/d1", 22, 4, 2.0,
        structural_hash="hash-submitted", status=AlphaStatus.SUBMITTED.value,
    )
    db_session.add(SubmissionAttempt(alpha_id=submitted.id, result="submitted"))
    candidate = _point_passed(
        db_session, "cand_m/cap@USA/TOP3000/d1", 22, 4, 2.0,
        structural_hash="hash-candidate", status=AlphaStatus.PASSED.value,
    )
    db_session.flush()

    dates = [f"d_{i:04d}" for i in range(1236)]
    rng = np.random.default_rng(20260819)
    series = rng.normal(0.0002, 0.01, len(dates))
    store.save_pnl(submitted.id, dates, series)
    store.save_pnl(candidate.id, dates, rho_sign * series)
    return candidate.id, submitted.id, store


def test_exact_negation_of_submitted_alpha_is_blocked(
    db_session: Session, tmp_path: Path
) -> None:
    """An inverted copy of a submitted alpha is a duplicate to BRAIN, not a hedge.

    Gating on signed rho let this through as the most diversified candidate on the
    board — the worst possible false negative for the gate whose whole job is
    catching duplicates.
    """
    cand_id, sub_id, store = _mirror_pair(db_session, tmp_path, rho_sign=-1.0)

    is_corr, reason, max_corr = check_portfolio_empirical_correlation(
        db_session, cand_id, pnl_store=store
    )

    assert is_corr, "an exact negation of a submitted alpha must be blocked"
    assert reason is not None and f"#{sub_id}" in reason
    assert max_corr == pytest.approx(-1.0, abs=1e-6), (
        "the reported correlation must keep its sign so the operator can tell an "
        "inverted collision from a co-moving one"
    )


def test_negative_correlation_is_reported_signed_to_the_operator(
    db_session: Session, tmp_path: Path
) -> None:
    """The number shown in the console must agree with the number the gate acted on."""
    from app.services.correlation import compute_max_self_correlation_with_submitted

    cand_id, sub_id, store = _mirror_pair(db_session, tmp_path, rho_sign=-1.0)

    self_corr, target_id, method = compute_max_self_correlation_with_submitted(
        db_session, cand_id, pnl_store=store
    )

    assert method == "empirical"
    assert target_id == sub_id, "the colliding alpha must be identified, not dropped"
    assert self_corr == pytest.approx(-1.0, abs=1e-6), (
        "reporting 0.00 for a perfect mirror tells the operator the opposite of the truth"
    )


def test_positive_correlation_still_blocks_and_reports_positive(
    db_session: Session, tmp_path: Path
) -> None:
    """The magnitude rule must not have broken the ordinary co-moving case."""
    cand_id, sub_id, store = _mirror_pair(db_session, tmp_path, rho_sign=1.0)

    is_corr, reason, max_corr = check_portfolio_empirical_correlation(
        db_session, cand_id, pnl_store=store
    )

    assert is_corr
    assert reason is not None and f"#{sub_id}" in reason
    assert max_corr == pytest.approx(1.0, abs=1e-6)


def test_uncorrelated_candidate_still_passes(db_session: Session, tmp_path: Path) -> None:
    """Gating on |rho| must not turn independent series into collisions."""
    store = PnLStore(tmp_path / "pnl_indep")
    submitted = _point_passed(
        db_session, "sub_i/cap@USA/TOP3000/d1", 22, 4, 2.0,
        structural_hash="hash-sub-i", status=AlphaStatus.SUBMITTED.value,
    )
    db_session.add(SubmissionAttempt(alpha_id=submitted.id, result="submitted"))
    candidate = _point_passed(
        db_session, "cand_i/cap@USA/TOP3000/d1", 22, 4, 2.0,
        structural_hash="hash-cand-i", status=AlphaStatus.PASSED.value,
    )
    db_session.flush()

    dates = [f"d_{i:04d}" for i in range(1236)]
    store.save_pnl(submitted.id, dates, np.random.default_rng(1).normal(0.0002, 0.01, len(dates)))
    store.save_pnl(candidate.id, dates, np.random.default_rng(2).normal(0.0002, 0.01, len(dates)))

    is_corr, reason, max_corr = check_portfolio_empirical_correlation(
        db_session, candidate.id, pnl_store=store
    )

    assert not is_corr, f"independent series must pass, got: {reason}"
    assert abs(max_corr) < 0.55
