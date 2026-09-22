# A股低频多因子研究项目

当前阶段只做研究、统计和模拟回测，不连接真实交易账户。

代码约定见 [CODE_STYLE.md](./CODE_STYLE.md)，完整策略口径见
[A股多因子选股算法实现文档.md](./A股多因子选股算法实现文档.md)。

## BaoStock 免费数据 Demo

BaoStock 无需 Token。数据先经过统一中间层，将供应商字段转换为项目字段并执行主键、空数据和非负数检查，然后才写入本地。后续因子代码只依赖统一字段。

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

输出位于 `data/normalized/baostock_demo/`：

```text
trade_calendar.csv
security_master.csv
daily_prices.csv
manifest.json
```

`daily_prices.csv` 的前三列固定为 `symbol, security_name, trade_date`，便于打开文件后立即核对代码、中文名称和日期。`security_name` 来自同一次运行拉取的证券基础信息，并按标准股票代码在中间层关联；`security_master.csv` 同时保留名称、上市日期、退市日期、证券类型和上市状态。

默认使用不复权价格。因子研究可以明确传入 `--adjustment forward` 或 `--adjustment backward`，但模拟成交和账户估值必须继续使用不复权价格。

BaoStock 日线没有返回精确发布时间，标准化层保守地将 `available_at` 设为交易日 18:00（Asia/Shanghai），并在运行清单中记录这是项目约定。PE、PB 等估值字段可用于探索，但在确认历史可见性和版本修订规则前，不进入正式无未来数据回测。

## Tushare Pro 数据 Demo

这个 demo 拉取三份最小数据：上交所交易日历、包含上市/退市/暂停上市状态的股票基础信息，以及指定交易日的全市场日线行情。输出位于 `data/raw/tushare_demo/`，该目录不会进入 Git。

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

生成文件：

```text
data/raw/tushare_demo/trade_calendar.csv
data/raw/tushare_demo/stock_basic.csv
data/raw/tushare_demo/daily.csv
data/raw/tushare_demo/manifest.json
```

`daily.available_at` 暂按当日 15:30（Asia/Shanghai）处理，方便演示日线时间约束；这不是 Tushare 提供的精确发布时间。财务数据以后必须使用公告日或首次可见时间，不能沿用这个约定。
