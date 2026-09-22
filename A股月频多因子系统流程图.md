# A 股月频多因子系统流程图

> 本文是《A 股月频多因子选股与模拟回测实现文档》的配套流程图集。
>
> 图集只展示系统边界、时间关系、状态转移和模块流向，不替代主文档中的规则定义。名词、标签口径和执行约束以主文档为准。
>
> 当前范围是月频决策、月内每日账户处理、模拟撮合和成本后研究回测，不接实盘，不执行真实下单。

## 0. 阅读方法

图中使用三类时间：

```text
as_of_time            可以做出当前决策的时间点
available_at          数据首次可以被策略使用的时间
label_available_at    未来收益标签已经完整实现、可以用于评价或训练的时间
```

最重要的约束只有一条：

```text
信号层只能看到 available_at <= as_of_time 的数据。
执行层只能看到 execution_date 当天实际可用的行情和状态。
未来收益、未来成交结果和未来标签不能反向决定已经发生的决策。
```

一条完整的月度链路是：

```text
月末决策
  -> 下一交易日执行
  -> 月内每日撮合并估值
  -> 下一次月末决策
  -> 本期标签完整结束后，才允许进入后续 IC、训练或评价
```

## 1. 系统总架构

```mermaid
flowchart TB
    subgraph DATA["数据与版本层"]
        RAW["原始行情、财务、状态、行业、指数和公司行动"]
        PIT["按 available_at 构建 point-in-time 快照"]
        DQ["数据质量检查和版本登记"]
        SNAP["as_of_time 决策快照"]
        RAW --> PIT --> DQ --> SNAP
    end

    subgraph SIGNAL["研究、因子和信号层"]
        UNIVERSE["历史股票池"]
        FACTOR["因子计算"]
        PREPROCESS["逐日横截面预处理"]
        WEIGHT["综合分权重"]
        SCORE["综合评分与排名"]
        TARGET["目标组合和目标权重"]
        UNIVERSE --> FACTOR --> PREPROCESS --> SCORE
        WEIGHT --> SCORE
        SCORE --> TARGET
    end

    subgraph EXECUTION["执行与账户层"]
        ORDERS["订单意图"]
        MATCH["A 股规则撮合"]
        FILLS["成交、部分成交、未成交"]
        ACCOUNT["现金、实际持仓、可卖数量、待成交订单"]
        ACTION["公司行动和每日结算"]
        VALUATION["每日收盘估值和风险暴露"]
        ORDERS --> MATCH --> FILLS --> ACCOUNT
        ACTION --> ACCOUNT
        ACCOUNT --> VALUATION
    end

    subgraph EVALUATION["标签、评价和反馈层"]
        LABEL["月频总收益标签"]
        FACTOR_EVAL["IC、RankIC、分位收益和因子稳定性"]
        PORTFOLIO_EVAL["实际账户收益、回撤、换手和未成交复盘"]
        REPORT["运行报告和版本清单"]
        LABEL --> FACTOR_EVAL
        VALUATION --> PORTFOLIO_EVAL
        FACTOR_EVAL --> REPORT
        PORTFOLIO_EVAL --> REPORT
    end

    SNAP --> UNIVERSE
    TARGET --> ORDERS
    ACCOUNT --> TARGET
    SNAP -. 标签区间结束后生成已完成样本 .-> LABEL
    FACTOR_EVAL -. 只使用已经完成的标签样本 .-> WEIGHT
    REPORT -. 需要新的合法 as_of_time 后才允许调整策略 .-> SNAP
```

图中的反馈不是“把评价结果塞回本期决策”。评价结果只能在之后的决策时点使用，并且必须满足 `label_available_at <= 新的 as_of_time`。

## 2. 时间对齐和防未来数据

```mermaid
flowchart LR
    subgraph FEATURE["特征和因子的可见性"]
        F1["数据事件发生"]
        F2["记录首次可见时间<br/>available_at"]
        F3{"available_at ≤ as_of_time?"}
        F4["允许进入当前决策快照"]
        F5["禁止进入当前决策"]
        F1 --> F2 --> F3
        F3 -->|是| F4
        F3 -->|否| F5
    end

    subgraph LABEL["标签的可见性"]
        L1["第 t 次决策<br/>decision_date_t"]
        L2["第 t 次计划执行<br/>entry_execution_date_t"]
        L3["第 t+1 次决策和执行<br/>exit_execution_date_t+1"]
        L4["行情和公司行动完整<br/>label_available_at"]
        L5["允许用于 IC、训练和评价"]
        L1 --> L2 --> L3 --> L4 --> L5
    end

    F5 -. 未来数据不能改变已发生决策 .-> L1
    L5 -. 只能用于未来的 as_of_time .-> F4
```

同一个真实时间轴上，行情“已经发生”不等于策略“已经可见”，标签“已经产生”也不等于标签“已经完整”。系统必须分别保存 `available_at` 和 `label_available_at`。

## 3. 月末决策和月内持有的完整时间线

```mermaid
sequenceDiagram
    autonumber
    participant D as 决策层
    participant P as 组合构建层
    participant A as 账户层
    participant M as 撮合层
    participant V as 估值层

    Note over D: 月末最后一个交易日 T 收盘后
    D->>D: 按 as_of_time 截断数据并构建历史快照
    D->>P: 历史股票池、因子、预处理和综合分
    P->>A: 目标组合和目标权重
    A->>A: 比较目标与实际持仓、现金和待成交订单
    A->>M: 次日订单意图

    Note over M: 下一交易日 T+1 执行窗口
    alt 可成交
        M-->>A: 成交或部分成交
    else 不可成交
        M-->>A: 未成交并记录停牌、涨跌停、现金或数量原因
    end

    loop T+1 到下一计划决策日之间
        A->>V: 现金、持仓、可卖数量、订单和公司行动
        V->>V: 每日收盘估值、净值和风险暴露
        V-->>A: 每日账户快照
    end

    Note over D,V: 下一计划决策日重新生成目标，旧订单先按过期规则处理
```

月频策略不代表股票固定持有 20 个交易日。两次决策之间持续持有，下一期重新评分；如果再次入选，可以继续持有。

## 4. 数据接入和质量门禁

```mermaid
flowchart TB
    SOURCES["交易日历、证券状态、日线、财务、行业、指数和公司行动"]
    KEY["统一主键、交易日期、公告日期和首次可见时间"]
    PIT["构造 available_at 版本"]
    CHECK["质量检查"]
    OK{"全部通过?"}
    SNAPSHOT["写入 as_of_time 数据快照"]
    FAIL["停止运行，或按明确规则排除并记录影响范围"]
    FORBIDDEN["禁止统一填 0、静默删除或用报告期回填"]

    SOURCES --> KEY --> PIT --> CHECK --> OK
    OK -->|是| SNAPSHOT
    OK -->|否| FAIL
    FAIL --> FORBIDDEN
```

质量门禁至少覆盖：

```text
主键唯一
交易日连续
价格、成交额和市值非负且无明显异常
复权因子和公司行动能对齐
停牌、退市、ST 和板块状态没有缺口
行业和指数成分保留历史版本
财务数据没有未来公告日
因子覆盖率和股票池数量没有异常跳变
```

## 5. 股票池构建和已有持仓处理

```mermaid
flowchart TB
    SNAP["决策时点快照"]
    KNOWN{"决策时点已知的过滤条件"}
    OUT["剔除 ST、退市整理、停牌、上市不足和低流动性证券"]
    DATA["剔除关键财务、市值或行业字段严重缺失的证券"]
    UNIVERSE["决策时点历史股票池"]
    CANDIDATES["本期新目标候选"]

    SNAP --> KNOWN
    KNOWN --> OUT --> DATA --> UNIVERSE --> CANDIDATES

    FUTURE["次日停牌、次日封板或次日无法成交"]
    FORBID["信号层禁止使用，必须在撮合层记录"]
    FUTURE --> FORBID
    classDef forbidden fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    class FUTURE,FORBID forbidden

    HOLD["当前真实持仓"]
    INUNIV{"仍在股票池?"}
    KEEP["按本期评分和排名处理"]
    EXIT["在可交易时生成卖出目标"]
    BLOCKED["无法成交则继续持有，占用资金和风险额度"]
    ACC["账户估值必须覆盖所有实际持仓"]

    HOLD --> INUNIV
    INUNIV -->|是| KEEP
    INUNIV -->|否| EXIT
    EXIT -->|卖出失败| BLOCKED
    HOLD --> ACC
    BLOCKED --> ACC
```

股票池只影响“哪些股票可以被选为目标”和“哪些股票可以生成退出目标”，不会让真实账户中的持仓凭空消失。次日停牌、封板和无法成交只能由撮合层产生结果。

## 6. 因子计算和横截面预处理

```mermaid
flowchart LR
    RAW["原始因子值"]
    TRUNCATE["按 available_at 截断"]
    UNIVERSE["限制在决策时点股票池"]
    MISSING["缺失值处理"]
    OUTLIER["去极值"]
    DIRECTION["统一为数值越大越好"]
    STANDARD["标准化"]
    NEUTRAL{"是否启用中性化?"}
    NEUTRAL_YES["行业、市值或 beta 中性化"]
    PROCESSED["可进入综合分的因子快照"]

    RAW --> TRUNCATE --> UNIVERSE --> MISSING --> OUTLIER --> DIRECTION --> STANDARD --> NEUTRAL
    NEUTRAL -->|是| NEUTRAL_YES --> PROCESSED
    NEUTRAL -->|否| PROCESSED

    MISSING -. 个股缺失过多 .-> DROP["剔除并记录原因"]
    MISSING -. 因子覆盖率过低 .-> SKIP["跳过该因子并记录"]
    MISSING -. 少量缺失 .-> FILL["行业内中位数填充并保留 missing_flag"]
    FILL --> OUTLIER
```

预处理必须逐交易日进行，不能把不同日期的股票混在同一个横截面里。缺失、极值、方向和中性化规则一旦进入正式实验，就必须版本化并冻结。

## 7. 综合评分和 IC 动态权重

```mermaid
flowchart TB
    FACTORS["已预处理因子"]
    MODE{"综合分方法"}

    EQUAL["第一版：类别等权"]
    SCORE1["综合评分"]
    RANK1["横截面排名"]
    FACTORS --> MODE
    MODE -->|category_equal_weight| EQUAL --> SCORE1 --> RANK1

    LABEL["历史月频总收益标签"]
    READY{"label_available_at ≤ 当前 as_of_time?"}
    VALID["进入已完成样本窗口"]
    INVALID["不可用于本期权重"]
    IC["计算 IC、RankIC 和 ICIR"]
    DIRECTION{"方向和处理规则已确认?"}
    NORMALIZE["归一化为非负权重"]
    SCORE2["综合评分"]
    RANK2["横截面排名"]

    FACTORS -->|ic_weight| IC
    LABEL --> READY
    READY -->|是| VALID --> IC
    READY -->|否| INVALID
    IC --> DIRECTION
    DIRECTION -->|是| NORMALIZE --> SCORE2 --> RANK2
    DIRECTION -->|否| REVIEW["先检查方向、预处理和标签口径，不直接给负权重"]
    REVIEW --> NORMALIZE
```

第一版默认使用类别等权，不启用 IC 加权、机器学习或因子择时。后续启用 IC 加权时，只能使用标签区间已经结束的历史样本；固定未来 20 日收益只能做辅助诊断，不能冒充月频调仓标签。

## 8. 目标组合、缓冲和约束

```mermaid
flowchart TB
    SCORE["综合评分和排名"]
    BUFFER{"是否启用缓冲?"}
    TOP["第一版无缓冲<br/>股票池内排名前 30 名"]
    OLD["旧持仓处理"]
    KEEP30["排名 ≤ 30：继续作为目标"]
    KEEP31["排名 31 到 45：保留，不主动卖"]
    SELL45["排名 > 45：不再作为目标，尝试卖出"]
    NONOLD["非旧持仓<br/>排名 ≤ 30 才新增"]
    TARGET0["目标组合和目标权重"]
    CONSTRAINTS["单票、行业、因子暴露、流动性、现金和换手上限"]
    FEASIBLE{"目标是否可执行?"}
    TARGET1["输出目标组合"]
    CASH["允许保留现金或部分调整，记录未分配原因"]

    SCORE --> BUFFER
    BUFFER -->|否| TOP --> TARGET0
    BUFFER -->|是| OLD
    OLD --> KEEP30 --> TARGET0
    OLD --> KEEP31 --> TARGET0
    OLD --> SELL45 --> TARGET0
    BUFFER -->|是| NONOLD
    NONOLD --> TARGET0
    TARGET0 --> CONSTRAINTS --> FEASIBLE
    FEASIBLE -->|是| TARGET1
    FEASIBLE -->|否| CASH --> TARGET1
```

矩阵中的“前 30 名”和“排名 31 到 45”必须使用同一个排名尺度。换手上限默认按单边口径；首次建仓是否豁免是独立配置，不能把目标权重当作必然实现的真实权重。

## 9. 信号到订单和 A 股撮合

```mermaid
flowchart LR
    TARGET["目标权重"]
    SHARES["目标股数并按最小交易单位取整"]
    DELTA["与实际持仓比较"]
    SELLS["先处理卖出和减少仓位"]
    BUYS["再处理买入和增加仓位"]
    LIMITS["现金、可卖数量、T+1、换手和交易规则"]
    INTENT["订单意图"]
    NEXT["下一交易日执行窗口"]
    AVAILABLE["只使用执行日当天可用的行情和状态"]
    RULES["停牌、涨跌停、成交量上限、费用和滑点"]
    RESULT["成交、部分成交、未成交或拒绝"]
    ACCOUNT["更新账户和订单状态"]

    TARGET --> SHARES --> DELTA --> SELLS --> BUYS --> LIMITS --> INTENT
    INTENT --> NEXT --> AVAILABLE --> RULES --> RESULT --> ACCOUNT

    FUTURE["当天最高价、最低价或收盘价反推的成交结果"]
    FORBID["禁止进入当时是否参与的决定"]
    FUTURE --> FORBID
    classDef forbidden fill:#fee2e2,stroke:#dc2626,color:#7f1d1d
    class FUTURE,FORBID forbidden
```

执行价格和成交规则必须在看到当天完整行情之前写死。日线级回测只能表达“开盘参考价和当日可交易状态”的近似撮合，不能假装知道盘中最高价和最低价。

## 10. 订单状态机

```mermaid
stateDiagram-v2
    state "待成交" as Pending
    state "部分成交" as PartiallyFilled
    state "已成交" as Filled
    state "已撤销" as Cancelled
    state "已过期" as Expired
    state "已拒绝" as Rejected

    [*] --> Pending
    Pending --> PartiallyFilled: 可成交量小于委托量
    Pending --> Filled: 一次全部成交
    Pending --> Cancelled: 主动撤单
    Pending --> Expired: 有效期结束且未成交
    Pending --> Rejected: 参数、账户或规则校验失败
    PartiallyFilled --> Filled: 剩余部分全部成交
    PartiallyFilled --> Cancelled: 撤销剩余部分
    PartiallyFilled --> Expired: 有效期结束仍有剩余
    Filled --> [*]
    Cancelled --> [*]
    Expired --> [*]
    Rejected --> [*]
```

每笔订单至少记录：

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

未成交订单不能无限重试。默认在下一个计划调仓日重新评估，月内重试必须由单独配置明确控制。

## 11. 账户、公司行动和每日估值闭环

```mermaid
flowchart TB
    DAY["交易日开始"]
    PREOPEN["处理生效日期到达的公司行动"]
    STATE["载入现金、实际持仓、可卖数量和待成交订单"]
    EXECUTE["执行窗口：撮合、费用、滑点和部分成交"]
    UPDATE["更新现金、股数、可卖数量和已实现盈亏"]
    CLOSE["收盘后使用原始价格估值"]
    EQUITY["总资产 = 现金 + 全部持仓市值"]
    RISK["更新风险暴露、换手和未成交原因"]
    SNAPSHOT["保存每日账户快照和净值"]
    NEXT{"还有下一个交易日?"}
    DECISION["进入下一次月末决策"]

    DAY --> PREOPEN --> STATE --> EXECUTE --> UPDATE --> CLOSE --> EQUITY --> RISK --> SNAPSHOT --> NEXT
    NEXT -->|是| DAY
    NEXT -->|否| DECISION

    DIV["现金分红：增加现金"]
    SHARE["送股、转增、拆分和合并：更新股数"]
    RIGHTS["配股和退市：按历史规则处理并保留记录"]
    PREOPEN --> DIV
    PREOPEN --> SHARE
    PREOPEN --> RIGHTS
```

公司行动必须真正更新账户，不能只存在于数据清单中。因子使用明确的复权价格；成交和账户估值使用原始价格；分红送转不能再通过复权价格重复计算一次收益。

## 12. 月频标签、IC 和组合复盘

```mermaid
flowchart TB
    subgraph LABEL_PATH["月频主标签"]
        D["decision_date_t"]
        E["entry_execution_date_t 的计划入场参考价或实际成交价"]
        X["下一计划执行时点 exit_execution_date_t+1"]
        RETURN["计划执行时点之间的含分红总收益"]
        AVAILABLE["label_available_at"]
        D --> E --> X --> RETURN --> AVAILABLE
    end

    subgraph AUX["辅助诊断标签"]
        A1["下一交易日开盘到收盘"]
        A2["固定未来 20 个交易日"]
        A3["下一执行时点到下一决策日收盘"]
    end

    subgraph EVAL["评价输出"]
        IC["IC、RankIC、ICIR、t 统计量和衰减"]
        QUANTILE["分位组合、单调性和因子换手"]
        PORT["实际账户成本后收益、回撤、换手和成交率"]
        REASON["未成交原因、持仓时间和风险暴露"]
    end

    AVAILABLE --> IC
    AVAILABLE --> QUANTILE
    RETURN --> PORT
    A1 -. 只做辅助诊断 .-> IC
    A2 -. 只做辅助诊断 .-> IC
    A3 -. 只做辅助诊断 .-> IC
```

因子评价必须使用月频主标签或明确标注的辅助标签；组合收益必须来自真实账户和实际成交，不能假设目标权重一定成交。未来收益只用于标签和评价，不进入信号层。

## 13. 开发、验证和最终观察流程

```mermaid
flowchart TB
    HYPOTHESIS["明确假设、数据版本、规则版本和验收指标"]
    DEV["开发区间：定义因子、方向和候选参数"]
    FREEZE1{"规则是否冻结?"}
    VALID["验证区间：冻结规则后检查稳健性"]
    CHANGED{"验证后是否修改了参数或规则?"}
    REVISE["回到开发区间，更新版本并重新验证<br/>原验证结果不再作为独立证据"]
    FINAL_FREEZE["冻结最终版本"]
    HOLDOUT["最终观察区间：只检查一次，不参与调参"]
    REPORT["按开发、验证、最终观察分别报告"]

    HYPOTHESIS --> DEV --> FREEZE1
    FREEZE1 -->|否| DEV
    FREEZE1 -->|是| VALID --> CHANGED
    CHANGED -->|是| REVISE --> VALID
    CHANGED -->|否| FINAL_FREEZE --> HOLDOUT --> REPORT
```

不允许用最终观察区间反复挑选参数、股票池或成本假设。成本提高、换手增加或样本缩短后结论明显不稳定时，应先回到数据、因子和执行问题，而不是继续增加模型复杂度。

## 14. 从工程基线到后续策略的路线

```mermaid
flowchart LR
    G0["工程门禁<br/>时间对齐、历史股票池、账户、撮合和公司行动"]
    G1{"工程门禁通过?"}
    BASE["第一版基线<br/>20 日反转、60 日动量、60 日低波"]
    CEW["类别等权、前 30 名、无缓冲、等权"]
    CHECK1{"成本后结果和无未来数据检查通过?"}

    RESEARCH["月频研究主线<br/>估值、质量、成长、中期动量、低波"]
    SINGLE["先做单因子和类别内部等权"]
    STABLE{"单因子稳定且相关性可解释?"}
    COMPOSITE["类别等权综合分和分位组合"]

    ENHANCE["后续增强"]
    BUFFER["排名缓冲和目标数量控制"]
    RISK["风险约束和市场状态检查"]
    ICW["已完成标签的 IC 加权"]
    ML["机器学习排序"]
    GATE{"复杂度是否带来可复现的增益?"}
    REPORT["保留简单基线并记录失败实验"]

    G0 --> G1
    G1 -->|否| G0
    G1 -->|是| BASE --> CEW --> CHECK1
    CHECK1 -->|否| BASE
    CHECK1 -->|是| RESEARCH --> SINGLE --> STABLE
    STABLE -->|否| REPORT
    STABLE -->|是| COMPOSITE --> ENHANCE
    ENHANCE --> BUFFER --> RISK --> ICW --> ML --> GATE
    GATE -->|否| REPORT
    GATE -->|是| REPORT
```

推进顺序是“工程基线 -> 月频研究主线 -> 后续增强”。因子越多、优化器越复杂，不会自动带来更可信的策略；只有数据、标签、撮合和成本链路稳定后，复杂度才有讨论价值。

## 15. 模块映射和运行闭环

```mermaid
flowchart LR
    CONFIG["配置、数据版本、规则版本和随机种子"]
    DATA["data<br/>加载、available_at 对齐、状态、公司行动"]
    FACTOR["factors<br/>因子、预处理和综合分"]
    PORTFOLIO["portfolio<br/>目标组合、约束、换手和风险"]
    BACKTEST["backtest<br/>账户、撮合、费用、估值和公司行动"]
    REPORT["reports<br/>因子评价、组合复盘和未成交分析"]
    MANIFEST["run_manifest<br/>版本、参数、日志和结果路径"]

    CONFIG --> DATA --> FACTOR --> PORTFOLIO --> BACKTEST --> REPORT
    BACKTEST --> MANIFEST
    REPORT --> MANIFEST
```

第一版可以压缩为四个模块：

```text
data.py        数据加载、时间对齐和公司行动
factors.py     因子计算、预处理和综合分
portfolio.py   目标组合、约束和订单意图
backtest.py    账户、撮合、估值和报告
```

无论代码如何拆分，稳定接口必须保持分层：

```text
策略层输出目标组合和理由
组合层输出订单意图
执行层负责成交、未成交和账户更新
账户层负责现金、股数、可卖数量和估值
评价层只使用已经完整的标签和真实账户结果
```

## 16. 一页式验收路径

```mermaid
flowchart LR
    A["数据没有未来信息"]
    B["历史股票池没有幸存者偏差"]
    C["因子方向和预处理可复现"]
    D["目标组合和实际账户分离"]
    E["T+1、停牌、涨跌停和部分成交可解释"]
    F["公司行动只计算一次"]
    G["成本后月频标签可用"]
    H["开发、验证和最终观察区间分开"]
    I["可以复现并交付报告"]

    A --> B --> C --> D --> E --> F --> G --> H --> I

    FAIL["任何一项失败：先修数据、规则或账务链路"]
    A -. 失败 .-> FAIL
    B -. 失败 .-> FAIL
    C -. 失败 .-> FAIL
    D -. 失败 .-> FAIL
    E -. 失败 .-> FAIL
    F -. 失败 .-> FAIL
    G -. 失败 .-> FAIL
    H -. 失败 .-> FAIL
    I --> FINAL["再增加因子、缓冲、风险规则或模型"]
```

## 17. 关键不变量

这些条件不随图形、模块或优化器变化：

```text
1. available_at <= as_of_time 才能进入当前决策。
2. label_available_at <= 当前 as_of_time 才能进入当前权重或训练。
3. 信号层不能使用次日停牌、封板或成交结果提前过滤。
4. 目标权重不等于真实持仓，目标成交不等于真实成交。
5. 卖不出的持仓继续占用资金和风险额度。
6. 因子使用复权口径，成交和估值使用原始价格，公司行动只处理一次。
7. 每个订单必须保留状态、成交量、价格和拒绝或未成交原因。
8. 月内每个交易日都必须更新账户、估值和风险暴露。
9. 回测必须报告成本后结果，并注明换手是单边还是双边口径。
10. 开发、验证和最终观察区间不能混用。
11. 数据版本、规则版本、因子版本、配置版本和随机种子必须进入运行清单。
12. 没有明确授权和仿真验证，不连接真实账户，不发送真实订单。
```
