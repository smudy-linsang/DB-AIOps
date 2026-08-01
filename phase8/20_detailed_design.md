# Phase 8 详细设计说明书 (Detailed Design Specification)

> 文档编号: PH8-DDS-01 | 版本: v1.0 | 状态: 评审中
> 粒度目标: 照图施工 —— 精确到文件、类、函数签名、伪代码、插入点、异常与降级分支
> 上游: [10_overview_design.md](10_overview_design.md) | 配套: [30_interface_design.md](30_interface_design.md)、[40_database_design.md](40_database_design.md)

---

## 0. 约定

- 所有新代码遵循项目现有风格: 中文 docstring/注释、`logger = logging.getLogger("monitor.xxx")`、配置用 `getattr(settings, 'KEY', default)` 读取。
- "阶段 X" 指 `diagnosis_pipeline.run_diagnosis` 中的编排阶段 (现有 1~5)。
- 涉及既有文件的改动均以"插入点 + 伪 diff"描述, 不重排既有代码。
- 新增模型字段/表见 [40_database_design.md](40_database_design.md), 本文仅引用。

---

## 1. 配置项设计 (dbmonitor/settings.py)

在 `PLAYBOOK_AUTO_CIRCUIT_BREAK` 配置块之后新增 Phase 8 配置块, 全部沿用现有 `os.environ.get` / `_envbool` 模式:

```python
# ============ Phase 8: LLM 智能诊断 ============
LLM_ENABLED = _envbool('LLM_ENABLED', False)          # 总开关, 默认关(保证回归安全)
LLM_PROVIDER = os.environ.get('LLM_PROVIDER', 'ollama')  # ollama | openai_compat
LLM_BASE_URL = os.environ.get('LLM_BASE_URL', 'http://localhost:11434/v1')
LLM_API_KEY = os.environ.get('LLM_API_KEY', 'ollama')    # ollama 任意值; 云API填真key
LLM_MODEL = os.environ.get('LLM_MODEL', 'qwen2.5:14b-instruct')
LLM_TIMEOUT_SEC = int(os.environ.get('LLM_TIMEOUT_SEC', 30))       # 单次调用超时
LLM_DIAG_BUDGET_SEC = int(os.environ.get('LLM_DIAG_BUDGET_SEC', 30))  # 阶段2.5总预算
LLM_MAX_INPUT_TOKENS = int(os.environ.get('LLM_MAX_INPUT_TOKENS', 6000))   # 证据包裁剪上限
LLM_MAX_OUTPUT_TOKENS = int(os.environ.get('LLM_MAX_OUTPUT_TOKENS', 2000))
LLM_TEMPERATURE = float(os.environ.get('LLM_TEMPERATURE', 0.1))    # 诊断要求低随机
LLM_RETRY_ON_BAD_JSON = int(os.environ.get('LLM_RETRY_ON_BAD_JSON', 1))  # JSON校验失败重试次数
LLM_REDACT_HOST = _envbool('LLM_REDACT_HOST', True)   # 证据包内 IP/主机名脱敏

# Embedding / 向量RAG
EMBED_ENABLED = _envbool('EMBED_ENABLED', False)
EMBED_BASE_URL = os.environ.get('EMBED_BASE_URL', LLM_BASE_URL)
EMBED_MODEL = os.environ.get('EMBED_MODEL', 'bge-m3')
EMBED_DIM = int(os.environ.get('EMBED_DIM', 1024))
CASE_INDEX_NAME = os.environ.get('CASE_INDEX_NAME', 'db_cases_v1')
RAG_TOP_K = int(os.environ.get('RAG_TOP_K', 5))
RAG_HYBRID_KNN_WEIGHT = float(os.environ.get('RAG_HYBRID_KNN_WEIGHT', 0.7))

# 8B 学习闭环
CASE_DISTILL_ENABLED = _envbool('CASE_DISTILL_ENABLED', True)   # 依赖 LLM_ENABLED
RULE_CALIBRATE_MIN_SAMPLES = int(os.environ.get('RULE_CALIBRATE_MIN_SAMPLES', 5))

# 8C Agentic 排查
AGENT_ENABLED = _envbool('AGENT_ENABLED', False)
AGENT_MAX_STEPS = int(os.environ.get('AGENT_MAX_STEPS', 6))
AGENT_TOOL_TIMEOUT_SEC = int(os.environ.get('AGENT_TOOL_TIMEOUT_SEC', 5))
AGENT_BUDGET_SEC = int(os.environ.get('AGENT_BUDGET_SEC', 60))
AGENT_AUTO_TRIGGER_CONF = float(os.environ.get('AGENT_AUTO_TRIGGER_CONF', 0.7))

# 8D 因果挖掘
CAUSAL_MINE_WINDOW_DAYS = int(os.environ.get('CAUSAL_MINE_WINDOW_DAYS', 30))
CAUSAL_MIN_STRENGTH = float(os.environ.get('CAUSAL_MIN_STRENGTH', 0.6))
CAUSAL_MAX_LAG_MIN = int(os.environ.get('CAUSAL_MAX_LAG_MIN', 30))

# 8E 自治策略
AUTONOMY_DEFAULT_LEVEL = os.environ.get('AUTONOMY_DEFAULT_LEVEL', 'L1')  # L0/L1/L2/L3
```

**开关联动矩阵** (brain.py 启动时判定, 记 INFO 日志):

| LLM_ENABLED | EMBED_ENABLED+ES_ENABLED | 行为 |
|---|---|---|
| False | 任意 | 阶段2.5 完全跳过; RAG 走旧 case_rag; 行为等同 Phase 7 |
| True | False | 阶段2.5 生效; RAG 走旧词法检索 |
| True | True | 全量: LLM + 向量 RAG |

---

## 2. 8A-1 `monitor/llm/providers.py` — LLM Provider 层

### 2.1 职责

统一封装 OpenAI 兼容协议的 chat 与 embeddings 两个能力; 处理超时、重试、错误分类; 不含任何业务语义。

### 2.2 类与函数设计

```python
"""LLM Provider 层 (Phase 8A)。仅依赖 requests, 不引入 SDK。"""

class LLMError(Exception):
    """LLM 调用异常基类"""
class LLMTimeout(LLMError): ...
class LLMBadResponse(LLMError): ...      # HTTP!=200 或响应结构异常
class LLMUnavailable(LLMError): ...      # 连接拒绝/DNS失败

@dataclass
class ChatResult:
    content: str            # assistant 文本
    prompt_tokens: int      # usage 缺失时置 -1
    completion_tokens: int
    latency_ms: int
    model: str

class OpenAICompatProvider:
    """OpenAI 兼容 Provider (Ollama /v1、DeepSeek、DashScope compatible-mode 通用)"""

    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: int = 30):
        # base_url 末尾去 '/', 组装 self.chat_url = f"{base_url}/chat/completions"
        #                        self.embed_url = f"{base_url}/embeddings"

    def chat(self, messages: list[dict], *,
             temperature: float = 0.1,
             max_tokens: int = 2000,
             json_mode: bool = True) -> ChatResult:
        """
        POST chat/completions。
        - json_mode=True 时 body 加 response_format={"type":"json_object"};
          若服务端返回 400 且错误信息含 'response_format' → 去掉该字段重发一次
          (兼容不支持 json_mode 的旧版 Ollama)。
        - requests.post(timeout=(3, self.timeout))  # (连接, 读取)
        - 异常映射: requests.Timeout→LLMTimeout;
                    ConnectionError→LLMUnavailable;
                    status!=200 或缺 choices→LLMBadResponse(含响应前200字符)
        """

    def embed(self, texts: list[str], model: str) -> list[list[float]]:
        """POST embeddings, input=texts。逐条校验维度==settings.EMBED_DIM,
        不符抛 LLMBadResponse。空文本以全零向量占位(不发请求)。"""


_provider_singleton = None

def get_provider() -> OpenAICompatProvider:
    """模块级单例, 按 settings 构造。settings 变化需重启进程(与项目其他引擎一致)。"""

def get_embedder() -> OpenAICompatProvider:
    """embedding 可能与 chat 不同 base_url, 独立单例。"""
```

### 2.3 重试策略

- `chat`: 网络类错误 (`LLMUnavailable`) 重试 1 次 (间隔 1s); `LLMTimeout` **不重试** (预算宝贵, 直接交给上层降级)。
- `embed`: 不重试, 失败由 case_rag_v2 降级处理。

---

## 3. 8A-2 `monitor/llm/schemas.py` — 结构化输出契约

### 3.1 诊断输出 JSON Schema (常量 `DIAGNOSIS_OUTPUT_SCHEMA`)

完整 Schema 见 [30_interface_design.md §5.1]。核心结构:

```json
{
  "root_causes": [{
      "name": "执行计划劣化导致慢查询风暴",
      "confidence": 0.85,
      "matched_rule_id": "R012",        // 与规则结果对应时填, 否则 null
      "reasoning": "证据E2显示...因此判定...",
      "evidence_refs": ["E2", "E5"],    // 必须引用证据包中的证据编号
      "category": "sql"                 // 枚举: 与 Incident.CATEGORY_CHOICES 一致
  }],
  "verdict_on_rules": [{"rule_id": "R011", "agree": true, "comment": "..."}],
  "plan_draft": {
      "immediate_actions": [{"desc": "...", "sql_hint": "...", "risk": "low|medium|high"}],
      "preventive_actions": ["..."]
  },
  "summary": "一段话中文综合结论 (<=200字)",
  "need_more_info": ["缺少 xx 时段的锁等待明细"]   // 供 8C Agent 使用
}
```

### 3.2 函数设计

```python
def validate_diagnosis_output(raw_text: str) -> dict:
    """
    1. 剥离 markdown 代码围栏 (```json ... ```) 与前后噪声文本:
       取第一个 '{' 到最后一个 '}' 的子串。
    2. json.loads → 失败抛 SchemaValidationError(stage='parse')
    3. jsonschema.validate(DIAGNOSIS_OUTPUT_SCHEMA) → 失败抛 (stage='schema')
    4. 语义修正 (宽容处理, 不抛错):
       - confidence 裁剪到 [0,1]
       - category 不在枚举内 → 'other'
       - evidence_refs 引用不存在编号 → 剔除该引用
       - root_causes 超过 5 条 → 截断前 5
    返回校验修正后的 dict。
    """

class SchemaValidationError(Exception):
    def __init__(self, stage: str, detail: str): ...
```

8B 复盘输出 Schema `DISTILL_OUTPUT_SCHEMA`、8C Agent 步输出 Schema `AGENT_STEP_SCHEMA` 同文件定义, 结构见接口文档 §5.2/§5.3。

---

## 4. 8A-3 `monitor/llm/evidence.py` — 证据包组装器

### 4.1 职责

把 `context_aggregator.build_incident_context` 的上下文 + 指标 features + 规则 RCA 结果 + RAG 案例, 组装成**带编号、可引用、预算内**的紧凑证据包。这是提示词质量的核心。

### 4.2 证据包结构 (内部 dict, 非对外 API)

```python
{
  "incident": {"id", "title", "category", "priority", "db_type",
               "occurred_at", "signal"},           # 基本面
  "evidences": [                                    # 编号证据列表
     {"eid": "E1", "type": "metric",  "label": "连接使用率 92%", "data": {...}},
     {"eid": "E2", "type": "ash",     "label": "Top等待事件", "data": {...}},
     {"eid": "E3", "type": "lock",    "label": "阻塞链 2 组", "data": {...}},
     {"eid": "E4", "type": "change",  "label": "3h前参数变更", "data": {...}},
     {"eid": "E5", "type": "sql",     "label": "Top SQL", "data": {...}},
     {"eid": "E6", "type": "baseline","label": "偏离基线 +4.2σ", "data": {...}},
  ],
  "rule_findings": [ {"rule_id","name","confidence","summary"} ],   # 规则引擎结论
  "similar_cases": [ {"case_id","title","root_cause","resolution","similarity"} ],
}
```

### 4.3 函数设计

```python
EVIDENCE_BUILDERS = [
    # (类型, 构建函数, 优先级)  优先级用于预算裁剪时的保留顺序
    ('metric',   _ev_key_metrics,    1),   # 与 signal 域相关的核心指标(复用 diagnosis_pipeline._evidence_for 的域→指标映射)
    ('ash',      _ev_ash_summary,    2),   # top_wait_events[:5] + top_sql_by_samples[:3]
    ('lock',     _ev_lock_chains,    2),   # blocking_chains[:3], 每链 waiters 截断 5
    ('change',   _ev_recent_changes, 3),   # recent_changes[:5]
    ('sql',      _ev_top_sql,        4),   # 每条 SQL 文本截断 300 字符
    ('baseline', _ev_baseline_dev,   5),   # 偏离幅度与方向
]

def build_evidence_pack(incident, context: dict, features: dict,
                        rule_roots: list, rag_matches: list) -> dict:
    """
    1. 按 EVIDENCE_BUILDERS 顺序生成证据条目, 空数据的 builder 跳过, 编号 E1..En。
    2. 脱敏 _redact(): 若 settings.LLM_REDACT_HOST:
       - 正则替换 IPv4 → 'x.x.x.*' (保留末段前的结构)
       - 剔除任何 key 含 'password'/'secret' 的字段
    3. 预算裁剪 _clip_to_budget(pack, max_tokens=LLM_MAX_INPUT_TOKENS):
       - token 估算: len(json.dumps(pack, ensure_ascii=False)) // 2  (中文≈2字符/token, 保守)
       - 超预算时按优先级从低到高整条移除证据 (先丢 baseline, 最后保 metric),
         similar_cases 最多保 3 条 → 2 条 → 1 条逐级压缩;
         循环直至达标, 记录被裁剪项到 pack['_clipped'] 供日志。
    返回证据包 dict。
    """
```

---

## 5. 8A-4 `monitor/llm/prompts.py` — 提示词模板

### 5.1 诊断系统提示词 (常量 `DIAGNOSIS_SYSTEM_PROMPT`)

要点 (完整文本编码期定稿, 结构如下):

```
你是资深数据库 SRE 专家 (Oracle/MySQL/PostgreSQL/达梦/TDSQL)。
任务: 基于给定的结构化证据做根因分析。
硬性要求:
1. 只能基于证据推理, 每条根因必须引用证据编号 (evidence_refs);
   证据不足时降低 confidence 并写入 need_more_info, 严禁编造。
2. 对 rule_findings 中每条规则结论给出 agree/disagree 判断 (verdict_on_rules)。
3. similar_cases 是历史相似案例, 可参考其根因方向, 但必须结合当前证据验证。
4. plan_draft 中 sql_hint 仅为提示, 不会被直接执行; 高危操作必须标 risk=high。
5. 输出严格为 JSON, 符合给定 Schema, 不输出任何 JSON 之外的文字。
<Schema 全文内嵌>
```

### 5.2 函数设计

```python
def build_diagnosis_messages(evidence_pack: dict) -> list[dict]:
    """返回 [{'role':'system','content':DIAGNOSIS_SYSTEM_PROMPT},
             {'role':'user','content': json.dumps(evidence_pack, ensure_ascii=False)}]"""

def build_distill_messages(incident_snapshot: dict) -> list[dict]:
    """8B 复盘提示词: 输入事故全生命周期快照, 输出 DISTILL_OUTPUT_SCHEMA JSON"""

def build_agent_system_prompt(tool_specs: list[dict]) -> str:
    """8C: 内嵌工具清单(名称/描述/参数Schema)与步进协议"""
```

---

## 6. 8A-5 `monitor/llm/brain.py` — 诊断大脑编排

### 6.1 主入口

```python
def diagnose_incident(incident, context, features,
                      rule_roots: list, rag_matches: list) -> dict | None:
    """
    诊断管道阶段 2.5 唯一入口。
    返回: {'llm_root_causes': [...], 'verdicts': [...], 'plan_draft': {...},
           'summary': str, 'need_more_info': [...], 'call_id': int}
    任何失败路径返回 None (调用方无感降级)。

    流程:
      t0 = time.time(); budget = settings.LLM_DIAG_BUDGET_SEC
      if not getattr(settings, 'LLM_ENABLED', False): return None
      pack = build_evidence_pack(...)                     # 异常→log.warning→return None
      messages = build_diagnosis_messages(pack)
      for attempt in range(1 + settings.LLM_RETRY_ON_BAD_JSON):
          剩余预算 = budget - (time.time()-t0); 若 <5s → break (降级)
          try:
              result = get_provider().chat(messages, max_tokens=LLM_MAX_OUTPUT_TOKENS,
                                           temperature=LLM_TEMPERATURE)
              parsed = validate_diagnosis_output(result.content)
              call_id = _log_call(incident, pack, result, status='ok')
              return _shape(parsed, call_id)
          except SchemaValidationError as e:
              # 重试时向 messages 追加一条 user: "上次输出不合法: {e.detail}, 请重新只输出合法JSON"
              last_err = e; continue
          except (LLMTimeout, LLMUnavailable, LLMBadResponse) as e:
              _log_call(incident, pack, None, status=_status_of(e), error=str(e))
              return None            # 网络/服务类错误不重试 JSON 轮
      _log_call(incident, pack, None, status='bad_json', error=str(last_err))
      return None
    """
```

### 6.2 结果融合 `merge_llm_with_rules` (供 diagnosis_pipeline 调用)

```python
def merge_llm_with_rules(rule_roots: list, llm_result: dict | None) -> list:
    """
    双引擎融合, 输出最终 root_causes 列表 (替换原纯规则列表)。
    规则:
      R1. llm_result 为 None → 原样返回 rule_roots, 每条补 source='rule'。
      R2. 对每条 rule_root:
          - verdicts 中 agree=True  → confidence = min(conf+0.15, 1.0), source='both'
          - agree=False → confidence = max(conf-0.20, 0.05), source='rule',
            附 llm_dissent=comment (前端显示"AI 持不同意见")
          - 未提及 → source='rule' 不变
      R3. llm_root_causes 中 matched_rule_id 为空(规则未覆盖的新假设):
          → 追加条目 source='llm', confidence=min(llm_conf, 0.85) (上限压制),
            携带 reasoning 与 evidence_refs。
      R4. 排序: sort key = (-confidence, source权重 both>rule>llm)
      R5. 截断 settings.DIAG_RCA_TOPN (现有配置, 默认3), 但 source='llm' 的
          至少保留 1 条 (若存在), 保证新假设可见。
    """
```

### 6.3 调用留痕 `_log_call`

写 `LLMCallLog` 表 (见 DB 设计 §2.1): scene='diagnosis', incident_id, model, prompt_chars, prompt_tokens, completion_tokens, latency_ms, status(ok/timeout/unavailable/bad_json/bad_response), error 前 500 字符, evidence_clipped(bool)。**不存完整提示词**, 仅存 `pack['incident']` 摘要与证据 eid 列表 (脱敏合规)。

---

## 7. 8A-6 向量 RAG: `monitor/case_rag_v2.py` + ES 改造

### 7.1 `elasticsearch_engine.py` 修改点

新增常量与 4 个函数 (跟随现有 `init_indices` / `index_metrics` 风格):

```python
CASES_INDEX = getattr(settings, 'CASE_INDEX_NAME', 'db_cases_v1')

def init_cases_index():
    """幂等创建 cases 索引 (无月度切分, 单索引)。mapping 见 40_database_design §4.1:
    text 字段(title/symptom/root_cause/resolution, ik或standard分词) +
    keyword(case_id/db_type/severity/tags) +
    dense_vector(embedding, dims=settings.EMBED_DIM, index=True, similarity='cosine')"""

def index_case(case_doc: dict) -> bool:
    """写入/覆盖一条案例文档, _id=case_id。失败 log.warning 返回 False。"""

def knn_search_cases(query_vector, db_type='', top_k=5) -> list[dict]:
    """ES kNN 检索: knn={field:'embedding', query_vector, k=top_k, num_candidates=50},
    filter: db_type 匹配或为空。返回 [{case_id, title, root_cause, resolution,
    tags, success_count, _score}]"""

def text_search_cases(query_text, db_type='', top_k=5) -> list[dict]:
    """multi_match(title^2, symptom, root_cause) BM25 检索, 同上返回结构。"""
```

### 7.2 `case_rag_v2.py` 设计

```python
class CaseRagV2:
    """向量+词法混合检索; 全程可降级。"""

    def search(self, symptom: str, db_type: str = '', top_k: int = None) -> RagResult:
        """
        1. 可用性判定: EMBED_ENABLED and ES_ENABLED and check_es_health().ok
           不满足 → return _fallback(symptom, db_type)   # 旧 case_rag.CaseRag().search
        2. vec = get_embedder().embed([symptom])[0]      # LLMError → _fallback
        3. knn = knn_search_cases(vec, db_type, top_k*2)
           bm25 = text_search_cases(symptom, db_type, top_k*2)
        4. RRF 融合: score(d) = Σ w_i / (60 + rank_i(d)),
           w_knn=RAG_HYBRID_KNN_WEIGHT, w_bm25=1-w_knn
        5. 取 top_k, 相似度归一化到 [0,1] (score/max_score),
           组装 RagResult (复用 case_rag.RagResult/CaseMatch 数据类, 保持下游兼容)
        """

    def index_case_from_model(self, alert_case) -> bool:
        """AlertCase → 向量化入 ES:
        embed_text = f"{title}\n症状:{symptom}\n根因:{root_cause}\n处置:{resolution}"
        embedding = embed([embed_text])[0]; index_case({...全字段, embedding})
        成功后 alert_case.embedding_indexed=True (新字段) save。"""

def search_cases_v2(symptom, db_type='', top_k=5):
    """模块级便捷函数, 签名与旧 case_rag.search_cases 一致。"""
```

### 7.3 `diagnosis_pipeline._search_cases` 修改

```python
# 原: from monitor.case_rag import search_cases
# 改为:
from django.conf import settings
if getattr(settings, 'EMBED_ENABLED', False):
    from monitor.case_rag_v2 import search_cases_v2 as search_cases
else:
    from monitor.case_rag import search_cases
# 其余解析逻辑不变 (返回结构兼容)
```

### 7.4 存量案例向量化

管理命令 `monitor/management/commands/backfill_case_vectors.py`:
遍历 `AlertCase.objects.filter(embedding_indexed=False)`, 逐条 `index_case_from_model`, 每 20 条 sleep 1s (保护 embedding 服务), 输出统计。

---

## 8. 8A-7 `diagnosis_pipeline.py` 插入阶段 2.5 (伪 diff)

`run_diagnosis` 中, 在"阶段2: RCA + 案例检索"块与"阶段3: 影响量化"块之间插入:

```python
    # 阶段2.5: LLM 综合研判 (Phase 8A; 失败/关闭时无损降级)
    llm_extra = {}
    try:
        from monitor.llm.brain import diagnose_incident, merge_llm_with_rules
        llm_result = diagnose_incident(inc, context, features, root_causes, similar_cases)
        root_causes = merge_llm_with_rules(root_causes, llm_result)
        if llm_result:
            llm_extra = {
                'llm_summary': llm_result['summary'],
                'llm_call_id': llm_result['call_id'],
                'need_more_info': llm_result['need_more_info'],
                'plan_draft': llm_result['plan_draft'],
            }
    except Exception as e:                      # 兜底: llm 包任何未捕获异常不破坏管道
        logger.warning("[diag] LLM 阶段异常降级: %s", e)

    rca_result = {
        'version': ...,                          # 原逻辑不变
        'engine': 'rca_v2+llm' if llm_extra else 'rca_v2+signal',   # ← 修改
        'root_causes': root_causes,
        'similar_cases': similar_cases,
        **{k: v for k, v in llm_extra.items() if k != 'plan_draft'},  # ← 新增
        'diagnosed_at': ..., 'budget_ms': ...,
    }
```

同函数"阶段4: 方案生成"调用处传入 plan_draft:

```python
    plans = generate_incident_plans(inc, root_causes,
                                    plan_draft=llm_extra.get('plan_draft'))  # ← 参数新增
```

预算说明: 阶段 2.5 使用独立预算 `LLM_DIAG_BUDGET_SEC` (30s), 总预算 `DIAG_BUDGET_SEC` 需相应上调至 120 (env 文档说明, 不改代码默认值的语义 —— 超预算现有逻辑仅 log warning 不中断)。

---

## 9. 8A-8 `remediation_planner.py` 修改 — LLM 方案融合

`generate_incident_plans(incident, root_causes, plan_draft=None)` 签名新增可选参数:

1. 既有场景模板匹配逻辑**完全不变**, 生成的模板方案仍是执行主体 (因其 SQL 参数化、风险标注可信)。
2. `plan_draft` 存在时, 追加一个 `plan_type='llm_advisory'` 的**参考方案**条目:
   - `steps` 来自 `plan_draft.immediate_actions`, 每步仅含 desc/sql_hint/risk, **无 executable 标志** —— 前端渲染为"AI 建议 (需人工转工单)", playbook_engine 不接受该类型 (在 `execute_run` 入口校验 plan_type 白名单, 见 8E)。
   - 方案卡片头部标注"由 AI 生成, 供参考, 执行须走审批"。
3. `preventive_actions` 写入方案的 `preventive` 字段 (前端"预防建议"折叠区)。

---

## 10. 8A-9 前端修改 (frontend/src)

| 位置 | 改动 |
|------|------|
| 事故详情页 (incident detail) | 新增 **AI 诊断卡片**: llm_summary 文本 + 根因列表按 source 打标 (both=绿"双引擎确认" / rule=蓝"规则" / llm=紫"AI假设", llm_dissent 显示黄色"AI持异议"提示) + reasoning 折叠展开 + evidence_refs 悬浮显示对应证据 |
| 方案区 | `llm_advisory` 类型渲染为独立"AI 建议"卡, 无执行按钮, 有"转工单"按钮 (走现有 AuditLog 创建流程) |
| 状态 | rca_result.engine 含 '+llm' 时显示 ✨ 图标; LLM 降级时无任何 AI 元素 (不显示错误) |

前端不需要新路由, 数据全部来自现有 `Incident.rca_result/plans` JSON 字段扩展。

---

## 11. 8B-1 反馈闭环: `monitor/api_views_feedback.py`

### 11.1 视图设计 (API 契约见接口文档 §3)

```python
class IncidentRcaFeedbackView(APIViewBase):   # 沿用项目 api_views 的鉴权装饰器风格
    """POST /api/v1/incidents/<incident_id>/rca-feedback/
    body: {"rule_id": "R012", "verdict": "correct"|"wrong", "comment": ""}
    权限: ACKNOWLEDGE_ALERTS (DBA及以上)
    逻辑:
      1. Incident 存在性校验; rule_id 必须在 inc.rca_result.root_causes 中
      2. RcaFeedback.objects.update_or_create(
             incident=inc, rule_id=rule_id, user=request.user.username,
             defaults={'verdict':..., 'comment':...})
      3. verdict='correct' 且该根因有 matched_case (similar_cases top1 相似度≥0.7):
         调用 CaseRag().record_success(case_id)      # ← 接通 G4 断点
    """

class IncidentPlanFeedbackView(APIViewBase):
    """POST /api/v1/incidents/<incident_id>/plan-feedback/
    body: {"plan_id": "...", "verdict": "adopted"|"useless", "comment": ""}
    写 PlanFeedback 表; adopted 时同上联动 record_success。"""
```

### 11.2 前端

事故详情页根因条目尾部 👍/👎 按钮; 方案卡片"已采纳/无效"按钮; 已反馈状态回显 (GET 详情接口返回 my_feedback)。

---

## 12. 8B-2 案例自动沉淀: `monitor/case_distiller.py`

```python
def distill_incident(incident_id: str) -> dict | None:
    """
    事故复盘 → AlertCase。由 Celery 任务在事故 resolved/closed 后异步调用。
    前置: settings.CASE_DISTILL_ENABLED and LLM_ENABLED; 该事故未沉淀过
          (AlertCase.objects.filter(source_incident=inc).exists() → 跳过)
    流程:
      1. snapshot = _build_incident_snapshot(inc):
         {title, category, signal, rca_result.root_causes(含反馈verdict),
          plans(含执行结果 PlaybookRun.status), impact, 时间线(t_detect/t_plan/t_resolve)}
      2. messages = build_distill_messages(snapshot)
         → provider.chat → validate(DISTILL_OUTPUT_SCHEMA)
         输出: {title, symptom, root_cause, resolution, tags[], quality: 'good'|'low'}
      3. quality=='low' (LLM 自评信息不足) → 只记日志不入库
      4. AlertCase 创建: case_id=f"AC-{inc.incident_id}", source='auto',
         source_incident=inc, confidence=0.6 (自动案例起始置信度低于人工0.7)
      5. CaseRagV2().index_case_from_model(case) 向量化
      6. 若事故处置实际成功 (存在 PlaybookRun status='verified' 或人工 resolved
         且 plan 反馈 adopted) → case.success_count=1
    失败: LLM 异常 → 写 LLMCallLog(scene='distill', status=...) → return None
          (Celery 任务不重试, 下个周期扫描补偿)
    """
```

**触发机制** (两条路, 互为补偿):
1. `Incident.transition()` 中 to_status ∈ {resolved, closed} 时 `tasks_phase8.distill_incident_task.delay(incident_id)` (USE_CELERY=False 时用现有 ThreadPool 方式, 与 sentinel 的做法一致)。
2. 周期任务 `scan_undistilled_incidents` (每小时): 扫描近 7 天 resolved/closed 且未沉淀事故, 逐个补偿, 单轮上限 10 个。

---

## 13. 8B-3 规则置信度校准: `monitor/rule_calibrator.py`

```python
def calibrate_rules() -> dict:
    """
    每日 Celery 任务。
    1. 聚合 RcaFeedback: per rule_id → total, correct_cnt
    2. total >= settings.RULE_CALIBRATE_MIN_SAMPLES 的规则:
       accuracy = correct_cnt/total
       平滑: calibrated = 0.3*0.6 + 0.7*accuracy   # 向先验0.6收缩, 防小样本过拟合
       RuleStat.objects.update_or_create(rule_id=..., defaults={
           'sample_count': total, 'accuracy': accuracy,
           'calibrated_confidence': round(calibrated, 2)})
    3. 返回 {'calibrated': n, 'skipped': m}
    """

def get_calibrated_base(rule_id: str) -> float:
    """带 5 分钟进程内缓存 (dict + 时间戳)。无记录返回 0.6 (现有先验)。"""
```

`rca_engine_v2._compute_confidence` 修改 (1 行): `base = 0.6` → `base = get_calibrated_base(rule['id'])` (import 放函数内, 防循环依赖, 与项目惯例一致)。

---

## 14. 8C-1 只读诊断工具箱: `monitor/llm/tools.py`

### 14.1 设计原则

- 每个工具 = 现有引擎函数的**只读薄封装**, 无任何新 SQL 拼接面; 需要下探目标库的工具只允许执行**预定义参数化查询** (复用 checkers/ash 已有采集 SQL)。
- 注册表驱动: 工具名/描述/参数 JSON Schema/实现函数/超时, Agent 与 API 共用一份定义。

### 14.2 工具注册表 (`TOOL_REGISTRY`)

| 工具名 | 参数 | 实现映射 | 返回摘要 |
|--------|------|----------|----------|
| get_metric_history | metric_key, minutes(≤180) | `timeseries.get_timeseries_storage().query` / ES query_metrics | 降采样后 ≤60 个点 + min/max/avg |
| get_top_sql | by('latency'\|'samples'), limit(≤10) | `context_aggregator.get_ash_timeline().top_sql_by_samples` / slow_query_engine | SQL指纹+均耗时+次数, 文本截300字符 |
| get_sql_plan | sql_id | `plan_capture` 已捕获计划查询 | 计划树摘要 + 是否发生突变 |
| get_lock_chain | - | ash_timeline.blocking_chains | 阻塞树 (blocker/waiters/等待秒) |
| get_ash_summary | minutes(≤60) | `get_ash_timeline` | top_wait_events[:5] |
| get_recent_changes | hours(≤168) | `change_stream.query_changes` (8D) / `_incident_recent_changes` | 变更列表[:10] |
| get_baseline_compare | metric_key | `BaselineModel` 当前时间槽 vs 当前值 | mean/std/偏离σ数 |
| get_replication_status | - | checker 采集缓存 (MonitorLog 最新) | 延迟/线程状态 |
| get_tablespace_usage | top_n(≤10) | MonitorLog.tablespaces 字段 | 使用率降序 |
| search_cases | query | case_rag_v2.search | top3 案例摘要 |

### 14.3 实现骨架

```python
@dataclass
class ToolSpec:
    name: str
    description: str          # 中文, 给 LLM 看
    params_schema: dict       # JSON Schema (properties + required)
    fn: Callable              # fn(incident, **params) -> dict
    timeout_sec: int = 5

TOOL_REGISTRY: dict[str, ToolSpec] = {...}

def run_tool(incident, name: str, params: dict) -> dict:
    """
    1. name 不在注册表 → {'error': 'unknown_tool'}
    2. jsonschema 校验 params → 失败 {'error': 'bad_params', 'detail': ...}
       (数值参数按 schema 的 maximum 强制钳制, 而非报错, 提高 Agent 容错)
    3. ThreadPoolExecutor 单任务 + future.result(timeout=spec.timeout_sec)
       超时 → {'error': 'timeout'}; 异常 → {'error': 'tool_failed', 'detail': str(e)[:200]}
    4. 结果 json 序列化后 >4000 字符 → 按类型截断 (列表砍半直至达标) + '_truncated': True
    """

def tool_specs_for_prompt() -> list[dict]:
    """[{name, description, params_schema}] 供 build_agent_system_prompt"""
```

---

## 15. 8C-2 Agent 排查循环: `monitor/llm/agent.py`

### 15.1 协议 (不依赖服务端 function-calling, 纯提示词协议, 全模型通用)

LLM 每步输出 `AGENT_STEP_SCHEMA` JSON (二选一):

```json
{"action": "tool", "tool": "get_lock_chain", "params": {}, "thought": "先看是否有阻塞链"}
{"action": "final", "conclusion": {"root_cause": "...", "confidence": 0.8,
  "evidence_summary": "...", "suggestions": ["..."]}, "thought": "证据已充分"}
```

### 15.2 主循环

```python
def run_agent_investigation(incident_id: str, trigger: str = 'auto') -> dict | None:
    """
    异步执行 (Celery task / ThreadPool), 不阻塞诊断管道。
    前置: AGENT_ENABLED and LLM_ENABLED; 同一事故并发互斥
          (cache.add(f'agent_lock_{incident_id}', 1, timeout=120) 失败即退出)

    trace = AgentTrace.objects.create(incident=inc, trigger=trigger, status='running')
    messages = [system(build_agent_system_prompt(tool_specs_for_prompt())
                       + 初始上下文: rca_result 摘要 + need_more_info)]
    for step in range(settings.AGENT_MAX_STEPS):
        剩余预算 <5s → 终止 status='budget_exceeded'
        reply = provider.chat(messages, json_mode=True)     # LLMError → status='llm_error'
        parsed = validate(AGENT_STEP_SCHEMA)                 # 失败: 纠错提示重试1次, 再失败终止
        _append_step(trace, step, parsed)                    # steps JSONField 增量落库(每步save, 前端可轮询看进度)
        if parsed['action'] == 'final':
            trace.status='done'; trace.conclusion=parsed['conclusion']
            _merge_into_incident(inc, parsed['conclusion'])  # 见 §15.3
            return conclusion
        obs = run_tool(inc, parsed['tool'], parsed['params'])
        messages += [assistant(json.dumps(parsed)), user(json.dumps({'observation': obs}))]
        # 防循环: 相同 (tool, params) 第二次出现 → observation 附加提示"该工具已调用过, 请换角度或给出结论"
    # 步数耗尽:
    messages += [user("步数已用尽, 请基于现有观察立即输出 final 结论")]
    最后一次 chat → final 或 status='no_conclusion'
    """
```

### 15.3 结论合并 `_merge_into_incident`

- `inc.rca_result['agent_conclusion'] = conclusion` (独立字段, 不覆盖 root_causes)。
- agent 结论与既有某条根因同 category 且 confidence 更高 → 该根因 confidence 取两者较大值, 附 `agent_confirmed=True`。
- `inc.save(update_fields=['rca_result','updated_at'])`; SSE 推送事故更新事件 (复用现有 SSEView 通道)。

### 15.4 触发点

1. **自动**: `run_diagnosis` 阶段 5 之后 (落库完成后), 条件: `AGENT_ENABLED and inc.priority in ('P1','P2') and top1_confidence < AGENT_AUTO_TRIGGER_CONF` → 异步派发。
2. **手动**: `POST /api/v1/incidents/<id>/investigate/` (接口文档 §3.4), 事故详情页"深度排查"按钮。

### 15.5 前端

事故详情页新增"排查轨迹"Tab: 按 AgentTrace.steps 渲染时间线 (💭 thought → 🔧 tool+params → 📄 observation 摘要), running 状态 3s 轮询, 结论卡片高亮。

---

## 16. 8D-1 变更事件流: `monitor/change_stream.py`

```python
def record_change(config_id, change_type, title, detail: dict,
                  source: str, occurred_at=None) -> ChangeEvent:
    """统一写入口。change_type: param_change|ddl|deploy|maintenance|other
    source: detector|collector|api|manual。dedup: (config, change_type, title,
    occurred_at 分钟粒度) 已存在则跳过。"""

def query_changes(config_id, hours=72, types=None) -> list[dict]:
    """诊断/工具箱统一读入口, 替换 context_aggregator._incident_recent_changes
    的数据源 (该函数改为调用本函数, 输出结构保持不变)。"""
```

接入三个生产源:
1. **config_drift 检测器** (`detectors/config_drift.py`): 检出漂移时除发事件外调用 `record_change(source='detector', change_type='param_change')` —— 插入点在其现有告警发射处。
2. **DDL 采集**: checkers 增量采集 (oracle: `dba_objects.last_ddl_time` 距上次采集有变化的对象 TOP20; mysql: `information_schema.tables.create_time/update_time` 变化) → `record_change(source='collector', change_type='ddl')`。各 checker 新增私有方法 `_collect_ddl_changes(cursor)`, 失败静默 (不影响主采集)。
3. **API 登记**: `POST /api/v1/changes/` 供发布系统/人工登记 deploy 事件 (接口文档 §3.6)。

---

## 17. 8D-2 因果挖掘: `monitor/causal_miner.py`

```python
def mine_causal_edges(config_id: int) -> list[dict]:
    """
    每周 Celery 任务对每个 active 库执行。
    1. 取 ES 近 CAUSAL_MINE_WINDOW_DAYS 天核心指标 (MetricDefinition 全量 metric_key,
       上限 20 个), 对齐为 5min 粒度序列 (缺口线性插值, 缺口>30min 分段)。
    2. 每指标 z-score 标准化; 方差≈0 的指标剔除。
    3. 对指标对 (A,B), lag ∈ {5,10,15,20,25,30}min:
       corr(lag) = pearson(A[t], B[t+lag])
       best_lag = argmax |corr|; strength = |corr(best_lag)|
    4. strength >= CAUSAL_MIN_STRENGTH 且 corr(best_lag) 显著大于 corr(0)
       (差值>0.1, 排除同源共变) → 产出边 A→B。
    5. 全量重写该库 CausalEdge 记录 (delete+bulk_create, 事务内)。
    """

def get_causal_effects(metric_key: str, config_id: int) -> list[dict]:
    """诊断期读接口: [{effect_metric, lag_min, strength}] 按 strength 降序。"""
```

`rca_engine_v2` 集成 (改 `_build_causal_chain` 与 `RCADiagnosis.effects` 填充): 命中规则的主指标先查 `get_causal_effects` (有库级学习结果优先), 空结果回落硬编码 `CAUSAL_GRAPH` (保底不回退能力)。因果链条目新增 `'type': 'learned_causal', 'strength': 0.72` 供前端区分"学习到的因果"与"专家先验"。

---

## 18. 8E-1 自治策略: `monitor/autonomy_policy.py`

```python
AUTONOMY_LEVELS = ('L0', 'L1', 'L2', 'L3')
# L0 观察: 一切方案仅展示, 不可一键执行 (按钮置灰, 仅"转工单")
# L1 半自动: 现状默认 —— 人工点执行, 走审批/低风险直执行 (兼容现有 PLAYBOOK_AUTO_LOW_RISK)
# L2 低风险自动: risk=low 的 playbook 事故创建后自动执行 (无人点按钮)
# L3 扩展自动: risk<=medium 自动执行 (medium 仍强制 precheck + 自动回滚预案存在才放行)

def get_autonomy_level(config) -> str:
    """DatabaseConfig.autonomy_level 字段 (新增, 默认 settings.AUTONOMY_DEFAULT_LEVEL);
    熔断降级检查: 24h 内该库 PlaybookRun(triggered_by='auto', status in
    failed/rolled_back) 计数 >= PLAYBOOK_AUTO_CIRCUIT_BREAK → 临时视为 L1
    (cache 记录 circuit_open_until, 24h) 并发降级通知。"""

def decide_execution(incident, plan) -> str:
    """返回 'auto_execute' | 'manual' | 'forbidden'
    规则矩阵:
      plan_type == 'llm_advisory'      → forbidden (红线: AI建议永不自动执行)
      level==L0                        → manual(仅转工单)
      level==L1                        → manual
      level==L2 and plan.risk=='low'   → auto_execute
      level==L3 and plan.risk in ('low','medium') and plan.has_rollback → auto_execute
      其余                              → manual
    """
```

**接线点**: 事故 plan_ready 后的执行入口 (api_views_incident 中触发 PlaybookRun 创建处) 调用 `decide_execution`; `auto_execute` → PlaybookRun 以 `triggered_by='auto'` 创建并直接进入 prechecking (跳过 pending_approval); playbook_engine 本体**零修改** (precheck 铁律天然继续生效)。`execute_run` 入口追加一行防御: plan_type 白名单校验 (拒绝 llm_advisory)。

### 18.1 修复场景扩展 (数据交付, 非代码)

新增管理命令 `init_phase8_playbooks.py`, 幂等 upsert 以下 Playbook 记录 (signal 关联, sql_by_db 五库适配, 均含 precheck/verify/rollback 三段):

| Playbook | signal | risk | 关键动作 |
|----------|--------|------|----------|
| PB-REPL-RESTART | repl_broken | medium | 重启复制线程 (mysql: START REPLICA; pg: 重建订阅提示) |
| PB-IDLE-TXN-KILL | long_transaction | medium | kill 超阈值空闲事务会话 (foreach_sql) |
| PB-TBS-AUTOEXTEND | space_high | medium | 数据文件扩容 (precheck 磁盘余量>2x扩容量) |
| PB-STATS-REFRESH | plan_change | low | 刷新统计信息 (ANALYZE/DBMS_STATS) |
| PB-SESS-CLEANUP | conn_high | medium | 清理 idle 超 30min 会话 (排除白名单用户) |

---

## 19. Celery 任务与调度 (`monitor/tasks_phase8.py`)

沿用 tasks.py 的 `@shared_task` 风格:

| 任务 | 触发 | 说明 |
|------|------|------|
| `distill_incident_task(incident_id)` | 事故 resolved/closed 事件 | §12; `bind=True, max_retries=0` |
| `scan_undistilled_incidents` | beat 每小时 | §12 补偿 |
| `calibrate_rules_task` | beat 每日 03:30 | §13 |
| `agent_investigate_task(incident_id, trigger)` | 诊断后条件触发/API | §15 |
| `mine_causal_edges_task` | beat 每周日 04:00 | §17, 逐库串行 |
| `backfill_case_vectors_task` | 手动/一次性 | §7.4 |

`USE_CELERY=False` 环境: 事件型任务 (distill/agent) 用 `threading.Thread(daemon=True)` 直接派发 (封装函数 `_dispatch(fn, *args)` 统一两种模式); beat 型任务由现有 APScheduler 路径注册 (与 recalculate_baselines 同机制)。

---

## 20. 日志与可观测

- 新 logger 命名: `monitor.llm`、`monitor.agent`、`monitor.distill`、`monitor.causal` (settings LOGGING 无需改动, 走 monitor 父 logger)。
- `PlatformMetric` 新增写入点 (复用现有 update_platform_metrics 任务): `llm_calls_24h`、`llm_error_rate_24h`、`llm_avg_latency_ms`、`rca_accuracy_7d`(来自 RuleStat 加权)、`case_auto_distilled_total`。
- AI 运营页 (前端新页面 /ai-ops): 上述指标卡片 + LLMCallLog 最近列表 + RuleStat 排行 (接口见接口文档 §3.7)。

---

## 21. 测试设计

### 21.1 单元测试 (`monitor/tests_phase8.py`)

| 用例组 | 关键用例 |
|--------|----------|
| providers | mock requests: 超时→LLMTimeout; 400含response_format→自动去参重发; usage缺失→tokens=-1 |
| schemas | 合法JSON通过; 带```json围栏剥离; confidence>1裁剪; 非法category→other; 烂JSON抛parse错 |
| evidence | 预算裁剪顺序 (baseline先被丢弃, metric保留); IP脱敏; password字段剔除 |
| brain | LLM_ENABLED=False→None; bad_json重试1次后降级; merge R1~R5 全分支 (agree加分/disagree减分/新假设压上限/TOPN保底) |
| case_rag_v2 | ES不可用→fallback旧检索; RRF融合排序正确性 |
| diagnosis_pipeline | llm包抛任意异常→管道正常完成且 engine='rca_v2+signal' |
| feedback | 权限校验; verdict=correct联动record_success (断言success_count+1) |
| distiller | 幂等 (二次调用跳过); quality=low不入库 |
| calibrator | 样本不足跳过; 平滑公式; get_calibrated_base缓存 |
| tools | 未知工具/参数钳制/超时/超长截断 |
| agent | 步数耗尽强制final; 重复工具调用提示; 并发互斥锁 |
| autonomy | decide_execution 全矩阵 (含 llm_advisory→forbidden, 熔断降级) |
| causal_miner | 构造已知lag的合成序列, 断言挖出正确边; 方差0剔除 |

LLM 相关测试全部 mock HTTP (`unittest.mock.patch('requests.post')`), 不依赖真实服务。

### 21.2 演练 (phase6/drills 扩展)

- `drill_llm_down.sh`: 诊断中途 kill Ollama, 断言事故照常 plan_ready 且 engine 无 '+llm'。
- `drill_llm_slow.sh`: tc/代理注入 35s 延迟, 断言阶段 2.5 预算超时降级, 总诊断 < DIAG_BUDGET_SEC 上调后的值。
- 既有全部 drills 在 LLM_ENABLED=True 下全量回归。

### 21.3 效果评测 (M1 验收)

`scripts/eval_rca_replay.py`: 从历史 Incident 抽 ≥20 例已知根因事故, 重放 `diagnose_incident` (只读, 不落库), 输出命中率/平均延迟/token 成本报表。

---

## 22. 部署与运维变更

1. **Ollama 部署** (DEPLOYMENT.md 追加章节): `ollama pull qwen2.5:14b-instruct && ollama pull bge-m3`; docker-compose.yml 新增可选 ollama 服务 (profile: llm, 挂 GPU 可选)。
2. `.env` 模板追加 §1 全部变量 (默认全关, 升级零影响)。
3. 迁移顺序: `python manage.py migrate` → `init_cases_index` (管理命令) → `backfill_case_vectors` → `init_phase8_playbooks` → 灰度开 `LLM_ENABLED` (先单库观察)。
4. 回滚预案: 关 `LLM_ENABLED/EMBED_ENABLED/AGENT_ENABLED` 即回到 Phase 7 行为; 新表不删 (无侵入)。
