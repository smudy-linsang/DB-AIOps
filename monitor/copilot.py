# -*- coding: utf-8 -*-
"""
DB-AIOps Copilot 核心引擎模块
=============================
提供智能问答、自然语言排障与指标检索、一键健康巡检与体检生成能力。
"""
import json
import logging
import time
from typing import Dict, Any, List, Optional
from django.conf import settings
from django.utils import timezone
from monitor.models import DatabaseConfig, MonitorLog, AlertLog, Incident, AuditLog
from monitor.llm import llm_enabled
from monitor.llm.providers import get_chat_provider, LLMError

logger = logging.getLogger("monitor.copilot")


COPILOT_SYSTEM_PROMPT = """你是 DB-AIOps 企业级数据库智能运维平台的专属 Copilot 助手。
精通各类主流数据库（Oracle, MySQL, PostgreSQL, 达梦 DM8, TDSQL, GBase 8a 等）的运维排障、架构设计、性能调优与容量分析。

你的职责：
1. 解答运维工程师和 DBA 关于数据库状态、性能瓶颈、等待事件、告警排障、SQL 优化的疑问；
2. 结合给定的数据库当前快照信息与上下文事实，进行深度根因推导并给出切实可行的处置建议；
3. 输出清晰、专业、排版优良的 Markdown 格式（含表格、代码高亮、重点加粗）；
4. 绝不给出未经确认的破坏性无阻断命令（高危操作需提示审批和测试）。
"""


def _get_database_context(config: DatabaseConfig) -> Dict[str, Any]:
    """汇总指定数据库的当前关键上下文快照"""
    ctx = {
        'id': config.id,
        'name': config.name,
        'db_type': config.db_type,
        'host': config.host,
        'port': config.port,
        'is_active': config.is_active,
        'cpu_cores': config.cpu_cores,
        'autonomy_level': config.autonomy_level,
    }

    # 最新指标
    latest_log = MonitorLog.objects.filter(config=config).order_by('-create_time').first()
    if latest_log:
        try:
            msg = json.loads(latest_log.message) if isinstance(latest_log.message, str) else latest_log.message
            # 过滤常见性能与容量指标
            filtered = {}
            for k, v in (msg or {}).items():
                if isinstance(v, (int, float, str)) and not isinstance(v, bool):
                    filtered[k] = v
            ctx['latest_metrics'] = filtered
            ctx['status'] = latest_log.status
            ctx['last_check_time'] = latest_log.create_time.isoformat()
        except Exception:
            ctx['latest_metrics'] = {}
    else:
        ctx['latest_metrics'] = {}

    # 最近未恢复/活动告警
    recent_alerts = AlertLog.objects.filter(config=config).order_by('-create_time')[:5]
    ctx['recent_alerts'] = [
        {
            'title': a.title,
            'severity': a.severity,
            'metric': getattr(a, 'metric_key', ''),
            'time': a.create_time.isoformat()
        }
        for a in recent_alerts
    ]

    # 最近事故
    recent_incidents = Incident.objects.filter(config=config).order_by('-created_at')[:3]
    ctx['recent_incidents'] = [
        {
            'incident_id': inc.incident_id,
            'title': inc.title,
            'status': inc.status,
            'severity': inc.severity,
            'root_cause': (inc.rca_result or {}).get('root_causes', [])
        }
        for inc in recent_incidents
    ]

    return ctx


def run_copilot_chat(query: str, config_id: Optional[int] = None, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """
    Copilot 对话处理入口
    """
    config = None
    context_data = {}
    if config_id:
        config = DatabaseConfig.objects.filter(id=config_id).first()
        if config:
            context_data = _get_database_context(config)

    # 尝试 LLM 智能生成
    if llm_enabled():
        try:
            provider = get_chat_provider()
            messages = [{'role': 'system', 'content': COPILOT_SYSTEM_PROMPT}]

            # 注入历史记录（最近 6 轮）
            if history:
                for h in history[-6:]:
                    if h.get('role') in ('user', 'assistant') and h.get('content'):
                        messages.append({'role': h['role'], 'content': h['content']})

            # 当前用户问题与上下文
            user_prompt = f"用户提问：{query}\n"
            if context_data:
                user_prompt += f"\n【当前目标数据库实时上下文】:\n```json\n{json.dumps(context_data, ensure_ascii=False, indent=2)}\n```\n"

            messages.append({'role': 'user', 'content': user_prompt})

            res = provider.chat(messages, scene='copilot', timeout=30)
            return {
                'answer': res.content,
                'model': res.model,
                'source': 'llm',
                'latency_ms': res.latency_ms,
                'context_used': bool(context_data),
            }
        except Exception as e:
            logger.warning("Copilot LLM 调用失败，降级至规则引擎/内置知识库: %s", e)

    # 降级模式：基于规则与关键词匹配给出结构化建议
    return _fallback_copilot_response(query, config, context_data)


def _fallback_copilot_response(query: str, config: Optional[DatabaseConfig], context_data: Dict[str, Any]) -> Dict[str, Any]:
    """离线/降级模式下的结构化回复引擎"""
    q_lower = query.lower()
    sections = []

    target_desc = f"数据库 **{config.name}** ({config.db_type})" if config else "当前未关联特定数据库"
    sections.append(f"### 🤖 DB-AIOps 智能运维助手 (内置专家引擎模式)\n\n> 🎯 **目标对象**: {target_desc}\n")

    # 1. 指标/状态查询类
    if any(k in q_lower for k in ['状态', '指标', 'cpu', '内存', '连接', 'status', 'metric', 'qps']):
        metrics = context_data.get('latest_metrics', {})
        if metrics:
            sections.append("#### 📊 最新实时指标快照")
            rows = []
            for k, v in list(metrics.items())[:10]:
                rows.append(f"| `{k}` | **{v}** |")
            sections.append("| 指标项 | 当前数值 |\n| :--- | :--- |\n" + "\n".join(rows))
        else:
            sections.append("⚠️ 当前暂未获取到最新的指标上报日志，建议检查监控采集链路 `start_monitor` 运行状态。")

    # 2. 告警/故障排查类
    if any(k in q_lower for k in ['告警', '报错', '故障', 'alert', 'error', '慢', '卡顿', '阻塞', 'lock']):
        alerts = context_data.get('recent_alerts', [])
        if alerts:
            sections.append("\n#### 🚨 关联活动告警分析")
            for a in alerts:
                sections.append(f"- **[{a['severity'].upper()}]** {a['title']} (`{a['metric']}`) - *{a['time']}*")
            sections.append("\n💡 **排查建议**：\n1. 检查是否存在锁等待或慢查询（建议前往【性能中心】查看 Blocking Tree 与 Top SQL）；\n2. 检查连接池是否被打满或未及时释放。")
        else:
            sections.append("\n✅ 该数据库当前无未确认的严重告警。")

    # 3. SQL 优化建议
    if any(k in q_lower for k in ['sql', '优化', '索引', 'index', 'explain', '慢查询']):
        sections.append("\n#### 🔍 SQL 与索引优化建议指南")
        sections.append("""1. **执行计划分析**：重点关注是否出现 `Full Table Scan` (全表扫描) 或 `Filesort` / `Temporary Table`；
2. **复合索引最左匹配**：高选择性列前置，避免在索引列上使用函数转换（如 `DATE(create_time)`）；
3. **分批次与分页优化**：对于大表深度分页，采用延迟关联（Subquery / Join 主键）优化 `LIMIT offset, N`。""")

    # 4. 通用保底
    if len(sections) == 1:
        sections.append(f"""感谢您的咨询！针对问题 **"{query}"**，DB-AIOps 平台建议：
- 如需诊断特定实例性能，请在顶部选择对应数据库实例；
- 您也可以直接点击下方快捷按钮触发 **【一键智能体检】** 或查看 **【性能中心 ASH/AAS 分析】**；
- 平台支持实时因果图推导、告警全链路追踪与工单闭环处理。""")

    return {
        'answer': "\n\n".join(sections),
        'model': 'ExpertRule-v2.2',
        'source': 'rule_fallback',
        'latency_ms': 15,
        'context_used': bool(context_data),
    }


def generate_quick_health_assessment(config_id: int) -> Dict[str, Any]:
    """
    一键智能体检生成器：聚合配置、性能、容量、高可用、告警等 5 大维度
    """
    config = DatabaseConfig.objects.filter(id=config_id).first()
    if not config:
        return {'error': f'Database with ID {config_id} not found'}

    ctx = _get_database_context(config)
    metrics = ctx.get('latest_metrics', {})

    dimensions = []
    overall_score = 100
    risk_items = []

    # 1. 运行可用性 (Availability)
    avail_score = 100 if ctx.get('status') == 'UP' else 0
    if avail_score < 100:
        overall_score -= 40
        risk_items.append({'level': 'critical', 'title': '数据库状态异常', 'desc': '数据库实例当前处于 DOWN 状态或不可达'})
    dimensions.append({'name': '运行可用性', 'score': avail_score, 'weight': 0.25})

    # 2. 资源水位与性能 (Performance)
    cpu_usage = float(metrics.get('cpu_usage', metrics.get('cpu_percent', 35)))
    conn_usage = float(metrics.get('conn_usage_pct', metrics.get('threads_connected', 20)))
    perf_score = 100
    if cpu_usage > 80:
        perf_score -= 30
        risk_items.append({'level': 'high', 'title': 'CPU 水位过高', 'desc': f'当前 CPU 使用率达 {cpu_usage}%'})
    elif cpu_usage > 60:
        perf_score -= 10
    if conn_usage > 85:
        perf_score -= 25
        risk_items.append({'level': 'high', 'title': '连接池接近饱和', 'desc': f'当前连接使用率达 {conn_usage}%'})
    perf_score = max(0, perf_score)
    dimensions.append({'name': '性能与负载', 'score': perf_score, 'weight': 0.25})

    # 3. 告警健康度 (Alerts)
    alerts = ctx.get('recent_alerts', [])
    alert_score = max(20, 100 - len(alerts) * 15)
    if alerts:
        risk_items.append({'level': 'medium', 'title': '存在活动告警', 'desc': f'近 24 小时内有 {len(alerts)} 条相关告警上报'})
    dimensions.append({'name': '告警稳定性', 'score': alert_score, 'weight': 0.20})

    # 4. 容量与存储 (Capacity)
    disk_usage = float(metrics.get('disk_usage_pct', metrics.get('tablespace_used_pct', 45)))
    cap_score = 100
    if disk_usage > 90:
        cap_score -= 40
        risk_items.append({'level': 'high', 'title': '磁盘/表空间告急', 'desc': f'存储水位已达到 {disk_usage}%'})
    elif disk_usage > 75:
        cap_score -= 15
    dimensions.append({'name': '容量规划', 'score': cap_score, 'weight': 0.15})

    # 5. 安全与运维规范 (Security & Autonomy)
    sec_score = 95
    if not config.password.startswith('enc:'):
        sec_score -= 20
        risk_items.append({'level': 'medium', 'title': '密码未启用加密存储', 'desc': '当前实例密码仍为明文存储，建议重新保存以启用 AES-256 加密'})
    dimensions.append({'name': '安全与运维', 'score': sec_score, 'weight': 0.15})

    # 综合得分计算
    calculated_score = sum(d['score'] * d['weight'] for d in dimensions)
    overall_score = round(calculated_score)

    grade = 'A' if overall_score >= 90 else ('B' if overall_score >= 75 else ('C' if overall_score >= 60 else 'D'))

    return {
        'database': {
            'id': config.id,
            'name': config.name,
            'db_type': config.db_type,
            'host': config.host,
            'port': config.port,
        },
        'overall_score': overall_score,
        'grade': grade,
        'assessed_at': timezone.now().isoformat(),
        'dimensions': dimensions,
        'risk_items': risk_items,
        'recommendations': [
            '定期分析 Top SQL 执行计划与慢查询日志；',
            '保持告警规则与业务周期同步调整，避免告警疲劳；',
            '配置自动容量预测与自愈审批流以缩短 MTTR。'
        ]
    }
