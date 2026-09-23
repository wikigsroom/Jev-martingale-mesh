# 复现、校验与归档

对应记录：FMZ-JEV-20260923-R1。所有命令在`D:\dev\jev-mesh`原生PowerShell执行，不使用Docker、WSL或其他OS虚拟化环境。

## 读取已有结果

先读[方案记录](strategy_record.md)与[JEV作用说明](jev_role.md)，再查[原始评估指标](../../reports/fmz_v2/evaluation.json)。完整配置在[参数JSON](../../reports/fmz_v2/final_parameters.json)。

`reports/fmz_v2`是本轮实际FMZ固定锚点版本；根目录`reports/final_*`是更早的另一种网格研究，不应混用。已完成29项关键测试；文档化本身没有重新训练或调优。

## 核对与单次回测

```powershell
python scripts/audit_fmz_delivery.py
python scripts/record_fmz.py --verify records/fmz-v2/2026-09-23-r1
python scripts/run_fmz.py --params reports/fmz_v2/final_parameters.json --resolution 1s --start 2026-03-01 --end 2026-09-22 --out reports/fmz_v2/reproduce_r1
python scripts/run_fmz.py --params reports/fmz_v2/final_parameters.json --variant no_jev --out reports/fmz_v2/reproduce_r1_no_jev
```

结束日期为UTC不包含该日的边界，因此`--end 2026-09-22`表示运行到9月21日23:59:59。主指标期望为：初始100U、期末172.61405995749513U、最大回撤17.08640859172561%、爆仓0次。浮点环境更换可能有很小的数值差异。

上述是R1的5秒新闻延迟。后续按用户要求建立的[60秒复跑记录L60](../../reports/fmz_v2/news_delay_60s_20260923/report_zh.md)单独保存参数与结果，原R1不覆盖。复现L60：

```powershell
python scripts/run_fmz.py --params reports/fmz_v2/news_delay_60s_20260923/parameters.json --resolution 1s --start 2026-03-01 --end 2026-09-22 --out reports/fmz_v2/news_delay_60s_20260923/reproduce_60s
```

L60期望期末77.94420553213098U、净收益−22.05579446786902%、最大回撤25.2317499829148%、爆仓0次、账户回撤停机。新闻到达时间为可用时间后60秒；复用+5秒信息构造的原分数缓存，未重新推理或调参。

单次回测输出JSON指标、每分钟权益CSV和发生动作的秒级汇总轨迹CSV。汇总轨迹不是逐订单交易所回执。最后清仓的成交费用已计入指标和权益，未单独成为轨迹行。

复现现有回测只需要已处理行情、资金费和事件分数缓存。若需重做金融头训练、真实GPU推理或原始数据下载，使用[项目README](https://github.com/wikigsroom/Jev-martingale-mesh/blob/d8e39df927d0a08f668afa570b141f9672e2b150/README.md)的共享数据/模型流程；不要把缓存回放称为重新训练。

## 归档内容与边界

`records/fmz-v2/2026-09-23-r1/`保存：

- `fmz-jev-20260923-r1.zip`：项目代码、测试、文档、原FMZ文件、正式调参记录、本轮报告、指标和权益/动作记录。
- `manifest.json`：记录编号、创建时间、关键结论、文件路径、文件大小、SHA256和大体积数据/模型依赖清单。
- `verification.json`：归档逐文件哈希、ZIP与依赖核对结果。

市场原始ZIP、处理后的秒线和模型权重继续保留在项目数据/模型目录，归档用哈希标识这些依赖，不重复打包。因此这是代码与研究证据快照，**不是包含全部数据和权重的独立运行包**。迁移到新机器时仍需复制清单中的依赖，保持项目相对路径。

归档脚本拒绝覆盖已有记录目录。SHA256可用于检查后续是否改动；它不等于操作系统层面禁止修改。

## 后续试验如何保留记录

单次回测使用新的`--out`文件名前缀。完整调参脚本有固定输出路径，重跑前须先归档已有版本或在新的原生工作目录运行。已归档的R1不要覆盖；后续记录采用新编号，并说明参数、数据、模型或撮合逻辑的变化。

```powershell
python scripts/record_fmz.py --record-id 2026-09-23-r2
```

此命令只归档当时项目已有状态，不会训练模型、产生新策略或重新计算历史收益。创建前会检查现有参数/代码/模型与选择冻结是否一致；如果先改动了被冻结的文件，必须形成对应的新研究记录，不能把旧指标作为新代码的成绩。

应同时保留正收益结果、负收益对照、压力失败以及样本时间边界。9月此前已观察，不恢复成“未见样本”；每条新闻固定暂停的较好结果作为对照保留，不改写原始选参依据。
