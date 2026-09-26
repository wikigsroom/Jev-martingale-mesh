# 2026-06 至 2026-09 历史回测（R1 / 5 秒 JEV）

- 回放区间：2026-06-22T00:00:00Z 至 2026-09-22T00:00:00Z（UTC，右端不含；实际最后一根 1 秒 bar：2026-09-21T23:59:59+00:00）
- 本区间独立从 1000.0000 U 起始，未继承区间开始前的持仓或权益。
- 初始资金 / 期末权益：1000.0000 U / 984.0180 U
- 净收益 / 区间回报 / CAGR：-15.9820 U / -1.5982% / -6.1960%
- 最大回撤 / 日频 Sharpe（无风险利率 0，年化 365）：12.9009% / -0.2262
- 成交 / 网格加仓 / 止盈 / 风控止损 / 强平：79 / 29 / 0 / 24 / 0
- 每笔成交名义金额下限：130.0000 U；严格高于交易所历史最低名义额 100 U（后续规则最低 50 U）。
- 正收益 / 亏损 / 持平月份：1 / 3 / 0；账户回撤停机：已关闭；因强平触发的停机：False。
- 有成交月份 / 最长连续无成交月份：4 / 0。
- JEV 信号 / JEV 平仓腿数 / JEV veto：924 / 0 / 9010
- 单边风险锁存：关闭；切换 / 保护性 veto / 反向腿平仓：0 / 0 / 0。
- 区间内 JEV 事件数 / accuracy / log-loss / Brier：13306 / 0.8015932661957011 / 0.6244325253647921 / 0.33269722064085844。
- 区间内 JEV 真实类别 up/range/down：[1321, 10666, 1319]；预测类别 up/range/down：[0, 13306, 0]。
- 手续费 / 净资金费：51.7822 U / -0.4684 U

## 回测口径
- 参数文件：`reports/fmz_v2/optimized_standard_parameters.json`；账户回撤停机阈值：关闭；账户初始权益 1000.0000 U，逐秒回放，分钟指标只用于因果特征，不重调 R1 网格参数。
- JEV 概率由冻结 NanoJev backbone 与按月 walk-forward 训练/选择/校准的 ChoiceHead 产生；事件在 `available_ms + 5s` 才进入策略，阈值与持有期保持冻结参数。
- 事件训练标签是未来 30 秒 BTCUSDT 价格区间代理，不代表新闻对价格的因果影响，也不等同于强平概率。
- 行情为 Binance 公共 USD-M BTCUSDT 1 分钟归档及聚合逐笔成交构造的 1 秒 OHLC；资金费率来自公开历史归档。
- 区间内行情缺失 mark 分钟并用因果 basis 重建：0 条；行情分钟数和资金费率覆盖审计见 dataset manifest。
- 新闻数据源：BlockBeats public newsflash feed；时间依据：BlockBeats newsflash add_time; historical first-receipt time unavailable，策略另加 5 秒延迟。
- 新闻内容口径：Public newsflash API snapshot; historical content snapshots unavailable。
- JEV 主干推理：cuda / float16 / batch 4 / 4 线程；评分头训练与推理设备：cuda。

## 重要限制
- 这是历史区间扩展压力测试，不是独立样本外收益承诺：R1 策略参数曾用 2026 数据筛选。
- 区间数据截至 2026-09-21T23:59:59+00:00 UTC；未将最后可用 bar 之后的数据外推。
- 详细指标：`metrics.json`；逐日/逐月汇总：`daily.csv`、`monthly.csv`；完整分钟权益与交易决策轨迹为同目录 CSV。
