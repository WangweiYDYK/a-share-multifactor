# Todo

## 暂缓：QMT 数据源与执行接入

当前不接入 QMT，相关事项列为后续候选，不进入当前实现主线。

### 前置条件

- [ ] 确认本机 QMT 客户端、`xtquant` 或实际 SDK 的版本与接口能力。
- [ ] 确认可用的交易日历、日线、证券基础信息权限和数据字段口径。
- [ ] 确认 QMT 与 BaoStock、Tushare 在股票代码、成交量单位、成交额单位和复权口径上的差异。
- [ ] 将 `normalize.py` 中按 `raw.source` 分支的字段映射拆为按 provider 注册的映射器，再接入 QMT。

### 第一阶段：只做数据源

- [ ] 新增 `data/providers/qmt.py`。
- [ ] 只覆盖交易日历、日线和证券基础信息。
- [ ] 通过 `RawDataset -> CanonicalDataService -> Snapshot -> DataRepository` 主链路接入。
- [ ] 在快照 `manifest.json` 中记录 QMT 的实际来源、版本、时间范围和字段类型。
- [ ] 不对 QMT 暂不提供的财务、行业、退市、公司行动等数据做占位或推测。

### 后续阶段：模拟交易或实盘执行

- [ ] 与数据源适配器分开，单独建立 `execution/brokers/qmt.py` 或等价执行层模块。
- [ ] 先完成模拟交易和账务闭环验证，不以回测结果直接切换实盘。
- [ ] 在获得明确授权并通过仿真验证前，不连接真实交易账户，不发送真实订单。
