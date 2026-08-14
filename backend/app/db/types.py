"""Portable column types.

:data:`JSONType` — JSON on SQLite, JSONB on Postgres (one symbol, both dialects).

The ``Vector`` TypeDecorator that used to live here went with the embeddings
table. Dedup/clustering only earn their keep above ~1,000 accumulated results
(STRATEGY.md §10); when that point arrives, add the column type back alongside
the model that needs it rather than keeping an unused abstraction warm.
"""

from __future__ import annotations

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB

# JSON on generic dialects, JSONB on Postgres.
JSONType = JSON().with_variant(JSONB(), "postgresql")
