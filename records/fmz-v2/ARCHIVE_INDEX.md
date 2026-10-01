# FMZ 策略归档索引

## 当前冻结方案

- **正式方案名**：`ETHUSDT 常规费率 JEV 光谱动态网格方案`
- **机器可读名称**：`ETHUSDT-RU-JEV-SPECTRAL-DYNAMIC-GRID-1S-V1`
- **归档编号**：`ETHUSDT-REGULAR-USER-JEV-SPECTRAL-DYNAMIC-GRID-1S-20261002`
- **记录日期**：`2026-10-02`
- **归档状态**：已冻结、已生成压缩备份；本次归档随当前提交推送 GitHub

### 方案口径

- 标的：`ETHUSDT`
- 执行分辨率：`1 秒`
- 初始权益：`1,000 U`
- 最低名义金额：`130 U`
- 动态网格：开启；基于已完成分钟波动率、趋势压力和 JEV 事件压力实时调整
- 网格范围：`0.5%` 至 `5.0%`；止盈距离为当前网格的 `75%`
- 最大加仓：`2` 层；账户回撤停机关闭
- JEV：ETHUSDT walk-forward 校准事件头，`5 秒`可用延迟，事件压力参与网格和新增交易门控
- Binance Regular User Maker / Taker：`0.0200% / 0.0500%`
- 主动成交滑点：`2 bp`

### 36 个月结果

- 期末权益：`2,290,664.03 U`
- 收益率：`+228,966.403%`
- 最大回撤：`13.626%`
- 成交 / 加仓：`6,092 / 970`
- JEV 事件 / 网格更新：`122,060 / 5,760`
- 手续费 / 资金费净额：`153,771.28 U / -385.62 U`
- 强平：`0`

### 文件位置

- **归档目录**：`records/fmz-v2/2026-10-02-ethusdt-jev-spectral-dynamic-grid-1s-v1/`
- **压缩备份**：`records/fmz-v2/ETHUSDT-REGULAR-USER-JEV-SPECTRAL-DYNAMIC-GRID-1S-20261002.zip`
- **参数文件**：`records/fmz-v2/2026-10-02-ethusdt-jev-spectral-dynamic-grid-1s-v1/strategy_parameters.json`
- **完整性清单**：`records/fmz-v2/2026-10-02-ethusdt-jev-spectral-dynamic-grid-1s-v1/archive_manifest.json`

后续提到“ETHUSDT 常规费率 JEV 光谱动态网格方案”或
`ETHUSDT-RU-JEV-SPECTRAL-DYNAMIC-GRID-1S-V1`，均指向本次冻结归档。
如需修改参数，应创建新的版本名和新的归档编号，不覆盖本版本。

## 历史冻结方案

- **正式方案名**：`ETHUSDT 常规费率 JEV-A3 固定网格风险闸门方案`
- **机器可读名称**：`ETHUSDT-RU-A3-FIXED-GRID-GATE-H180-S1-2BP-V1`
- **归档编号**：`ETHUSDT-REGULAR-USER-ACTION3-FIXED-GRID-GATE-H180-2BP-20261002`
- **记录日期**：`2026-10-02`
- **归档状态**：已冻结、已生成压缩备份；本次归档将随当前提交推送 GitHub

### 方案口径

- 标的：`ETHUSDT`
- 执行分辨率：`1 秒`
- 初始权益：`1,000 U`
- 基础网格：固定 `0.3%`，最多加仓 `3` 层
- JEV：`action=3`，阈值 `0.30`，保持 `180 秒`，延迟 `5 秒`
- JEV 作用：暂停新增腿、加仓和再入场，不强制平掉已有仓位
- 动态网格：关闭，`dynamic_base=0`
- Binance Regular User Maker / Taker：`0.0200% / 0.0500%`
- 主动成交滑点：`2 bp`

### 文件位置

- **归档目录**：`records/fmz-v2/2026-10-02-ethusdt-regular-user-action3-fixed-grid-gate-h180-s1-2bp-v1/`
- **压缩备份**：`records/fmz-v2/ETHUSDT-REGULAR-USER-ACTION3-FIXED-GRID-GATE-H180-2BP-20261002.zip`
- **参数文件**：`records/fmz-v2/2026-10-02-ethusdt-regular-user-action3-fixed-grid-gate-h180-s1-2bp-v1/strategy_parameters.json`
- **完整性清单**：`records/fmz-v2/2026-10-02-ethusdt-regular-user-action3-fixed-grid-gate-h180-s1-2bp-v1/archive_manifest.json`

### 压缩备份校验

- SHA-256：`bc8596cc333575240092dcf1759698909e711f220887ccc84e14507767b2cb9c`
- ZIP 条目数：`39`
- 空文件：`0`

### 36 个月记录

- 期末权益：`2,036.57 U`
- 收益率：`+103.66%`
- 最大回撤：`55.95%`
- 成交 / 加仓：`1,648 / 726`
- JEV 信号 / 阻断检查 / 强制平腿：`16,495 / 217,910 / 0`
- 强平：`0`

后续提到“ETHUSDT 常规费率 JEV-A3 固定网格风险闸门方案”或
`ETHUSDT-RU-A3-FIXED-GRID-GATE-H180-S1-2BP-V1`，均指向本次冻结归档。
如需修改参数，应创建新的版本名和新的归档编号，不覆盖本版本。
## 更早历史冻结方案

- **正式方案名**：`ETHUSDT 常规费率 JEV-A2 动态无加仓方案`
- **机器可读名称**：`ETHUSDT-RU-A2-DYN-NOADD-H1800-S1-2BP-V1`
- **归档编号**：`ETHUSDT-REGULAR-USER-ACTION2-DYNAMIC-NOADD-H1800-2BP-20260930`
- **记录日期**：`2026-10-01`
- **原始归档日期**：`2026-09-30`
- **归档状态**：已冻结、已生成压缩备份；仅本地归档，未提交或推送 GitHub

### 方案口径

- 标的：`ETHUSDT`
- 执行分辨率：`1 秒`
- Binance 费率等级：`Regular User`
- Maker / Taker：`0.0200% / 0.0500%`
- 主动成交滑点假设：`2 bp`
- 初始权益：`1,000 U`
- JEV 策略：`action=2`
- JEV 保持期：`1,800 秒`
- 加仓：关闭，`max_adds=0`
- 动态基础参数：开启

### 文件位置

- 归档目录：`records/fmz-v2/2026-09-30-regular-user-action2-dynamic-noadd-h1800-2bp-ethusdt/`
- 压缩备份：`records/fmz-v2/ETHUSDT-REGULAR-USER-ACTION2-DYNAMIC-NOADD-H1800-2BP-20260930.zip`
- 参数文件：`records/fmz-v2/2026-09-30-regular-user-action2-dynamic-noadd-h1800-2bp-ethusdt/strategy_parameters.json`
- 完整性清单：`records/fmz-v2/2026-09-30-regular-user-action2-dynamic-noadd-h1800-2bp-ethusdt/archive_manifest.json`

### 压缩备份校验

- SHA-256：`f208c9de0612e0ad17b56660145714d222176f418efbc8d33532e6a599c08e1d`
- ZIP 条目数：`27`
- 空文件：`0`

### 使用说明

后续提到“ETHUSDT 常规费率 JEV-A2 动态无加仓方案”或
`ETHUSDT-RU-A2-DYN-NOADD-H1800-S1-2BP-V1`，均指向上述冻结归档。
如需修改参数，应创建新的版本名和新的归档编号，不覆盖本版本。
