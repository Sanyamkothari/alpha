"""The single audited package allowed to talk to WorldQuant BRAIN.

Every byte of BRAIN traffic goes through :class:`BrainClient`. Nothing else in
the codebase may import a raw HTTP client — ``tests/test_brain_no_post.py``
enforces that, and additionally pins the exact set of POST endpoints this
package is permitted to call.

Simulation is automated; submission is not. See docs/DECISIONS.md.
"""

from app.services.brain.client import (
    ALLOWED_POST_PATHS,
    BrainAuthError,
    BrainClient,
    BrainError,
    SimulationSettings,
    SimulationTimeout,
)

__all__ = [
    "BrainClient",
    "BrainError",
    "BrainAuthError",
    "SimulationTimeout",
    "SimulationSettings",
    "ALLOWED_POST_PATHS",
]
