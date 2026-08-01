# Phase 8 概要设计文档 (High-Level Design)

> 文档编号: PH8-HLD-01 | 版本: v1.0 | 状态: 评审中
> 关联: [README.md](README.md) | 下游: [20_detailed_design.md](20_detailed_design.md)

---

## 1. 背景与问题陈述

### 1.1 现状

项目经过 Phase 0-7 演进, 已具备:

- **数据面**: 5 类数据库 checker (`monitor/checkers/`)、三层检测器 (`monitor/detectors/` L1 硬阈值 / L2 基线 / L3 复合)、ASH 时间线 (`context_aggregator.get_ash_timeline`)、执行计划捕获 (`plan_capture.py`)、ES 时序存储 (`elasticsearch_engine.py`)。
- **流程面**: 1-5-10 事故闭环 —— Sentinel 秒级发现 → `diagnosis_pipeline.run_diagnosis` 五阶段诊断 → `remediation_planner` 方案 → `playbook_engine` 执行 → `verify_loop` 验证, `Incident` 状态机 + SLA 秒表。
- **安全面**: SQL 白名单/黑名单、风险四级、审批流 (`approval_engine`)、审计 (`AuditLog`)、破坏演练 (`phase6/drills`)。

### 1.2 核心差距 (代码审计结论)

| # | 差距 | 证据 |
|---|------|------|
| G1 | RCA 是 40 条 lambda 阈值规则, "因果图谱"仅 4 个硬编码节点, 置信度为启发式常数 (base 0.6 + 加分) | `rca_engine_v2.py` RULES_V2 / CAUSAL_GRAPH / `_compute_confidence` |
| G2 | 修复建议是规则内写死的静态字符串, 与现场无关 | `RULES_V2[*].suggestions`、`_SIGNAL_ROOT_CAUSE` |
| G3 | Case RAG 无 embedding/向量检索 (Jaccard + 关键词重叠), `needs_llm` 标志存在但全库无任何 LLM 调用 | `case_rag.py`、全库 grep 无 LLM SDK |
| G4 | 学习闭环断裂: `CaseRag.record_success()` 定义后无任何调用方; 案例只能人工录入 | grep 结果仅定义处 |
| G5 | 诊断是被动一次性的: 规则命中什么报什么, 无法主动追查 (查计划→追锁链→比基线) | `diagnosis_pipeline._run_rca` |
| G6 | 无效果度量: 根因命中率、方案采纳率无处记录 | 无对应模型/API |
| G7 | 自动修复仅覆盖 3 个写死场景 (kill 会话/扩数据文件/清理连接) | `auto_remediation_engine.generate_remediation_plan` |

### 1.3 目标

对照五个能力目标:

| 能力目标 | 落到的子阶段 |
|----------|--------------|
| 智能的 | 8A: LLM 推理引擎 + 真 RAG |
| 能够做到根因分析的 | 8A 双引擎互验 + 8D 数据驱动因果 |
| 能够快速定位问题的 | 8C Agentic 主动排查 |
| 能够给出准确解决方案的 | 8A 定制化方案生成 + 8B 案例学习 |
| 有自主运维能力的 | 8E 分级自治 + 既有 playbook 安全底座 |

---

## 2. 总体架构

### 2.1 架构图 (Phase 8 增量, 标 ★ 为新增)

```
┌─────────────────────────────────────────────────────────────────────┐
│                          前端 (frontend/)                            │
│  事故详情页: +AI诊断卡片★ +排查轨迹★ +反馈按钮★   AI运营页★          │
└──────────────────────────────┬──────────────────────────────────────┘
                               │ REST /api/v1/...
┌──────────────────────────────┴──────────────────────────────────────┐
│                        Django (monitor/)                             │
│                                                                      │
│  发现层 (Phase6A, 不变)      诊断层 (Phase6B, 增强)                   │
│  sentinel / detectors  ───►  diagnosis_pipeline.run_diagnosis        │
│                              ├─ 阶段1 context_aggregator (不变)       │
│                              ├─ 阶段2 RCA规则 + 案例检索 (RAG升级★)    │
│                              ├─ 阶段2.5 LLM综合研判★ ──┐              │
│                              ├─ 阶段3 impact (不变)     │              │
│                              ├─ 阶段4 planner (LLM增强★)│              │
│                              └─ 阶段5 落库通知 (不变)    │              │
│                                                        │              │
│  ┌─────────────── monitor/llm/ (新包★) ────────────────┴───────────┐ │
│  │ providers.py   统一 LLM Provider (ollama/openai兼容API, 可切换)  │ │
│  │ schemas.py     结构化输出 JSON Schema + 校验                     │ │
│  │ prompts.py     提示词模板 (诊断/复盘/方案)                        │ │
│  │ evidence.py    证据包组装器 (context→紧凑JSON, token预算裁剪)     │ │
│  │ brain.py       诊断大脑编排 (调用/校验/降级/留痕)                 │ │
│  │ agent.py       [8C] Agentic 排查循环 (只读工具箱)                 │ │
│  │ tools.py       [8C] 只读诊断工具注册表                            │ │
│  └──────────────────────────────────────────────────────────────────┘ │
│                                                                      │
│  ┌────────── 学习闭环 (8B★) ──────────┐  ┌───── 因果/变更 (8D★) ────┐ │
│  │ feedback (根因确认/方案采纳 API)    │  │ causal_miner (lead-lag)  │ │
│  │ case_distiller (LLM复盘沉淀案例)    │  │ change_stream (变更事件) │ │
│  │ rule_calibrator (规则置信度校准)    │  └──────────────────────────┘ │
│  └────────────────────────────────────┘                              │
│                                                                      │
│  处置层 (Phase6C, 8E 增强): autonomy_policy★ → playbook_engine (不变) │
└──────────┬──────────────────────┬────────────────────┬───────────────┘
           │                      │                    │
     PostgreSQL              Elasticsearch          LLM 服务★
  (新表: LLMCallLog,      (新索引: db_cases_v1     Ollama 本地 或
   RcaFeedback,            dense_vector 案例向量)   OpenAI兼容 API
   ChangeEvent,                                    (DeepSeek/Qwen)
   AgentTrace...)
```

### 2.2 数据流 (一次事故的智能诊断旅程)

```
Event → Incident(open) → run_diagnosis:
  1. build_incident_context  (指标/ASH/变更/拓扑)          [现有]
  2. RCA规则 + 向量RAG案例检索 (并行)                       [RAG升级]
 2.5. evidence.py 组装证据包 → brain.diagnose_incident      [新增]
      → LLM 输出 {root_causes[], reasoning, plan_draft}
      → schemas 校验 → 与规则结果融合 (双引擎互验)
      → 失败/超时 → 降级: 仅用规则结果, 流程不中断
  3. impact_engine                                          [现有]
  4. remediation_planner + LLM plan_draft 融合               [增强]
  5. 落库 rca_result(engine='rca_v2+llm') → plan_ready 通知  [现有]
─── 处置后 ───
  resolved/closed → case_distiller (LLM复盘) → AlertCase 入库 + 向量化
  人工反馈 (确认根因/采纳方案) → RcaFeedback → rule_calibrator 周期校准
```

### 2.3 模块与文件规划

| 模块 | 路径 | 阶段 | 新增/修改 |
|------|------|------|-----------|
| LLM Provider 层 | `monitor/llm/providers.py` | 8A | 新增 |
| 输出 Schema | `monitor/llm/schemas.py` | 8A | 新增 |
| 提示词模板 | `monitor/llm/prompts.py` | 8A | 新增 |
| 证据包组装 | `monitor/llm/evidence.py` | 8A | 新增 |
| 诊断大脑 | `monitor/llm/brain.py` | 8A | 新增 |
| 向量 RAG | `monitor/case_rag_v2.py` | 8A | 新增 (旧 case_rag 保留为降级路径) |
| ES 向量索引 | `monitor/elasticsearch_engine.py` | 8A | 修改 (新增 cases 索引函数) |
| 诊断管道 | `monitor/diagnosis_pipeline.py` | 8A | 修改 (插入阶段 2.5) |
| 方案生成 | `monitor/remediation_planner.py` | 8A | 修改 (融合 LLM plan_draft) |
| 反馈 API | `monitor/api_views_feedback.py` | 8B | 新增 |
| 案例沉淀 | `monitor/case_distiller.py` | 8B | 新增 |
| 置信度校准 | `monitor/rule_calibrator.py` | 8B | 新增 |
| Agent 循环 | `monitor/llm/agent.py` | 8C | 新增 |
| 只读工具箱 | `monitor/llm/tools.py` | 8C | 新增 |
| 因果挖掘 | `monitor/causal_miner.py` | 8D | 新增 |
| 变更事件流 | `monitor/change_stream.py` | 8D | 新增 |
| 自治策略 | `monitor/autonomy_policy.py` | 8E | 新增 |
| 修复场景扩展 | `monitor/remediation_planner.py` | 8E | 修改 |
| Celery 任务 | `monitor/tasks_phase8.py` | 8B/8D | 新增 |
| REST API | `monitor/api_views_phase8.py` | 8A-8E | 新增 |
| 路由 | `dbmonitor/urls.py` | 8A-8E | 修改 |
| 配置 | `dbmonitor/settings.py` | 8A | 修改 (LLM_* 配置块) |
| 前端 | `frontend/src/` AI诊断卡片/轨迹/反馈 | 8A-8C | 修改 |

---

## 3. 关键技术选型

### 3.1 LLM 接入

| 决策点 | 选型 | 理由 |
|--------|------|------|
| 协议 | **OpenAI 兼容 Chat Completions API** 单协议 | Ollama、DeepSeek、通义(dashscope兼容模式)、vLLM 均支持, 一套代码全覆盖 |
| 默认部署 | Ollama 本地 (`qwen2.5:14b-instruct` 起步) | 监控数据不出域; 无 API 成本; 显存不足可降 7b |
| 生产可选 | DeepSeek API / 通义 API | settings 切换 `LLM_PROVIDER`, 不改代码 |
| SDK | **不引入 SDK, 用 `requests` 直连** | 避免依赖锁定; 项目已有 requests 传递依赖; 超时/重试自控 |
| 结构化输出 | prompt 内嵌 JSON Schema + `response_format={"type":"json_object"}`(支持时) + 本地严格校验 | 双保险; 校验失败最多重试 1 次后降级 |

### 3.2 Embedding 与向量检索

| 决策点 | 选型 | 理由 |
|--------|------|------|
| Embedding | Ollama `bge-m3` (1024 维), 兼容 OpenAI `/v1/embeddings` | 中文效果好; 与 LLM 同一 Ollama 实例, 零新增部署 |
| 向量库 | **复用 Elasticsearch 8.x `dense_vector` + kNN** | ES 已在架构内 (`ES_ENABLED`), 不引入 pgvector/milvus 新组件 |
| 降级 | ES 或 embedding 不可用 → 回退旧 `case_rag.py` 词法检索 | 保持可用性红线 |

### 3.3 因果分析 (8D)

- 不引入重型因果库 (DoWhy 等), 用 **滞后互相关 (lead-lag cross-correlation)** 从 ES 历史指标离线挖掘"指标 A 异常领先指标 B"边, 结果落 `CausalEdge` 表, 替换 `CAUSAL_GRAPH` 硬编码字典的数据来源。
- 依赖仅 numpy (已有)。

### 3.4 新增依赖清单 (requirements.txt)

```
requests>=2.31.0        # LLM HTTP 调用 (显式声明)
jsonschema>=4.21.0      # LLM 结构化输出校验
```

(embedding/向量检索复用 elasticsearch 既有依赖; 不引入 openai/langchain/sklearn)

---

## 4. 子阶段概要

### 4A. LLM 诊断大脑 + 真 RAG

- `monitor/llm/` 包: Provider 抽象 (chat/embed 两个方法) + 证据包组装 (token 预算内裁剪) + 诊断提示词 + JSON Schema 校验 + 降级与留痕。
- `diagnosis_pipeline.run_diagnosis` 插入阶段 2.5: 输入 = 规则 RCA 结果 + RAG 案例 + 证据包; 输出 = 融合后的 `rca_result.root_causes` (每条标注 `source: rule|llm|both`) + `llm_summary`。
- `case_rag_v2.py`: 案例写入时生成 embedding 存 ES `db_cases_v1` 索引; 检索用 kNN + 关键词混合 (RRF 融合)。
- 融合策略 (双引擎互验): LLM 确认规则根因 → 置信度上调; LLM 提出规则未覆盖的新根因假设 → 以 `source=llm` 附带 `reasoning` 进入列表, 排序权重略低于双验证根因。

### 4B. 学习闭环

- `RcaFeedback` 表 + 反馈 API: 事故详情页一键"根因正确/错误"、"方案已采纳/无效"。
- `case_distiller.py`: 事故进入 resolved/closed 时由 Celery 异步触发, LLM 生成结构化复盘 (症状/根因/处置/验证), 写入 `AlertCase` (source='auto') 并向量化; 处置成功自动调用 `record_success()`。
- `rule_calibrator.py`: 每日任务, 按 `RcaFeedback` 统计各规则历史准确率, 写 `RuleStat` 表; `_compute_confidence` 改为读校准值 (无数据时回落 0.6 基线)。

### 4C. Agentic 主动排查

- 只读工具箱 (`tools.py`): `get_metric_history` / `get_top_sql` / `get_sql_plan` / `get_lock_chain` / `get_ash_summary` / `get_recent_changes` / `get_baseline_compare` / `search_cases` 等, 全部复用现有引擎函数, 注册表声明参数 Schema。
- Agent 循环 (`agent.py`): LLM 以 function-calling 风格逐步调用工具收敛根因; 硬约束: 最多 6 步、单工具 5s 超时、总预算 60s、只读白名单、无 SQL 自由拼接 (工具内部参数化)。
- 触发: P1/P2 事故且阶段 2.5 融合置信度 < 0.7 时自动追加; 或人工在事故页点"深度排查"。
- 全轨迹落 `AgentTrace` 表, 前端时间线展示 (思考→工具→观察→结论)。

### 4D. 因果与变更关联

- `change_stream.py`: 统一变更事件模型 `ChangeEvent` (参数变更/DDL/发布标记), 来源: config_drift 检测器、DDL 采集 (checker 增量)、人工/API 登记; 诊断时按时间对齐注入证据包。
- `causal_miner.py`: 每周离线任务, 对每库指标对做滞后互相关, 产出 `CausalEdge(cause_metric, effect_metric, lag_min, strength)`; `rca_engine_v2._build_causal_chain` 改读该表。

### 4E. 自主运维分级放权

- `autonomy_policy.py`: 每库配置自治等级 L0 观察(只建议) / L1 半自动(一键执行) / L2 白名单低风险自动 / L3 扩展自动; 策略判定插在 playbook 触发点之前, 决定 `pending_approval` 还是自动进入 `prechecking`。
- 复用 `PLAYBOOK_AUTO_CIRCUIT_BREAK` 熔断: 同库 24h 内自动执行失败 ≥N 次自动降级到 L1。
- 修复场景从 3 个扩到 Top 高频信号全覆盖 (基于 `Event.signal` 统计): repl 重启、慢查杀会话、空闲事务清理、表空间自动扩容、统计信息刷新等, 每个场景以 Playbook 数据形式交付 (非硬编码)。

---

## 5. 里程碑与工作量估算

| 里程碑 | 内容 | 预估 |
|--------|------|------|
| M1 | 8A: llm 包 + 管道 2.5 + 向量 RAG + 前端 AI 卡片 + 演练降级验证 | 2.5 周 |
| M2 | 8B: 反馈 API + 案例沉淀 + 置信度校准 + AI 运营页(命中率/采纳率) | 1.5 周 |
| M3 | 8C: 工具箱 + Agent 循环 + 轨迹前端 | 2 周 |
| M4 | 8D: 变更事件流 + 因果挖掘 | 1.5 周 |
| M5 | 8E: 自治策略 + 场景扩展 + 演练全回归 | 1.5 周 |

---

## 6. 风险与对策

| 风险 | 等级 | 对策 |
|------|------|------|
| LLM 幻觉产生错误根因误导处置 | 高 | 双引擎标注 source; LLM-only 根因强制显示 reasoning + 证据引用; 反馈闭环持续度量命中率; 永不自动执行 LLM-only 根因的方案 (至少 L1 人工确认) |
| 本地模型效果不足 | 中 | Provider 可切换云 API; 提示词与证据包质量优先于模型大小; M1 验收含 20 个历史事故回放评测 |
| 诊断延迟拖破 5 分钟 SLA | 中 | 阶段 2.5 独立预算 (默认 30s) 超时即降级; Agentic 排查异步化, 不阻塞 plan_ready |
| token 泄露敏感数据 | 中 | 证据包组装时脱敏 (IP 打码可选、密码字段剔除); 默认本地部署; LLMCallLog 只存摘要 |
| ES 不可用导致 RAG 失效 | 低 | 自动回退旧词法检索 (现有 `ES_ENABLED` 开关模式一致) |

---

## 7. 验收标准 (概要)

1. 关闭 LLM (`LLM_ENABLED=False`) 时全链路行为与 Phase 7 完全一致 (回归测试)。
2. 打开 LLM 后, 注入演练故障 (phase6/drills 全套), 事故 `rca_result` 含 `llm_summary` 且 `engine='rca_v2+llm'`, plan_ready 时间不超预算。
3. 拔掉 LLM 服务 (kill Ollama), 诊断 100% 降级成功, 事故流转无阻塞。
4. 历史事故回放集 (≥20 例) LLM 根因命中率 ≥70% (人工评判)。
5. 事故 resolve 后 5 分钟内自动生成复盘案例并可被下一次相似事故检索到。
