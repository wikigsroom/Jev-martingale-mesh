# JEV 优化方案：标准窗口 1 秒回测

## 结论

在当前数据、当前执行模型和本轮候选空间内，最终采用 `JEV action=3`：事件触发后只暂停新增腿和再入场，不强制平掉已有仓位；已有篮子仍由止盈、篮子止损和硬风险规则管理。

这不是收益承诺。当前策略的 36 个月收益为 `+103.6567%`，最大回撤为 `55.9477%`；短期窗口仍可能亏损，不能据此宣称达到 `500%+`。

## 选择口径

- 先用 1 分钟数据筛选 110 个候选，覆盖 3/6/9/12/24/36 个月窗口。
- 再用完整 1 秒数据复核 JEV 阈值与持有期候选；所有窗口独立从 `1,000 U` 起算。
- 硬约束固定为 `base_amount_min=130 U`、`min_trade_notional=130 U`、账户回撤停机关闭、杠杆不超过 `10x`、不使用 Docker/WSL/虚拟化环境。
- 选择优先考虑完整 36 个月 1 秒收益且要求无强平；因此没有采用只在短期预筛中得分最高、但 1 秒长期收益较低的候选。

## 最终参数

### 账户、成本与下单

| 参数 | 值 |
|---|---:|
| initial_equity | 1000 U |
| base_amount_min | 130 U |
| min_trade_notional | 130 U |
| leverage | 10x |
| gross_utilization | 0.70 |
| account_drawdown_stop | 0（关闭） |
| stop_cooldown_minutes | 0 |
| reentry_delay_seconds | 30 |
| poll_seconds | 1 |
| maker/taker fee | 0.02% / 0.05% |
| slippage | 2 bps |

### 网格、趋势和风险

| 参数 | 值 |
|---|---:|
| controller | 4（趋势单向入口） |
| base_spacing | 0.003 |
| base_amount_rate | 1.0 |
| ratio | 1.05 |
| profit_target | 0.20 |
| max_loss_notional_multiple | 9 |
| max_adds | 3 |
| basket_stop | 0.40 |
| EMA fast / slow | 60 / 120 分钟 |
| trend_enter | 0.003 |
| trend_exit_fraction | 0.70 |
| trend_confirm_minutes | 60 |
| trend_liquidate_opposite | true |
| allow_countertrend_add | false |
| directional_stop / trail | 0.05 / 0.20 |
| risk_guard | 0（关闭） |

### JEV 规则

| 参数 | 值 |
|---|---:|
| jev_action | 3：暂停新增腿/再入场，不强制清仓 |
| jev_breakout_threshold | 0.30 |
| jev_direction_threshold | 0.25 |
| jev_hold_seconds | 180 |
| news_delay_seconds | 5 |

## 标准窗口结果

所有区间右端为 `2026-09-22 00:00:00 UTC`，最后可用 1 秒 bar 为 `2026-09-21 23:59:59 UTC`。

| 窗口 | 独立区间 | 期末权益 | 收益率 | CAGR | 最大回撤 | 成交 | 加仓 | 风控止损 | JEV信号 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 3m | 2026-06-22 至 2026-09-22 | 984.02 U | -1.60% | -6.20% | 12.90% | 79 | 29 | 24 | 924 |
| 6m | 2026-03-22 至 2026-09-22 | 817.49 U | -18.25% | -32.97% | 30.05% | 174 | 68 | 50 | 2,387 |
| 9m | 2025-12-22 至 2026-09-22 | 1,244.39 U | +24.44% | +33.84% | 29.13% | 327 | 135 | 92 | 3,961 |
| 12m | 2025-09-22 至 2026-09-22 | 1,632.83 U | +63.28% | +63.34% | 27.31% | 489 | 195 | 143 | 6,067 |
| 24m | 2024-09-22 至 2026-09-22 | 2,040.75 U | +104.08% | +42.89% | 32.25% | 1,101 | 465 | 312 | 12,376 |
| 36m | 2023-09-25 至 2026-09-22 | 2,036.57 U | +103.66% | +26.83% | 55.95% | 1,648 | 726 | 451 | 16,495 |

六个窗口均无强平；账户回撤停机关闭，所以权益可能经历超过 50% 的历史回撤而继续运行。

## JEV 模型评估

当前 walk-forward JEV 共 `122,060` 个预测，预测类别计数为 `up=0 / range=122,060 / down=0`。整体 accuracy `76.03%` 与类别不平衡基线相同；这说明 accuracy 不能证明方向判断有效。当前策略把 JEV 用作风险闸门而不是方向开仓器，是为了避免把不可靠的 direction 概率直接用于双向马丁的反向平仓。

`action=3` 在 36 个月中产生 `16,495` 个策略信号和 `217,910` 次 veto 检查，但强制平仓腿数为 `0`。这使 JEV 主要承担“停止继续堆仓”的职责，而不是在噪声事件下频繁反向交易。

后续真正提升模型质量的优先级是：对 breakout 类别重采样/加权、增加概率校准与 PR-AUC/事件后回撤指标、将方向头限定在 breakout 条件下训练、加入 30/60/180 秒多周期标签，并按策略收益 uplift 做 walk-forward 选择；不能继续只优化 accuracy。

## 文件

- 最终参数：`reports/fmz_v2/optimized_standard_parameters.json`
- 六窗口汇总：`reports/fmz_v2/standard_backtest_optimized_1s/summary.json`
- 六窗口 CSV：`reports/fmz_v2/standard_backtest_optimized_1s/summary.csv`
- 每窗口详细指标、逐日、逐月、分钟权益和交易轨迹：对应 `3m`、`6m`、`9m`、`12m`、`24m`、`36m` 子目录。
- 图表：`standard_returns_drawdown.png`、`standard_equity_curves.png`、`standard_monthly_heatmap.png`。
