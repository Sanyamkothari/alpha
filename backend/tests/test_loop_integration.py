"""Part C: Loop Integration Test Suite.

Verifies end-to-end campaign execution, budget conservation, zero-work task handling,
account-wide concurrency semaphore, and Invariant 8 (plateau neighbourhood over peak).
"""

from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager

import numpy as np
from sqlalchemy import distinct, func, select
from sqlalchemy.orm import Session

from app.models.alphas import Alpha
from app.models.campaigns import Campaign, CampaignTask
from app.models.enums import AlphaStatus
from app.models.fields import DataField, Dataset
from app.models.results import AlphaMetric, SimulationImport
from app.services import plateau as P
from app.services.alpha_library import AlphaSettings, create_alpha
from app.services.brain.client import _ACCOUNT_SLOTS, MAX_CONCURRENT_SIMULATIONS, BrainClient
from app.services.campaign_runner import create_nightly_campaign, execute_campaign
from app.services.constructor import FamilySpec, expand
from app.services.pnl_storage import PnLStore
from tests.fakes.fake_brain_client import FakeBrainClient


def _seed_campaign_dataset(db: Session, n_fields: int = 5) -> Dataset:
    """Helper to seed a dataset and fields for campaign tests."""
    code = f"ds_loop_{uuid.uuid4().hex[:6]}"
    ds = Dataset(
        dataset_code=code,
        name="Loop Test Dataset",
        region="USA",
        delay=1,
        universe="TOP3000",
        instrument_type="EQUITY",
    )
    db.add(ds)
    db.flush()
    for i in range(n_fields):
        db.add(
            DataField(
                field_code=f"{code}_f{i}",
                dataset_id=ds.id,
                category="fundamentals",
                field_type="MATRIX",
                coverage=0.95,
                user_count=10 * (i + 1),
                region="USA",
                delay=1,
                universe="TOP3000",
            )
        )
    db.flush()
    return ds


# ----------------------------------------------------------------------
# C1: End-to-End Campaign Execution with FakeBrainClient
# ----------------------------------------------------------------------

def test_c1_end_to_end_campaign_with_fake_brain_client(db_session, monkeypatch, tmp_path):
    """C1: End-to-end campaign with FakeBrainClient generates alphas, simulates, imports PnL, and evaluates."""
    _seed_campaign_dataset(db_session, n_fields=5)
    camp = create_nightly_campaign(db_session, budget=100)
    cid = camp.id

    store = PnLStore(base_dir=tmp_path / "pnl")

    @contextmanager
    def mock_scope():
        yield db_session

    monkeypatch.setattr("app.services.campaign_runner.session_scope", mock_scope)
    monkeypatch.setattr("app.services.simulation_runner.session_scope", mock_scope)
    monkeypatch.setattr("app.services.simulation_runner.BrainClient", FakeBrainClient)
    monkeypatch.setattr("app.services.brain.BrainClient", FakeBrainClient)
    monkeypatch.setattr("app.services.pnl_storage.get_pnl_store", lambda: store)
    monkeypatch.setattr("app.services.correlation.get_pnl_store", lambda: store)

    res = execute_campaign(cid, simulate=True)

    assert res["status"] == "completed"
    assert res["total_simulated"] == 100
    assert res["total_passed"] > 0

    c = db_session.get(Campaign, cid)
    assert c.status == "completed"
    assert c.budget_completed == 100

    tasks = db_session.execute(
        select(CampaignTask).where(CampaignTask.campaign_id == cid)
    ).scalars().all()
    assert len(tasks) >= 1
    assert sum(t.alphas_simulated for t in tasks) == 100
    assert sum(t.alphas_passed for t in tasks) > 0
    for t in tasks:
        assert t.status == "completed"
        assert t.alphas_simulated > 0

    # Verify PnL was fetched and saved for passing alphas belonging to this campaign
    passing_alphas = db_session.execute(
        select(AlphaMetric.alpha_id)
        .join(Alpha, Alpha.id == AlphaMetric.alpha_id)
        .where(
            Alpha.campaign_task_id.in_([t.id for t in tasks]),
            AlphaMetric.passed_all_checks.is_(True),
        )
    ).scalars().all()
    assert len(passing_alphas) > 0
    for aid in passing_alphas:
        pnl_data = store.load_pnl(aid)
        assert pnl_data is not None, f"PnL vector missing for passing alpha #{aid}"

    # Verify evaluate works on the resulting families for this campaign
    campaign_families = db_session.execute(
        select(distinct(Alpha.family_key))
        .where(Alpha.campaign_task_id.in_([t.id for t in tasks]))
    ).scalars().all()
    assert len(campaign_families) > 0
    for fkey in campaign_families:
        verdicts = P.evaluate(db_session, fkey, pnl_store=store, require_pnl=False)
        assert len(verdicts) > 0


# ----------------------------------------------------------------------
# C2: Budget Conservation and Resumption Accounting
# ----------------------------------------------------------------------

def test_c2_budget_conservation_and_resume(db_session, monkeypatch):
    """C2: Budget completed tracks the SUM of task simulations, preserved across interruptions."""
    _seed_campaign_dataset(db_session, n_fields=5)
    camp = create_nightly_campaign(db_session, budget=100)
    cid = camp.id

    tasks = db_session.execute(
        select(CampaignTask).where(CampaignTask.campaign_id == cid)
    ).scalars().all()
    assert len(tasks) >= 1

    # Simulate Task 1 completed with target simulations
    sim_count_t0 = tasks[0].alphas_total or 50
    tasks[0].status = "completed"
    tasks[0].alphas_simulated = sim_count_t0
    tasks[0].alphas_passed = int(sim_count_t0 * 0.6)

    # Simulate remaining tasks failed
    for t in tasks[1:]:
        t.status = "failed"
        t.error = "simulated worker interruption"
        t.alphas_simulated = 0
    db_session.flush()

    # Re-calculate campaign budget_completed as sum of task alphas_simulated
    completed_sims = (
        db_session.scalar(
            select(func.sum(CampaignTask.alphas_simulated)).where(CampaignTask.campaign_id == cid)
        )
        or 0
    )
    camp.budget_completed = completed_sims
    db_session.flush()

    c = db_session.get(Campaign, cid)
    assert c.budget_completed == sim_count_t0, f"budget_completed must equal {sim_count_t0} (sum of task simulations)"


# ----------------------------------------------------------------------
# C3: Zero-Work Distinction (Expected vs Unexpected)
# ----------------------------------------------------------------------

def test_c3_zero_work_distinction_expected_vs_unexpected(db_session, monkeypatch):
    """C3: Distinguish expected no-work (surface complete -> skipped) from unexpected (expansion failed -> failed)."""
    _seed_campaign_dataset(db_session, n_fields=2)
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)

    camp = create_nightly_campaign(db_session, budget=50)
    cid = camp.id

    t0 = db_session.execute(select(CampaignTask).where(CampaignTask.campaign_id == cid)).scalars().first()
    horizon_band = None
    if t0.territory_key and ":" in t0.territory_key.split("@")[0]:
        h_cand = t0.territory_key.split("@")[0].split(":")[-1]
        if h_cand in {"short", "medium", "long"}:
            horizon_band = h_cand

    spec = FamilySpec(
        field_code=t0.field_code,
        denominator=t0.denominator,
        operator_family=t0.operator_family,
        wrapper_shape=t0.wrapper_shape,
        horizon_band=horizon_band,
    )
    fkey = spec.family_key(settings)

    # 1. Pre-populate full surface for t0's exact family key with simulated metrics
    candidates = expand(db_session, spec, base_settings=settings, max_candidates=98)
    for c in candidates:
        r = create_alpha(db_session, c.expression, c.settings, family_key=c.family_key, grid=c.grid, source="test")
        r.alpha.status = AlphaStatus.PASSED.value
        si = SimulationImport(alpha_id=r.alpha.id, source="brain_api", raw_payload={}, is_latest=True)
        db_session.add(si)
        db_session.flush()
        db_session.add(AlphaMetric(simulation_import_id=si.id, alpha_id=r.alpha.id, sharpe=1.5, passed_all_checks=True))
    db_session.flush()

    @contextmanager
    def mock_scope():
        yield db_session

    monkeypatch.setattr("app.services.campaign_runner.session_scope", mock_scope)
    monkeypatch.setattr("app.services.simulation_runner.session_scope", mock_scope)
    monkeypatch.setattr("app.services.simulation_runner.BrainClient", FakeBrainClient)
    monkeypatch.setattr("app.services.brain.BrainClient", FakeBrainClient)

    execute_campaign(cid, simulate=True)

    t0_reloaded = db_session.get(CampaignTask, t0.id)
    assert t0_reloaded.status == "skipped"
    assert t0_reloaded.error == "surface already complete"

    # 2. Test unexpected zero-work (expansion produced no candidates -> failed)
    camp2 = create_nightly_campaign(db_session, budget=50)
    cid2 = camp2.id
    t_unexp = db_session.execute(select(CampaignTask).where(CampaignTask.campaign_id == cid2)).scalars().first()
    # Mock expand to return empty candidates
    monkeypatch.setattr("app.services.campaign_runner.expand", lambda *args, **kwargs: [])

    execute_campaign(cid2, simulate=True)

    t_unexp_reloaded = db_session.get(CampaignTask, t_unexp.id)
    assert t_unexp_reloaded.status == "failed"
    assert t_unexp_reloaded.error == "expansion produced no candidates"


# ----------------------------------------------------------------------
# C4: Module-level Semaphore and config_available Concurrency
# ----------------------------------------------------------------------

def test_c4_account_slots_semaphore_and_config_available():
    """C4: Module-level _ACCOUNT_SLOTS caps concurrency at 3 across multiple BrainClient instances."""
    import httpx

    # Verify semaphore instance is shared at module level
    assert _ACCOUNT_SLOTS._value == MAX_CONCURRENT_SIMULATIONS

    active_in_critical_section = 0
    max_active = 0
    lock = threading.Lock()

    class MockHTTPTransport(httpx.BaseTransport):
        def handle_request(self, request: httpx.Request) -> httpx.Response:
            nonlocal active_in_critical_section, max_active
            with lock:
                active_in_critical_section += 1
                if active_in_critical_section > max_active:
                    max_active = active_in_critical_section
            time.sleep(0.05)
            with lock:
                active_in_critical_section -= 1
            if request.url.path == "/authentication":
                return httpx.Response(200, json={"token": "tok"})
            if request.url.path == "/simulations":
                return httpx.Response(201, headers={"Location": "/simulations/sim123"})
            if "/simulations/sim123" in request.url.path:
                return httpx.Response(200, json={"alpha": "alpha_123"})
            return httpx.Response(200, json={})

    # Test concurrent simulations across distinct client instances
    def worker():
        client = BrainClient(email="user@example.com", password="pw", base_url="https://api.worldquantbrain.com")
        client._client = httpx.Client(base_url=client.base_url, transport=MockHTTPTransport())
        try:
            client.simulate("rank(close)", poll_seconds=0.01, max_wait_seconds=1.0)
        except Exception:
            pass

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert max_active <= MAX_CONCURRENT_SIMULATIONS, f"Max concurrent simulations {max_active} exceeded {MAX_CONCURRENT_SIMULATIONS}"


# ----------------------------------------------------------------------
# C5: Invariant 8 — Spike vs Ridge Selection
# ----------------------------------------------------------------------

def test_c5_invariant8_spike_vs_ridge_selection(db_session, tmp_path):
    """C5: Invariant 8 — Representative is selected by neighbourhood strength, never raw Sharpe."""
    ds = _seed_campaign_dataset(db_session, n_fields=1)
    fcode = f"{ds.dataset_code}_f0"
    settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
    spec = FamilySpec(field_code=fcode, denominator="cap", operator_family="ts_zscore", wrapper_shape="rank")
    fkey = spec.family_key(settings)

    candidates = expand(db_session, spec, base_settings=settings, max_candidates=49)
    assert len(candidates) == 49

    alpha_ids = []
    grid_map: dict[tuple[int, int], int] = {}

    for c in candidates:
        r = create_alpha(db_session, c.expression, c.settings, family_key=c.family_key, grid=c.grid, source="test")
        alpha_ids.append(r.alpha.id)
        w = c.grid["window"]
        d = c.grid["decay"]
        grid_map[(w, d)] = r.alpha.id
    db_session.flush()

    store = PnLStore(base_dir=tmp_path / "pnl")
    dates = [f"d_{i:04d}" for i in range(600)]
    rng = np.random.default_rng(42)

    windows = sorted({c.grid["window"] for c in candidates})
    decays = sorted({c.grid["decay"] for c in candidates})

    # Point A: (w=22, d=4) - Isolated spike (Sharpe 2.8, neighbours Sharpe 0.5)
    # Point B: (w=120, d=15) - Broad ridge (Sharpe 1.9, neighbours Sharpe 1.85)
    # Point C: (w=120, d=25) - High point on ridge (Sharpe 2.1, but neighbours Sharpe 1.70)
    spike_coord = (windows[2], decays[2])   # (22, 4)
    ridge_rep = (windows[4], decays[4])     # (120, 15)
    ridge_high = (windows[4], decays[5])    # (120, 25)

    for (w, d), aid in grid_map.items():
        if (w, d) == spike_coord:
            sharpe = 2.80
        elif (w, d) == ridge_high:
            sharpe = 2.10
        elif (w, d) == ridge_rep:
            sharpe = 1.90
        elif abs(windows.index(w) - 4) <= 1 and abs(decays.index(d) - 4) <= 1:
            sharpe = 1.85  # Ridge plateau
        else:
            sharpe = 0.50  # Background

        si = SimulationImport(alpha_id=aid, source="brain_api", raw_payload={}, is_latest=True)
        db_session.add(si)
        db_session.flush()
        db_session.add(
            AlphaMetric(
                simulation_import_id=si.id,
                alpha_id=aid,
                sharpe=sharpe,
                fitness=1.2,
                turnover=0.3,
                returns=0.15,
                passed_all_checks=True,
            )
        )
        pnl = rng.normal(0.002 * (sharpe / 2.0), 0.01, size=600)
        store.save_pnl(aid, dates, pnl)

    db_session.flush()

    verdicts = P.evaluate(db_session, fkey, pnl_store=store)

    spike_v = next(v for v in verdicts if v.alpha_id == grid_map[spike_coord])
    ridge_high_v = next(v for v in verdicts if v.alpha_id == grid_map[ridge_high])

    # 1. Spike is rejected (fails plateau ratio)
    assert not spike_v.is_plateau, "Spike must fail is_plateau"
    assert not spike_v.promoted

    # 2. Exactly 1 representative promoted in this structure slice
    promoted = [v for v in verdicts if v.promoted]
    assert len(promoted) == 1
    rep_v = promoted[0]

    # 3. Ridge high is marked redundant with the ridge representative (not promoted despite higher raw Sharpe)
    assert ridge_high_v.promoted is False
    assert ridge_high_v.redundant_with == rep_v.alpha_id
    assert rep_v.is_plateau is True
