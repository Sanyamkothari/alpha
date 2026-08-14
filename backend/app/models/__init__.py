"""All ORM models.

Importing this package registers every table on ``Base.metadata`` — which is what
Alembic autogenerate and ``Base.metadata.create_all`` rely on. Keep every model
module imported here.

Scope note: the schema was pruned to the alpha-generation core (see STRATEGY.md).
The hypothesis, template, critic, dedup, notebook, knowledge-graph and
experiment-ledger models were removed along with their tables. Do not re-add a
model module without also adding a migration — an unimported model silently
disappears from autogenerate, and an unmigrated one silently reappears.
"""

from app.models._common import Base
from app.models.alphas import Alpha, AlphaStatusHistory
from app.models.brain import BrainFetchLog
from app.models.fields import Category, DataField, Dataset, FieldTag, Tag
from app.models.operators import (
    Operator,
    OperatorArgument,
    OperatorCompatibility,
    OperatorExample,
)
from app.models.prompts import LLMRun
from app.models.results import AlphaMetric, SimulationImport

__all__ = [
    "Base",
    # fields
    "Dataset",
    "DataField",
    "Category",
    "Tag",
    "FieldTag",
    # operators
    "Operator",
    "OperatorArgument",
    "OperatorExample",
    "OperatorCompatibility",
    # alphas
    "Alpha",
    "AlphaStatusHistory",
    # results
    "SimulationImport",
    "AlphaMetric",
    # LLM cost ledger
    "LLMRun",
    # read-only audit
    "BrainFetchLog",
]
