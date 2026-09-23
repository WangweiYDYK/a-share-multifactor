import json
import tempfile
import unittest
from pathlib import Path

from ashare_multifactor.data.contracts import CanonicalDataset
from ashare_multifactor.data.repository import DataRepository, DataRepositoryError
from ashare_multifactor.data.storage import write_snapshot


class DataRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.root = Path(self.temp_dir.name) / "snapshots"
        rows = [
            _daily_row("2026-09-20", "10.00", "2026-09-20T18:00:00+08:00"),
            _daily_row("2026-09-21", "10.10", "2026-09-21T18:00:00+08:00"),
        ]
        dataset = CanonicalDataset(
            name="daily_prices",
            primary_key=("trade_date", "symbol", "adjustment"),
            rows=rows,
            metadata={"adjustment": "none"},
        )
        self.manifest_path = write_snapshot(
            self.root,
            [dataset],
            snapshot_id="baostock_test",
            as_of="2026-09-21",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_manifest_records_source_and_version(self):
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        entry = manifest["datasets"][0]

        self.assertEqual(manifest["snapshot_id"], "baostock_test")
        self.assertEqual(manifest["as_of"], "2026-09-21")
        self.assertEqual(entry["source"], "baostock")
        self.assertEqual(entry["source_version"], "test-version")
        self.assertEqual(entry["schema"]["volume"], "float")

    def test_load_filters_by_available_at_and_restores_types(self):
        repository = DataRepository(self.root)

        loaded = repository.load("daily_prices", as_of="2026-09-20", source="baostock")

        self.assertEqual(len(loaded.rows), 1)
        self.assertEqual(loaded.rows[0]["trade_date"], "2026-09-20")
        self.assertEqual(loaded.rows[0]["volume"], 1000.0)
        self.assertIsInstance(loaded.rows[0]["trade_status"], int)
        self.assertEqual(loaded.metadata["source"], "baostock")
        self.assertEqual(loaded.metadata["source_version"], "test-version")
        self.assertEqual(loaded.metadata["snapshot_id"], "baostock_test")

    def test_load_date_only_includes_rows_later_that_day(self):
        repository = DataRepository(self.root)

        loaded = repository.load("daily_prices", as_of="2026-09-21")

        self.assertEqual([row["trade_date"] for row in loaded.rows], ["2026-09-20", "2026-09-21"])
        self.assertEqual(loaded.metadata["available_rows"], 2)

    def test_load_rejects_unknown_source(self):
        repository = DataRepository(self.root)

        with self.assertRaises(DataRepositoryError):
            repository.load("daily_prices", as_of="2026-09-21", source="tushare_pro")


def _daily_row(trade_date: str, close: str, available_at: str) -> dict[str, object]:
    return {
        "trade_date": trade_date,
        "symbol": "600000.SH",
        "security_name": "浦发银行",
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "pre_close": close,
        "volume": "1000",
        "amount": "1000000",
        "turnover_rate": None,
        "pct_change": "1.0",
        "pe_ttm": None,
        "pb_mrq": None,
        "ps_ttm": None,
        "pcf_ncf_ttm": None,
        "trade_status": "1",
        "is_st": "0",
        "adjustment": "none",
        "source": "baostock",
        "source_version": "test-version",
        "retrieved_at": available_at,
        "available_at": available_at,
    }


if __name__ == "__main__":
    unittest.main()
