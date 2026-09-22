# A 股月频多因子选股与模拟回测实现文档

> 本文是项目当前唯一主实现文档，合并并替代原《A 股多因子选股算法实现文档》和《A 股超短量化程序基础实现文档》。
>
> 当前唯一主线是 A 股低频多因子选股研究与模拟回测：月频决策、月内持续持有、每日记账与估值。第一版不接实盘，不执行真实下单或撤单，不使用未来数据。
>
> 本文描述工程实现和验收方法，不是投资建议，也不保证任何因子、参数或策略在未来有效。
>
> 配套流程图见 [《A 股月频多因子系统流程图》](./A股月频多因子系统流程图.md)。主文档定义规则口径，流程图展示时间关系、状态转移和模块边界；两者冲突时以本文为准。

## 1. 目标、边界和成功标准

### 1.1 阶段目标

第一版应形成一条可复现、可解释、可检查的最小纵向链路：

```text
历史数据
  -> 按 available_at 对齐
  -> 历史股票池
  -> 因子计算
  -> 横截面预处理
  -> 综合评分
  -> 目标组合
  -> 与实际账户比较
  -> 次日模拟撮合
  -> 每日账户估值
  -> 因子评价和组合复盘
```

月频决策指：每月最后一个交易日收盘后生成信号，并调整下一期的目标股票和目标权重；下一交易日尝试执行。

月内持续持有指：两次决策之间，每个交易日都处理成交、公司行动、停牌、涨跌停、现金和持仓估值。月频决策不等于每只股票持有固定 20 个交易日后强制卖出。新开仓的目标观察期约一个交易月，重新入选的股票可以继续持有，实际持有期可以超过一个月。

### 1.2 第一版范围

第一版应能：

- 指定历史区间、股票池、因子配置、组合约束和数据版本；
- 在每个决策时点只使用当时已经可获得的数据；
- 输出每只股票在每个因子的原始值、预处理值、综合分和排名；
- 输出目标组合、实际持仓、订单、成交和未成交原因；
- 处理 T+1、历史涨跌停、停牌、公司行动、费用、滑点和成交量上限；
- 输出因子评价、每日净值、成本后收益、回撤、换手和风险暴露；
- 保存数据版本、规则版本、因子版本、配置版本、`as_of` 时间和随机种子。

第一版不包含：

- 自动实盘下单或真实账户连接；
- 把机器学习或大语言模型直接作为买卖决策黑箱；
- 依赖 Level-2 全量盘口的高频策略；
- 默认融券做空；
- 用当前指数成分股回看历史；
- 用当前证券状态或交易规则覆盖全部历史区间；
- 在验证集或最终观察集上反复调参。

### 1.3 成功标准

第一版是否可用，不以“因子数量多”或“回测收益高”判断，而以以下问题是否都能明确回答：

- 每个信号使用了什么数据，数据在决策时是否真的可获得？
- 历史股票池、退市证券、ST 和停牌状态是否正确？
- 买入和卖出是否经过真实 A 股约束，而不是默认一定成交？
- 收益是成本前还是成本后，收益是否包含分红送转但不重复计算？
- 目标组合和真实账户之间的差异是否可解释？
- 因子表现和组合表现能否用同一数据版本复现？
- 结论在开发、验证和最终观察区间是否分别报告，而非只展示最好的一段？

## 2. 设计原则和核心定义

### 2.1 设计原则

- **时间点正确优先于策略复杂。** 任何特征、因子和信号都只能在 `as_of_time` 之前或当时可获得。
- **先做最短闭环，再增加假设。** 先完成少量因子、等权评分、月频调仓和成本后回测，再考虑 IC 加权、模型和多层风控。
- **信号层和执行层分离。** 信号层只生成目标组合，不操纵账户；订单是否成交、成交多少和为何未成交由撮合层记录。
- **目标不等于持仓。** 目标组合是希望执行的组合，实际账户还受现金、旧持仓、涨跌停、停牌、T+1 和未成交订单约束。
- **目标权重不等于收益权重。** 执行偏差、现金和个股收益会改变实际权重；风险约束必须检查实际账户。
- **原始价格和复权价格分开。** 因子计算使用明确口径的复权价格；成交和账户估值使用原始价格；公司行动只处理一次。
- **历史规则按生效日期查询。** 涨跌停、费用、ST、IPO、停牌和交易单位不能使用单一当前规则覆盖历史区间。
- **稳健性优先于单点最优。** 开发区间定义规则，验证区间冻结规则后检查，最终观察区间只检查一次。

### 2.2 核心对象

```text
trade_date             交易日
as_of_time             做出决策的时间点
available_at           数据实际可被策略使用的时间
rebalance_date         生成目标组合的日期
execution_date         计划实际尝试成交的日期
factor_value           某只股票在某一天的某个因子原始值
processed_factor_value 预处理后的因子值
composite_score        多个因子加权后的综合分
target_portfolio       目标股票和目标权重
account_state          现金、真实持仓、可卖数量、订单和未成交状态
fill                   一笔实际模拟成交
valuation              盘后持仓市值和总资产
future_label           仅用于评价的已实现未来收益
```

### 2.3 最重要的时间约束

```text
日线因子：使用已完成交易日的收盘数据，在收盘后计算
财务数据：使用 announcement_date <= as_of_time 的当时可见版本
分析师或事件数据：使用发布或首次可见时间
成交价格：只能从 execution_date 当天可知的行情产生
未来收益：只用于标签和评价，不能进入选股逻辑
```

## 3. 运行时间线

### 3.1 月末决策和月内持有

以月末最后一个交易日为 `T`：

```text
每月最后一个交易日 T 收盘后（as_of_time = T 收盘后）
  -> 更新 T 日及以前的数据
  -> 构建 T 时点的历史股票池
  -> 计算因子、预处理和综合分
  -> 生成目标组合
  -> 与当前实际账户比较，准备下一交易日订单

T+1 日的执行窗口
  -> 按当天的历史交易状态尝试成交
  -> 处理停牌、涨跌停、部分成交、费用和滑点
  -> 更新现金、持仓、可卖数量和订单状态

T+1 日收盘后及之后每个交易日
  -> 记录收盘估值和净值
  -> 处理已发生的公司行动
  -> 更新风险暴露
  -> 不做日频主动调仓，除非启用单独定义并冻结的风险规则

下一计划决策日
  -> 重新生成目标组合
  -> 未成交订单先按已定义的过期规则处理
```

### 3.2 计划时间与标签时间

月频主标签使用“计划执行时点到下一计划执行时点”的口径：

```text
decision_date_t         第 t 次决策日期
entry_execution_date_t  第 t 次计划执行日期，通常是 decision_date_t 的下一交易日
entry_price_t           实际模拟成交价格；没有成交时记录建模前的入场参考价格
decision_date_t1        下一次计划的决策日期
exit_execution_date_t1  下一次计划的执行日期
exit_price_t1           下一次执行时点的对应价格
```

主收益标签：

```text
total_return_label(i, t)
  = exit_price(i, decision_date_t1 后的计划执行时点)
    / entry_price(i, entry_execution_date_t)
    * cumulative_adjustment_factor
    - 1
```

其中 `cumulative_adjustment_factor` 只表示原始价格之外尚未计入的现金分红和送转等公司行动，不能与复权价格调整重复。因子 IC 和组合收益必须明确使用同一种口径，不能一个用复权收益、另一个用原始价格收益。

辅助诊断标签可以包括：

```text
下一交易日开盘到收盘收益
固定未来 20 个交易日收益
下一执行时点到下一决策日收盘收益
```

所有标签都必须由实际行情和公司行动计算，不能假设目标组合一定按目标权重成交。计划执行参考价、实际成交价和账户真实收益分别保存。

### 3.3 标签可得时间

历史样本只有在标签区间完全结束后才可用于评价或训练：

```text
label_available_at(t)
  = exit_execution_date_t1 的行情和公司行动全部可用之后
```

因子 IC 和动态权重只能使用 `label_available_at <= as_of_time` 的历史样本。仅因为 `decision_date_t < as_of_time` 不足以说明下一期收益已经实现；如果中间发生停牌或延后执行，还要等实际退出区间可用。

## 4. 数据层

### 4.1 最小数据表

至少准备以下数据：

```text
交易日历
股票基础信息
上市日期、退市日期和历史板块
历史 ST、*ST 和退市整理状态
每日停牌、交易状态和涨跌停限制
日线 OHLCV
成交额、成交量、换手率、流通市值和总市值
复权因子和公司行动
历史行业分类及变更
指数历史成分及变更
财务报告数据，按公告日或首次可见时间可用
分析师预期、公告和事件数据，按实际可见时间可用
```

### 4.2 每条数据的时间字段

```text
trade_date
as_of_time
available_at
source
source_version
```

数据加载时必须按 `available_at <= as_of_time` 截断。财务数据不能用报告期直接回填，也不能把后续修订值覆盖到当时版本。

### 4.3 价格口径和公司行动

```text
factor_price        按固定后复权口径计算的因子价格
execution_price     撮合使用的原始成交价格
valuation_price     盘后估值使用的原始收盘价格
adjustment_factor   现金分红、送转、配股等产生权益变化的事件因子
```

处理规则：

- 因子计算使用一种固定、可版本化的复权口径；
- 成交和每日市值使用原始价格和真实股数；
- 收到现金分红时增加现金，不能同时把总收益再按含分红复权价格计算一次；
- 送股、转增和拆并股按公司行动更新股数，不能把它当普通投资收益；
- 配股、回购和特殊公司行动是否参与，必须按当时可见公告和历史规则配置；
- 任何无法可靠处理的公司行动必须进入日志和数据质量报告。

### 4.4 数据质量检查

每次运行至少检查：

```text
主键唯一
交易日连续
价格、成交额和市值非负且无明显异常
复权因子和公司行动能对上
停牌、退市和 ST 状态没有缺口
行业分类时间版本可用
财务数据没有未来公告日
因子覆盖率没有突然变化
股票池数量没有异常跳变
订单或未成交数量没有超出可交易范围
```

质量问题不能通过统一填 0 或静默删除解决。必须记录原因、影响范围和剔除数量。

## 5. 股票池

### 5.1 决策时点股票池

股票池在决策时点构建，只使用当时已知的状态：

```text
剔除决策时点已确认的 ST、*ST 和退市整理证券
剔除决策时点已停牌或不可交易的证券
剔除上市不足最小交易日的证券
剔除总市值或流通市值缺失的证券
剔除净资产为负或关键财务字段严重缺失的证券
剔除近期成交额不足的证券
剔除历史版本行业分类缺失且无法可靠处理的证券
```

“次日无法正常买入”和“次日无法正常卖出”不属于决策时点已知信息。不能为了构建历史股票池或消除回测中的未成交，使用次日是否封板、是否停牌的结果做前置过滤。

### 5.2 已持仓不在股票池时的处理

股票池只决定新目标和目标权重候选，不等于账户持仓的物理清仓：

- 已持仓但跌出股票池时，可以在可交易时生成卖出目标；
- 已停牌、跌停或无法成交时，继续按实际状态持有，并记录未成交原因；
- 卖不出的股票继续占用资金、仓位和风险额度；
- 已退市或发生公司行动时，按历史规则结算或更新股数，不能让持仓静默消失；
- 估值层必须覆盖所有实际持仓，即使该股票已不在信号股票池中。

## 6. 因子层

### 6.1 因子方向

所有因子进入综合分前统一为“数值越大越好”：

```text
正向因子：原始值越大越好，direction = +1
负向因子：原始值越小越好，direction = -1
```

### 6.2 因子分类

| 类别 | 因子示例 | 计算或口径 | 方向 |
|---|---|---|---|
| 估值 | EP | 净利润 TTM / 总市值，即 1 / PE_TTM | 正向 |
| 估值 | BP | 净资产 / 总市值，即 1 / PB | 正向 |
| 估值 | 自由现金流收益率 | 自由现金流 TTM / 总市值 | 正向 |
| 质量 | ROE | 净利润 TTM / 平均净资产 | 正向 |
| 质量 | 毛利率 | 毛利 TTM / 营收 TTM | 正向 |
| 质量 | 应计利润 | (净利润 TTM - 经营现金流 TTM) / 总资产 | 负向 |
| 成长 | 营收同比 | 营收 TTM / 四个季度前营收 TTM - 1 | 正向 |
| 成长 | 净利润同比 | 净利润 TTM / 四个季度前净利润 TTM - 1 | 正向 |
| 动量 | 12-1 动量 | close(t-21) / close(t-252) - 1，使用固定复权口径 | 正向 |
| 动量 | 60 日动量 | close(t) / close(t-60) - 1，使用固定复权口径 | 正向 |
| 反转 | 20 日反转 | -(close(t) / close(t-20) - 1)，使用固定复权口径 | 正向 |
| 低波 | 60 日波动率 | std(日收益, 60) * sqrt(252) | 负向 |
| 低波 | 特质波动率 | 市场模型残差的波动率 | 负向 |
| 流动性 | 20 日成交额 | log(过去 20 日成交额均值) | 正向，需设容量上限 |
| 流动性 | Amihud 非流动性 | 平均(|日收益| / 日成交额) | 视策略定义 |

### 6.3 短线因子和月频因子不要混用

月频多因子主库优先使用：

```text
估值
质量
成长
中期动量
低波
流动性和容量
分析师预期
```

短线事件分、涨停事件、分钟级封板和资金流可以用于独立研究，但不能在没有单因子验证和成本检查的情况下直接混入月频综合分。不同时间尺度的因子必须分别评价，再检查相关性和换手。

### 6.4 因子计算接口

每个因子定义至少包含：

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FactorSpec:
    name: str
    category: str
    direction: int
    neutralize: bool
    weight: float
    version: str


@dataclass(frozen=True)
class FactorSnapshot:
    trade_date: str
    symbol: str
    values: dict[str, float]
    available_at: datetime
    factor_version: str
```

计算函数只接受已经按 `as_of_time` 截断的数据，不直接读取当前状态或全量未来数据。

## 7. 因子预处理

所有横截面处理必须逐交易日进行，不能混合不同日期的样本。

### 7.1 推荐顺序

```text
原始因子
  -> 按 available_at 截断
  -> 剔除不可用股票
  -> 缺失值处理
  -> 去极值
  -> 统一方向
  -> 标准化
  -> 可选行业中性和市值中性化
  -> 进入综合分
```

### 7.2 缺失值处理

```text
1. 某只股票缺失因子过多时剔除，并记录原因
2. 某因子当日覆盖率过低时跳过该因子，并记录
3. 少量缺失可以用行业内中位数填充
4. 填充后保留 missing_flag
5. 不用 0 统一填充所有缺失值
```

缺失值处理规则必须在配置中固定，不能根据回测好坏来回切换。

### 7.3 去极值

推荐 MAD：

```text
median = median(x)
mad = median(abs(x - median))
lower = median - 3 * 1.4826 * mad
upper = median + 3 * 1.4826 * mad
x = clip(x, lower, upper)
```

也可以使用固定分位数截尾。两种方法选一种并固定。

### 7.4 标准化和中性化

标准化：

```text
z_score = (x - mean(x)) / std(x)

排名百分位：
u = rank(x) / (n + 1)
z = inverse_normal_cdf(u)
```

横截面中性化：

```text
x = alpha
  + sum(beta_j * industry_j)
  + gamma * log_market_cap
  + delta * beta
  + epsilon
```

取残差 `epsilon` 作为中性化后的因子值。估值、质量、成长通常需要行业和市值中性化；动量、低波可以只做行业中性化；如果因子本身就是目标暴露，不应无条件中性化掉。

## 8. 综合评分

### 8.1 基本公式

```text
score(i, t) = sum_k(weight(k, t) * z(i, k, t))
```

其中 `weight(k, t)` 只能来自 `t` 时点已经可得的信息。

### 8.2 第一版建议

第一版使用固定的类别等权：

```text
value_weight = 1 / category_count
weight(k) = value_weight / category_factor_count
```

这样不会因为某个类别定义了更多因子而获得更大权重。第一版不默认启用 IC 加权、机器学习或因子择时。

### 8.3 IC 和 RankIC

月频主 IC：

```text
IC(k, t) = corr(factor(k, t), monthly_total_return_label(t))
RankIC(k, t) = SpearmanCorr(factor(k, t), monthly_total_return_label(t))
```

动态权重只能使用已经完成的标签样本：

```text
valid_history =
  samples with label_available_at <= current_as_of_time

raw_weight(k, t) =
  mean(IC(k, valid_history_window))
  / std(IC(k, valid_history_window))

weight(k, t) = normalized_positive_weight(raw_weight(k, t))
```

必须满足：

- 不能使用下一期尚未实现的收益计算本期权重；
- 窗口长度、截断范围和负 IC 处理方式固定；
- 负 IC 先检查方向和预处理错误，不直接给负权重；
- 权重变化过快时必须检查换手和交易成本；
- 固定未来 20 日收益不能冒充月频调仓标签。

### 8.4 回归和机器学习

只有满足以下条件后再考虑：

```text
单因子已经完成样本外检验
高度相关因子已经合并或剔除
开发、验证和最终观察区间已经固定
T+1、涨跌停、停牌、费用和未成交已经进入回测
模型输出只作为横截面排序分数
```

机器学习不能绕过组合约束、风险约束和撮合规则，也不能使用未来标签。

## 9. 组合构建

### 9.1 第一版无缓冲基线

第一版先定义无缓冲的清晰基线：

```text
target_n = 30
rank = 综合分从高到低排序
targets = 股票池内排名前 target_n 的股票
weight = 目标权重
```

无缓冲基线不是最终最优方案，而是验证选股、权重和执行链路的参照。

### 9.2 后续缓冲规则

缓冲规则必须与目标持仓数量使用同一排名尺度：

```text
默认目标数量 target_n = 30

旧持仓：
  排名 <= 30              继续作为目标
  排名 31 到 45           保留，不主动卖出，但不新增排名相同且需要替换的股票
  排名 > 45               不再作为目标，并尝试卖出

非旧持仓：
  排名 <= 30              作为目标
  排名 > 30               不新增
```

当保留旧持仓和目标数量发生冲突时，优先级固定为：

```text
1. 排名 <= target_n 的股票
2. 旧持仓中排名在 target_n 到 target_n + buffer_band 的股票
3. 如果没有冲突，允许未达到 target_n 的仓位暂时留现金或按规则减少股票
```

不能使用“全市场前 40%”代替 `rank > 45`，除非回测区间、股票池和最终持仓数量都能证明这种口径仍然控制数量。

### 9.3 权重分配

第一版使用等权：

```text
base_weight = (1 - cash_buffer) / target_n
```

受行业、单票和流动性约束后，权重可以重新归一化，但必须记录调整次数和未分配现金。

### 9.4 组合约束

```text
单票最大权重
最大持仓数量
行业最大权重
因子暴露限制
单次调仓换手上限
现金保留比例
最低流动性要求
ST、停牌和退市状态限制
涨跌停和 T+1 不可成交处理
```

### 9.5 换手口径

换手上限默认按单边口径：

```text
buy_turnover = sum(max(target_weight - actual_weight, 0))
sell_turnover = sum(max(actual_weight - target_weight, 0))
one_way_turnover = 0.5 * (buy_turnover + sell_turnover)
```

也可以按资金流口径：

```text
one_way_turnover_by_value =
  0.5 * (buy_value + sell_value) / total_equity
```

两种口径在报告中必须明确标注，不能只写“换手率”。

首次建仓是否豁免换手上限作为独立配置：

```text
turnover_limit_exempt_initial = true
```

首次建仓仍受单票、行业、流动性和现金约束，只是不受旧持仓换手上限限制。

### 9.6 约束冲突和现金

目标权重是执行目标，不是保证实现的状态。以下情况允许保留现金或增加现金：

- 目标股票在次日不可买；
- 权重受单票、行业或流动性约束；
- 最小交易单位导致无法精确配置；
- 换手上限阻止全部调整；
- 已有持仓无法卖出，资金继续被占用。

不能用强制补满、默认成交或忽略旧仓位的方式让回测看起来更完整。

### 9.7 后续优化器

如果后续使用优化器，可以写成：

```text
maximize  alpha^T * w
        - lambda * w^T * Sigma * w
        - cost * turnover

subject to  sum(w) <= 1 - cash_buffer
            0 <= w_i <= max_weight
            industry_exposure <= industry_limit
            factor_exposure <= factor_limit
            turnover <= turnover_limit
            only_tradeable_assets = true
```

第一版先不做完整优化器。规则选股加等权更容易发现数据、权重和执行问题。

## 10. 账户、撮合和公司行动

### 10.1 账户状态

账户至少保存：

```text
cash
available_cash
total_equity
long_positions
short_positions
available_to_sell
frozen_cash
pending_orders
unfilled_orders
realized_pnl
unrealized_pnl
daily_valuation
corporate_action_adjustments
rule_version
```

### 10.2 信号到订单

```text
目标权重
  -> 计算目标股数，按最小交易单位取整
  -> 与当前实际持仓比较
  -> 先处理卖出和减少仓位
  -> 再处理买入和增加仓位
  -> 受现金、可用数量、T+1 和换手上限约束
  -> 生成订单，不直接假设成交
```

任何订单都要记录：

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

### 10.3 执行价格和成交模型

第一版默认使用次一交易日开盘作为执行参考：

```text
execution_reference_price =
  next_trade_date_open_price
```

为了避免“知道次日开盘价后再决定是否参与”的隐含未来信息，执行规则必须在看到次日价格前写死：

```text
如果停牌，不成交
如果开盘价或当日状态显示无法买入，不成交
如果开盘价或当日状态显示无法卖出，不成交
成交量上限参与比例固定
滑点按固定规则计算
```

若使用当天开盘价判断涨跌停，应记录这是日线级近似模型。更精细的实现应使用历史逐笔、分钟行情或集合竞价数据，并按 `available_at` 切分。不能使用当天最高价、最低价或收盘价反推出一个当时无法知道的成交结果。

### 10.4 A 股交易约束

```text
T+1：买入日不能按普通规则当天卖出
涨停：可能买不到或只能部分成交
跌停：可能卖不出或只能部分成交
停牌：不能成交
ST 和特殊证券：历史涨跌幅限制不同
最小交易单位：通常为 100 股，但按历史规则和证券状态配置
费用：佣金、印花税、过户费按历史生效规则
滑点：按成交额、波动率和参与率建模
成交量上限：单日成交不能超过市场成交量的一定比例
```

### 10.5 公司行动

公司行动必须进入账户状态，不能只放在数据表：

```text
现金分红：增加现金
送股、转增、拆分和合并：更新股数
配股：按当时可见公告和账户选择规则处理
退市：按历史结算规则处理，保留可追溯记录
```

公司行动的处理顺序必须与估值顺序一致，避免同一天既调整市值又重复计算收益。

### 10.6 费用和滑点

```text
买入成本 = 佣金 + 过户费 + 滑点
卖出成本 = 佣金 + 印花税 + 过户费 + 滑点
```

费用和历史规则示例只能作为配置结构，不能把当前费率写死为全部历史区间：

```toml
[execution]
commission_rate = 0.0003
stamp_tax_sell = 0.0005
transfer_fee = 0.00001
slippage_bps = 10
lot_size = 100
max_participation_rate = 0.05
rule_version = "historical_fee_rule_v1"
```

### 10.7 未成交和过期

每笔未成交订单必须有明确状态：

```text
pending
partially_filled
filled
cancelled
expired
rejected
```

未成交原因至少包括：

```text
停牌
涨停买不到
跌停卖不出
成交量上限
现金不足
可卖数量不足
T+1 限制
换手上限
达到订单有效期
```

未成交订单不能无限重试。默认在下一个计划调仓日重新评估；是否在月内重试由单独配置控制。

## 11. 风险控制

多因子选股解决横截面选股问题，不能自动解决系统性风险。风险规则必须独立于因子评分，并且简单、可解释、可回测。

可选检查：

```text
指数是否在长期均线之上
市场上涨和下跌家数
涨停和跌停家数
全市场成交额是否明显萎缩
市场波动率是否突然升高
因子近期是否拥挤或失效
组合行业和市值暴露是否超限
单票、单行业和流动性集中度是否超限
```

第一版建议先记录风险指标，不默认加入主动择时。待基线稳定后，再逐项验证：

```text
风险偏高：总仓位从 100% 降到 50%
极端行情：暂停开新仓，只处理已有持仓
波动率骤升：减少持仓数量，提高现金比例
因子拥挤：降低该因子权重，或暂时使用等权
```

任何风险规则都必须在开发区间定义、验证区间冻结，并检查是否只是事后拟合。

## 12. 因子评价和组合复盘

### 12.1 单因子评价

```text
IC 均值
RankIC 均值
IC 标准差
ICIR
t 统计量
IC 衰减速度
分位组合收益
分位收益单调性
多空组合收益，仅作为研究诊断
因子换手率
因子覆盖率和缺失率
```

因子评价必须和月频标签、股票池、预处理、成交假设和费用口径一致。

### 12.2 组合评价

```text
总收益
年化收益，仅在样本足够时报告
最大回撤
波动率
夏普比率
胜率
盈亏比
平均持仓时间
换手率，注明单边或双边口径
交易次数
成本前收益和成本后收益
成交率
未成交原因
按市场阶段分组的收益
按行业、市值和流动性分组的收益
```

### 12.3 稳健性评价

```text
不同历史区间是否稳定
不同股票池是否稳定
不同调仓频率是否稳定
不同交易成本假设下是否稳定
剔除表现最好的少数交易后是否仍然成立
单因子和综合分排名是否单调
因子之间是否高度相关
```

只在一个年份、一个行业或少数几笔交易上有效的策略，不能视为稳健。

## 13. 权重校准与防过拟合

推荐时间切分：

```text
开发区间：定义因子、方向和候选参数
验证区间：冻结规则后检查稳健性
最终观察区间：完全不参与调参，最后一次检查
```

推荐做法：

```text
先固定因子定义，再调权重
先使用等权，再使用 IC 加权
每次只改变一个变量
记录每次实验的配置、数据版本和结果
优先选择参数平原，而不是单点最优
不随机打乱时间序列
不用验证集反复改参数
```

参数敏感性检查至少覆盖：

```text
top_n 从合理区间变化时结果是否稳定
调仓周期变化时结果是否稳定
中性化前后差异是否可解释
不同去极值方法是否差异巨大
成本提高一倍后是否仍然成立
```

## 14. 工程结构

### 14.1 推荐目录

```text
ashare_multifactor/
  config/
    multifactor.toml
  src/
    data/
      calendar.py
      pit.py
      universe.py
      corporate_actions.py
      quality.py
    factors/
      definitions.py
      value.py
      quality.py
      growth.py
      momentum.py
      volatility.py
      liquidity.py
      preprocess.py
      composite.py
    portfolio/
      constructor.py
      constraints.py
      turnover.py
      risk.py
    strategy/
      multifactor.py
    execution/
      simulator.py
      account.py
      fees.py
      calendar_rules.py
    reports/
      factor_report.py
      portfolio_report.py
      execution_report.py
  scripts/
    run_multifactor_backtest.py
```

第一版可以压缩为四个模块：

```text
data.py        数据加载、时间对齐和公司行动
factors.py     因子计算、预处理和综合分
portfolio.py   目标组合、约束和订单意图
backtest.py    账户、撮合、估值和报告
```

### 14.2 稳定接口

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class FactorSpec:
    name: str
    category: str
    direction: int
    neutralize: bool
    weight: float
    version: str


@dataclass(frozen=True)
class FactorSnapshot:
    trade_date: str
    symbol: str
    values: dict[str, float]
    available_at: datetime
    factor_version: str


@dataclass(frozen=True)
class TargetPortfolio:
    decision_date: str
    execution_date: str
    weights: dict[str, float]
    scores: dict[str, float]
    reasons: dict[str, str]
    constraint_state: dict[str, str]


@dataclass(frozen=True)
class AccountState:
    as_of_time: datetime
    cash: float
    positions: dict[str, float]
    available_to_sell: dict[str, float]
    pending_orders: tuple


@dataclass(frozen=True)
class OrderIntent:
    signal_time: datetime
    order_time: datetime
    symbol: str
    side: str
    requested_volume: float
    requested_price: float
    reason: str
```

策略层只生成目标组合和理由，不直接操纵账户。组合约束、订单意图、撮合和账户更新分别放在独立模块。

### 14.3 配置示例

```toml
[universe]
exclude_st = true
exclude_suspended = true
min_listed_days = 120
min_amount_20d = 50000000
max_missing_factor_ratio = 0.30

[rebalance]
frequency = "monthly"
signal_at = "close"
execute_at = "next_open"
buffer_enabled = false
buffer_band = 15

[composite]
method = "category_equal_weight"
missing = "renormalize"

[portfolio]
top_n = 30
max_weight = 0.04
max_industry_weight = 0.20
max_turnover = 0.40
turnover_mode = "one_way"
turnover_limit_exempt_initial = true
cash_buffer = 0.05

[execution]
commission_rate = 0.0003
stamp_tax_sell = 0.0005
transfer_fee = 0.00001
slippage_bps = 10
lot_size = 100
max_participation_rate = 0.05
rule_version = "historical_trade_rule_v1"

[backtest]
start_date = "2020-01-01"
end_date = "2024-12-31"
development_end = "2022-12-31"
validation_end = "2023-12-31"
random_seed = 7
```

### 14.4 主流程伪代码

```python
def run_multifactor(as_of_time, config, account_state):
    universe = build_universe(
        decision_date=as_of_time.date(),
        config=config.universe,
        data_version=config.data_version,
    )

    factor_snapshots = {}
    for spec in config.factor_specs:
        raw = compute_factor(
            spec=spec,
            universe=universe,
            as_of_time=as_of_time,
        )
        processed = preprocess_factor(
            raw,
            universe=universe,
            as_of_time=as_of_time,
        )
        factor_snapshots[spec.name] = processed

    score = composite_score(
        factors=factor_snapshots,
        weights=config.weights,
        available_at=as_of_time,
    )

    target = construct_target_portfolio(
        score=score,
        universe=universe,
        current_positions=account_state.positions,
        constraints=config.portfolio,
    )

    orders = create_order_intents(
        target=target,
        account=account_state,
        execution_date=next_trade_date(as_of_time),
        constraints=config.execution,
    )

    return target, orders
```

真正运行时，`as_of_time`、数据版本、规则版本、因子版本、配置版本和随机种子都必须写入运行日志。没有这些信息，结果不可复现。

## 15. 第一版最小纵向切片

第一版不要同时实现二十个因子。先完成下面的纵向切片：

```text
股票池：全 A，剔除 ST、停牌、上市不足 120 日和低流动性股票
因子：20 日反转、60 日动量、60 日低波
预处理：去极值、方向统一、标准化、行业中性化
综合分：类别等权
调仓：每月最后一个交易日收盘后生成信号，下一交易日执行
选股：综合分前 30 名，第一版无缓冲区
权重：等权，单票不超过 4%，行业不超过 20%，现金保留 5%
账户：现金、真实持仓、可卖数量、未成交订单和每日估值
撮合：T+1、历史涨跌停、停牌、公司行动、费用和滑点
评价：月度 RankIC、分位收益、净值、回撤、换手和未成交原因
```

实现顺序：

```text
1. 建立交易日历、历史股票池、退市和状态数据
2. 对齐日线、停牌、市值、行业和公司行动
3. 实现三个因子及预处理
4. 实现类别等权综合分和前 30 名目标组合
5. 实现账户、月频调仓和 A 股撮合
6. 输出 RankIC、分位收益、组合报告和未成交原因
7. 确认无未来数据后，再增加估值、质量和成长因子
```

## 16. 后续策略路线

后续策略按三个层次推进，不要一开始混在一起：

| 层次 | 内容 | 主要问题 |
|---|---|---|
| 工程基线 | 20 日反转、60 日动量、60 日低波，类别等权，月频无缓冲调仓 | 数据、评分、账户和成本链路是否正确 |
| 月频研究主线 | 估值、质量、成长、中期动量、低波 | 多因子组合是否有稳定的成本后超额收益 |
| 后续增强 | 排名缓冲、风险约束、IC 加权、机器学习排序 | 复杂度是否带来可信、可复现的增益 |

第一轮研究建议从每类一两个定义清楚的因子开始：价值用 EP/BP，质量用 ROE 或现金流质量，中期动量用 12-1 动量，低波用历史波动率。先看单因子，再做类别等权、类别内部等权。财务数据没有可靠的历史公告版本时，不加入财务因子。

每次只改一个关键变量，检查它对成本后收益、最大回撤、换手、行业暴露和市值暴露的影响。

## 17. 研究、验证和实盘边界

### 17.1 研究和模拟回测

开发和回测阶段只处理历史数据、统计结果和模拟账户。研究结论只代表当前数据版本、股票池和成本假设下的历史结果。

### 17.2 仿真运行

进入仿真前必须确认：

```text
股票代码和证券状态已核实
账户类型不与真实资金账户混淆
订单价格、数量和方向正确
订单状态、部分成交、撤单和拒单可恢复
断线、重连、重复下单和进程重启有明确处理
日志不泄露凭据
```

TqSdk 可以作为行情、历史数据、股票模拟或回测接口之一，但不是完整的历史财务、行业、退市和公司行动数据库保证。股票模拟使用股票账户模式；股票交易不使用期货的 `TargetPosTask` 或期货 `offset`。具体接口能力必须以项目实际安装版本为准。

### 17.3 实盘

没有明确授权和仿真验证，不连接真实交易账户，不执行真实下单或撤单。实盘不是回测的开关。

## 18. 验收清单

```text
[ ] 数据按 available_at 对齐，没有未来数据
[ ] 历史股票池按决策时点生成，没有幸存者偏差
[ ] 财务数据按公告日或首次可见时间使用
[ ] 信号层没有使用次日封板或次日停牌结果
[ ] 因子方向、缺失值和极值规则固定且有记录
[ ] 横截面处理只在同一交易日内进行
[ ] 中性化前后因子都有记录
[ ] 综合分权重只来自已可得样本
[ ] 目标组合和实际账户状态分开保存
[ ] 月内每日都有持仓估值和净值
[ ] T+1、历史涨跌停、停牌和费用进入撮合
[ ] 部分成交、未成交和过期订单都有原因
[ ] 公司行动更新现金或股数且不重复计算收益
[ ] 卖不出的持仓继续占用资金和风险额度
[ ] 换手上限注明口径，首次建仓豁免规则明确
[ ] 缓冲规则与目标持仓数量使用同一排名尺度
[ ] RankIC、ICIR 和分位收益使用同一个月频标签口径
[ ] 成本后收益，而不是只报告成本前收益
[ ] 开发、验证和最终观察区间没有混用
[ ] 运行配置、数据版本、规则版本和因子版本已保存
```

## 19. 结论

月频多因子策略的核心是：

```text
时间点对齐的数据
+ 历史股票池
+ 可解释的因子
+ 严格的横截面预处理
+ 方向统一的综合评分
+ 有约束的目标组合
+ 真实账户状态
+ A 股历史撮合和公司行动
+ 成本后样本外验证
```

工程上先完成“三个因子、类别等权、月频无缓冲调仓、真实账户记账、成本后回测”的最小闭环。第一版能稳定复现并解释每个入选理由，比继续增加因子、优化器或机器学习更重要。
