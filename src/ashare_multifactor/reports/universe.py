"""Render an offline HTML report from the three recorded universe result files."""

from __future__ import annotations

import argparse
import json
from html import escape
from pathlib import Path
from string import Template
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[3]
REASON_LABELS = {
    "not_stock": "非股票证券",
    "missing_list_date": "缺少上市日期",
    "not_listed_at_decision": "决策日尚未上市",
    "listing_too_short": "上市交易日不足",
    "delisted_at_decision": "决策日已退市",
    "missing_security_status": "缺少当日股票状态",
    "unknown_security_status": "股票状态未知",
    "not_listed": "非正常上市状态",
    "not_tradable": "当日不可交易",
    "st": "ST 股票",
    "suspended": "当日停牌",
    "delisting": "退市整理期",
    "missing_daily_basic": "缺少当日市值",
    "invalid_market_cap": "市值无效",
    "inconsistent_market_cap": "流通市值大于总市值",
    "missing_industry": "缺少有效行业",
    "insufficient_price_history": "日线记录不足",
    "low_liquidity": "日均成交额不足",
}
DATASET_LABELS = {
    "trade_calendar": "交易日历",
    "security_master": "证券基础信息",
    "daily_prices": "日线行情",
    "daily_basic": "市值信息",
    "security_status": "历史股票状态",
    "industry_membership": "历史行业归属",
}


def _text(value: Any) -> str:
    return escape(str(value if value is not None else "缺失"), quote=True)


def _number(value: Any, divisor: float = 1) -> str:
    return "缺失" if value is None else f"{float(value) / divisor:,.2f}"


def _json_details(value: Any, label: str) -> str:
    content = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    return f"<details><summary>{_text(label)}</summary><pre>{_text(content)}</pre></details>"


def render_universe_report(
    summary: Mapping[str, Any],
    eligible: Sequence[Mapping[str, Any]],
    exclusions: Sequence[Mapping[str, Any]],
) -> str:
    """Keep report calculations presentation-only; do not rerun universe selection."""
    included, excluded = len(eligible), len(exclusions)
    total = included + excluded
    rate = included / total * 100 if total else 0
    reason_counts = summary["exclusion_counts"]
    ordered_reasons = sorted(reason_counts.items(), key=lambda item: (-item[1], item[0]))
    reason_bars, options = [], []
    for reason, count in ordered_reasons:
        label = _text(REASON_LABELS.get(reason, reason))
        share = count / excluded * 100 if excluded else 0
        reason_bars.append(
            f'<div class="reason"><span>{label}</span><span class="track">'
            f'<span style="width:{share:.4f}%"></span></span><b>{count}</b></div>'
        )
        options.append(f'<option value="{_text(reason)}">{label} ({count})</option>')

    stock_rows = []
    for state, rows in (("eligible", eligible), ("excluded", exclusions)):
        for row in rows:
            reasons = row["reasons"]
            labels = " / ".join(REASON_LABELS.get(reason, reason) for reason in reasons)
            name = _text(row.get("security_name"))
            symbol = _text(row["symbol"])
            industry = _text(row.get("industry_name"))
            searchable = _text(f'{row["symbol"]} {row.get("security_name", "")}'.lower())
            stock_rows.append(
                f'<tr data-state="{state}" data-search="{searchable}" '
                f'data-reasons="{_text(" ".join(reasons))}">'
                f'<th scope="row"><code>{symbol}</code><small>{name}</small></th>'
                f'<td><span class="status {state}">{"入池" if state == "eligible" else "排除"}'
                f'</span></td><td class="numeric">{_text(row.get("listing_trading_days"))}</td>'
                f'<td class="numeric">{_number(row.get("average_amount"))}</td>'
                f'<td class="numeric">{_number(row.get("total_mv"), 100000000)}</td>'
                f'<td>{industry}</td><td class="decision">{_text(labels or "满足当前筛选规则")}'
                f'{_json_details(row, "决策依据")}</td></tr>'
            )

    sources = []
    for name, data in summary["datasets"].items():
        sources.append(
            f'<tr><th scope="row">{_text(DATASET_LABELS.get(name, name))}'
            f'<small><code>{_text(name)}</code></small></th>'
            f'<td>{_text(data.get("source"))}<small>{_text(data.get("source_version"))}</small></td>'
            f'<td class="numeric">{_text(data.get("visible_rows"))}</td>'
            f'<td><code>{_text(data.get("snapshot_id"))}</code>'
            f'{_json_details(data, "版本与内容摘要")}</td></tr>'
        )
    config = summary["config"]
    warning = (
        "合成数据演示 · 非真实历史股票池 · 不构成投资建议"
        if summary.get("synthetic") else
        "研究原型 · 数据历史可见性需核验 · 不构成投资建议"
    )
    return PAGE.substitute(
        month=_text(summary["month"]), as_of=_text(summary["as_of"]),
        decision_date=_text(summary["decision_date"]), warning=warning,
        total=total, included=included, excluded=excluded, rate=f"{rate:.1f}",
        included_width=f"{rate:.4f}", excluded_width=f"{100 - rate if total else 0:.4f}",
        reason_bars="".join(reason_bars) or '<p class="muted">无排除原因</p>',
        reason_options="".join(options), stock_rows="".join(stock_rows),
        source_rows="".join(sources), listing_days=_text(config["min_listing_trading_days"]),
        liquidity_window=_text(config["liquidity_window"]),
        minimum_amount=_number(config["min_average_amount"]),
        industry_system=_text(config["industry_system"]),
        rule_version=_text(summary["rule_version"]),
        manifest_details=_json_details(summary, "完整运行清单"),
        initial_empty="" if not total else "hidden",
    )


def write_universe_report(result_dir: Path) -> Path:
    """Create a new report next to existing JSON outputs, inside this repository."""
    directory = (PROJECT_ROOT / result_dir).resolve()
    try:
        directory.relative_to(PROJECT_ROOT)
    except ValueError as exc:
        raise ValueError("Report directory must stay inside the repository.") from exc
    data = [json.loads((directory / f"{name}.json").read_text(encoding="utf-8"))
            for name in ("manifest", "eligible", "exclusions")]
    report = directory / "report.html"
    # Exclusive creation preserves a previously published run's report.
    with report.open("x", encoding="utf-8") as stream:
        stream.write(render_universe_report(*data))
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result_dir", type=Path)
    args = parser.parse_args(argv)
    try:
        report = write_universe_report(args.result_dir)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f"Report failed: {exc}\n")
    print(f"Saved universe report: {report}")
    return 0


PAGE = Template('''<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>$month 月末股票池研究报告</title>
<style>
:root { color-scheme: light; --ink:#24282b; --muted:#62696e; --line:#dce1e3;
  --green:#157653; --red:#b8404c; --paper:#fff; --soft:#f4f6f6; }
* { box-sizing:border-box; letter-spacing:0; }
body { margin:0; color:var(--ink); background:var(--paper);
  font:14px/1.6 "Segoe UI","Microsoft YaHei",sans-serif; }
a { color:#176e88; text-underline-offset:3px; }
a:hover { color:var(--ink); }
header { border-bottom:1px solid var(--line); }
.header,main,footer { max-width:1280px; margin:auto; padding:24px 32px; }
.header { display:flex; gap:24px; justify-content:space-between; align-items:center; }
.brand { font-weight:650; font-size:16px; }
nav { display:flex; flex-wrap:wrap; gap:20px; }
nav a { color:var(--muted); text-decoration:none; }
main { padding-top:28px; }
h1 { font-size:28px; line-height:1.35; margin:0 0 10px; font-weight:650; }
h2 { font-size:18px; margin:0 0 16px; font-weight:650; }
p { margin:0; }
.muted,small { color:var(--muted); }
small { display:block; font-size:12px; font-weight:400; margin-top:4px; }
.warning { color:#973644; background:#fff1f2; border-left:3px solid var(--red);
  padding:10px 14px; margin:18px 0 24px; }
.metrics { display:grid; grid-template-columns:repeat(4,minmax(0,1fr)); gap:20px;
  margin:0 0 24px; }
.metrics dt { color:var(--muted); }
.metrics dd { margin:4px 0 0; font-size:32px; line-height:1.25; font-weight:600;
  font-variant-numeric:tabular-nums; }
.green { color:var(--green); }
.red { color:var(--red); }
.mix { display:flex; height:14px; background:var(--soft); overflow:hidden; }
.mix .yes { background:var(--green); }
.mix .no { background:var(--red); }
.legend { display:flex; flex-wrap:wrap; justify-content:space-between; gap:12px; margin-top:8px; }
.dot { display:inline-block; width:9px; height:9px; margin-right:6px; }
.dot.yes { background:var(--green); }
.dot.no { background:var(--red); }
section { padding:28px 0; border-bottom:1px solid var(--line); scroll-margin-top:16px; }
section:first-of-type { padding-top:0; }
.section-title { display:flex; flex-wrap:wrap; gap:6px 20px; align-items:baseline;
  justify-content:space-between; margin-bottom:16px; }
.section-title h2 { margin:0; }
.reason-grid { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px 40px; }
.reason { display:grid; grid-template-columns:minmax(130px,1fr) minmax(70px,1fr) 24px;
  gap:12px; align-items:center; }
.reason b { font-weight:500; text-align:right; font-variant-numeric:tabular-nums; }
.track { height:8px; background:var(--soft); }
.track span { display:block; height:100%; background:var(--red); }
.rules { display:flex; flex-wrap:wrap; gap:8px 24px; margin-top:22px; color:var(--muted); }
.controls { display:flex; flex-wrap:wrap; gap:12px; align-items:end; margin-bottom:16px; }
label { display:grid; gap:6px; color:var(--muted); font-size:12px; }
input,select { font:14px "Segoe UI","Microsoft YaHei",sans-serif; height:38px;
  padding:6px 10px; border:1px solid #a9b2b7; border-radius:4px; background:var(--paper);
  color:var(--ink); max-width:100%; }
input { width:250px; }
#visible-count { margin-left:auto; padding-bottom:8px; color:var(--muted); }
.table-wrap { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font-size:13px; }
.stock-table { min-width:950px; table-layout:fixed; }
.source-table { min-width:760px; table-layout:fixed; }
th,td { padding:13px 12px; border-bottom:1px solid var(--line); vertical-align:top;
  text-align:left; overflow-wrap:anywhere; }
thead th { background:var(--soft); color:var(--muted); font-weight:500; font-size:12px; }
tbody th { font-weight:500; }
tbody tr:hover { background:#fafbfb; }
.numeric { text-align:right; font-variant-numeric:tabular-nums; }
.status { display:inline-block; font-weight:600; white-space:nowrap; }
.status::before { content:""; display:inline-block; width:6px; height:6px;
  margin-right:6px; background:currentColor; }
.status.eligible { color:var(--green); }
.status.excluded { color:var(--red); }
code { font:12px/1.6 Consolas,monospace; overflow-wrap:anywhere; }
summary { cursor:pointer; color:#176e88; width:fit-content; }
details { margin-top:8px; }
pre { font:12px/1.65 Consolas,"Microsoft YaHei",monospace; white-space:pre-wrap;
  overflow-wrap:anywhere; background:var(--soft); padding:12px; margin:10px 0 0; }
.empty { padding:28px; text-align:center; color:var(--muted); }
[hidden] { display:none !important; }
footer { color:var(--muted); font-size:12px; display:flex; flex-wrap:wrap;
  justify-content:space-between; gap:12px; }
:focus-visible { outline:2px solid #176e88; outline-offset:3px; }
@media(max-width:700px) {
  .header,main,footer { padding:20px 16px; }
  .header { align-items:flex-start; flex-direction:column; gap:10px; }
  h1 { font-size:24px; }
  .metrics { grid-template-columns:repeat(2,minmax(0,1fr)); gap:20px; }
  .reason-grid { grid-template-columns:1fr; }
  .controls label { flex:1 1 180px; min-width:0; }
  input,select { width:100%; font-size:16px; }
  #visible-count { flex-basis:100%; margin:0; }
}
@media print { .controls,nav { display:none; } .table-wrap { overflow:visible; }
  table.stock-table,table.source-table { min-width:0; } }
</style>
</head>
<body>
<header><div class="header"><span class="brand">A 股多因子研究 / 股票池</span>
<nav aria-label="报告导航"><a href="#overview">概览</a><a href="#stocks">逐股结果</a>
<a href="#provenance">数据溯源</a></nav></div></header>
<main>
<section id="overview">
<h1>$month 月末股票池</h1>
<p class="muted">决策日 $decision_date · 数据截止 $as_of</p>
<p class="warning">$warning</p>
<dl class="metrics">
<div><dt>候选股票</dt><dd>$total</dd></div>
<div><dt>入池</dt><dd class="green">$included</dd></div>
<div><dt>排除</dt><dd class="red">$excluded</dd></div>
<div><dt>入池比例</dt><dd>$rate%</dd></div>
</dl>
<div class="mix" role="img" aria-label="入池 $included 只，排除 $excluded 只，共 $total 只">
<span class="yes" style="width:$included_width%"></span>
<span class="no" style="width:$excluded_width%"></span></div>
<div class="legend"><span><i class="dot yes" aria-hidden="true"></i>入池 $included 只</span>
<span><i class="dot no" aria-hidden="true"></i>排除 $excluded 只</span></div>
<div class="rules"><span>上市 ≥ $listing_days 个交易日</span>
<span>近 $liquidity_window 日日均成交额 ≥ $minimum_amount 元</span>
<span>行业体系 $industry_system</span></div>
</section>
<section aria-labelledby="reasons-title">
<div class="section-title"><h2 id="reasons-title">排除原因</h2>
<p class="muted">单位：只 · 同一股票可命中多个原因 · 条长为占排除股票比例</p></div>
<div class="reason-grid">$reason_bars</div>
</section>
<section id="stocks">
<div class="section-title"><h2>逐股结果</h2><p class="muted">金额：元 · 总市值：亿元</p></div>
<div class="controls">
<label>股票代码 / 名称<input id="search" type="search" placeholder="搜索股票" autocomplete="off"></label>
<label>结果<select id="state"><option value="all">全部结果</option>
<option value="eligible">入池</option><option value="excluded">排除</option></select></label>
<label>排除原因<select id="reason"><option value="all">全部原因</option>$reason_options</select></label>
<output id="visible-count" aria-live="polite">显示 $total / $total 只</output>
</div>
<noscript><p class="muted">当前显示全部结果；筛选需要启用 JavaScript。</p></noscript>
<div class="table-wrap"><table class="stock-table" aria-label="股票池逐股筛选结果">
<colgroup><col style="width:17%"><col style="width:7%"><col style="width:10%">
<col style="width:13%"><col style="width:10%"><col style="width:16%"><col style="width:27%"></colgroup>
<thead><tr><th scope="col">股票</th><th scope="col">结果</th><th class="numeric" scope="col">上市交易日</th>
<th class="numeric" scope="col">日均成交额</th><th class="numeric" scope="col">总市值</th>
<th scope="col">行业</th><th scope="col">判定与依据</th></tr></thead>
<tbody id="stock-rows">$stock_rows</tbody></table></div>
<p id="empty" class="empty" $initial_empty>没有匹配的股票</p>
</section>
<section id="provenance">
<div class="section-title"><h2>数据溯源</h2><p class="muted">按决策时点截断后的可见行数</p></div>
<div class="table-wrap"><table class="source-table" aria-label="数据来源与快照版本">
<colgroup><col style="width:22%"><col style="width:27%"><col style="width:11%"><col style="width:40%"></colgroup>
<thead><tr><th scope="col">数据集</th><th scope="col">来源 / 版本</th>
<th scope="col" class="numeric">可见行数</th><th scope="col">快照 / 内容摘要</th></tr></thead>
<tbody>$source_rows</tbody></table></div>
$manifest_details
</section>
</main>
<footer><span>研究原型 · 未评估财务过滤与下一交易日成交 · 无收益回测结果</span>
<span>规则版本 $rule_version</span></footer>
<script>
(() => {
  const rows = Array.from(document.querySelectorAll('#stock-rows tr'));
  const search = document.getElementById('search');
  const state = document.getElementById('state');
  const reason = document.getElementById('reason');
  const count = document.getElementById('visible-count');
  const empty = document.getElementById('empty');
  function filter() {
    const query = search.value.trim().toLowerCase();
    let visible = 0;
    rows.forEach(row => {
      const matches = row.dataset.search.includes(query)
        && (state.value === 'all' || row.dataset.state === state.value)
        && (reason.value === 'all' || row.dataset.reasons.split(' ').includes(reason.value));
      row.hidden = !matches;
      if (matches) visible += 1;
    });
    count.textContent = '显示 ' + visible + ' / ' + rows.length + ' 只';
    empty.hidden = visible !== 0;
  }
  search.addEventListener('input', filter);
  state.addEventListener('change', filter);
  reason.addEventListener('change', filter);
})();
</script>
</body>
</html>
''')


if __name__ == "__main__":
    raise SystemExit(main())
