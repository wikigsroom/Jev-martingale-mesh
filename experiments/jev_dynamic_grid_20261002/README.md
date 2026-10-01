# ETHUSDT JEV 光谱动态网格方案

这是针对“动态网格”要求重新建立的独立实验目录。它不把动态参数当作展示字段，而是让参数变化实际撤掉旧加仓单、重算持仓止盈价，并按新间距重新挂出下一层加仓单。

## 策略定义

- 初始权益：`1000 USDT`。
- `base_amount_min` 与交易所最小名义约束：`130 USDT`；任何方向的第一笔和加仓都不能低于该名义金额。
- 撮合分辨率：全程 `1 秒`，收益、手续费、资金费和风险检查均按秒计算；曲线文件按 60 秒采样只是展示压缩。
- 网格间距：以已完成分钟的波动率 `sigma30` 为基础，乘以 `vol_multiplier`，再叠加趋势压力和 JEV 事件压力，最后裁剪到 `[minimum_spacing, maximum_spacing]`。
- 止盈距离：`profit_fraction * 当前网格间距`，不是固定的旧止盈距离。
- 加仓：`max_adds >= 1`，每次动态更新会清除旧的待成交加仓报价；当前持仓仍保留，下一秒按新网格重挂。
- 风险控制：趋势状态限制反向开仓；JEV 高压力期间禁止新开仓和新加仓。回撤停机限制保持关闭，不用 15% 之类的隐藏上限。

## JEV 的位置

JEV 使用 ETHUSDT 专属的、按月 walk-forward 校准的 NanoJev choice head。输入事件来自 BlockBeats public newsflash feed；历史首达时间不可得，因此采用发布时间代理，并在 `available_ms + 5 秒` 后才让策略使用。`p_breakout = p_up + p_down` 只表示短时方向突破压力代理，不是盈利概率。

JEV 不是“每条新闻固定停机”：它按事件压力连续放大网格间距、提高止盈距离、降低倍增和允许的加仓层数；只有压力超过冻结阈值时才触发入场/加仓门控。报告同时运行同参数 `mode=1` 的无 JEV 反事实，单独估计 JEV 的增量。

## 运行

```powershell
$env:NUMBA_CACHE_DIR = "$PWD/.numba-cache-dynamic-20261002"
python -m experiments.jev_dynamic_grid_20261002.verify
python -m experiments.jev_dynamic_grid_20261002.tune --stage screen --workers 2
python -m experiments.jev_dynamic_grid_20261002.tune --stage validate --workers 2 --top-n 6
python -m experiments.jev_dynamic_grid_20261002.run --stage final --config experiments/jev_dynamic_grid_20261002/reports/selected_config.json
```

## 固定约束与数据边界

数据签名固定为 `42a2ead2d2c80a1fce404526e6bdcd30b86ed5bd891a172602ce78f24ee3d3ad`，覆盖 `2023-09-24T00:00:00Z` 至 `2026-09-22T00:00:00Z`（结束时间不含）。这接近但不是自然月意义上的完整 36 个月；报告会保留真实边界，不补造 2026-09-22 之后的资金费或行情。

本实验不调用 Docker、WSL 或其他虚拟化环境。最终文件位于 `reports/`：滚动筛选、验证、逐期限收益表、60 秒展示曲线、JEV 事件轨迹和运行审计。
