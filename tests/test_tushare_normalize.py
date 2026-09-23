import unittest

from ashare_multifactor.data.contracts import RawDataset
from ashare_multifactor.data.normalize import CanonicalDataService


class TushareNormalizationTest(unittest.TestCase):
    def setUp(self):
        self.service = CanonicalDataService()
        self.retrieved_at = "2026-09-22T08:00:00+00:00"

    def test_normalizes_tushare_trade_calendar(self):
        raw = RawDataset(
            name="trade_calendar",
            source="tushare_pro",
            source_version="test",
            retrieved_at=self.retrieved_at,
            rows=[
                {
                    "exchange": "SSE",
                    "cal_date": "20260921",
                    "is_open": "1",
                    "pretrade_date": "20260918",
                }
            ],
        )

        result = self.service.normalize(raw)

        self.assertEqual(result.primary_key, ("exchange", "trade_date"))
        self.assertEqual(
            result.rows[0],
            {
                "exchange": "SSE",
                "trade_date": "2026-09-21",
                "is_open": 1,
                "previous_trade_date": "2026-09-18",
                "source": "tushare_pro",
                "source_version": "test",
                "retrieved_at": self.retrieved_at,
                "available_at": self.retrieved_at,
            },
        )

    def test_normalizes_tushare_security_master(self):
        raw = RawDataset(
            name="security_master",
            source="tushare_pro",
            source_version="test",
            retrieved_at=self.retrieved_at,
            rows=[
                {
                    "ts_code": "600000.SH",
                    "symbol": "600000",
                    "name": "浦发银行",
                    "area": "上海",
                    "industry": "银行",
                    "market": "主板",
                    "exchange": "SSE",
                    "list_status": "L",
                    "list_date": "19991110",
                    "delist_date": None,
                    "is_hs": "H",
                }
            ],
        )

        result = self.service.normalize(raw)
        row = result.rows[0]

        self.assertEqual(row["symbol"], "600000.SH")
        self.assertEqual(row["security_name"], "浦发银行")
        self.assertEqual(row["list_date"], "1999-11-10")
        self.assertEqual(row["security_type"], "stock")
        self.assertEqual(row["list_status"], "listed")
        self.assertEqual(row["industry"], "银行")
        self.assertEqual(row["exchange"], "SH")

    def test_normalizes_tushare_daily_units_and_availability(self):
        raw = RawDataset(
            name="daily_prices",
            source="tushare_pro",
            source_version="test",
            retrieved_at=self.retrieved_at,
            rows=[
                {
                    "ts_code": "600000.SH",
                    "trade_date": "20260921",
                    "open": "10.00",
                    "high": "10.20",
                    "low": "9.90",
                    "close": "10.10",
                    "pre_close": "10.00",
                    "change": "0.10",
                    "pct_chg": "1.00",
                    "vol": "1234.5",
                    "amount": "6789.0",
                }
            ],
        )

        result = self.service.normalize(raw)
        row = result.rows[0]

        self.assertEqual(row["symbol"], "600000.SH")
        self.assertEqual(row["volume"], 123450.0)
        self.assertEqual(row["amount"], 6789000.0)
        self.assertEqual(row["adjustment"], "none")
        self.assertEqual(row["pct_change"], 1.0)
        self.assertEqual(row["available_at"], "2026-09-21T18:00:00+08:00")
        self.assertIsNone(row["turnover_rate"])
        self.assertIsNone(row["trade_status"])


if __name__ == "__main__":
    unittest.main()
