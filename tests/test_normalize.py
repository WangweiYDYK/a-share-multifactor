import unittest

from ashare_multifactor.data.contracts import RawDataset
from ashare_multifactor.data.normalize import CanonicalDataService, DataQualityError


class CanonicalDataServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = CanonicalDataService()

    def test_normalizes_baostock_daily_row(self):
        raw = RawDataset(
            name="daily_prices",
            source="baostock",
            source_version="test",
            retrieved_at="2026-09-22T08:00:00+00:00",
            rows=[
                {
                    "date": "2026-09-21",
                    "code": "sh.600000",
                    "open": "10.00",
                    "high": "10.20",
                    "low": "9.90",
                    "close": "10.10",
                    "preclose": "10.00",
                    "volume": "1000",
                    "amount": "10050",
                    "adjustflag": "3",
                    "turn": "0.4",
                    "tradestatus": "1",
                    "pctChg": "1.0",
                    "peTTM": "5.0",
                    "pbMRQ": "0.6",
                    "psTTM": "1.0",
                    "pcfNcfTTM": "4.0",
                    "isST": "0",
                }
            ],
        )

        result = self.service.normalize(raw)

        self.assertEqual(result.rows[0]["symbol"], "600000.SH")
        self.assertEqual(result.rows[0]["adjustment"], "none")
        self.assertEqual(result.rows[0]["close"], 10.1)
        self.assertEqual(result.rows[0]["available_at"], "2026-09-21T18:00:00+08:00")
        self.assertEqual(
            result.metadata["availability_basis"],
            "project_convention_eod_18_00_asia_shanghai",
        )

    def test_rejects_duplicate_primary_keys(self):
        row = {"calendar_date": "2026-09-21", "is_trading_day": "1"}
        raw = RawDataset(
            name="trade_calendar",
            source="baostock",
            source_version="test",
            retrieved_at="2026-09-22T08:00:00+00:00",
            rows=[row, row],
        )

        with self.assertRaises(DataQualityError):
            self.service.normalize(raw)


if __name__ == "__main__":
    unittest.main()
