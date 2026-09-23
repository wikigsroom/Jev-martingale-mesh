# Git仓库与分支说明

远端：`git@github.com:wikigsroom/Jev-martingale-mesh.git`。

| 分支 | 用途 | 本地工作目录 |
| --- | --- | --- |
| `main` | 可运行代码、测试、参数、研究报告、审计与冻结快照 | `D:\dev\jev-mesh` |
| `docs` | 独立的文档首页、方案说明、报告与参数证据 | `D:\dev\jev-mesh-docs` |

两个本地目录各有自己的`.git`，推送到同一GitHub仓库的不同分支；`docs`独立起始提交，不需要依赖代码历史阅读文档。程序仓库仍保留与源码一起更新的原始文档，独立分支由导出脚本生成。

## 克隆与安装

```powershell
git clone --branch main git@github.com:wikigsroom/Jev-martingale-mesh.git
git clone --single-branch --branch docs git@github.com:wikigsroom/Jev-martingale-mesh.git Jev-martingale-mesh-docs
```

在程序仓库执行原生Python命令：

```powershell
python -m pip install -e ".[test,report]"
python -m pytest -q
```

测试使用合成数据，可在未下载行情与模型时运行。实际历史回测仍需准备`data/processed`；重新训练/推理还需要模型权重。下载与准备流程见[项目README](../README.md)。

禁止使用Docker、WSL等OS虚拟化环境；本项目按原生Windows方式运行。

## 纳入Git的研究记录

保留代码、测试、文档、原FMZ参考文件、第三方许可、正式3000组搜索、最终参数、冻结哈希、收益指标、成本/延迟压力失败和事件规则对照。R1 ZIP保留该次代码及完整权益轨迹，防止后续试验覆盖历史结果。

行情原始数据、处理数据、模型权重、缓存和本机凭据不纳入Git。重复的早期探索目录、大型权益/动作CSV也不直接纳入Git；R1完整轨迹在[快照ZIP](../records/fmz-v2/2026-09-23-r1/fmz-jev-20260923-r1.zip)中，或可从数据重算。最终报告引用的分月汇总和正式搜索表仍被保留。

`.gitattributes`禁止自动转换换行符，以保持研究证据、参数和源代码的SHA256。Git整理不会重写R1归档及旧冻结记录。

## 更新独立文档分支

先在`main`完成并提交文档和代码，再从程序仓库运行：

```powershell
python scripts/export_docs_branch.py --output D:/dev/jev-mesh-docs
git -C D:/dev/jev-mesh-docs status --short
git -C D:/dev/jev-mesh-docs add .
git -C D:/dev/jev-mesh-docs commit -m "docs: update research documentation"
git -C D:/dev/jev-mesh-docs push origin docs
```

导出保留文档内部结构，把未复制的源码/数据/归档链接转换为固定程序提交的GitHub链接，或明确标出仅本地存在的资料。`documentation_manifest.json`记录来源提交和每个导出文件的SHA256。导出脚本不会执行Git推送；对于独立分支上的手工修改，会拒绝静默覆盖。

SSH密钥只通过各仓库的本地Git配置指定，不复制到项目、提交内容或文档分支。
