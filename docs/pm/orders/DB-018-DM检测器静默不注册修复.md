# DB-018：DM 检测器静默不注册修复

执行：GG

## 背景

- 依据：DB-017 接单回执 §2.1 发现，PM 2026-11-06 裁决构成缺陷单（窄口径两项）；PM 已在 main 上实核代码：`monitor/checkers/dm.py:11` import dmPython、`monitor/checkers/__init__.py:33` 可选依赖、45 仅在可用时注册、`monitor/db_connector.py:240` 连接层用 pyodbc、缺驱动明确报错。
- 缺陷（窄口径两项）：① requirements.txt 把 pyodbc 声明为达梦依赖，检测器实际要的是未声明的 dmPython；②已纳管 DM 实例缺 dmPython 时，检测器静默不注册、无告警，造成“能连上、无结论”的不对称。
- 不在本单：DM 实库连通与方言行为（无实例，记缺口）；dmPython 本身缺失（需官方授权安装，环境依赖，不要求安装）。

## 任务

1. **依赖声明订正**：requirements.txt 分层写清——pyodbc 为连接层依赖，dmPython 为 DM 检测器依赖（需官方授权安装）。
2. **缺驱动时可见**：缺 dmPython 时，已纳管 DM 实例的检测器不注册必须有可见信号（注册/启动时日志告警 + 巡检结论中注明“DM 检测器未注册：缺 dmPython”），不得静默跳过。
3. 不得改动检测器本身的方言逻辑；不得要求安装 dmPython。

## 要求

- 直推 main；接单后先在黑板上回接单确认与 ETA（reports/ 下简短回执），飞书群只作广播。
- 附 `scripts/validate.sh` 证据：每次代码编辑后跑 unit，提交前跑 backend（frontend 无改动可注明不适用），证据贴进报告。

## 验收物

- 修复报告（结论三档之一），两项缺陷逐项核对。

## 约束

- 不改 Xinchuang 现有链路行为。
- 不碰 DB-001 设计工作，不占用 DB-002。
- 不改 `docs/pm/STATUS.md`、`docs/pm/DECISIONS.md`（仅部门经理维护）。
- DB-AIOps 仓库代码改动遵守直推 main 纪律与 `scripts/validate.sh` 纪律。

## 版本记录

| 版本 | 日期 | 修订人 | 修订说明 |
|---|---|---|---|
| v1.0 | 2026-11-06 | muse | DB-018 下达：DM 检测器静默不注册修复，派 GG |
