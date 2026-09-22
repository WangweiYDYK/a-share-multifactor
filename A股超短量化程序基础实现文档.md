# A 股超短量化程序基础实现文档

> 目标：建立一个可复现的 A 股超短研究与模拟交易程序。第一阶段只做“盘后选股 -> 次日模拟执行 -> 结果复盘”，不接实盘、不使用未来数据、不把模型预测当作交易事实。
>
> 本文按 TqSdk 技能包中的约束编写。TqSdk 用于行情、历史数据、股票模拟/回测和交易接口；涨停事件、题材、龙虎榜、公告等信息可能需要其他数据源补充。

## 1. 验收边界

第一版完成后，程序应能：

- 指定一个历史区间和股票池；
- 按交易时点可获得的数据计算市场状态；
- 生成候选股票及入选理由；
- 按预先写定的买入、卖出、仓位和止损规则进行模拟；
- 正确处理 T+1、涨跌停、停牌、现金和仓位限制；
- 输出每日交易记录、净值、收益、最大回撤和未成交原因；
- 保存输入数据版本、策略参数和运行时间，保证结果可以复现。

第一版不包含：

- 自动实盘下单；
- 机器学习或大语言模型直接决定买卖；
- Level-2 全量盘口；
- 只根据龙虎榜或网络“大师语录”生成交易信号；
- 使用未来收盘结果反推盘中成交。

## 2. 推荐的最小纵向切片

先固定一种简单、可验证的事件策略，不要同时实现多个游资体系。建议从以下方向中选一个：

```text
事件：前一交易日涨停或强势放量
信号：当日收盘后筛选，次日开盘或开盘后确认
持有：至少跨过买入日，符合 T+1 规则
退出：次日收盘、止损或预设止盈
```

也可以先从“昨日涨停股次日溢价统计”开始，不下单，只计算：

```text
昨日涨停股票 -> 次日开盘收益、最高收益、收盘收益、最大回撤
```

这个统计能先验证数据链路、标签定义和市场环境指标，再进入交易模拟。

## 3. 系统分层

```text
数据源
  -> 原始数据层
  -> 标准化数据层
  -> 事件与特征层
  -> 策略信号层
  -> 组合与风控层
  -> 撮合/账户层
  -> 绩效与复盘层
```

建议目录：

```text
ashort_quant/
  README.md
  pyproject.toml
  config/
    strategy.toml
  src/
    data/
      sources.py
      normalize.py
      quality.py
    features/
      market_breadth.py
      limit_events.py
      labels.py
    strategy/
      base.py
      yesterday_limitup.py
    portfolio/
      risk.py
      constraints.py
    execution/
      simulator.py
      tqsdk_adapter.py
    reports/
      daily_report.py
  scripts/
    download_data.py
    build_features.py
    run_backtest.py
  data/
    raw/
    normalized/
    derived/
  reports/
```

第一版也可以先压缩为 4 个模块：

```text
data.py       下载与标准化
features.py   市场指标、涨停事件、标签
strategy.py   选股与交易规则
backtest.py   撮合、账户、报告
```

## 4. 数据方案

### 4.1 主数据

至少准备：

```text
交易日历
股票基础信息
上市与退市日期
ST 状态和停牌状态
日线 OHLCV
成交额、换手率、流通市值
1 分钟行情
指数行情
```

### 4.2 超短事件数据

研究首板、连板、回封和炸板时，还需要：

```text
首次触板时间
开板次数
开板总时长
最终是否封板
最终封板时间
封板时成交额或可用的封单字段
触板后的最大回落
连板高度
次日开盘、最高、最低、收盘
```

如果数据源不能直接给出这些字段，可以从分钟行情加工。加工规则要固定并版本化。

### 4.3 市场状态

每日保存：

```text
上涨家数、下跌家数
涨停家数、跌停家数、炸板家数
最高连板高度
首板数量、二板数量及晋级率
昨日涨停股次日平均溢价
高位股负反馈数量
全市场成交额
主要指数涨跌幅
```

“冰点、修复、高潮、分歧、退潮”等名称只能作为分类标签，不能代替原始数值。先保存数值，再根据固定规则映射市场阶段。

## 5. TqSdk 在项目中的位置

### 5.1 只读行情

TqSdk 的数据对象是持续更新的引用。订阅一次后，在 `wait_update()` 中推进，不要在循环里反复订阅，也不要用 `sleep()` 等待数据。

```python
from tqsdk import TqApi, TqAuth

# 股票合约代码应先通过 query_quotes/query_symbol_info 核实，不要猜测代码格式。
SYMBOL = "已核实的股票合约代码"

with TqApi(auth=TqAuth("快期账户", "账户密码")) as api:
    quote = api.get_quote(SYMBOL)
    klines = api.get_kline_serial(SYMBOL, 60, data_length=240)

    while True:
        api.wait_update()
        if api.is_changing(klines.iloc[-1], "datetime"):
            # iloc[-1] 是正在形成的 K 线；收盘信号使用 iloc[-2]。
            closed = klines.iloc[-2]
            print(closed.datetime, closed.close, quote.last_price)
```

真实项目中不要把账户密码写进源文件。使用环境变量或项目已有的安全配置。上面的代码只是接口结构示例，不代表已经连接成功或获得数据权限。

### 5.2 历史回测和模拟

TqSdk 技能包规定：

- 本地股票模拟或股票回测使用 `TqSimStock()`；
- 历史回测使用 `TqBacktest(...)` 配合 `TqSimStock()`；
- 股票交易不使用期货的 `offset`；
- 股票不使用 `TargetPosTask`；
- 回测结束要处理 `BacktestFinished`；
- 没有认证或数据权限时，只能报告已完成的离线部分，不能声称行情或回测已经成功运行。

伪代码结构：

```python
from datetime import date
from tqsdk import TqApi, TqAuth, TqBacktest, TqSimStock, BacktestFinished

api = TqApi(
    TqSimStock(),
    backtest=TqBacktest(
        start_dt=date(2025, 1, 1),
        end_dt=date(2025, 12, 31),
    ),
    auth=TqAuth("快期账户", "账户密码"),
)

try:
    # 订阅行情，创建股票策略和账户风控。
    # 股票下单不传 futures offset，也不使用 TargetPosTask。
    while True:
        api.wait_update()
        # 只在已完成的分钟线或明确的行情事件更新后计算信号。
except BacktestFinished:
    print("历史回测结束")
finally:
    api.close()
```

这段结构不是完整策略，日期、股票代码、数据权限和账户参数必须按实际环境核实。回测结果只有在程序真实运行并输出结果后才能报告。

### 5.3 长区间导出

如果目标是导出长时间的 CSV 行情，而不是执行历史策略，应考虑 `tqsdk.tools.DataDownloader`。技能资料说明：`dur_sec=0` 表示 Tick 下载，长区间下载可能需要付费或相应权限。

## 6. 时间切片与防未来数据

所有特征都要标记：

```text
trade_date
as_of_time
available_at
source
source_version
```

例如：

```text
09:31 的分钟 K 线不能用于 09:30 的决策
收盘后的龙虎榜只能用于下一个交易日
盘后公告按市场首次可见时间切分
次日最高价只能作为结果标签，不能作为当日特征
```

标签和特征分开存储：

```text
features_2025-01-02.parquet
labels_2025-01-02.parquet
```

训练或回测时，任何特征时间必须早于信号时间；任何结果标签必须晚于成交时间。

## 7. 策略接口

策略不直接访问数据库细节，也不直接操纵账户。先定义稳定接口：

```python
from dataclasses import dataclass
from datetime import datetime

@dataclass(frozen=True)
class Signal:
    trade_date: str
    symbol: str
    action: str          # BUY / HOLD / SKIP
    score: float
    reason: str
    invalidation: str


def generate_signals(as_of: datetime, features) -> list[Signal]:
    """只使用 as_of 之前可获得的特征生成信号。"""
    signals = []
    for row in features:
        if row["market_stage"] not in {"修复", "发酵"}:
            continue
        if row["is_yesterday_limit_up"] and row["turnover_rate"] >= 3:
            signals.append(
                Signal(
                    trade_date=str(as_of.date()),
                    symbol=row["symbol"],
                    action="BUY",
                    score=float(row["score"]),
                    reason="昨日涨停且满足换手条件",
                    invalidation="开盘后跌破预设失效价或无法成交",
                )
            )
    return signals
```

这里的条件只是工程示例，不是投资建议。真实策略必须使用历史数据做参数固定，并保留“不交易”的结果。

## 8. 组合和 A 股交易约束

风控先于信号：

```text
单票最大仓位
组合最大持仓数
单日最大亏损
单笔风险预算
总现金保留比例
连续亏损后的暂停规则
异常行情下的熔断/停机规则
```

A 股约束必须进入撮合器，而不是只写在说明里：

```text
T+1：买入日不能按普通规则当天卖出
涨停买不到或部分成交
跌停卖不出或部分成交
停牌不能成交
ST 和特殊股票涨跌幅规则不同
交易费用和印花税
滑点和成交量上限
集合竞价和连续竞价时段
```

每个订单至少记录：

```text
signal_time
order_time
fill_time
symbol
side
requested_price
filled_price
requested_volume
filled_volume
status
reject_reason
unfilled_reason
```

不要把“订单已提交”当成“已经成交”。TqSdk 中 `insert_order()` 的请求要在后续 `wait_update()` 后推进，`FINISHED` 也可能表示成交、撤单或拒单，需要结合 `volume_left`、`last_msg` 和成交记录判断。

## 9. 回测结果

第一版报告至少包括：

```text
总收益
年化收益（仅在样本足够时）
最大回撤
胜率
盈亏比
交易次数
平均持仓时间
单笔收益分布
连续亏损次数
成交率
未成交原因
按市场阶段的收益
按股票流动性分组的收益
```

不要只输出收益曲线。超短策略最需要知道：

```text
信号是否真的可成交？
收益是否依赖少数几笔？
涨停买不到和跌停卖不出是否被正确计算？
手续费、滑点和冲击成本加入后是否仍成立？
策略是否只在某一段行情有效？
```

训练、调参和验证按时间切分：

```text
开发区间：确定规则和参数
验证区间：冻结规则后检查稳健性
观察区间：完全不参与调参
```

不要随机打乱时间序列，也不要用验证区间反复改参数。

## 10. 推荐实现顺序

### 阶段 1：数据和统计

- 建交易日历、股票状态和日线表；
- 计算涨跌家数、涨停家数和昨日涨停溢价；
- 输出每日候选股；
- 不下单，只检查数据质量和时间切分。

### 阶段 2：分钟级事件

- 为候选股票补充 1 分钟数据；
- 加工首次触板、炸板、回封和次日表现；
- 固定交易规则和标签定义；
- 加入交易费用、滑点和成交限制。

### 阶段 3：本地股票回测

- 使用 `TqSimStock()` 与 `TqBacktest`；
- 股票订单单独实现，不套用期货 `TargetPosTask`；
- 输出逐笔成交、持仓变化和每日净值；
- 只做一次与改动风险匹配的定向回测。

### 阶段 4：仿真运行

- 使用本地股票模拟或符合账户要求的快期股票模拟；
- 先只读行情和生成信号；
- 再验证订单状态、部分成交、撤单和异常恢复；
- 连续观察一段时间后再评估是否需要实盘权限。

### 阶段 5：实盘前检查

实盘不是回测的开关。至少确认：

```text
账户类型和授权正确
股票代码已核实
订单价格、数量和方向正确
T+1 和涨跌停逻辑已验证
断线、重连、重复下单和进程重启可处理
实盘日志不泄露凭据
有明确的一键停机和每日最大亏损限制
```

没有用户明确授权和经过仿真验证，不执行真实下单或撤单。

## 11. “蒸馏”或 AI 的正确位置

如果后续想把交易者经验蒸馏到程序里，建议只让模型做以下工作：

```text
整理原始资料
提取候选规则
解释某次信号为什么入选
生成盘后复盘
发现规则与实际执行的差异
```

核心买卖规则仍应由可回测的结构化字段执行：

```text
市场状态
题材强弱
涨停事件
成交量和换手率
可成交性
风险预算
失效条件
```

不要直接训练“今天该买哪只股票”的黑箱模型，也不要把事后复盘、成功截图和网络语录当作可靠交易标签。

## 12. 开始前的最小清单

```text
[ ] 确定数据源和使用权限
[ ] 确定股票代码格式并通过 TqSdk 查询核实
[ ] 确定信号时点和成交时点
[ ] 确定一套简单策略，不混合多个大师体系
[ ] 确定 T+1、涨跌停、停牌和费用规则
[ ] 确定开发、验证、观察时间区间
[ ] 先跑“昨日涨停次日溢价”统计
[ ] 再跑本地股票回测
[ ] 没有仿真验证前不接实盘
```

## 13. 结论

最小可行的 A 股超短量化程序不是“接上 TqSdk 就自动交易”，而是：

```text
可追溯数据
+ 不泄漏的特征
+ 一套明确事件策略
+ A 股真实交易约束
+ 可解释的撮合和风控
+ 按时间切分的验证
```

TqSdk 可以承担行情、历史下载、股票模拟和回测接口，但完整的 A 股超短研究库还需要补充涨停事件、题材、龙虎榜、公告和市场宽度等数据。先完成盘后候选和次日模拟这一条纵向链路，再扩展模型和盘口数据。
