# DB-AIOps v2.0 多大模型智能路由与高可用网关详细设计说明书
# (Multi-LLM Smart Router & HA Gateway Detailed Design Specification)

> **版本号**：v2.0-LLM-ROUTER  
> **编制日期**：2026-08-16  
> **文档目标**：达到**照图施工**级别的工程落地水准，指导后端路由调度引擎、凭据池化机制、场景分流策略与前端多模型管理界面的全面实施。

---

## 目录
1. [设计背景与架构定位](#1-设计背景与架构定位)
2. [系统整体架构与数据流图](#2-系统整体架构与数据流图)
3. [数据库与存储层详细设计 (4NF Schema)](#3-数据库与存储层详细设计-4nf-schema)
4. [核心智能路由引擎算法与状态机 (LLMRouterEngine)](#4-核心智能路由引擎算法与状态机-llmrouterengine)
5. [RESTful API 接口契约规范](#5-restful-api-接口契约规范)
6. [前端交互与多模型管理控制台设计 (UI/UX)](#6-前端交互与多模型管理控制台设计-uiux)
7. [安全保障、加密与熔断机制](#7-安全保障加密与熔断机制)
8. [分步实施与验证计划](#8-分步实施与验证计划)

---

## 1. 设计背景与架构定位

### 1.1 现状痛点
1. **单点依赖风险**：当前系统仅支持单一全局 `LLM_BASE_URL` 和 `LLM_API_KEY`，一旦该服务商出现网络波动、429 限流或欠费，整个系统的 Copilot、RCA 3.0 与 Agent 排查将全线瘫痪；
2. **场景资源错配**：简单 SQL 格式化与超复杂多跳因果归因使用同一个模型，无法兼顾响应时延（Latency）与推理深度（Reasoning Quality）；
3. **订阅资产闲置**：用户同时订阅了 **MiniMax TokenPlanPlus**、**Google Gemini 1.5 Pro**、**DeepSeek** 等多家优质资源，现有系统无法做多模型池化聚合与智能调度。

### 1.2 改造目标
借鉴 **CLIProxyAPI** 的核心设计理念，在 DB-AIOps 内部构建一个轻量、高性能、零第三方额外依赖的 **LLM Smart Router**：
- **多凭据轮询池化 (Round-Robin & Health Check)**：单厂商支持多 Key 负载均衡，429 自动冷却退避；
- **场景化智能分流 (Scene-Based Dispatching)**：按 SQL 优化、RCA 深度归因、Copilot 实时对话自动选择最优模型；
- **毫秒级链式容灾降级 (Failover Fallback Chains)**：主模型异常 0.8 秒内无感切换至备用模型，最终降级至本地规则引擎；
- **全链路留痕与耗时追踪 (Trace & Audit)**：完整记录模型切换轨迹、Tokens 消耗与时延分布。

---

## 2. 系统整体架构与数据流图

```
┌───────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                               DB-AIOps 业务层 (Copilot / RCA 3.0 / Agent / Inspection)                 │
└──────────────────────────────────────────────────┬────────────────────────────────────────────────────┘
                                                   │ chat(messages, scene="rca_deep|copilot|sql_fast")
                                                   ▼
┌───────────────────────────────────────────────────────────────────────────────────────────────────────┐
│                              DB-AIOps LLM Smart Router 核心调度引擎                                    │
├───────────────────────────────────────────────────────────────────────────────────────────────────────┤
│ 1. 场景解析器 (Scene Matcher)          ➔ 依据 scene 获取绑定的路由链 (如: MiniMax -> Gemini -> DeepSeek)   │
│ 2. 凭据健康检测器 (Credential Pool)    ➔ 过滤处于 CircuitBreak(熔断)/Cooldown 状态的节点               │
│ 3. 负载均衡器 (Balancer)               ➔ 根据权重/轮询策略 (Round-Robin / Active-Standby) 获取当前可用 Key │
│ 4. 故障转移状态机 (Failover Engine)    ➔ 捕获 429/5xx/Timeout，执行重试或下潜至下一优先级备用模型          │
└──────────────────┬───────────────────────────────┬───────────────────────────────┬────────────────────┘
                   │ 优先级 1                      │ 优先级 2 (降级)               │ 优先级 3 (保底)
                   ▼                               ▼                               ▼
       ┌────────────────────────┐      ┌────────────────────────┐      ┌────────────────────────┐
       │ MiniMax TokenPlanPlus  │      │ Google Gemini 1.5 Pro  │      │ DeepSeek / 本地 Ollama │
       │ (api.minimax.chat)     │      │ (generativelanguage)   │      │ (api.deepseek.com)     │
       └────────────────────────┘      └────────────────────────┘      └────────────────────────┘
```

---

## 3. 数据库与存储层详细设计 (4NF Schema)

为支持多模型提供商、凭据池与路由策略的持久化，在 `monitor/models.py` 中新增以下 2 张核心表：

### 3.1 模型提供商与凭据表 (`llm_provider_credential`)

```python
class LLMProviderCredential(models.Model):
    """大模型服务商与 API 凭据池"""
    PROVIDER_CHOICES = [
        ('minimax', 'MiniMax 名之梦'),
        ('gemini', 'Google Gemini'),
        ('deepseek', 'DeepSeek 深度求索'),
        ('openai', 'OpenAI 官方'),
        ('qwen', '阿里通义千问'),
        ('moonshot', '月之暗面 Kimi'),
        ('ollama', '本地私有 Ollama'),
        ('custom', '自定义兼容端点'),
    ]

    name = models.CharField(max_length=64, help_text="配置名称，如 MiniMax-主力账号01")
    provider_type = models.CharField(max_length=32, choices=PROVIDER_CHOICES, default='custom')
    base_url = models.CharField(max_length=255, help_text="API 接入端点 Base URL")
    api_key = models.CharField(max_length=255, help_text="加密存储的 API Key (AES-256)")
    model_name = models.CharField(max_length=64, help_text="模型 ID，如 MiniMax-Text-01 / gemini-1.5-pro")
    
    # 状态与限流控制
    is_active = models.BooleanField(default=True, db_index=True)
    priority = models.IntegerField(default=10, help_text="数字越小优先级越高 (1-100)")
    weight = models.IntegerField(default=1, help_text="同一优先级下的负载权重")
    
    # 熔断与健康状态
    is_healthy = models.BooleanField(default=True)
    cooldown_until = models.DateTimeField(null=True, blank=True, help_text="429 限流退避截止时间")
    consecutive_fails = models.IntegerField(default=0, help_text="连续失败计数")
    last_error_message = models.TextField(blank=True, default='')
    last_latency_ms = models.IntegerField(default=0, help_text="最近一次响应耗时")
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'llm_provider_credential'
        ordering = ['priority', '-weight', 'id']
```

### 3.2 场景路由策略表 (`llm_scene_routing_rule`)

```python
class LLMSceneRoutingRule(models.Model):
    """运维场景与模型调度映射规则"""
    SCENE_CHOICES = [
        ('global_default', '全局默认兜底'),
        ('copilot_chat', 'Copilot 专家日常对话'),
        ('rca_deep_reasoning', 'RCA 3.0 根因深度推理'),
        ('sql_explain_opt', 'SQL 执行计划与索引优化'),
        ('incident_warroom', '排障作战室自愈决策'),
    ]

    scene_code = models.CharField(max_length=64, unique=True, choices=SCENE_CHOICES)
    scene_name = models.CharField(max_length=64)
    description = models.CharField(max_length=255, blank=True, default='')
    
    # 绑定的主选与备选凭据
    primary_credential = models.ForeignKey(
        LLMProviderCredential, on_delete=models.SET_NULL, null=True,
        related_name='primary_routes'
    )
    fallback_credentials = models.ManyToManyField(
        LLMProviderCredential, blank=True,
        related_name='fallback_routes'
    )
    
    # 场景超参数覆盖
    temperature = models.FloatField(default=0.1)
    timeout_sec = models.IntegerField(default=20)
    max_tokens = models.IntegerField(default=2048)
    
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'llm_scene_routing_rule'
```

---

## 4. 核心智能路由引擎算法与状态机 (LLMRouterEngine)

在 `monitor/llm/router.py` 中实现统一路由调度器：

### 4.1 核心算法实现

```python
class LLMRouterEngine:
    """智能多模型调度与容灾降级路由器"""

    @classmethod
    def chat_with_fallback(cls, messages: list, scene: str = 'copilot_chat', **kwargs) -> ChatResult:
        rule = LLMSceneRoutingRule.objects.filter(scene_code=scene).first()
        candidate_credentials = cls._get_candidate_chain(rule)
        
        failover_traces = []
        now = timezone.now()

        for cred in candidate_credentials:
            if not cred.is_active or (cred.cooldown_until and cred.cooldown_until > now):
                continue
                
            provider = OpenAICompatProvider(
                base_url=cred.base_url,
                api_key=decrypt_password(cred.api_key),
                model=cred.model_name,
                timeout=kwargs.get('timeout') or (rule.timeout_sec if rule else 25)
            )
            
            t0 = time.time()
            try:
                result = provider.chat(
                    messages,
                    temperature=kwargs.get('temperature') or (rule.temperature if rule else 0.1),
                    max_tokens=kwargs.get('max_tokens') or (rule.max_tokens if rule else 2048),
                    scene=scene
                )
                
                # 成功调用：重置失败计数
                cred.consecutive_fails = 0
                cred.is_healthy = True
                cred.last_latency_ms = int((time.time() - t0) * 1000)
                cred.save(update_fields=['consecutive_fails', 'is_healthy', 'last_latency_ms'])
                
                result.failover_traces = failover_traces
                result.provider_name = cred.name
                return result

            except Exception as exc:
                latency = int((time.time() - t0) * 1000)
                err_str = str(exc)
                failover_traces.append({
                    'provider': cred.name,
                    'model': cred.model_name,
                    'error': err_str[:150],
                    'latency_ms': latency
                })
                
                if '429' in err_str:
                    cred.cooldown_until = now + timedelta(seconds=60)
                
                cred.consecutive_fails += 1
                if cred.consecutive_fails >= 3:
                    cred.is_healthy = False
                cred.last_error_message = err_str[:300]
                cred.save(update_fields=['cooldown_until', 'consecutive_fails', 'is_healthy', 'last_error_message'])
                logger.warning("[LLMRouter] 凭据 %s 失败，触发自动故障转移: %s", cred.name, exc)

        raise LLMAllProvidersExhausted(f"所有可用大模型均失败，轨迹: {failover_traces}")
```

---

## 5. RESTful API 接口契约规范

### 5.1 凭据管理接口族

| HTTP 方法 | 接口路径 | 权限要求 | 说明 |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/llm/credentials/` | `alert.config_view` | 获取已注册的大模型凭据列表（Key脱敏，包含延迟与健康状态） |
| `POST` | `/api/v1/llm/credentials/` | `superuser` | 新增一个大模型服务商凭据并验证连通性 |
| `PUT` | `/api/v1/llm/credentials/{id}/` | `superuser` | 修改凭据参数、优先级或启停状态 |
| `DELETE` | `/api/v1/llm/credentials/{id}/` | `superuser` | 删除指定模型凭据 |
| `POST` | `/api/v1/llm/credentials/{id}/ping/`| `alert.config_view` | 单独对指定凭据进行 1-Click 探活测试 |

### 5.2 场景路由规则接口族

| HTTP 方法 | 接口路径 | 权限要求 | 说明 |
| :--- | :--- | :--- | :--- |
| `GET` | `/api/v1/llm/routes/` | `alert.config_view` | 获取各运维场景（Copilot、RCA、SQL）绑定的主备路由链 |
| `PUT` | `/api/v1/llm/routes/{scene_code}/`| `superuser` | 更新特定场景的主模型与备选模型顺序 |

---

## 6. 前端交互与多模型管理控制台设计 (UI/UX)

在前端 [`frontend/src/pages/LLMConfigSettings.jsx`](file:///Users/mac/DB_Monitor/db-aiops/frontend/src/pages/LLMConfigSettings.jsx) 中进行 Tab 化升级：

1. **Tab 1: 凭据连接池 (Credentials Pool)**：
   - 卡片式展示已配置的模型服务（MiniMax、Gemini 1.5 Pro、DeepSeek 等）；
   - 状态指示灯：🟢 健康 (正常) / 🟡 429 冷却中 (倒计时 mm:ss) / 🔴 连通异常；
   - 支持一键测试（Ping）、快速复制、编辑与优先级权重调整。
2. **Tab 2: 场景智能路由 (Scene Routing Matrix)**：
   - 矩阵式配置不同业务场景对应的主模型与 Failover 顺序；
   - 示例：
     - `Copilot 专家日常对话` ➔ `[主选] MiniMax TokenPlanPlus` ➔ `[备选1] Gemini 1.5 Pro`
     - `RCA 3.0 根因深度推理` ➔ `[主选] Google Gemini 1.5 Pro` ➔ `[备选1] DeepSeek-Chat`
3. **Tab 3: 连通性测试沙箱与路由追踪 (Sandbox & Tracing)**：
   - 提供模拟提问输入框，实时展示路由引擎的调度决策树与多跳 Failover 链路。

---

## 7. 安全保障、加密与熔断机制

1. **API Key AES-256 加密存储**：所有存入数据库的 API Key 均经过项目主密钥哈希加密，前端输出永远做 `sk-****abcd` 密文掩码；
2. **429 智能避让与指数退避**：单 Key 触发 Rate Limit 时自动进入 60 秒静默冷却，不影响该提供商下其他 Key 的轮询；
3. **Fail-Closed 与离线平滑降级**：当外部网络不可达或所有云端 Key 均失效时，Copilot 和 RCA 自动切回内置的 DBA 专家规则引擎，绝不阻断系统告警与排障流程。

---

## 8. 分步实施与验证计划

- **Step 1: 模型与路由数据结构构建**：创建 `LLMProviderCredential` 与 `LLMSceneRoutingRule` ORM 模型并执行数据库迁移；
- **Step 2: 编写智能路由引擎 Core**：在 `monitor/llm/router.py` 中实现凭据池管理、负载均衡与 Failover 机制；
- **Step 3: 改造现有调用端**：将 `copilot.py`、`rca_engine_v3.py` 与 `api_views_phase8.py` 平滑迁移至 `LLMRouterEngine.chat()`；
- **Step 4: 控制台 UI 升级**：构建前端多凭据管理与场景路由可视化配置面板；
- **Step 5: 单元与链路容灾测试**：模拟主模型 429/超时，验证毫秒级故障转移与降级行为。
