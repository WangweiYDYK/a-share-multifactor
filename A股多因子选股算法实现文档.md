# A 股多因子选股算法实现文档

> 目标：说明一套可复现、可解释、按时间点对齐的 A 股多因子选股算法。第一阶段只做“历史因子计算 -> 横截面打分 -> 组合构建 -> 次日或下期模拟成交 -> 结果复盘”，不接实盘，不使用未来数据。
>
> 当前暂定口径：低频多因子，月度调仓，新开仓目标持有期约一个交易月（约 20 个交易日）。信号在每月最后一个交易日收盘后生成，下一个交易日执行；使用缓冲区保留的股票，实际持有期可以超过一个月。
>
> 关联文档：《A 股超短量化程序基础实现文档.md》。那份文档聚焦超短事件策略和 A 股撮合约束，本文聚焦横截面选股、因子处理和组合构建。
>
> 本文描述的是工程实现方法，不是投资建议，也不保证任何因子或参数在未来有效。

## 1. 验收边界

第一版完成后，程序应能：

- 指定历史区间、股票池、因子配置和调仓频率；
- 第一版调仓频率固定为月频，便于先验证数据链路、因子有效性和成本后的组合结果；
- 在每个决策时点只使用当时已经可获得的数据；
- 对每只股票计算原始因子、标准化因子和综合分；
- 输出每日或每期的候选股票、综合分和入选理由；
- 按预设约束构建目标组合，并和当前持仓比较生成订单；
- 在模拟撮合中处理 T+1、涨跌停、停牌、费用和滑点；
- 输出因子评价、组合净值、收益、回撤、换手和未成交原因；
- 保存数据版本、因子版本、参数版本和运行时间，保证结果可复现。

第一版不包含：

- 自动实盘下单；
- 把机器学习或大语言模型直接当作买卖决策的黑箱；
- 依赖 Level-2 全量盘口的高频策略；
- 默认可以融券做空；
- 在验证集或观察集上反复调参；
- 用当前指数成分股回看历史，造成幸存者偏差。

## 2. 算法总览

多因子选股不是把多个买入条件写成一串 AND，而是下面的流程：

```text
原始数据
  -> 按时间点对齐
  -> 历史股票池过滤
  -> 计算原始因子
  -> 缺失值处理
  -> 去极值
  -> 统一因子方向
  -> 标准化
  -> 行业和市值中性化
  -> 合成综合分
  -> 选股和权重分配
  -> 组合约束
  -> 生成订单
  -> A 股撮合
  -> 因子评价和组合复盘
```

核心定义：

```text
trade_date       交易日
as_of_time       做出决策的时间点
available_at     数据实际可被策略使用的时间
rebalance_date   重新计算综合分并生成目标组合的日期
execution_date   实际尝试成交的日期
factor_value     某只股票在某一天的某个因子原始值
z_score          预处理后的因子值
composite_score  多个因子加权后的综合分
target_weight    目标组合中每只股票的权重
```

最重要的原则是：任何特征、因子和综合分都只能在 `as_of_time` 之前或当时可获得。任何未来收益、未来价格和未来标签只能用于评价，不能进入选股逻辑。

## 3. 数据层

### 3.1 最小数据表

至少准备以下数据：

```text
交易日历
股票基础信息
上市日期、退市日期、ST 状态
停牌状态和可交易状态
日线 OHLCV
成交额、成交量、换手率、流通市值、总市值
复权因子和公司行动
行业分类及历史变更
指数成分及历史变更
财务报告数据，按公告日可用
分析师预期和事件数据，按实际可见时间可用
```

### 3.2 时间对齐

每条数据至少保留：

```text
trade_date
as_of_time
available_at
source
source_version
```

时间对齐规则：

```text
财务报表：使用 announcement_date <= as_of_time 的最新版本，不能使用 report_period 直接回填
分析师预期：使用发布或修订时间，不能用事后最终值
龙虎榜、公告、新闻：使用市场首次可见时间
日线因子：收盘后计算，才能用于下一交易日
分钟因子：只能在对应分钟 K 线完成后使用
未来收益：只用于标签和评价，不能进入因子
```

### 3.3 数据质量检查

每次运行至少检查：

```text
主键是否唯一
交易日是否连续
价格、成交额和市值是否为负或异常
复权前后收益是否一致
停牌和退市状态是否缺失
行业分类是否有空值
财务数据是否出现未来公告日
因子覆盖率是否突然变化
股票池数量是否异常
```

数据质量问题不能靠填 0 或直接删除解决，应该记录原因，并在报告中展示覆盖率变化。

## 4. 股票池构建

### 4.1 基础过滤

第一版可以使用：

```text
剔除 ST、*ST 和退市整理期股票
剔除停牌股票
剔除上市不足 120 个交易日的股票
剔除总市值或流通市值缺失的股票
剔除净资产为负的股票
剔除财务数据严重过期或缺失的股票
```

### 4.2 可交易性过滤

为了避免回测中出现“选到了但买不到”：

```text
剔除信号日涨停且次日无法正常买入的股票
剔除信号日跌停且次日无法正常卖出的股票
剔除近期成交额过低的股票
剔除超过组合容量上限的股票
```

### 4.3 历史股票池

股票池必须按历史时点生成，不能直接用今天的成分股：

```text
今天存在的股票，不代表过去也存在
今天的指数成分股，不代表过去也是成分股
今天退市的股票，历史回测时也必须保留
行业分类要用当时可获得的版本
```

示例配置：

```toml
[universe]
exclude_st = true
exclude_suspended = true
min_listed_days = 120
min_amount_20d = 50000000
max_missing_factor_ratio = 0.30
```

## 5. 因子库

### 5.1 方向约定

所有因子在进入综合分之前，必须统一成“数值越大越好”：

```text
正向因子：原始值越大，预期收益越高，direction = +1
负向因子：原始值越小，预期收益越高，direction = -1
```

例如估值因子 EP 越高越好，是正向；应计利润越高通常越差，是负向；波动率越高通常越差，是负向。

### 5.2 常见因子

| 类别 | 因子 | 计算方式 | 方向 |
|---|---|---|---|
| 估值 | EP | 净利润 TTM / 总市值，等于 1 / PE_TTM | 正向 |
| 估值 | BP | 净资产 / 总市值，等于 1 / PB | 正向 |
| 估值 | 自由现金流收益率 | 自由现金流 TTM / 总市值 | 正向 |
| 估值 | 股息率 | 过去一年现金分红 / 总市值 | 正向 |
| 质量 | ROE | 净利润 TTM / 平均净资产 | 正向 |
| 质量 | ROIC | 税后经营利润 / 投入资本 | 正向 |
| 质量 | 毛利率 | 毛利 TTM / 营收 TTM | 正向 |
| 质量 | 应计利润 | (净利润 TTM - 经营现金流 TTM) / 总资产 | 负向 |
| 质量 | 资产负债率 | 总负债 / 总资产 | 负向 |
| 成长 | 营收同比 | 营收 TTM / 四个季度前营收 TTM - 1 | 正向 |
| 成长 | 净利润同比 | 净利润 TTM / 四个季度前净利润 TTM - 1 | 正向 |
| 成长 | 盈利超预期 | 实际利润与预期利润之差除以预期波动 | 正向 |
| 动量 | 12-1 动量 | close(t-21) / close(t-252) - 1 | 正向 |
| 动量 | 60 日动量 | close(t) / close(t-60) - 1 | 正向 |
| 反转 | 20 日反转 | -(close(t) / close(t-20) - 1) | 正向 |
| 低波 | 60 日波动率 | std(日收益, 60) * sqrt(252) | 负向 |
| 低波 | 下行波动率 | 只对负收益计算波动并年化 | 负向 |
| 低波 | 特质波动率 | 市场模型残差的波动率 | 负向 |
| 流动性 | 20 日换手率 | 过去 20 日换手率均值 | 视策略而定 |
| 流动性 | 20 日成交额 | log(过去 20 日成交额均值) | 正向，但需设容量上限 |
| 流动性 | Amihud 非流动性 | 平均(|日收益| / 日成交额) | 视策略而定 |
| 事件 | 涨停事件分 | 首板、连板、炸板回封等映射为规则分数 | 正向 |
| 事件 | 盈利上调 | 分析师一致预期上调幅度 | 正向 |

### 5.3 短线因子和经典因子的区别

短线多因子更适合：

```text
5 日反转
20 日动量
换手活跃度
波动和拥挤度
资金流
涨停和事件分
```

经典月频多因子更适合：

```text
估值
质量
成长
中期动量
低波
流动性
分析师预期
```

不要把两种时间尺度的因子随意混在一起。如果混用，应该先分别验证，再检查因子相关性和换手成本。

### 5.4 因子计算示例

```python
def momentum_20(close):
    """使用已完成的历史收盘价计算 20 日动量。"""
    return close.shift(1).pct_change(20)


def reversal_20(close):
    """20 日反转，方向已经统一为越大越好。"""
    return -(close.pct_change(20))


def volatility_60(returns):
    """60 日年化波动率。"""
    return returns.rolling(60).std() * (252 ** 0.5)
```

真实实现中，`close` 必须是按 `as_of_time` 截断后的复权价格，并且因子日期、可用日期和交易日期要分开保存。

## 6. 因子预处理

预处理必须逐个交易日做横截面处理，不能把不同日期的数据混在一起。

### 6.1 缺失值处理

按顺序处理：

```text
1. 如果某只股票缺失因子过多，剔除该股票
2. 如果某个因子当日覆盖率过低，跳过该因子并记录
3. 对少量缺失值，可以用行业内中位数填充
4. 填充后保留 missing_flag，便于后续分析
5. 不要用 0 直接填充所有缺失值
```

### 6.2 去极值

推荐使用 MAD 去极值：

```text
median = median(x)
mad = median(abs(x - median))
lower = median - 3 * 1.4826 * mad
upper = median + 3 * 1.4826 * mad
x = clip(x, lower, upper)
```

也可以使用 1% 和 99% 分位数截尾。两种方法选一种并固定，不要按回测结果来回切换。

### 6.3 统一方向

```text
if direction == +1:
    aligned = x
if direction == -1:
    aligned = -x
```

方向统一之后，综合分中的所有因子权重都可以按正数理解。若使用 IC 加权，也必须先统一方向，再根据历史 IC 决定权重。

### 6.4 标准化

两种常用方法：

```text
z-score:
z = (x - mean(x)) / std(x)

排名百分位:
u = rank(x) / (n + 1)
z = inverse_normal_cdf(u)
```

z-score 保留数值距离，排名法更稳健，受极值影响更小。财务数据经常使用排名法，量价因子可以使用 z-score，但都必须在同一交易日的横截面内计算。

### 6.5 行业和市值中性化

对因子做横截面回归：

```text
x = alpha
  + sum(beta_j * industry_j)
  + gamma * log(total_market_cap)
  + delta * beta
  + epsilon
```

取残差 `epsilon` 作为中性化后的因子。这样做可以降低因子只是行业暴露或小市值暴露的假象。

不是所有因子都必须中性化：

```text
估值、质量、成长：通常需要行业和市值中性化
动量：可以只做行业中性化，市值中性化要谨慎
低波：通常做行业中性化
流动性、小市值相关因子：不应完全中性化掉目标暴露
```

### 6.6 推荐处理顺序

```text
原始因子
  -> 按 as_of_time 截断
  -> 剔除不可用股票
  -> 缺失值处理
  -> 去极值
  -> 统一方向
  -> 标准化
  -> 可选的中性化
  -> 进入综合分
```

## 7. 综合评分

### 7.1 基本公式

对股票 i 在交易日 t：

```text
score(i, t) = sum_k( weight(k, t) * z(i, k, t) )
```

其中：

```text
z(i, k, t) 是股票 i 在第 k 个因子上的标准化值
weight(k, t) 是该因子的权重
```

最终可以把综合分再次标准化到 0 到 100，方便展示，但排序本身不受影响。

### 7.2 等权

第一版建议使用等权：

```text
weight(k) = 1 / 因子数量
```

优点：

```text
实现简单
不容易过拟合
每个因子的作用可以单独解释
后续替换权重时容易比较
```

缺点：

```text
没有考虑因子历史有效性
因子之间可能高度相关
```

### 7.3 固定权重

例如：

```text
score = 0.30 * z(20 日动量)
      + 0.20 * z(-5 日收益)
      + 0.20 * z(换手活跃度)
      - 0.15 * z(20 日波动率)
      + 0.15 * z(事件分)
```

这些权重只是工程示例，不是推荐参数。固定权重必须在开发区间确定，然后在验证区间冻结。

### 7.4 IC 加权

先定义：

```text
IC(k, t) = corr(factor(i, k, t), future_return(i, t + 1))
RankIC(k, t) = SpearmanCorr(factor(i, k, t), future_return(i, t + 1))
```

使用滚动历史 IC 计算权重：

```text
raw_weight(k, t) = mean(IC(k, t-window : t-1)) / std(IC(k, t-window : t-1))
raw_weight(k, t) = clip(raw_weight(k, t), 0, max_weight)
weight(k, t) = raw_weight(k, t) / sum_j(raw_weight(j, t))
```

必须注意：

```text
只能使用 t-1 及之前的数据
不能使用未来 IC
窗口长度要固定
IC 为负的因子应该先检查方向，而不是直接给负权重
权重变化过快会增加换手和成本
```

### 7.5 回归和机器学习

可以在以下条件满足后再考虑：

```text
因子已经通过单因子检验
因子之间高度相关的已经合并或剔除
开发、验证、观察区间已经固定
成本、涨跌停和 T+1 已经进入回测
```

常见方法：

```text
横截面回归：future_return = sum(beta_k * factor_k) + error，用预测值作为综合分
正则化回归：Ridge、Lasso、Elastic Net
树模型：用于非线性交互，但可解释性下降
机器学习模型：只能作为排序器，不能绕过组合约束和撮合规则
```

机器学习不应成为第一版。第一版的目标是证明数据链路、因子方向、组合构建和成本模型是对的。

### 7.6 缺失因子处理

如果某只股票只缺失部分因子：

```text
方法一：剔除该股票
方法二：对可用因子重新归一化权重
方法三：用行业内中位数填充，并保留缺失标记
```

方法二会增加不可比性，方法一最保守。无论选择哪种，都要在日志中记录剔除原因和数量。

### 7.7 一个横截面示例

假设某日只有 5 只股票，3 个因子已经完成去极值、方向统一和标准化：

| 股票 | z(动量) | z(-5 日收益) | z(-波动) | 等权分 | 排名 |
|---|---:|---:|---:|---:|---:|
| A | 1.10 | -0.80 | -0.90 | -0.20 | 4 |
| B | 0.10 | 0.50 | 0.40 | 0.33 | 2 |
| C | -0.90 | -0.40 | -1.30 | -0.87 | 5 |
| D | 0.70 | 1.40 | 0.90 | 1.00 | 1 |
| E | -0.20 | 0.20 | 0.00 | 0.00 | 3 |

如果组合只选前两名，就选 D 和 B。这里的数字只是算法示意，不是实际市场数据。

## 8. 组合构建

### 8.1 选股规则

常见方式：

```text
选择综合分最高的前 N 只
选择综合分最高的前 10% 或前 20%
使用缓冲区，只有跌出前 20% 或后 30% 才卖出，降低换手
```

第一版建议使用固定数量，例如：

```text
top_n = 30
buffer_in = 前 20%
buffer_out = 前 40%
```

### 8.2 权重分配

常见方式：

```text
等权：每只股票权重相同
综合分加权：分数越高权重越大
波动率倒数加权：低波动股票权重更高
风险平价：按协方差分配风险
组合优化：最大化预期收益，同时惩罚风险、换手和约束
```

第一版建议从等权开始。等权不是低级的做法，它能先暴露选股逻辑本身的问题。

### 8.3 组合约束

至少包含：

```text
单票最大权重
最大持仓数量
行业最大权重
因子暴露限制
每次调仓最大换手
现金保留比例
最低流动性要求
ST 和停牌股票不可买入
涨跌停和 T+1 不可成交处理
```

### 8.4 优化形式

如果使用组合优化，可以写成：

```text
maximize    alpha^T * w
          - lambda * w^T * Sigma * w
          - cost * turnover

subject to  sum(w) = 1 - cash_buffer
            0 <= w_i <= max_weight
            industry_exposure <= industry_limit
            factor_exposure <= factor_limit
            turnover <= turnover_limit
```

其中：

```text
alpha 是综合分或预测收益
Sigma 是收益协方差矩阵
lambda 是风险厌恶系数
turnover 是相对当前持仓的换手
```

第一版可以用规则选股加等权，不必立刻做完整优化器。

### 8.5 示例参数

```toml
[rebalance]
frequency = "monthly"

[portfolio]
top_n = 30
max_weight = 0.04
max_industry_weight = 0.20
max_turnover = 0.40
cash_buffer = 0.05
buffer_in_quantile = 0.20
buffer_out_quantile = 0.40
```

## 9. 市场状态与风险控制

多因子选股解决的是横截面选股问题，不能自动解决系统性风险。市场状态应该单独作为风险开关：

```text
指数是否在长期均线之上
市场上涨和下跌家数
涨停和跌停家数
成交额是否明显萎缩
市场波动率是否突然升高
因子近期是否出现拥挤或失效
```

示例规则：

```text
风险偏高：总仓位从 100% 降到 50%
极端行情：暂停开新仓，只处理已有持仓
波动率骤升：减少持仓数量，提高现金比例
因子拥挤：降低该因子权重，或暂时使用等权
```

市场状态规则必须简单、可解释、可回测，不要用一堆事后拟合的阈值。

## 10. 回测与撮合

### 10.1 时间线

```text
T 日收盘后
  -> 数据更新
  -> 计算因子
  -> 生成综合分
  -> 生成目标组合

T+1 日
  -> 开盘或指定时间尝试成交
  -> 处理涨跌停、停牌和部分成交
  -> 更新持仓和现金

T+1 日收盘后
  -> 计算收益和评价指标
```

不能使用 T+1 的最高价、最低价或收盘价反推 T 日或 T+1 开盘时就知道的结果。

### 10.2 A 股交易约束

```text
T+1：买入日不能按普通规则当天卖出
涨停：可能买不到
跌停：可能卖不出
停牌：不能成交
ST 和特殊股票：涨跌幅限制不同
最小交易单位：通常为 100 股
交易费用：佣金、印花税、过户费等按最新规则配置
滑点：按成交额、波动率和参与率建模
成交量上限：单日成交不能超过市场成交量的一定比例
```

### 10.3 成本模型

```text
买入成本 = 佣金 + 过户费 + 滑点
卖出成本 = 佣金 + 印花税 + 过户费 + 滑点
```

示例配置：

```toml
[execution]
commission_rate = 0.0003
stamp_tax_sell = 0.0005
transfer_fee = 0.00001
slippage_bps = 10
lot_size = 100
max_participation_rate = 0.05
```

这些只是配置示例，实际费率必须按当时规则核实。

### 10.4 订单记录

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

订单提交不等于成交。部分成交、拒单和撤单都必须进入回测结果。

## 11. 因子与策略评价

### 11.1 单因子评价

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

### 11.2 组合评价

```text
总收益
年化收益，仅在样本足够时报告
最大回撤
波动率
夏普比率
胜率
盈亏比
平均持仓时间
换手率
交易次数
成本前收益和成本后收益
成交率
未成交原因
按市场阶段分组的收益
按行业、市值和流动性分组的收益
```

### 11.3 稳健性评价

```text
不同历史区间是否稳定
不同股票池是否稳定
不同调仓频率是否稳定
不同交易成本假设下是否稳定
剔除表现最好的少数交易后是否仍然成立
单因子和综合分排名是否单调
因子之间是否高度相关
```

一个多因子策略如果只在某一年、某一行业或少数几笔交易上有效，不能视为稳健。

## 12. 权重校准与防过拟合

推荐时间切分：

```text
开发区间：定义因子、方向和候选参数
验证区间：冻结因子后检查稳健性
观察区间：完全不参与调参，最后只检查一次
```

推荐做法：

```text
先固定因子定义，再调权重
先使用等权，再使用 IC 加权
每次只改变一个变量
记录每次实验的配置和结果
优先选择参数平原，而不是单点最优
不要随机打乱时间序列
不要用验证集反复改参数
```

参数敏感性检查：

```text
top_n 从 20 到 50 是否都合理
调仓周期从 10 日到 40 日是否都合理
中性化前后差异是否可解释
不同去极值方法是否差异巨大
成本提高一倍后是否仍然成立
```

## 13. 常见错误

### 13.1 时间相关

```text
用报告期代替公告日
用当前指数成分股回看历史
用未来的行业分类
用未来价格或未来收益构造特征
用未来标签训练模型后在同一段回测
```

### 13.2 数据处理

```text
缺失值统一填 0
极值不处理
不同因子方向未统一
不同日期的数据混在一起标准化
行业中性化后又把行业因子当作选股因子
复权价格和原始价格混用
```

### 13.3 组合和交易

```text
只算收益，不算费用和滑点
选到涨停股却假设一定买得到
选到跌停股却假设一定卖得出
忽略 T+1
忽略小市值股票的容量限制
忽略部分成交和现金约束
```

### 13.4 研究过程

```text
不断在验证集上调参
只展示最好的一段回测
因子数量越多越好，没有检查相关性
把长期有效和短期有效因子随意混合
把单次成功当作稳定规律
```

## 14. 工程实现

### 14.1 推荐目录

```text
ashort_quant/
  config/
    multifactor.toml
  src/
    data/
      pit.py
      universe.py
      quality.py
    factors/
      definitions.py
      value.py
      quality.py
      growth.py
      momentum.py
      volatility.py
      liquidity.py
      events.py
      preprocess.py
      composite.py
    portfolio/
      constructor.py
      constraints.py
      risk.py
    strategy/
      multifactor.py
    execution/
      simulator.py
    reports/
      factor_report.py
      portfolio_report.py
  scripts/
    run_multifactor_backtest.py
```

第一版也可以继续压缩成：

```text
data.py        数据加载和时间对齐
factors.py     因子计算和预处理
multifactor.py 合成评分和组合构建
backtest.py    撮合、账户和报告
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


@dataclass(frozen=True)
class FactorSnapshot:
    trade_date: str
    symbol: str
    values: dict[str, float]
    available_at: datetime


@dataclass(frozen=True)
class TargetPortfolio:
    trade_date: str
    weights: dict[str, float]
    scores: dict[str, float]
    reasons: dict[str, str]
```

策略层只负责生成目标组合和理由，不直接操纵账户。组合约束、撮合和账户更新分别放在独立模块。

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

[composite]
method = "equal_weight"
missing = "renormalize"

[portfolio]
top_n = 30
max_weight = 0.04
max_industry_weight = 0.20
max_turnover = 0.40
cash_buffer = 0.05

[execution]
commission_rate = 0.0003
stamp_tax_sell = 0.0005
transfer_fee = 0.00001
slippage_bps = 10
lot_size = 100

[backtest]
start_date = "2020-01-01"
end_date = "2024-12-31"
development_end = "2022-12-31"
validation_end = "2023-12-31"
```

### 14.4 主流程伪代码

```python
def run_multifactor(as_of, config):
    """在 as_of 时点生成目标组合，只使用当时可获得的数据。"""

    universe = build_universe(as_of=as_of, config=config.universe)

    raw_factors = {}
    for spec in config.factor_specs:
        raw_factors[spec.name] = compute_factor(
            spec=spec,
            universe=universe,
            as_of=as_of,
        )

    z_factors = {}
    for name, values in raw_factors.items():
        spec = config.factor_specs[name]
        values = handle_missing(values)
        values = winsorize(values)
        values = values * spec.direction
        values = standardize(values)
        if spec.neutralize:
            values = neutralize(
                values,
                universe=universe,
                by=["industry", "log_market_cap"],
            )
        z_factors[name] = values

    score = composite_score(
        factors=z_factors,
        weights=config.weights,
        missing="renormalize",
    )

    target = construct_portfolio(
        score=score,
        current_holdings=config.current_holdings,
        constraints=config.portfolio,
    )

    return target
```

真正运行时，`as_of`、数据版本和因子版本都必须写入运行日志。没有这些信息，结果不可复现。

## 15. 第一版最小纵向切片

第一版不要同时实现二十个因子。建议只做下面的纵向切片：

```text
股票池：全 A，剔除 ST、停牌、上市不足 120 日和低流动性股票
因子：20 日反转、60 日动量、60 日低波
处理：去极值、方向统一、标准化、行业中性化
合成：等权综合分
调仓：每月最后一个交易日收盘后生成信号，下一交易日执行，新开仓目标持有约 20 个交易日
选股：综合分前 30 名
约束：单票不超过 4%，行业不超过 20%，现金保留 5%
撮合：T+1、涨跌停、停牌、佣金、印花税和滑点
评价：RankIC、分位收益、净值、回撤、换手和未成交原因
```

实现顺序：

```text
1. 建立交易日历和历史股票池
2. 对齐日线、停牌、市值和行业数据
3. 实现三个因子及预处理
4. 实现等权综合分和前 30 名选股
5. 实现月频调仓和 A 股撮合
6. 输出 RankIC、分位收益和组合报告
7. 确认无未来数据后，再增加估值、质量和成长因子
```

## 16. 验收清单

```text
[ ] 数据按可用时间对齐，没有未来数据
[ ] 股票池按历史时点生成，没有幸存者偏差
[ ] 财务数据按公告日可用
[ ] 因子方向统一，缺失值和极值有明确规则
[ ] 截面处理只在同一交易日内进行
[ ] 中性化前后的因子都有记录
[ ] 综合分权重来自开发区间，没有用验证集调参
[ ] T+1、涨跌停、停牌和费用进入撮合
[ ] 部分成交和未成交原因有记录
[ ] RankIC、ICIR 和分位组合收益可复现
[ ] 成本后收益，而不是只报告成本前收益
[ ] 开发、验证和观察区间没有混用
[ ] 运行配置、数据版本和因子版本已保存
```

## 17. 结论

多因子选股算法的核心是：

```text
时间点对齐的数据
+ 可解释的因子
+ 严格的横截面预处理
+ 方向统一的综合评分
+ 带约束的组合构建
+ A 股真实撮合
+ 样本外验证
```

工程上先完成“三个因子、等权评分、月频调仓、成本后回测”的最小闭环，再考虑 IC 加权、机器学习、因子择时和更复杂的优化器。第一版能稳定复现并解释每个入选理由，比堆更多因子更重要。
