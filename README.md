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
