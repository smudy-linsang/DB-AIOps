# Phase 8 接口设计文档 (Interface Design Specification)

> 文档编号: PH8-IFD-01 | 版本: v1.0 | 状态: 评审中
> 范围: REST API、内部模块接口契约、LLM 结构化输出 Schema、Agent 工具调用协议
> 上游: [20_detailed_design.md](20_detailed_design.md)

---

## 1. 通用约定

- 路由前缀沿用项目现状 `/api/v1/`, 注册于 `dbmonitor/urls.py`, 视图集中在 `monitor/api_views_phase8.py` 与 `monitor/api_views_feedback.py`。
- 鉴权: 沿用 `monitor/auth.py` Token + 权限装饰器体系; 各接口所需权限见表。
- 响应包络 (与现有 api_views 一致):

```json
成功: {"code": 0, "message": "ok", "data": {...}}
失败: {"code": <非0>, "message": "错误描述", "data": null}
```

- 错误码分配 (Phase 8 专段 8xxx):

| code | 含义 |
|------|------|
| 8001 | LLM 功能未启用 (LLM_ENABLED=False) |
| 8002 | LLM 服务不可用/超时 |
| 8003 | 事故不存在或状态不允许该操作 |
| 8004 | 反馈参数非法 (rule_id 不在诊断结果中等) |
| 8005 | Agent 排查已在进行中 (并发互斥) |
| 8006 | 自治等级参数非法 |

---

## 2. REST API 一览

| # | 方法 | 路径 | 功能 | 权限 | 阶段 |
|---|------|------|------|------|------|
| 1 | POST | `/api/v1/incidents/<incident_id>/rca-feedback/` | 根因反馈 | ACKNOWLEDGE_ALERTS | 8B |
| 2 | POST | `/api/v1/incidents/<incident_id>/plan-feedback/` | 方案反馈 | ACKNOWLEDGE_ALERTS | 8B |
| 3 | POST | `/api/v1/incidents/<incident_id>/investigate/` | 触发深度排查 | EXECUTE_OPERATIONS | 8C |
| 4 | GET | `/api/v1/incidents/<incident_id>/agent-trace/` | 查询排查轨迹 | VIEW_ALERTS | 8C |
| 5 | POST | `/api/v1/changes/` | 登记变更事件 | MANAGE_DATABASES | 8D |
| 6 | GET | `/api/v1/databases/<config_id>/changes/` | 查询变更事件 | VIEW_DATABASE | 8D |
| 7 | GET | `/api/v1/ai-ops/stats/` | AI 运营统计 | VIEW_METRICS | 8B |
| 8 | GET | `/api/v1/ai-ops/llm-calls/` | LLM 调用日志列表 | VIEW_AUDITLOGS | 8A |
| 9 | GET | `/api/v1/ai-ops/rule-stats/` | 规则准确率排行 | VIEW_METRICS | 8B |
| 10 | PUT | `/api/v1/databases/<config_id>/autonomy/` | 设置自治等级 | MANAGE_DATABASES | 8E |
| 11 | GET | `/api/v1/databases/<config_id>/causal-graph/` | 查询因果图 | VIEW_METRICS | 8D |
| 12 | POST | `/api/v1/llm/test-connection/` | LLM 连通性测试 | MANAGE_DATABASES | 8A |

---

## 3. REST API 详细定义

### 3.1 根因反馈

```
POST /api/v1/incidents/INC-20260727-0001/rca-feedback/
Request:
{
  "rule_id": "R012",              // 必填, 须存在于 incident.rca_result.root_causes[].rule_id
  "verdict": "correct",           // 必填, 枚举: correct | wrong
  "comment": "确实是统计信息过期"   // 可选, <=500字
}
Response.data:
{
  "feedback_id": 17,
  "case_success_bumped": true     // 是否联动了 record_success
}
错误: 8003 (事故不存在), 8004 (rule_id 不在诊断结果中 / verdict 非法)
幂等: 同 (incident, rule_id, user) 重复提交为覆盖更新
```

### 3.2 方案反馈

```
POST /api/v1/incidents/<incident_id>/plan-feedback/
Request:  {"plan_id": "PLAN-xxx", "verdict": "adopted",   // adopted | useless
           "comment": ""}
Response.data: {"feedback_id": 18}
```

### 3.3 事故详情扩展 (既有接口的返回增量, 非新接口)

`GET /api/v1/incidents/<incident_id>/` 的 data 中新增/扩展字段:

```json
{
  "rca_result": {
    "engine": "rca_v2+llm",
    "root_causes": [{
       "rule_id": "R012", "name": "执行计划劣化", "confidence": 0.88,
       "source": "both",                    // 新增: rule | llm | both
       "reasoning": "证据E2显示...",         // source含llm时存在
       "evidence_refs": ["E2","E5"],
       "llm_dissent": null,                 // AI 持异议时为字符串
       "agent_confirmed": true              // 8C 深挖确认过
    }],
    "llm_summary": "综合判断为...",           // 8A, 可能不存在(降级)
    "need_more_info": ["..."],
    "agent_conclusion": {...}               // 8C, 可能不存在
  },
  "plans": [ {...模板方案...},
             {"plan_type": "llm_advisory", "steps": [...], "preventive": [...]} ],
  "my_feedback": {"rca": {"R012": "correct"}, "plan": {}}   // 当前用户已提交的反馈
}
```

### 3.4 深度排查

```
POST /api/v1/incidents/<incident_id>/investigate/
Request: {}                        // 无参数
Response.data: {"trace_id": 5, "status": "running"}
错误: 8001(AGENT_ENABLED=False), 8005(已在排查中), 8003
```

### 3.5 排查轨迹

```
GET /api/v1/incidents/<incident_id>/agent-trace/
Response.data:
{
  "traces": [{
    "trace_id": 5, "trigger": "manual", "status": "done",   // running|done|llm_error|budget_exceeded|no_conclusion
    "started_at": "...", "finished_at": "...",
    "steps": [
      {"seq": 0, "thought": "先看是否有阻塞链", "tool": "get_lock_chain",
       "params": {}, "observation_summary": "发现2组阻塞...", "elapsed_ms": 1240},
      {"seq": 1, "thought": "...", "action": "final"}
    ],
    "conclusion": {"root_cause": "...", "confidence": 0.8,
                   "evidence_summary": "...", "suggestions": ["..."]}
  }]
}
```

### 3.6 变更事件

```
POST /api/v1/changes/
Request:
{
  "config_id": 3,
  "change_type": "deploy",         // param_change | ddl | deploy | maintenance | other
  "title": "订单服务 v2.13 发布",
  "detail": {"version": "2.13", "operator": "zhangsan"},   // 自由 JSON
  "occurred_at": "2026-07-27T10:00:00+08:00"               // 可选, 默认 now
}
Response.data: {"change_id": 91}

GET /api/v1/databases/3/changes/?hours=72&types=ddl,param_change
Response.data: {"changes": [{"change_id","change_type","title","detail",
                             "source","occurred_at"}], "total": 12}
```

### 3.7 AI 运营统计

```
GET /api/v1/ai-ops/stats/?days=7
Response.data:
{
  "llm": {"calls": 152, "error_rate": 0.03, "avg_latency_ms": 4210,
          "token_in_total": 812000, "token_out_total": 96000},
  "rca": {"feedback_total": 40, "accuracy": 0.78},          // correct/total
  "plans": {"feedback_total": 25, "adopt_rate": 0.56},
  "cases": {"auto_distilled": 31, "manual": 12, "vector_indexed": 43},
  "agent": {"runs": 9, "done": 7, "avg_steps": 3.4}
}
```

### 3.8 LLM 调用日志 / 规则准确率

```
GET /api/v1/ai-ops/llm-calls/?scene=diagnosis&status=ok&page=1&page_size=20
Response.data: {"items": [{"id","scene","incident_id","model","status",
    "prompt_tokens","completion_tokens","latency_ms","created_at"}], "total": 152}

GET /api/v1/ai-ops/rule-stats/
Response.data: {"items": [{"rule_id":"R012","rule_name":"执行计划劣化",
    "sample_count":12,"accuracy":0.92,"calibrated_confidence":0.82}]}
```

### 3.9 自治等级 / 因果图 / LLM 测试

```
PUT /api/v1/databases/3/autonomy/
Request: {"level": "L2"}            // L0|L1|L2|L3, 非法→8006
Response.data: {"level": "L2", "circuit_open": false}

GET /api/v1/databases/3/causal-graph/
Response.data: {"edges": [{"cause":"slow_queries","effect":"cpu_usage",
    "lag_min":5,"strength":0.74,"mined_at":"..."}],
    "fallback_static": false}       // true=无学习结果, 用的硬编码先验

POST /api/v1/llm/test-connection/
Request: {}                          // 用当前 settings 配置发一次 ping 对话
Response.data: {"ok": true, "model": "qwen2.5:14b-instruct",
                "latency_ms": 890, "embed_ok": true, "embed_dim": 1024}
```

---

## 4. 内部模块接口契约 (Python)

### 4.1 brain (8A)

```python
# monitor/llm/brain.py
def diagnose_incident(incident, context: dict, features: dict,
                      rule_roots: list[dict], rag_matches: list[dict]) -> dict | None
# 返回 None = 降级; 否则:
# {'llm_root_causes': [...], 'verdicts': [...], 'plan_draft': {...},
#  'summary': str, 'need_more_info': [str], 'call_id': int}

def merge_llm_with_rules(rule_roots: list[dict], llm_result: dict | None) -> list[dict]
# 输出条目结构 = 原 root_cause dict + {'source': 'rule'|'llm'|'both',
#   'reasoning'?: str, 'evidence_refs'?: [str], 'llm_dissent'?: str}
```

### 4.2 provider (8A)

```python
# monitor/llm/providers.py
get_provider() -> OpenAICompatProvider    # chat 用
get_embedder() -> OpenAICompatProvider    # embed 用
OpenAICompatProvider.chat(messages, *, temperature, max_tokens, json_mode) -> ChatResult
OpenAICompatProvider.embed(texts: list[str], model: str) -> list[list[float]]
# 异常: LLMTimeout / LLMUnavailable / LLMBadResponse (均继承 LLMError)
```

### 4.3 case_rag_v2 (8A) —— 与旧版兼容契约

```python
search_cases_v2(symptom: str, db_type: str = '', top_k: int = 5) -> RagResult
# RagResult / CaseMatch 复用 monitor/case_rag.py 既有数据类, 字段不增不减,
# 保证 diagnosis_pipeline._search_cases 的鸭子类型解析零改动。
CaseRagV2().index_case_from_model(alert_case: AlertCase) -> bool
```

### 4.4 tools / agent (8C)

```python
# monitor/llm/tools.py
run_tool(incident, name: str, params: dict) -> dict
# 成功: 工具自定义结构 (见 §6); 失败统一: {'error': 'unknown_tool'|'bad_params'|
#   'timeout'|'tool_failed', 'detail'?: str}
tool_specs_for_prompt() -> list[{'name','description','params_schema'}]

# monitor/llm/agent.py
run_agent_investigation(incident_id: str, trigger: str) -> dict | None
```

### 4.5 学习闭环 (8B)

```python
# monitor/case_distiller.py
distill_incident(incident_id: str) -> dict | None    # 返回 {'case_id':...} 或 None

# monitor/rule_calibrator.py
calibrate_rules() -> {'calibrated': int, 'skipped': int}
get_calibrated_base(rule_id: str) -> float            # 默认 0.6
```

### 4.6 变更/因果 (8D)

```python
# monitor/change_stream.py
record_change(config_id, change_type, title, detail, source, occurred_at=None) -> ChangeEvent
query_changes(config_id, hours=72, types=None) -> list[dict]

# monitor/causal_miner.py
mine_causal_edges(config_id: int) -> list[dict]
get_causal_effects(metric_key: str, config_id: int) -> list[{'effect_metric','lag_min','strength'}]
```

### 4.7 自治 (8E)

```python
# monitor/autonomy_policy.py
get_autonomy_level(config: DatabaseConfig) -> str            # 'L0'..'L3' (含熔断降级)
decide_execution(incident, plan: dict) -> str                # 'auto_execute'|'manual'|'forbidden'
```

---

## 5. LLM 结构化输出 Schema (jsonschema Draft-07)

### 5.1 诊断输出 `DIAGNOSIS_OUTPUT_SCHEMA`

```json
{
  "type": "object",
  "required": ["root_causes", "summary"],
  "properties": {
    "root_causes": {
      "type": "array", "maxItems": 5,
      "items": {
        "type": "object",
        "required": ["name", "confidence", "reasoning", "evidence_refs"],
        "properties": {
          "name":            {"type": "string", "maxLength": 100},
          "confidence":      {"type": "number"},
          "matched_rule_id": {"type": ["string", "null"]},
          "reasoning":       {"type": "string", "maxLength": 1000},
          "evidence_refs":   {"type": "array", "items": {"type": "string"}},
          "category":        {"type": "string"}
        }
      }
    },
    "verdict_on_rules": {
      "type": "array",
      "items": {"type": "object",
        "required": ["rule_id", "agree"],
        "properties": {"rule_id": {"type": "string"},
                       "agree":   {"type": "boolean"},
                       "comment": {"type": "string", "maxLength": 300}}}
    },
    "plan_draft": {
      "type": "object",
      "properties": {
        "immediate_actions": {"type": "array", "maxItems": 5,
          "items": {"type": "object", "required": ["desc", "risk"],
            "properties": {"desc": {"type": "string", "maxLength": 300},
                           "sql_hint": {"type": "string", "maxLength": 500},
                           "risk": {"enum": ["low", "medium", "high"]}}}},
        "preventive_actions": {"type": "array", "maxItems": 5,
                               "items": {"type": "string", "maxLength": 300}}
      }
    },
    "summary":        {"type": "string", "maxLength": 600},
    "need_more_info": {"type": "array", "maxItems": 5, "items": {"type": "string"}}
  }
}
```

语义后校验 (schemas.py 代码内, 非 jsonschema): confidence 裁剪 [0,1]; category 非法→'other'; evidence_refs 无效引用剔除。

### 5.2 复盘输出 `DISTILL_OUTPUT_SCHEMA` (8B)

```json
{
  "type": "object",
  "required": ["title", "symptom", "root_cause", "resolution", "quality"],
  "properties": {
    "title":      {"type": "string", "maxLength": 100},
    "symptom":    {"type": "string", "maxLength": 800},
    "root_cause": {"type": "string", "maxLength": 800},
    "resolution": {"type": "string", "maxLength": 1000},
    "tags":       {"type": "array", "maxItems": 8, "items": {"type": "string", "maxLength": 20}},
    "quality":    {"enum": ["good", "low"]}
  }
}
```

### 5.3 Agent 步输出 `AGENT_STEP_SCHEMA` (8C)

```json
{
  "type": "object",
  "required": ["action", "thought"],
  "properties": {
    "action":  {"enum": ["tool", "final"]},
    "thought": {"type": "string", "maxLength": 500},
    "tool":    {"type": "string"},
    "params":  {"type": "object"},
    "conclusion": {
      "type": "object",
      "required": ["root_cause", "confidence"],
      "properties": {
        "root_cause":       {"type": "string", "maxLength": 300},
        "confidence":       {"type": "number"},
        "evidence_summary": {"type": "string", "maxLength": 800},
        "suggestions":      {"type": "array", "maxItems": 5,
                             "items": {"type": "string", "maxLength": 300}}
      }
    }
  },
  "allOf": [
    {"if": {"properties": {"action": {"const": "tool"}}},
     "then": {"required": ["tool", "params"]}},
    {"if": {"properties": {"action": {"const": "final"}}},
     "then": {"required": ["conclusion"]}}
  ]
}
```

---

## 6. Agent 工具调用协议

### 6.1 会话结构

```
system:  角色设定 + 工具清单(name/description/params_schema) + AGENT_STEP_SCHEMA
         + 规则: 每轮只输出一个JSON; 最多N步; 重复调用同一工具同参数将被拒绝
user:    初始上下文 {incident摘要, rca_result.root_causes, need_more_info}
assistant: {"action":"tool","tool":"get_lock_chain","params":{},"thought":"..."}
user:    {"observation": {...工具返回...}}
assistant: {"action":"tool",...} / {"action":"final","conclusion":{...}}
...
```

### 6.2 工具返回结构 (observation 内容)

| 工具 | 成功返回 (示例结构) |
|------|---------------------|
| get_metric_history | `{"metric":"qps","points":[[ts,v],...],"stat":{"min":..,"max":..,"avg":..}}` |
| get_top_sql | `{"items":[{"sql_id","text_prefix","avg_ms","execs"}]}` |
| get_sql_plan | `{"sql_id","plan_lines":[...],"plan_changed":true,"prev_plan_hash":"..."}` |
| get_lock_chain | `{"chains":[{"blocker":{"sid","sql_prefix","duration_sec"},"waiters":[...]}]}` |
| get_ash_summary | `{"top_wait_events":[{"event","samples","pct"}]}` |
| get_recent_changes | `{"changes":[{"change_type","title","occurred_at"}]}` |
| get_baseline_compare | `{"metric","current":..,"mean":..,"std":..,"sigma_dev":4.2}` |
| get_replication_status | `{"role","lag_seconds","io_running","sql_running"}` |
| get_tablespace_usage | `{"items":[{"name","used_pct","free_mb"}]}` |
| search_cases | `{"cases":[{"case_id","title","root_cause","similarity"}]}` |
| 任何失败 | `{"error":"timeout"}` 等 (见 §4.4) |

### 6.3 安全约束 (协议级)

1. 工具全部只读; 参数经 jsonschema 校验且数值钳制上限 (minutes≤180, limit≤10 等)。
2. LLM 无法传入自由 SQL —— 不存在接受 SQL 文本参数的工具 (get_sql_plan 只接受已捕获的 sql_id)。
3. 观察结果超长截断 (4000 字符), 防止上下文爆炸。
4. 单事故并发互斥; 全局同时运行的 Agent 数由 Celery 队列并发度自然限制 (文档化: 建议 worker 并发≤2 处理 agent 队列)。
