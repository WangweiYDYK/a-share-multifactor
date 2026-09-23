"""A focused smoke check of the demo's offline visual report."""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path

from ashare_multifactor.data.universe_demo import run
from ashare_multifactor.reports.universe import render_universe_report, write_universe_report

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class UniverseReportTest(unittest.TestCase):
    def test_demo_report_and_safe_rendering(self) -> None:
        run_id = datetime.now(timezone.utc).strftime("visual-%Y%m%dT%H%M%S%fZ")
        manifest = run(PROJECT_ROOT / "artifacts" / "universe-demo" / run_id)
        report = manifest.parent / "report.html"
        html = report.read_text(encoding="utf-8")
        self.assertIn('lang="zh-CN"', html)
        self.assertIn("8.3%", html)
        self.assertEqual(html.count('<tr data-state="eligible"'), 1)
        self.assertEqual(html.count('<tr data-state="excluded"'), 11)
        self.assertIn("合成数据演示", html)
        self.assertIn("缺少当日股票状态", html)
        self.assertIn("synthetic-universe-2025-08-v1", html)
        self.assertNotIn('<script src=', html)
        with self.assertRaises(FileExistsError):
            write_universe_report(manifest.parent)
        with self.assertRaisesRegex(ValueError, "inside the repository"):
            write_universe_report(PROJECT_ROOT.parent)

        summary = json.loads(manifest.read_text(encoding="utf-8"))
        eligible = json.loads((manifest.parent / "eligible.json").read_text(encoding="utf-8"))
        eligible[0]["security_name"] = '<script>alert("unexpected")</script>'
        escaped = render_universe_report(summary, eligible, [])
        self.assertNotIn('<script>alert(', escaped)
        self.assertIn("&lt;script&gt;", escaped)
        empty = render_universe_report({**summary, "exclusion_counts": {}}, [], [])
        self.assertIn("0.0%", empty)
        self.assertIn('id="empty" class="empty" >', empty)
        print(f"\nVisual report: {report}")


if __name__ == "__main__":
    unittest.main()
