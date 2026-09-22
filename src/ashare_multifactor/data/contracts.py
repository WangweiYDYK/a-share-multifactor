"""Provider-neutral contracts for the data ingestion boundary."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class RawDataset:
    """Rows returned by one provider before project-level normalization."""

    name: str
    source: str
    source_version: str
    retrieved_at: str
    rows: Sequence[Mapping[str, Any]]
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class CanonicalDataset:
    """Rows with stable project field names and validated primary keys."""

    name: str
    primary_key: tuple[str, ...]
    rows: Sequence[Mapping[str, Any]]
    metadata: Mapping[str, Any] = field(default_factory=dict)
