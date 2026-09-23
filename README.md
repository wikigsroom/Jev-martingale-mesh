# jev-mesh：BTCUSDT 永续事件网格研究程序

GitHub使用两个分支：[main程序仓库](https://github.com/wikigsroom/Jev-martingale-mesh/tree/main)与[docs独立文档](https://github.com/wikigsroom/Jev-martingale-mesh/tree/docs)。克隆、文件范围和文档同步见[Git仓库说明](docs/repository_layout.md)。Git克隆不包含行情原始/处理数据或模型权重；本机现有数据不随代码推送，完整回测仍需按下述流程准备。

**FMZ源码版本（本次交付）**：依据`参考内容/FMZ.COM`中实际导出的固定锚点、线性网格重新实现，加入止盈后的趋势确认、反向平仓和NanoJev干预。报告、参数、3000组搜索记录及秒级复核集中在[reports/fmz_v2](reports/fmz_v2/final_report_zh.md)。上一轮研究仍保留在原路径，使用的是另一种网格结构，结果不能混为一谈。

方案已记录为 **FMZ-JEV-20260923-R1**：[文档总入口](docs/fmz-v2/README.md)、[方案与决策记录](docs/fmz-v2/strategy_record.md)、[JEV角色及效果说明](docs/fmz-v2/jev_role.md)、[复现和归档](docs/fmz-v2/reproduction.md)。JEV主要限制新闻发生后的追加与重开；止盈后的趋势判断由EMA模块负责。当前历史盈利结果对成本和消息延迟敏感，JEV尚未证明优于简单新闻暂停规则。

**60秒新闻延迟复跑（FMZ-JEV-20260923-L60）**：按新要求，仅把延迟从5秒改为60秒，100U变为77.9442U，净收益−22.0558%，最大回撤25.2317%，模拟爆仓0次，5月23日触发账户回撤停机。[完整报告与轨迹](reports/fmz_v2/news_delay_60s_20260923/report_zh.md)、[60秒参数](reports/fmz_v2/news_delay_60s_20260923/parameters.json)。复用原JEV分数并延后交付，没有重新推理或调参；原R1的5秒参数及成绩保留为对照。

```powershell
python scripts/run_fmz.py --params reports/fmz_v2/news_delay_60s_20260923/parameters.json --resolution 1s --start 2026-03-01 --end 2026-09-22 --out reports/fmz_v2/news_delay_60s_20260923/reproduce_60s
```

复现原R1的5秒延迟基线：

```powershell
python scripts/run_fmz.py --resolution 1s --start 2026-03-01 --end 2026-09-22 --out reports/fmz_v2/reproduce
python scripts/run_fmz.py --variant no_jev --out reports/fmz_v2/reproduce_no_jev
```

本次完整流程（原生Windows Python，无Docker/WSL）：

```powershell
python scripts/review_fmz.py
node scripts/probe_fmz_source.cjs
python -m pytest -q
python scripts/tune_fmz.py --trials 3000 --workers 4 --out reports/fmz_v2/search_margin_verified
python scripts/confirm_fmz.py --search reports/fmz_v2/search_margin_verified --limit 10
python scripts/evaluate_fmz.py
python scripts/diagnose_fmz_events.py
python scripts/screen_fmz_costs.py
python scripts/build_fmz_report.py
```

上述调优流程会重写`reports/fmz_v2`中的相应结果；重跑新实验前应保留当前冻结记录。`run_fmz.py`只做模拟，默认读取`reports/fmz_v2/final_parameters.json`，可用`--params`切换配置。`--end`为不包含该日的UTC边界。策略按秒运行，趋势指标使用此前已完成分钟；成交后报价等待`poll_seconds`与止盈后重开等待`reentry_delay_seconds`分别生效。

FMZ版由`fmz_engine.py`、`fmz_config.py`、`fmz_data.py`实现。已持仓和双方下一层挂单共同占用保证金预算，成交和重新开仓时再次检查；保留止盈保护，不将拒单算作加仓。结果提供分钟权益和发生动作的秒级汇总轨迹，后者不是交易所逐订单回执。JEV模式支持双边清仓、方向干预和仅禁止加仓/重开；实际选中的模式及其贡献须以同参数关闭JEV的对照为准。

## 前一轮研究与共享数据/模型

原生 Windows / Python 实现的双向有限马丁网格、NanoJev 新闻事件熔断、回测和自动调参程序。固定初始总资金100 USDT、杠杆上限10倍；触发事件时两边全部平掉并暂停网格。**包含模拟成交和爆仓；没有真实交易下单接口。**

“秒级”表示行情回放和决策频率，金融标签为未来30秒；最终参数不要求每笔仓位几秒内平掉。主参数最大一轮为48小时，见报告中的适用范围。

最终结果见 [中文报告](reports/final_report_zh.md)、[独立HTML报告](reports/final_report_zh.html)、[最终参数](reports/final_parameters.json)。搜索可以得到亏损结果，程序不会伪造盈利参数。报告中的全期、独立9月测试、未来条件情景为不同口径。

## 已实现

- 多、空分别维护均价、止盈、上一次加仓价格和有限追加层数；数量和价格按交易所步长取整。
- 初始双边开仓预检、总名义额预算、无追加保证金、历史最小订单金额变化。
- Maker/Taker费、额外市价滑点、实际资金费、多空未实现盈亏与标记价维持保证金。
- 整篮止损、价格趋势/振幅闸门、账户回撤停机、跳空优先强平及强平费用。
- 真实NanoJev权重推理；冻结骨干，用3—5月数据训练金融决策头，6月选择与校准。
- 秒级事件输入，预测未来30秒的上涨/震荡/下跌；突破概率触发全部弃仓。
- 保留原始ZIP、SHA256、新闻时间戳、数据缺口检查、候选参数和冻结记录。
- 独立执行保护状态机处理撤单、两边清仓、部分成交重试、零仓位确认及暂停；只返回执行意图。

## 快速复现已有回测

在此目录的原生 PowerShell 执行，不需要 Docker、WSL 或虚拟机：

```powershell
python -m pip install -e ".[test,report]"
python -m pytest -q
python scripts/run.py backtest --params reports/final_parameters.json --resolution 1s --start 2026-09-01 --end 2026-09-22 --out reports/my_holdout
```

日期为UTC，`--end`不包含该日。已有数据覆盖2026-03-01至2026-09-21，共205个完整日。默认使用最后冻结的金融NanoJev信号。`--signals none`为相同网格参数的不使用新闻对照；另有`rules`和`original_nanojev`。

```powershell
python scripts/run.py backtest --signals none --resolution 1s --start 2026-09-01 --end 2026-09-22 --out reports/my_no_news
python scripts/run.py backtest --resolution 1m --out reports/my_mark_check
```

输出JSON指标和CSV权益轨迹。1秒回放仍按每秒计算，默认将记录压缩为每分钟末一条，以控制内存。`--path 0`逐秒采用两种OHLC路径中较低的权益，`1`先高后低，`2`先低后高；它们都不是盘口级真实撮合重放。

## 完整数据、模型、调参与报告流程

现有原始数据和模型已在本机下载，无需重复联网。若要从头获取：

```powershell
python scripts/acquire.py market --end 2026-09-22
python scripts/repair_market.py
python scripts/fetch_exchange_rules.py
python scripts/acquire.py prepare
python scripts/acquire.py news
python scripts/acquire_seconds.py --start 2026-03-01 --end 2026-09-21 --workers 3
python scripts/prepare_events.py
python scripts/download_model.py
```

公开REST访问受网络限制时，`fetch_exchange_rules.py --proxy http://你的代理:端口`可显式指定代理，不修改系统代理。新闻及交易所接口均不需要私有账户凭证。数据已冻结到本次窗口；要换窗口，必须同步修改资金费请求和训练切分，不能只更改回测参数便声称重新完成实验。

本机常规Python使用CPU版PyTorch。训练/实时模型推理使用现有的原生CUDA Python可执行文件；调用Python本身，不启动所在软件的应用或服务：

```powershell
$JevCudaPython = 'D:\dev\comfyui\1_17_2\python_embeded\python.exe'
& $JevCudaPython -I scripts/fit_nanojev.py --batch 3
```

`-I`隔离用户site-packages，防止本机CPU版PyTorch覆盖该原生CUDA运行时。在其他机器可直接使用已安装CUDA版PyTorch、Transformers、Safetensors的Python。此次验证运行时：Python3.12、PyTorch2.10.0+cu126、Transformers5.3.0、RTX3060 12GB。骨干BF16，决策头float32。

然后按顺序执行：

```powershell
python scripts/audit_data.py
python scripts/tune.py --trials 300 --shortlist 10
python scripts/freeze_active_event.py
python scripts/evaluate.py
python scripts/build_report.py
```

`tune.py`只接触9月前的策略收益，写入参数、模型和引擎哈希；`evaluate.py`先核对哈希，再测试9月。再次根据9月结果选择参数会破坏这个留出划分。两次前期探索及其淘汰原因保存在`reports/preliminary*`，不是隐去的不利结果。

## 单条事件推理

```powershell
& $JevCudaPython -I scripts/score_event.py --input examples/event_question.json
```

示例只有已知状态、问题和三个候选，不包含未来标签。实际消息服务需构造相同的30秒问题结构，并提供当时已完成的历史行情。`EventScorer`可以常驻进程，避免每条消息重载模型。输出的`p_breakout=p_up+p_down`是短时价格方向越阈值代理，不是爆仓概率或盈利概率。现有样本没有证明模型优于简单类别频率基线。

`FlattenGuard.trigger()`阻止新开仓；`reconcile()`先处理挂单，再给出LONG和SHORT的只减仓意图。订单完成或失败后通过`order_finished()`解除在途标记，再根据实际剩余量重试。只有挂单消失、两侧仓位均为零才进入PAUSED，冷却结束且行情健康才可恢复。接入具体交易所时，需正确映射对冲模式的positionSide和只减仓语义。

## 文件说明

| 路径 | 内容 |
| --- | --- |
| `src/jevmesh/engine.py` | Numba网格、费用、保证金、资金费、强平和账户停机模拟 |
| `src/jevmesh/guard.py` | 撤单—双侧清仓—确认—暂停执行状态机 |
| `src/jevmesh/nanojev.py` | 与NanoJev原始选择头结构一致的实现 |
| `src/jevmesh/model_runtime.py` | 真实模型单条事件推理 |
| `src/jevmesh/dataset.py` | 新闻可用时间、无未来行情输入、30秒标签和回放数据 |
| `models/event_head.pt` | 最终金融决策头及温度参数 |
| `models/nanojev-unified` | 官方开源模型权重与分词器 |
| `reports/final_parameters.json` | 最终选中参数，不能解释为实盘推荐 |
| `reports/selection_freeze.json` | 切分、候选、冻结时间及哈希 |
| `reports/evaluation.json` | 实际回测、对照、费用、压力情景及模型评估 |
| `data/audit` | 每日归档校验、接口响应、数据完整性与训练收据 |

## 模拟的边界

新闻来源是事后取得的出版/修改时间代理，没有历史真实接收日志。1秒标记价使用此前完整分钟的标记价基差估计，另用真实1分钟标记价复核。没有盘口队列、深度、真实成交优先级或交易所故障流量，因此不能把“模拟未爆仓”称为“不会爆仓”，也不能把回测收益称为预期保证收益。未来30天的块重采样只是一种样本条件情景，详见报告。

上游NanoJev采用MIT许可证，相关声明保存在`THIRD_PARTY_NOTICES`。价格归档来源为Binance公开历史数据，新闻内容来源为Bitcoin Magazine公开接口；原始资料保留来源信息，不建议无条件再分发媒体正文。
