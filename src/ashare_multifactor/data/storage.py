"""Local storage for canonical research datasets."""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

from ashare_multifactor.data.contracts import CanonicalDataset


def write_datasets(output_dir: Path, datasets: Sequence[CanonicalDataset]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_datasets = []

    for dataset in datasets:
        path = output_dir / f"{dataset.name}.csv"
        fieldnames = list(dataset.rows[0].keys())
        with path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(dataset.rows)
        manifest_datasets.append(
            {
                "name": dataset.name,
                "rows": len(dataset.rows),
                "primary_key": list(dataset.primary_key),
                "path": str(path.resolve()),
                "metadata": dict(dataset.metadata),
            }
        )

    manifest_path = output_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "run_at": datetime.now(timezone.utc).isoformat(),
                "datasets": manifest_datasets,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return manifest_path.resolve()
