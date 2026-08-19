"""Filter Backtest Harness — scores the gate stack as a classifier (STRATEGY.md Rule 5).

Evaluates the gate stack on synthetic families with known ground truth:
1. False Discovery Rate: proportion of pure-noise (Sharpe = 0.0) families that promote an alpha (target < 5%).
2. Statistical Power: proportion of stationary (Sharpe = 1.5) families that promote at least one alpha (target >= 85%).
3. Stage Attrition: tracks exactly where real alphas are rejected.
"""

from __future__ import annotations

import math
import tempfile
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import structlog
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base
from app.models.fields import DataField, Dataset
from app.models.operators import Operator, OperatorArgument
from app.models.results import AlphaMetric, SimulationImport
from app.services.alpha_library import AlphaSettings, create_alpha
from app.services.constructor import FamilySpec, expand
from app.services.filter_config import (
    DEFAULT_FILTER_CONFIG,
    TRADING_DAYS_PER_YEAR,
    FilterConfig,
)
from app.services.plateau import evaluate
from app.services.pnl_storage import PnLStore

log = structlog.get_logger("filter_backtest")


@dataclass
class SyntheticFamilySpec:
    true_sharpe: float = 0.0
    intra_corr: float = 0.92
    backtest_days: int = 1236
    regime_break_at: float | None = None  # Fraction (e.g. 0.5) where signal dies
    field_code: str = "close"
    operator_family: str = "ts_zscore"
    wrapper_shape: str = "rank"
    n_points: int = 49


@dataclass
class FilterScorecard:
    total_families: int
    null_families: int
    promoted_null: int
    null_promotion_rate: float
    true_signal_families: int
    promoted_true_signal: int
    true_signal_survival_rate: float
    stage_attrition: dict[str, int] = field(default_factory=dict)
    config_fingerprint: str = ""


def generate_synthetic_pnl(
    true_annual_sharpe: float,
    n_days: int = 1236,
    daily_vol: float = 0.01,
    regime_break_at: float | None = None,
    rng: np.random.Generator | None = None,
) -> np.ndarray:
    """Generate 1D daily PnL series with known Sharpe and optional regime breakdown."""
    r = rng or np.random.default_rng(42)
    daily_sharpe = true_annual_sharpe / math.sqrt(TRADING_DAYS_PER_YEAR)
    daily_mean = daily_sharpe * daily_vol

    if regime_break_at is not None and 0.0 < regime_break_at < 1.0:
        break_idx = int(n_days * regime_break_at)
        h1 = r.normal(daily_mean, daily_vol, break_idx)
        # Post-break: zero mean / decay
        h2 = r.normal(0.0, daily_vol, n_days - break_idx)
        return np.concatenate([h1, h2])

    return r.normal(daily_mean, daily_vol, n_days)


def generate_family_pnl_matrix(
    n_alphas: int,
    true_annual_sharpe: float,
    intra_corr: float = 0.92,
    n_days: int = 1236,
    regime_break_at: float | None = None,
    rng: np.random.Generator | None = None,
) -> tuple[list[str], np.ndarray]:
    """Generate (N_alphas x T_days) matrix of correlated daily PnL vectors."""
    r = rng or np.random.default_rng(42)
    dates = [f"d_{i:04d}" for i in range(n_days)]

    # Generate common factor
    daily_vol = 0.01
    common_pnl = generate_synthetic_pnl(
        true_annual_sharpe,
        n_days=n_days,
        daily_vol=daily_vol,
        regime_break_at=regime_break_at,
        rng=r,
    )

    # Blend common factor and idiosyncratic noise for target correlation
    # corr = intra_corr => blend = sqrt(intra_corr)*common + sqrt(1 - intra_corr)*idio
    w_common = math.sqrt(max(0.0, min(1.0, intra_corr)))
    w_idio = math.sqrt(max(0.0, 1.0 - intra_corr))

    rows: list[np.ndarray] = []
    for _ in range(n_alphas):
        idio = r.normal(0.0, daily_vol, n_days)
        row = w_common * common_pnl + w_idio * idio
        rows.append(row)

    return dates, np.vstack(rows)


def run_filter_backtest(
    n_null_replications: int = 100,
    n_signal_replications: int = 100,
    true_sharpe: float = 1.50,
    cfg: FilterConfig = DEFAULT_FILTER_CONFIG,
    seed: int = 42,
) -> FilterScorecard:
    """Run full calibration backtest over synthetic null and signal families."""
    rng = np.random.default_rng(seed)
    stage_attrition: dict[str, int] = defaultdict(int)

    promoted_null = 0
    promoted_signal = 0

    # Setup throwaway in-memory database
    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(eng)
    session_factory = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False, future=True)

    with session_factory() as db:
        # Seed minimal operators
        op_rank = Operator(name="rank", category="cross_section", definition="rank", returns="matrix")
        op_zscore = Operator(name="ts_zscore", category="time_series", definition="ts_zscore", returns="matrix")
        db.add_all([op_rank, op_zscore])
        db.flush()
        db.add_all([
            OperatorArgument(operator_id=op_rank.id, name="x", position=0, arg_type="matrix", required=True),
            OperatorArgument(operator_id=op_zscore.id, name="x", position=0, arg_type="matrix", required=True),
            OperatorArgument(operator_id=op_zscore.id, name="d", position=1, arg_type="int", required=True, is_window=True, min_value=2, max_value=512),
        ])
        # Seed minimal dataset and fields
        ds = Dataset(dataset_code="synth_ds", name="Synthetic Dataset", region="USA", universe="TOP3000", delay=1)
        db.add(ds)
        db.flush()
        db.add(
            DataField(
                field_code="synth_field",
                dataset_id=ds.id,
                category="price",
                field_type="MATRIX",
                region="USA",
                universe="TOP3000",
                delay=1,
                coverage=0.95,
                user_count=50,
            )
        )
        db.commit()

    temp_dir = tempfile.mkdtemp()
    store = PnLStore(Path(temp_dir) / "pnl")

    # 1. Null families (pure noise, true_sharpe = 0.0)
    for rep in range(n_null_replications):
        with session_factory() as db:
            spec = FamilySpec(
                field_code="synth_field",
                mechanism=f"null_{rep}",
                operator_family="ts_zscore",
                wrapper_shape="rank",
            )
            settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
            fk = spec.family_key(settings)
            candidates = expand(db, spec, base_settings=settings, max_candidates=49)
            if not candidates:
                continue

            dates, pnl_matrix = generate_family_pnl_matrix(
                len(candidates), true_annual_sharpe=0.0, intra_corr=0.92, n_days=cfg.backtest_days, rng=rng
            )

            created_ids: list[int] = []
            for i, c in enumerate(candidates):
                res = create_alpha(db, c.expression, c.settings, family_key=c.family_key, grid=c.grid, source="backtest")
                aid = res.alpha.id
                created_ids.append(aid)
                pnl_vec = pnl_matrix[i]
                store.save_pnl(aid, dates, pnl_vec)

                std = float(np.std(pnl_vec, ddof=1))
                sr = (float(np.mean(pnl_vec)) / std * math.sqrt(TRADING_DAYS_PER_YEAR)) if std > 1e-12 else 0.0
                si = SimulationImport(alpha_id=aid, source="brain_api", raw_payload={}, is_latest=True)
                db.add(si)
                db.flush()
                db.add(
                    AlphaMetric(
                        simulation_import_id=si.id,
                        alpha_id=aid,
                        sharpe=sr,
                        fitness=max(0.1, sr * 0.8),
                        turnover=0.3,
                        returns=sr * 0.05,
                        passed_all_checks=True,
                    )
                )
            db.commit()

            verdicts = evaluate(db, fk, pnl_store=store, cfg=cfg)
            if any(v.promoted for v in verdicts):
                promoted_null += 1

    # 2. Signal families (true_sharpe = 1.50)
    for rep in range(n_signal_replications):
        with session_factory() as db:
            spec = FamilySpec(
                field_code="synth_field",
                mechanism=f"signal_{rep}",
                operator_family="ts_zscore",
                wrapper_shape="rank",
            )
            settings = AlphaSettings(region="USA", universe="TOP3000", delay=1)
            fk = spec.family_key(settings)
            candidates = expand(db, spec, base_settings=settings, max_candidates=49)
            if not candidates:
                continue

            dates, pnl_matrix = generate_family_pnl_matrix(
                len(candidates), true_annual_sharpe=true_sharpe, intra_corr=0.92, n_days=cfg.backtest_days, rng=rng
            )

            created_ids = []
            for i, c in enumerate(candidates):
                res = create_alpha(db, c.expression, c.settings, family_key=c.family_key, grid=c.grid, source="backtest")
                aid = res.alpha.id
                created_ids.append(aid)
                pnl_vec = pnl_matrix[i]
                store.save_pnl(aid, dates, pnl_vec)

                std = float(np.std(pnl_vec, ddof=1))
                sr = (float(np.mean(pnl_vec)) / std * math.sqrt(TRADING_DAYS_PER_YEAR)) if std > 1e-12 else 0.0
                si = SimulationImport(alpha_id=aid, source="brain_api", raw_payload={}, is_latest=True)
                db.add(si)
                db.flush()
                db.add(
                    AlphaMetric(
                        simulation_import_id=si.id,
                        alpha_id=aid,
                        sharpe=sr,
                        fitness=max(0.5, sr * 0.8),
                        turnover=0.3,
                        returns=sr * 0.05,
                        passed_all_checks=True,
                    )
                )
            db.commit()

            verdicts = evaluate(db, fk, pnl_store=store, cfg=cfg)
            if any(v.promoted for v in verdicts):
                promoted_signal += 1
            else:
                # Track stage attrition reasons
                for v in verdicts:
                    for r in v.reasons:
                        stage_attrition[r.split(":")[0]] += 1

    null_rate = (promoted_null / n_null_replications) if n_null_replications else 0.0
    signal_survival = (promoted_signal / n_signal_replications) if n_signal_replications else 0.0

    return FilterScorecard(
        total_families=n_null_replications + n_signal_replications,
        null_families=n_null_replications,
        promoted_null=promoted_null,
        null_promotion_rate=null_rate,
        true_signal_families=n_signal_replications,
        promoted_true_signal=promoted_signal,
        true_signal_survival_rate=signal_survival,
        stage_attrition=dict(stage_attrition),
        config_fingerprint=cfg.fingerprint(),
    )
