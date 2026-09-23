"""Read immutable canonical data snapshots."""

from __future__ import annotations

import csv
import json
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from ashare_multifactor.data.contracts import CanonicalDataset
from ashare_multifactor.data.schemas import coerce_row, dataset_schema

SHANGHAI_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")


class DataRepositoryError(RuntimeError):
    """Raised when a snapshot cannot be selected or read."""


class DataNotAvailableError(DataRepositoryError):
    """Raised when no row is available at the requested as_of time."""


class DataRepository:
    """Load canonical datasets from versioned snapshot directories."""

    def __init__(self, root: Path, *, snapshot_id: str | None = None) -> None:
        self.root = Path(root)
        self.snapshot_id = snapshot_id

    def load(
        self,
        dataset: str,
        as_of: datetime | date | str,
        source: str | None = None,
    ) -> CanonicalDataset:
        """Load a dataset and keep rows whose available_at is not after as_of.

        Date-only as_of values are interpreted as the end of that trading day in
        Asia/Shanghai. Naive datetime values use the same timezone.
        """
        cutoff = _coerce_as_of(as_of)
        snapshot_dir, entry = self._select_dataset(dataset, source)
        rows = _read_dataset(snapshot_dir, entry)
        available_rows = [row for row in rows if _is_available(row, cutoff, dataset)]
        if not available_rows:
            raise DataNotAvailableError(
                f"{dataset} from snapshot {snapshot_dir.name!r} has no rows available "
                f"at {cutoff.isoformat()}."
            )

        primary_key = tuple(str(value) for value in entry.get("primary_key", ()))
        if not primary_key:
            raise DataRepositoryError(f"Snapshot entry for {dataset!r} has no primary_key.")

        metadata = {
            **dict(entry.get("metadata", {})),
            "snapshot_id": snapshot_dir.name,
            "snapshot_dir": str(snapshot_dir.resolve()),
            "source": _entry_source(entry, rows),
            "source_version": _entry_source_version(entry, rows),
            "requested_source": source,
            "requested_as_of": cutoff.isoformat(),
            "total_rows": len(rows),
            "available_rows": len(available_rows),
            "filtered_rows": len(rows) - len(available_rows),
        }
        return CanonicalDataset(
            name=dataset,
            primary_key=primary_key,
            rows=available_rows,
            metadata=metadata,
        )

    def _select_dataset(
        self,
        dataset: str,
        source: str | None,
    ) -> tuple[Path, Mapping[str, Any]]:
        candidates = []
        for manifest_path in self._manifest_paths():
            manifest = _read_manifest(manifest_path)
            for entry in manifest.get("datasets", []):
                if entry.get("name") != dataset:
                    continue
                entry_source = entry.get("source")
                if source is not None and entry_source != source:
                    continue
                candidates.append(
                    (
                        _manifest_run_at(manifest),
                        manifest_path.parent.name,
                        manifest_path.parent,
                        entry,
                    )
                )

        if not candidates:
            source_note = f" for source {source!r}" if source else ""
            raise DataRepositoryError(
                f"No snapshot contains dataset {dataset!r}{source_note} under {self.root}."
            )

        _, snapshot_name, snapshot_dir, entry = max(
            candidates,
            key=lambda item: (item[0], item[1]),
        )
        return snapshot_dir, entry

    def _manifest_paths(self) -> list[Path]:
        if not self.root.is_dir():
            raise DataRepositoryError(f"Snapshot root does not exist: {self.root}")

        if self.snapshot_id is not None:
            return [self.root / self.snapshot_id / "manifest.json"]
        return sorted(self.root.glob("*/manifest.json"))


def _read_manifest(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise DataRepositoryError(f"Snapshot manifest does not exist: {path}")
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DataRepositoryError(f"Invalid snapshot manifest: {path}") from exc
    if not isinstance(manifest, dict):
        raise DataRepositoryError(f"Snapshot manifest must be a JSON object: {path}")
    return manifest


def _manifest_run_at(manifest: Mapping[str, Any]) -> datetime:
    value = manifest.get("run_at")
    if not value:
        return datetime.min.replace(tzinfo=timezone.utc)
    return _parse_timestamp(str(value), "manifest.run_at").astimezone(timezone.utc)


def _read_dataset(snapshot_dir: Path, entry: Mapping[str, Any]) -> list[dict[str, Any]]:
    dataset = str(entry.get("name", ""))
    path = _resolve_dataset_path(snapshot_dir, entry)
    schema = entry.get("schema")
    if schema is None:
        schema = dataset_schema(dataset, [])
    if not isinstance(schema, dict):
        raise DataRepositoryError(f"Snapshot schema for {dataset!r} must be an object.")

    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise DataRepositoryError(f"Dataset file has no header: {path}")
        return [coerce_row(row, schema) for row in reader]


def _resolve_dataset_path(snapshot_dir: Path, entry: Mapping[str, Any]) -> Path:
    raw_path = entry.get("path")
    if not raw_path:
        raise DataRepositoryError("Snapshot dataset entry is missing path.")

    path = Path(str(raw_path))
    if not path.is_absolute():
        path = snapshot_dir / path
    resolved = path.resolve()
    try:
        resolved.relative_to(snapshot_dir.resolve())
    except ValueError as exc:
        raise DataRepositoryError(f"Dataset path escapes snapshot directory: {resolved}") from exc
    if not resolved.is_file():
        raise DataRepositoryError(f"Dataset file does not exist: {resolved}")
    return resolved


def _is_available(row: Mapping[str, Any], cutoff: datetime, dataset: str) -> bool:
    value = row.get("available_at")
    if value in (None, ""):
        raise DataRepositoryError(f"{dataset} row is missing available_at: {row!r}")
    return _parse_timestamp(str(value), "available_at") <= cutoff


def _parse_timestamp(value: str, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataRepositoryError(f"Invalid {label} timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise DataRepositoryError(f"{label} must include a timezone: {value!r}")
    return parsed


def _coerce_as_of(value: datetime | date | str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, time.max, tzinfo=SHANGHAI_TZ)
    else:
        text = str(value).strip()
        try:
            if len(text) == 10:
                parsed = datetime.combine(date.fromisoformat(text), time.max, tzinfo=SHANGHAI_TZ)
            else:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError as exc:
            raise DataRepositoryError(
                f"Invalid as_of {value!r}; expected YYYY-MM-DD or ISO 8601."
            ) from exc

    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=SHANGHAI_TZ)
    return parsed


def _entry_source(entry: Mapping[str, Any], rows: list[Mapping[str, Any]]) -> str | None:
    value = entry.get("source")
    if value:
        return str(value)
    return _single_row_value(rows, "source")


def _entry_source_version(
    entry: Mapping[str, Any],
    rows: list[Mapping[str, Any]],
) -> str | None:
    value = entry.get("source_version")
    if value:
        return str(value)
    return _single_row_value(rows, "source_version")


def _single_row_value(rows: list[Mapping[str, Any]], column: str) -> str | None:
    values = {str(row[column]) for row in rows if row.get(column) not in (None, "")}
    if len(values) != 1:
        return None
    return next(iter(values))
