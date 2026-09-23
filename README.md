# A股低频多因子研究项目

当前阶段只做研究、统计和模拟回测，不连接真实交易账户。

代码约定见 [CODE_STYLE.md](./CODE_STYLE.md)，完整策略口径见
[A股多因子选股算法实现文档.md](./A股多因子选股算法实现文档.md)。

数据供应源先经过统一中间层：适配器返回 `RawDataset`，`CanonicalDataService`
将供应商字段转换为项目字段并执行主键、空数据和非负数检查，然后写入不可变快照。
每个快照位于 `data/normalized/snapshots/<snapshot_id>/`，清单记录每个数据集的
实际来源、数据源版本、时间范围和字段类型。后续因子代码只依赖统一字段。

## BaoStock 免费数据 Demo

BaoStock 无需 Token。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .

fetch-baostock-demo `
  --start-date 2025-06-01 `
  --end-date 2025-06-10 `
  --symbols 600000.SH 000001.SZ
```

也可以直接运行模块：

```powershell
python -m ashare_multifactor.data.baostock_demo `
  --start-date 2025-06-01 `
  --end-date 2025-06-10
```

默认快照根目录为 `data/normalized/snapshots/`，每次运行创建独立快照：

```text
<snapshot_id>/
  trade_calendar.csv
  security_master.csv
  daily_prices.csv
  daily_prices_review.csv
  manifest.json
```

`daily_prices.csv` 保持标准机器可读格式，前三列为 `trade_date, symbol, security_name`。`daily_prices_review.csv` 专供人工检查：第一行是英文技术字段，第二行是中文字段说明，第三行开始才是数据；程序、因子和回测不得读取检查版。

`security_name` 来自同一次运行拉取的证券基础信息，并按标准股票代码在中间层关联。证券状态统一为 `listed`、`delisted`、`paused` 或 `unknown`。

默认使用不复权价格。因子研究可以明确传入 `--adjustment forward` 或 `--adjustment backward`，但模拟成交和账户估值必须继续使用不复权价格。

BaoStock 日线没有返回精确发布时间，标准化层保守地将 `available_at` 设为交易日 18:00（Asia/Shanghai），并在运行清单中记录这是项目约定。PE、PB 等估值字段可用于探索，但在确认历史可见性和版本修订规则前，不进入正式无未来数据回测。

## Tushare Pro 数据 Demo

这个 demo 拉取三份最小数据：上交所交易日历、包含上市/退市/暂停上市状态的股票基础信息，以及指定交易日的全市场日线行情。它和 BaoStock 共用同一套标准化与快照落盘链路。

安装：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

将 `.env.example` 复制为不会提交的 `.env`，填入 Token：

```text
TUSHARE_TOKEN=你的_Tushare_Pro_Token
```

也可以在当前 PowerShell 会话设置 Token。然后选择一个已经收盘且为交易日的日期：

```powershell
$env:TUSHARE_TOKEN = "你的_Tushare_Pro_Token"
fetch-tushare-demo --trade-date 2026-09-21
```

也可以不安装命令行入口：

```powershell
python -m ashare_multifactor.data.tushare_demo --trade-date 2026-09-21
```

Tushare 快照与 BaoStock 使用相同的目录结构。Tushare 的 `vol` 和 `amount` 会在标准化层分别换算为股和元。`daily` 接口没有返回换手率、PE、PB、ST 状态时，相应统一字段保留为空，不用推测值填充。日线没有精确发布时间，因此也按交易日 18:00（Asia/Shanghai）这一项目约定写入 `available_at`。财务数据以后必须使用公告日或首次可见时间，不能沿用这个约定。

## 数据快照读取

`DataRepository` 默认读取最新快照，也可以固定 `snapshot_id` 保证可复现：

```python
from ashare_multifactor.data import DataRepository

repository = DataRepository("data/normalized/snapshots")
daily = repository.load(
    "daily_prices",
    as_of="2026-09-21",
    source="tushare_pro",
)

pinned = DataRepository(
    "data/normalized/snapshots",
    snapshot_id=daily.metadata["snapshot_id"],
)
```

`as_of` 只保留 `available_at <= as_of` 的行。只写日期的 `as_of` 按当天 23:59:59（Asia/Shanghai）处理；不带时区的日期时间按 Asia/Shanghai 处理。读取结果会在 `metadata` 中保留 `snapshot_id`、`source`、`source_version`、请求时间和实际可用行数。

## 月末历史股票池原型

先用离线合成数据跑通 `固定快照 -> DataRepository -> 月末筛选 -> 结果与清单`。
这不是实际历史股票池：股票代码、行情、行业和日历都是合成样例，其中日历仅按工作日生成，不能用于真实 A 股研究。

在仓库根目录运行，不需要 Token 或网络：

```powershell
$env:PYTHONPATH = "src"
python -m ashare_multifactor.data.universe_demo
```

每次运行创建 `artifacts/universe-demo/<run_id>/`，不会覆盖旧结果：

```text
snapshots/synthetic-universe-2025-08-v1/  六类标准数据及快照清单
result/eligible.json                    入池名单与依据
result/exclusions.json                  排除名单及逐股原因
result/manifest.json                    数据来源、版本、摘要、规则与配置
result/report.html                      可离线打开的可视化报告
```

直接在浏览器打开 `result/report.html`，查看入池比例、排除原因、可筛选逐股名单、
决策依据和数据来源版本。排除原因可重复计数，不等于排除股票数量；缺失值显示为缺失。
报告不依赖网络或服务端。页面只展示已保存结果，不重新运行筛选，也不代表收益回测。

旧运行目录也可以补生成报告（已有报告不会被覆盖）：

```powershell
$env:PYTHONPATH = "src"
python -m ashare_multifactor.reports.universe artifacts/universe-demo/<run_id>/result
```

样例月份为 `2025-08`，按样例日历在 `2025-08-29 18:00:00+08:00` 决策。
预期 12 只候选中 1 只入池、11 只排除。演示阈值为上市至少 120 个交易日、
近 20 个交易日日均成交额至少 5,000 元；这些只是样例配置，不是正式策略参数。
排除原因覆盖 ST、停牌、退市、上市不足、缺失市值、低流动性、缺失行业、
行情缺口和未知状态。决策之后才可见的更正不会影响当时结果。

真实快照准备好后可复用同一构建器：

```python
from pathlib import Path
from ashare_multifactor.data import DataRepository
from ashare_multifactor.data.universe import UniverseConfig, build_universe_from_repository

result = build_universe_from_repository(
    DataRepository(Path("data/normalized/snapshots"), snapshot_id="your-fixed-snapshot-id"),
    "2025-08",
    config=UniverseConfig(min_average_amount=50_000_000, industry_system="your-industry-system"),
)
```

输入必须包含 `trade_calendar`、`security_master`、`daily_prices`、`daily_basic`、
`security_status`、`industry_membership`，每行都有带时区的 `available_at` 及来源版本。
日历须覆盖已知最早上市日至目标月末，日线使用不复权成交额；市值、成交额统一为元。
状态必须是决策日的历史状态，行业区间采用 `[effective_from, effective_to)`，
不能用当前 ST、当前行业或仅包含现存股票的名单代替。整类关键数据缺失会阻止构建，
单只股票缺失则记录排除原因，不静默补零。

现有 BaoStock/Tushare demo 尚未补齐上述六类历史数据，也未确认新增数据的历史可见性，
因此不能直接用它们生成可信的全市场历史股票池。本原型不计算因子、不评估财务过滤，
也不判断下一交易日能否实际成交；QMT 与执行层继续暂缓。

定向验证命令（会在仓库内保留一份合成结果）：

```powershell
$env:PYTHONPATH = "src"
python -m unittest discover -s tests -p test_universe.py -v
```
