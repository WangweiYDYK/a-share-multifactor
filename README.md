# A股低频多因子研究项目

当前阶段只做研究、统计和模拟回测，不连接真实交易账户。

代码约定见 [CODE_STYLE.md](./CODE_STYLE.md)，完整策略口径见
[A股多因子选股算法实现文档.md](./A股多因子选股算法实现文档.md)。

## Tushare Pro 数据 Demo

这个 demo 拉取三份最小数据：上交所交易日历、包含上市/退市/暂停上市状态的股票基础信息，以及指定交易日的全市场日线行情。输出位于 `data/raw/tushare_demo/`，该目录不会进入 Git。

安装：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

在当前 PowerShell 会话设置 Token，然后选择一个已经收盘且为交易日的日期：

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

