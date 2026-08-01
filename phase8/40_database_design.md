# Phase 8 数据库设计文档 (Database Design Specification)

> 文档编号: PH8-DBD-01 | 版本: v1.0 | 状态: 评审中
> 范围: PostgreSQL 新增表 (Django Models)、既有表字段变更、Elasticsearch 向量索引、迁移与回滚
> 上游: [20_detailed_design.md](20_detailed_design.md)

---

## 1. 总览

| 对象 | 类型 | 阶段 | 说明 |
|------|------|------|------|
| `LLMCallLog` | 新表 | 8A | LLM 调用审计留痕 |
| `RcaFeedback` | 新表 | 8B | 根因人工反馈 |
| `PlanFeedback` | 新表 | 8B | 方案人工反馈 |
| `RuleStat` | 新表 | 8B | 规则准确率校准结果 |
| `AgentTrace` | 新表 | 8C | Agent 排查轨迹 |
| `ChangeEvent` | 新表 | 8D | 统一变更事件流 |
| `CausalEdge` | 新表 | 8D | 学习到的因果边 |
| `AlertCase` | 字段变更 | 8A/8B | +source, +source_incident, +embedding_indexed |
| `DatabaseConfig` | 字段变更 | 8E | +autonomy_level |
| ES `db_cases_v1` | 新索引 | 8A | 案例向量索引 |

所有新表定义追加在 `monitor/models.py` 末尾 (SqlPlan 之后), 遵循既有风格: 中文 verbose_name、`created_at = models.DateTimeField(auto_now_add=True)`、显式 `Meta.verbose_name` 与索引。

命名规范: 表名走 Django 默认 `monitor_<小写类名>`; JSON 结构字段一律 `models.JSONField`。

---

## 2. 新增表设计

### 2.1 LLMCallLog — LLM 调用日志

```python
class LLMCallLog(models.Model):
    """LLM 调用留痕 (Phase 8A)。只存摘要不存全量提示词。"""
    SCENE_CHOICES = (
        ('diagnosis', '事故诊断'), ('distill', '案例复盘'),
        ('agent', '深度排查'), ('test', '连通性测试'),
    )
    STATUS_CHOICES = (
        ('ok', '成功'), ('timeout', '超时'), ('unavailable', '服务不可用'),
        ('bad_response', '响应异常'), ('bad_json', '输出校验失败'),
    )
    scene = models.CharField(max_length=20, choices=SCENE_CHOICES, db_index=True, verbose_name="场景")
    incident_id = models.CharField(max_length=48, blank=True, default='', db_index=True, verbose_name="关联事故ID")
    config = models.ForeignKey('DatabaseConfig', on_delete=models.SET_NULL,
        null=True, blank=True, verbose_name="数据库")
    model = models.CharField(max_length=80, verbose_name="模型名")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, db_index=True, verbose_name="状态")
    prompt_chars = models.IntegerField(default=0, verbose_name="提示词字符数")
    prompt_tokens = models.IntegerField(default=-1, verbose_name="输入token", help_text="-1=服务端未返回usage")
    completion_tokens = models.IntegerField(default=-1, verbose_name="输出token")
    latency_ms = models.IntegerField(default=0, verbose_name="延迟(ms)")
    evidence_eids = models.JSONField(default=list, verbose_name="证据编号列表", help_text="如 ['E1','E2']")
    evidence_clipped = models.BooleanField(default=False, verbose_name="证据被预算裁剪")
    error = models.TextField(blank=True, default='', verbose_name="错误信息", help_text="截断500字符")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True, verbose_name="创建时间")

    class Meta:
        verbose_name = "LLM调用日志"
        verbose_name_plural = "LLM调用日志"
        ordering = ['-created_at']
        indexes = [models.Index(fields=['scene', 'status', 'created_at'])]
```

数据保留: 90 天, 由现有日终清理任务风格新增 `cleanup_llm_logs` (delete created_at < now-90d)。
预估容量: 每事故诊断 1 条 + 复盘 1 条 + Agent 每次 1 条, 日均 < 500 行, 无压力。

### 2.2 RcaFeedback — 根因反馈

```python
class RcaFeedback(models.Model):
    """根因人工反馈 (Phase 8B), 学习闭环与规则校准的数据源。"""
    VERDICT_CHOICES = (('correct', '正确'), ('wrong', '错误'))
    incident = models.ForeignKey('Incident', on_delete=models.CASCADE,
        related_name='rca_feedbacks', verbose_name="事故")
    rule_id = models.CharField(max_length=20, db_index=True, verbose_name="规则ID",
        help_text="root_causes 中的 rule_id; LLM新假设为 'LLM'")
    source = models.CharField(max_length=10, default='rule', verbose_name="根因来源",
        help_text="rule/llm/both, 冗余存储便于统计")
    verdict = models.CharField(max_length=10, choices=VERDICT_CHOICES, verbose_name="判定")
    comment = models.CharField(max_length=500, blank=True, default='', verbose_name="备注")
    user = models.CharField(max_length=100, verbose_name="反馈人")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "根因反馈"
        verbose_name_plural = "根因反馈"
        constraints = [models.UniqueConstraint(
            fields=['incident', 'rule_id', 'user'], name='uniq_rca_feedback')]
        indexes = [models.Index(fields=['rule_id', 'verdict'])]
```

### 2.3 PlanFeedback — 方案反馈

```python
class PlanFeedback(models.Model):
    """方案采纳反馈 (Phase 8B)。"""
    VERDICT_CHOICES = (('adopted', '已采纳'), ('useless', '无效'))
    incident = models.ForeignKey('Incident', on_delete=models.CASCADE,
        related_name='plan_feedbacks', verbose_name="事故")
    plan_id = models.CharField(max_length=64, verbose_name="方案ID",
        help_text="incident.plans[].plan_id")
    plan_type = models.CharField(max_length=30, default='template', verbose_name="方案类型",
        help_text="template/llm_advisory, 冗余便于统计采纳率分渠道")
    verdict = models.CharField(max_length=10, choices=VERDICT_CHOICES, verbose_name="判定")
    comment = models.CharField(max_length=500, blank=True, default='', verbose_name="备注")
    user = models.CharField(max_length=100, verbose_name="反馈人")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="创建时间")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "方案反馈"
        verbose_name_plural = "方案反馈"
        constraints = [models.UniqueConstraint(
            fields=['incident', 'plan_id', 'user'], name='uniq_plan_feedback')]
```

### 2.4 RuleStat — 规则校准统计

```python
class RuleStat(models.Model):
    """规则历史准确率与校准置信度 (Phase 8B), rule_calibrator 每日重算。"""
    rule_id = models.CharField(max_length=20, primary_key=True, verbose_name="规则ID")
    rule_name = models.CharField(max_length=100, blank=True, default='', verbose_name="规则名")
    sample_count = models.IntegerField(default=0, verbose_name="反馈样本数")
    correct_count = models.IntegerField(default=0, verbose_name="正确数")
    accuracy = models.FloatField(default=0.0, verbose_name="准确率")
    calibrated_confidence = models.FloatField(default=0.6, verbose_name="校准后基础置信度",
        help_text="0.3*0.6 + 0.7*accuracy, rca_engine_v2 读取")
    updated_at = models.DateTimeField(auto_now=True, verbose_name="更新时间")

    class Meta:
        verbose_name = "规则准确率统计"
        verbose_name_plural = "规则准确率统计"
```

### 2.5 AgentTrace — 排查轨迹

```python
class AgentTrace(models.Model):
    """Agentic 深度排查轨迹 (Phase 8C)。steps 增量写, 前端轮询渲染。"""
    STATUS_CHOICES = (
        ('running', '排查中'), ('done', '完成'), ('llm_error', 'LLM异常'),
        ('budget_exceeded', '超预算'), ('no_conclusion', '未收敛'),
    )
    incident = models.ForeignKey('Incident', on_delete=models.CASCADE,
        related_name='agent_traces', verbose_name="事故")
    trigger = models.CharField(max_length=10, default='auto', verbose_name="触发方式",
        help_text="auto/manual")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES,
        db_index=True, default='running', verbose_name="状态")
    steps = models.JSONField(default=list, verbose_name="步骤轨迹",
        help_text="[{seq,thought,tool,params,observation_summary,elapsed_ms}]")
    conclusion = models.JSONField(default=dict, verbose_name="结论")
    total_tokens = models.IntegerField(default=0, verbose_name="累计token")
    started_at = models.DateTimeField(auto_now_add=True, verbose_name="开始时间")
    finished_at = models.DateTimeField(null=True, blank=True, verbose_name="结束时间")

    class Meta:
        verbose_name = "AI排查轨迹"
        verbose_name_plural = "AI排查轨迹"
        ordering = ['-started_at']
```

steps 内 observation 只存**摘要** (observation_summary ≤500字符), 完整观察不落库 (体积控制)。

### 2.6 ChangeEvent — 变更事件流

```python
class ChangeEvent(models.Model):
    """统一变更事件 (Phase 8D): 参数变更/DDL/发布/维护。"""
    TYPE_CHOICES = (
        ('param_change', '参数变更'), ('ddl', 'DDL'), ('deploy', '应用发布'),
        ('maintenance', '维护操作'), ('other', '其他'),
    )
    SOURCE_CHOICES = (
        ('detector', '漂移检测'), ('collector', '采集器'),
        ('api', 'API登记'), ('manual', '人工'),
    )
    config = models.ForeignKey('DatabaseConfig', on_delete=models.CASCADE,
        related_name='change_events', verbose_name="数据库")
    change_type = models.CharField(max_length=20, choices=TYPE_CHOICES, db_index=True, verbose_name="变更类型")
    title = models.CharField(max_length=200, verbose_name="变更标题")
    detail = models.JSONField(default=dict, verbose_name="变更详情")
    source = models.CharField(max_length=20, choices=SOURCE_CHOICES, verbose_name="来源")
    occurred_at = models.DateTimeField(db_index=True, verbose_name="发生时间")
    dedup_key = models.CharField(max_length=200, db_index=True, verbose_name="去重键",
        help_text="config_id|change_type|title|occurred_at(分钟)")
    created_at = models.DateTimeField(auto_now_add=True, verbose_name="记录时间")

    class Meta:
        verbose_name = "变更事件"
        verbose_name_plural = "变更事件"
        ordering = ['-occurred_at']
        constraints = [models.UniqueConstraint(fields=['dedup_key'], name='uniq_change_dedup')]
        indexes = [models.Index(fields=['config', 'change_type', 'occurred_at'])]
```

数据保留: 180 天。DDL 采集高频库注意: 单库单轮最多写 20 条 (collector 端限制)。

### 2.7 CausalEdge — 因果边

```python
class CausalEdge(models.Model):
    """离线挖掘的指标因果边 (Phase 8D), causal_miner 每周全量重写。"""
    config = models.ForeignKey('DatabaseConfig', on_delete=models.CASCADE,
        related_name='causal_edges', verbose_name="数据库")
    cause_metric = models.CharField(max_length=100, db_index=True, verbose_name="因指标")
    effect_metric = models.CharField(max_length=100, verbose_name="果指标")
    lag_min = models.IntegerField(verbose_name="滞后(分钟)")
    strength = models.FloatField(verbose_name="强度", help_text="|pearson| at best lag")
    direction = models.CharField(max_length=10, default='positive', verbose_name="方向",
        help_text="positive/negative (相关系数符号)")
    window_days = models.IntegerField(default=30, verbose_name="挖掘窗口天数")
    mined_at = models.DateTimeField(auto_now=True, verbose_name="挖掘时间")

    class Meta:
        verbose_name = "因果边"
        verbose_name_plural = "因果边"
        constraints = [models.UniqueConstraint(
            fields=['config', 'cause_metric', 'effect_metric'], name='uniq_causal_edge')]
```

---

## 3. 既有表字段变更

### 3.1 AlertCase (8A/8B)

```python
# 追加 3 个字段 (均带默认值, 迁移零风险):
source = models.CharField(max_length=10, default='manual', verbose_name="案例来源",
    help_text="manual=人工, auto=事故自动沉淀")
source_incident = models.ForeignKey('Incident', on_delete=models.SET_NULL,
    null=True, blank=True, related_name='distilled_cases', verbose_name="来源事故")
embedding_indexed = models.BooleanField(default=False, verbose_name="已向量化",
    help_text="ES db_cases_v1 中存在向量文档")
```

注意: 既有 `symptom_signature` 为 JSONField 但旧 case_rag 按字符串读写 —— case_rag_v2 兼容两种类型 (`str(sig) if not isinstance(sig, str)`), 不做数据订正 (向量检索不依赖该字段)。

### 3.2 DatabaseConfig (8E)

```python
autonomy_level = models.CharField(max_length=4, default='L1', verbose_name="自治等级",
    help_text="L0观察/L1半自动/L2低风险自动/L3扩展自动")
```

### 3.3 Incident — 无字段变更

`rca_result`/`plans` 均为 JSONField, 8A 的 llm_summary、source、agent_conclusion、llm_advisory 方案等全部作为 JSON 内部结构扩展, **不加列** (结构见接口文档 §3.3)。

---

## 4. Elasticsearch 索引设计

### 4.1 `db_cases_v1` — 案例向量索引

单索引不按月切分 (案例量级: 千级)。`init_cases_index()` 幂等创建:

```json
{
  "settings": {"number_of_shards": 1, "number_of_replicas": 0},
  "mappings": {
    "properties": {
      "case_id":       {"type": "keyword"},
      "title":         {"type": "text"},
      "db_type":       {"type": "keyword"},
      "severity":      {"type": "keyword"},
      "symptom":       {"type": "text"},
      "root_cause":    {"type": "text"},
      "resolution":    {"type": "text"},
      "tags":          {"type": "keyword"},
      "source":        {"type": "keyword"},
      "success_count": {"type": "integer"},
      "confidence":    {"type": "float"},
      "created_at":    {"type": "date"},
      "embedding":     {"type": "dense_vector", "dims": 1024,
                        "index": true, "similarity": "cosine"}
    }
  }
}
```

- `_id = case_id` (upsert 语义, 与 PG 表一对一)。
- `dims` 取自 `settings.EMBED_DIM`; **换 embedding 模型必须换索引名** (db_cases_v2) 并全量重建 —— 运维手册注明。
- 分词: 默认 standard (部署了 ik 插件的环境可在创建前通过 env `CASE_INDEX_ANALYZER=ik_max_word` 指定, init 函数读取)。

### 4.2 一致性策略

- PG `AlertCase` 为**权威源**, ES 为检索投影; 写序: PG 先 commit → ES 索引 → 成功回写 `embedding_indexed=True`。
- ES 写失败: `embedding_indexed` 保持 False, 每日任务 `backfill_case_vectors_task` 扫描补偿。
- 删除案例 (Admin 操作): post_delete signal 中 best-effort 删 ES 文档, 失败仅告警 (孤儿向量文档不影响正确性, 检索后拼 PG 数据时会过滤不存在的 case_id)。

---

## 5. 迁移方案

### 5.1 迁移文件

单个迁移 `monitor/migrations/00XX_phase8.py` (makemigrations 自动生成, 内容审查要点):

1. CreateModel × 7 (LLMCallLog, RcaFeedback, PlanFeedback, RuleStat, AgentTrace, ChangeEvent, CausalEdge)
2. AddField × 4 (AlertCase ×3, DatabaseConfig ×1)
3. 全部字段带 default → **无数据回填、无表锁风险** (PG ADD COLUMN with default 为元数据操作)

### 5.2 执行顺序 (部署手册)

```bash
python manage.py migrate monitor
python manage.py init_cases_index          # 新管理命令, 幂等
python manage.py backfill_case_vectors     # 存量案例向量化 (可选, EMBED_ENABLED 时)
python manage.py init_phase8_playbooks     # 8E 场景 Playbook 种子 (幂等 upsert)
```

### 5.3 回滚

- 应用层回滚: 关闭 `LLM_ENABLED/EMBED_ENABLED/AGENT_ENABLED` 环境变量即可, 新表新字段留存无副作用。
- Schema 回滚 (极端情况): `migrate monitor <上一版本>`; ES 索引 `DELETE db_cases_v1`。新字段均无业务写依赖 (default 兜底), 回滚安全。

---

## 6. 容量与索引评估

| 表 | 预估行量 (年) | 主要查询模式 | 覆盖索引 |
|----|---------------|--------------|----------|
| LLMCallLog | ~15万 (90天保留后稳态 ~4万) | scene+status+时间过滤分页 | (scene,status,created_at) |
| RcaFeedback | ~5千 | rule_id 聚合 (校准) | (rule_id,verdict) |
| PlanFeedback | ~3千 | plan_type 聚合 | 默认 PK 足够 |
| RuleStat | ≤100 (规则数) | 全表读+缓存 | PK |
| AgentTrace | ~2千 | incident 反查 | FK 自动索引 |
| ChangeEvent | ~10万 (180天保留) | config+type+时间窗 | (config,change_type,occurred_at) |
| CausalEdge | ≤ 库数×400 (20指标全组合) | config+cause_metric | uniq约束 + cause_metric单列 |

全部为轻量表, 无分区需求; JSONField 均存摘要级数据 (单行 < 32KB)。
