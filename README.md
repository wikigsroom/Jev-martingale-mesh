# Jev Martingale Mesh · 研究文档

这是独立的`docs`分支。程序与测试见[main分支](https://github.com/wikigsroom/Jev-martingale-mesh/tree/main)，本次文档对应程序提交[`d8e39df927d0`](https://github.com/wikigsroom/Jev-martingale-mesh/commit/d8e39df927d0a08f668afa570b141f9672e2b150)。

| 入口 | 内容 |
| --- | --- |
| [方案总览](docs/fmz-v2/README.md) | FMZ-JEV-20260923-R1版本与证据入口 |
| [方案及决策记录](docs/fmz-v2/strategy_record.md) | 参数、策略流程、选参依据和失败情景 |
| [JEV的作用](docs/fmz-v2/jev_role.md) | 事件过滤、趋势分工与模型增量证据 |
| [完整报告](reports/fmz_v2/final_report_zh.md) | 秒级回测、对照、分月结果及压力测试 |
| [最终参数](reports/fmz_v2/final_parameters.json) | 候选1623的完整机器可读配置 |
| [原策略审查](reports/fmz_v2/source_review/review_zh.md) | FMZ源码问题与BTC适配边界 |
| [复现和归档](docs/fmz-v2/reproduction.md) | 本地命令、数据要求及冻结记录 |
| [Git分支说明](docs/repository_layout.md) | 程序和文档两个本地仓库的维护方式 |

本版在2026-03-01至09-21的基础成本模拟中，100U变为172.61U，最大回撤17.09%，模拟爆仓0次。费用加倍且滑点10bp时为−21.53%，新闻延迟60秒时为−22.06%；每条新闻固定暂停的规则对照收益高于JEV方案。这里保留全部这些结论，历史收益不代表未来盈利。

本分支提供阅读副本与参数证据，不包含程序运行代码、行情或模型权重。未复制的源码和ZIP归档链接指向上述固定程序提交。Markdown/HTML中的链接可能已调整；原始冻结哈希对应`main`中的原始文件，导出前后哈希记录在[documentation_manifest.json](documentation_manifest.json)。

文档由程序仓库的`scripts/export_docs_branch.py`导出。请在程序仓库更新后重新导出；脚本会保护文档仓库中的手工修改。
