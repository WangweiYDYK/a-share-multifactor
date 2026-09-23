"""Console and file output for one reference pick list."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ashare_multifactor.picks.builder import PickList
from ashare_multifactor.reports.performance import write_csv, write_json

DASH = "-"
PICK_COLUMNS = (
    "rank",
    "symbol",
    "security_name",
    "industry_code",
    "composite_score",
    "weight",
    "last_close",
    "last_quote_date",
    "average_amount",
    "circ_mv",
    "return_20d",
    "return_60d",
    "volatility_60d",
)


def format_picks(result: PickList, limit: int | None = None) -> str:
    """Compact one-line-per-name view for the terminal."""
    lines = [
        "决策日 {0}｜候选 {1} 只｜入选 {2} 只｜价格口径 {3}".format(
            result.decision_date,
            result.summary["candidate_count"],
            len(result.picks),
            result.manifest["factor_basis"],
        )
    ]
    shown = result.picks if limit is None else result.picks[:limit]
    for pick in shown:
        lines.append(
            "{0:>3}. {1} {2}  综合分 {3:+.3f}  参考权重 {4}  "
            "收盘 {5:.2f}  20日 {6}  60日 {7}  年化波动 {8}  20日均额 {9}".format(
                pick["rank"],
                pick["symbol"],
                pick["security_name"],
                pick["composite_score"],
                _percent(pick["weight"]),
                pick["last_close"],
                _percent(pick["return_20d"]),
                _percent(pick["return_60d"]),
                _percent(pick["volatility_60d"]),
                _amount(pick["average_amount"]),
            )
        )
    if limit is not None and len(result.picks) > limit:
        lines.append("... 另有 {0} 只，见 picks.csv".format(len(result.picks) - limit))
    return "\n".join(lines)


def render_markdown(result: PickList) -> str:
    """Readable report that can be opened without any tooling."""
    lines = [
        "# 月度参考买入清单 {0}".format(result.decision_date),
        "",
        "- 决策时点：{0}".format(result.cutoff),
        "- 快照：{0}".format(result.manifest.get("snapshot_id") or "未命名快照"),
        "- 数据来源：{0}".format(
            "、".join(result.manifest.get("sources") or []) or "未知"
        ),
        "- 候选 {0} 只，入选 {1} 只，价格口径 {2}".format(
            result.summary["candidate_count"],
            len(result.picks),
            result.manifest["factor_basis"],
        ),
        "",
        "| 排名 | 代码 | 名称 | 综合分 | 参考权重 | 收盘 | 20日 | 60日 | 年化波动 | 20日均额 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for pick in result.picks:
        lines.append(
            "| {0} | {1} | {2} | {3:+.3f} | {4} | {5:.2f} | {6} | {7} | {8} | {9} |".format(
                pick["rank"],
                pick["symbol"],
                pick["security_name"],
                pick["composite_score"],
                _percent(pick["weight"]),
                pick["last_close"],
                _percent(pick["return_20d"]),
                _percent(pick["return_60d"]),
                _percent(pick["volatility_60d"]),
                _amount(pick["average_amount"]),
            )
        )
    lines.extend(["", "## 使用的筛选", ""])
    for name, status in sorted(result.screens.items()):
        lines.append("- `{0}`：{1}".format(name, status))
    lines.extend(["", "## 剔出的原因", ""])
    counts = result.summary.get("exclusion_counts") or {}
    if counts:
        for reason, count in counts.items():
            lines.append("- `{0}`：{1} 只".format(reason, count))
    else:
        lines.append("- 无")
    lines.extend(["", "## 说明", ""])
    for limitation in result.manifest.get("limitations") or []:
        lines.append("- {0}".format(limitation))
    lines.extend(
        [
            "",
            "> {0}".format(result.manifest["disclaimer"]),
            "",
            "权重是按等权加约束算出的参考仓位，不是必须执行的金额。",
            "",
        ]
    )
    return "\n".join(lines)


def write_pick_artifacts(result: PickList, output_dir: Path) -> Path:
    """Write picks.csv, exclusions.csv, picks.md, and the run manifest."""
    output_dir.mkdir(parents=True, exist_ok=True)
    write_json(output_dir / "manifest.json", result.manifest)
    write_csv(
        output_dir / "picks.csv",
        result.picks,
        fieldnames=PICK_COLUMNS,
    )
    write_csv(output_dir / "exclusions.csv", result.exclusions)
    markdown = output_dir / "picks.md"
    markdown.write_text(render_markdown(result), encoding="utf-8")
    return markdown


def _percent(value: Any) -> str:
    if value is None:
        return DASH
    return "{0:+.1%}".format(float(value))


def _amount(value: Any) -> str:
    if value is None:
        return DASH
    return "{0:.2f}亿".format(float(value) / 100_000_000.0)
