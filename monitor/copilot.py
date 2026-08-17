# -*- coding: utf-8 -*-
"""
DB-AIOps Copilot 核心引擎与证据路由工具集 (Evidence Router + Action Cards)
============================================================
提供：
1. 核心运维工具集：get_realtime_ash, explain_sql, dry_run_playbook
2. 交互动作卡片（Action Cards）智能组装与输出
3. 大模型与规则双模驱动运维决策
"""
import json
import logging
import re
import time
from datetime import timedelta
from typing import Dict, Any, List, Optional
from django.conf import settings
from django.db.models import F, Q
from django.utils import timezone
from monitor.models import DatabaseConfig, MonitorLog, AlertLog, Incident
from monitor.playbook_engine_v2 import PlaybookExecutor

logger = logging.getLogger("monitor.copilot")


COPILOT_SYSTEM_PROMPT = """你是 DB-AIOps 企业级数据库智能运维平台的专属 Copilot 助手。
精通各类主流数据库（Oracle, MySQL, PostgreSQL, 达梦 DM8, TDSQL, GBase 8a 等）的运维排障、架构设计、性能调优与容量分析。

你拥有以下核心运维工具（平台已自动为你集成调用）：
1. `get_realtime_ash(sql_id)`：实时探查 ASH 等待事件与 SQL 文本；
2. `explain_sql(sql_text)`：自动生成执行计划与缺少索引诊断；
3. `dry_run_playbook(code, params)`：自愈预案影响面安全评估。

你的职责：
1. 解答运维工程师和 DBA 关于数据库状态、性能瓶颈、等待事件、告警排障、SQL 优化的疑问；
2. 结合给定的数据库当前快照信息与工具产出，进行深度根因推导并给出切实可行的处置建议；
3. 输出清晰、专业、排版优良的 Markdown 格式（含表格、代码高亮、重点加粗）；
4. 当发现长事务锁阻塞、表空间告急或卡死慢 SQL 时，平台会自动挂载【交互式动作卡片 (Action Cards)】供 DBA 一键点击执行。

证据约束：工具返回 `available=false` 表示当前没有可核验观测。此时必须明确回答“暂无数据”，
不得猜测、补齐、生成样例对象或把推测表述为真实数据库状态。
"""


# =============================================================================
# 1. 核心运维证据工具集
# =============================================================================

def get_global_managed_inventory(configs=None) -> Dict[str, Any]:
    """工具 1: 调用者数据范围内的纳管数据库资产与拓扑。"""
    configs = configs.filter(is_active=True) if configs is not None else DatabaseConfig.objects.none()
    summary = []
    for c in configs:
        last_log = MonitorLog.objects.filter(config=c).order_by('-create_time').first()
        active_alerts = AlertLog.objects.filter(config=c, status='active').count()
        summary.append({
            'id': c.id,
            'name': c.name,
            'db_type': c.db_type,
            'host': c.host,
            'port': c.port,
            'status': last_log.status if last_log else 'UNKNOWN',
            'active_alerts_count': active_alerts,
            'cpu_cores': c.cpu_cores,
            'autonomy_level': c.autonomy_level,
        })
    return {
        'total_databases_count': len(summary),
        'databases': summary,
        'db_types_distribution': {
            t: len([x for x in summary if x['db_type'] == t])
            for t in set(x['db_type'] for x in summary)
        }
    }


def get_alerts_and_baseline_status(config: Optional[DatabaseConfig] = None, configs=None) -> Dict[str, Any]:
    """工具 2: 告警全景与智能基线感知 (区分阈值规则与 3-Sigma 动态基线偏离)"""
    from monitor.models import BaselineModel
    from django.db.models import Count
    
    if config:
        config_ids = [config.id]
    elif configs is not None:
        config_ids = configs.values_list('id', flat=True)
    else:
        config_ids = []

    alert_qs = AlertLog.objects.filter(config_id__in=config_ids)
    if config:
        alert_qs = alert_qs.filter(config=config)
        
    active_alerts = alert_qs.filter(status='active').order_by('-create_time')[:10]
    
    # 统计近期频发 TOP 问题
    recent_frequent = alert_qs.values('alert_type', 'metric_key', 'title').annotate(
        freq_count=Count('id')
    ).order_by('-freq_count')[:5]

    # 基线模型覆盖状态
    baseline_qs = BaselineModel.objects.filter(config_id__in=config_ids)
    if config:
        baseline_qs = baseline_qs.filter(config=config)
    baseline_count = baseline_qs.count()

    return {
        'active_alerts': [
            {
                'id': a.id,
                'db_name': a.config.name,
                'alert_type': a.alert_type,
                'rule_category': '动态基线偏离 (3-Sigma)' if a.alert_type == 'baseline' else '静态阈值/容量规则',
                'title': a.title,
                'severity': a.severity,
                'metric_key': a.metric_key,
                'created_at': a.create_time.strftime('%Y-%m-%d %H:%M:%S'),
                'is_resolved': a.status == 'resolved',
            }
            for a in active_alerts
        ],
        'frequent_problem_top5': list(recent_frequent),
        'baseline_models_active': baseline_count,
        'baseline_mechanism': '3-Sigma 自适应高斯核密度时段基线 (168个槽位 × 7天周期)',
    }


def get_realtime_ash(config: DatabaseConfig, sql_id: Optional[str] = None) -> Dict[str, Any]:
    """工具 3: 读取最近两分钟的真实 ASH-lite 样本；没有样本即明确降级。"""
    from monitor.timeseries import get_timeseries_storage

    rows = get_timeseries_storage().query_session_samples(
        config.id, timezone.now() - timedelta(seconds=120))
    if sql_id:
        rows = [r for r in rows if str(r.get('sql_id') or r.get('sql_digest') or '') == str(sql_id)]
    if not rows:
        return {
            'available': False,
            'reason': '最近 120 秒暂无 ASH 会话采样',
            'observed_at': None,
            'sql_id': sql_id,
            'sql_text': None,
            'blocked_session_count': 0,
            'top_sessions': [],
        }

    observed_at = max((r.get('time') for r in rows if r.get('time')), default=None)
    latest_rows = [r for r in rows if not observed_at or r.get('time') == observed_at]
    ranked = sorted(
        latest_rows,
        key=lambda r: float(r.get('wait_secs') or r.get('active_secs') or 0),
        reverse=True,
    )
    representative = next((r for r in ranked if r.get('sql_text')), ranked[0])
    wait_values = [float(r.get('wait_secs') or 0) * 1000 for r in latest_rows]
    return {
        'available': True,
        'reason': '',
        'observed_at': observed_at.isoformat() if hasattr(observed_at, 'isoformat') else str(observed_at or ''),
        'sql_id': representative.get('sql_id') or representative.get('sql_digest'),
        'sql_text': representative.get('sql_text'),
        'wait_class': representative.get('wait_class'),
        'wait_event': representative.get('wait_event'),
        'avg_wait_time_ms': round(sum(wait_values) / len(wait_values), 2) if wait_values else None,
        'blocked_session_count': sum(1 for r in latest_rows if r.get('is_blocked')),
        'top_sessions': [
            {
                'session_id': r.get('session_id'),
                'user': r.get('user_name'),
                'state': r.get('state'),
                'is_blocked': bool(r.get('is_blocked')),
                'blocker_id': r.get('blocker_id'),
                'wait_event': r.get('wait_event'),
            }
            for r in ranked[:5]
        ],
    }


def explain_sql(config: DatabaseConfig, sql_text: str) -> Dict[str, Any]:
    """工具 4: 只展示平台已经真实采集的执行计划。"""
    from monitor.models import SqlPlan
    from monitor.sqlfingerprint import unified_digest

    digest = unified_digest(config.db_type, None, sql_text)
    plan = SqlPlan.objects.filter(
        config=config, sql_digest=digest, is_current=True).order_by('-captured_at').first()
    if not plan:
        return {
            'available': False,
            'reason': '该 SQL 暂无已采集执行计划；请先在性能中心执行受控 EXPLAIN',
            'sql_text': sql_text,
            'sql_digest': digest,
            'execution_plan_tree': [],
            'missing_index_suggestion': None,
        }

    suggestions = []
    try:
        import dataclasses
        from monitor.index_advisor import IndexAdvisor
        from monitor.sqlfingerprint import normalize
        normalized = normalize(sql_text).replace('`', '').replace('[', '').replace(']', '')
        for candidate in (IndexAdvisor().analyze_queries(
                [{'query': normalized, 'exec_count': 1}]) or [])[:3]:
            suggestions.append(
                dataclasses.asdict(candidate) if dataclasses.is_dataclass(candidate) else str(candidate))
    except Exception as exc:
        logger.debug("Copilot 索引建议生成失败: %s", exc)
    return {
        'available': True,
        'reason': '',
        'sql_text': sql_text,
        'sql_digest': digest,
        'db_type': config.db_type,
        'plan_hash': plan.plan_hash,
        'execution_plan_tree': plan.plan_json,
        'plan_text': plan.plan_text,
        'cost_total': plan.cost_total,
        'captured_at': plan.captured_at.isoformat(),
        'source': plan.source,
        'index_suggestions': suggestions,
        'missing_index_suggestion': suggestions[0] if suggestions else None,
    }


def get_tablespace_and_capacity_status(config: Optional[DatabaseConfig] = None, configs=None) -> Dict[str, Any]:
    """工具: 获取全库或指定库的各表空间使用率、磁盘占用与容量预测"""
    configs = [config] if config else (configs.filter(is_active=True) if configs is not None else [])
    results = []
    for c in configs:
        last_log = MonitorLog.objects.filter(config=c).order_by('-create_time').first()
        tablespaces = []
        if last_log:
            try:
                msg = json.loads(last_log.message) if isinstance(last_log.message, str) else last_log.message
                if isinstance(msg, dict):
                    ts = msg.get('tablespaces') or msg.get('tablespace_usage') or []
                    if isinstance(ts, list):
                        tablespaces = ts
                    elif isinstance(ts, dict):
                        tablespaces = [{'name': k, 'used_pct': v} for k, v in ts.items()]
            except Exception as e:
                logger.debug("解析表空间非致命异常: %s", e)

        results.append({
            'config_id': c.id,
            'db_name': c.name,
            'db_type': c.db_type,
            'available': bool(tablespaces),
            'reason': '' if tablespaces else '最新监控快照中没有表空间采集数据',
            'observed_at': last_log.create_time.isoformat() if last_log and tablespaces else None,
            'tablespaces': tablespaces,
            'high_watermark_count': len([t for t in tablespaces if float(t.get('used_pct') or 0) >= 85])
        })
    return {
        'available': any(r['available'] for r in results),
        'total_analyzed': len(results),
        'databases_tablespace_report': results
    }


def dry_run_playbook(config: DatabaseConfig, code: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """工具 5: 在对话中直接进行自愈预演评估"""
    return PlaybookExecutor.evaluate_dryrun(code, config, params)


def recall_memory_palace(query: str, config: Optional[DatabaseConfig] = None,
                         configs=None, include_global: bool = False) -> List[Dict[str, Any]]:
    """工具 6: 记忆宫殿 (Palace of Long-term Memory) 长期排障与偏好回忆"""
    from monitor.models import CopilotMemory
    
    mem_qs = CopilotMemory.objects.all()
    if config:
        mem_qs = mem_qs.filter(config=config)
    elif configs is None:
        mem_qs = mem_qs.none()
    elif not include_global:
        mem_qs = mem_qs.filter(config_id__in=configs.values_list('id', flat=True))
        
    keywords = [w for w in re.split(r'[\s,，、_]+', query) if len(w) >= 2]
    # 中文通常没有空格；补充 2-4 字滑窗，使“回忆容量经验”能在 DB 层匹配“容量”。
    chinese_runs = re.findall(r'[\u4e00-\u9fff]{2,}', query)
    for run in chinese_runs:
        for width in range(2, min(4, len(run)) + 1):
            keywords.extend(run[index:index + width]
                            for index in range(0, len(run) - width + 1))
    keywords = list(dict.fromkeys(keywords))[:32]
    if keywords:
        predicate = Q()
        for keyword in keywords:
            predicate |= Q(locus_key__icontains=keyword)
            predicate |= Q(title__icontains=keyword)
            predicate |= Q(content__icontains=keyword)
        mem_qs = mem_qs.filter(predicate)

    matched = list(mem_qs.order_by('-importance', '-updated_at')[:5])
    if matched:
        CopilotMemory.objects.filter(id__in=[m.id for m in matched]).update(
            access_count=F('access_count') + 1,
            last_recalled_at=timezone.now(),
        )
    return [{
        'memory_type': mem.get_memory_type_display(),
        'locus_key': mem.locus_key,
        'title': mem.title,
        'content': mem.content,
        'tags': mem.tags,
        'importance': mem.importance,
    } for mem in matched]


# =============================================================================
# 2. 数据库实时上下文与时序指标聚合
# =============================================================================

def _get_database_context(config: DatabaseConfig) -> Dict[str, Any]:
    """汇总指定数据库的当前关键上下文快照与历史时序"""
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

    # 最新指标与表空间/容量快照
    latest_log = MonitorLog.objects.filter(config=config).order_by('-create_time').first()
    if latest_log:
        try:
            msg = json.loads(latest_log.message) if isinstance(latest_log.message, str) else latest_log.message
            filtered = {}
            for k, v in (msg or {}).items():
                if isinstance(v, (int, float, str)) and not isinstance(v, bool):
                    filtered[k] = v
            ctx['latest_metrics'] = filtered
            
            # 完整透传表空间/磁盘/容量实时状态数据
            if isinstance(msg, dict):
                if 'tablespaces' in msg:
                    ctx['tablespaces'] = msg['tablespaces']
                if 'tablespace_usage' in msg:
                    ctx['tablespace_usage'] = msg['tablespace_usage']
                if 'database_sizes' in msg:
                    ctx['database_sizes'] = msg['database_sizes']
                if 'space_usage_pct' in msg:
                    ctx['space_usage_pct'] = msg['space_usage_pct']

            ctx['status'] = latest_log.status
            ctx['last_check_time'] = latest_log.create_time.isoformat()
        except Exception:
            ctx['latest_metrics'] = {}
    else:
        ctx['latest_metrics'] = {}

    # 历史时序采样（最近 5 个点）
    past_logs = MonitorLog.objects.filter(config=config).order_by('-create_time')[1:6]
    ctx['historical_metric_trend'] = []
    for pl in past_logs:
        try:
            pmsg = json.loads(pl.message) if isinstance(pl.message, str) else pl.message
            ctx['historical_metric_trend'].append({
                'time': pl.create_time.strftime('%H:%M:%S'),
                'cpu': pmsg.get('cpu_usage') or pmsg.get('cpu_used_pct'),
                'conn': pmsg.get('active_connections') or pmsg.get('conn_usage_pct'),
                'tps': pmsg.get('tps') or pmsg.get('qps'),
            })
        except Exception as e:
            logger.debug("解析历史时序非致命异常: %s", e)

    # 最近未恢复/活动告警
    recent_alerts = AlertLog.objects.filter(config=config).order_by('-create_time')[:5]
    ctx['recent_alerts'] = [
        {
            'title': a.title,
            'severity': a.severity,
            'alert_type': a.alert_type,
            'status': a.status,
            'metric': getattr(a, 'metric_key', ''),
            'time': a.create_time.isoformat()
        }
        for a in recent_alerts
    ]

    # 最近事故与自愈工单
    recent_incidents = Incident.objects.filter(config=config).order_by('-created_at')[:3]
    ctx['recent_incidents'] = [
        {
            'incident_id': inc.incident_id,
            'title': inc.title,
            'status': inc.status,
            'severity': getattr(inc, 'priority', 'P1'),
            'root_cause': (inc.rca_result or {}).get('root_causes', [])
        }
        for inc in recent_incidents
    ]

    return ctx


# =============================================================================
# 3. 交互式动作卡片 (Action Cards) 组装
# =============================================================================

def _build_action_cards(query: str, config: Optional[DatabaseConfig], tool_results: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    根据用户提问、数据库状态及工具执行结果，智能挂载一键交互动作卡片
    """
    if not config:
        return []

    cards = []
    q = query.lower()

    # 1. 行锁/阻塞/卡顿 -> 挂载【安全终止根源阻塞会话】卡片
    if any(k in q for k in ['锁', '阻塞', 'lock', 'block', '卡死', 'kill', '会话', 'session']):
        ash_info = tool_results.get('ash') or {}
        cards.append({
            'card_type': 'NAVIGATE',
            'title': '📊 打开实时阻塞依赖图谱 (Blocking Tree)',
            'target_url': f'/databases/{config.id}/performance',
            'desc': ('跳转到性能中心，全屏查看会话因果树与锁等待拓扑。'
                     if ash_info.get('available') else '当前暂无 ASH 样本，可在性能中心核对采集状态。')
        })

    # 2. 表空间/磁盘容量类 -> 挂载【一键扩容表空间】卡片
    # 扩容和终止会话都必须关联事故证据与审批运行单；聊天层不生成无证据执行按钮。

    # 3. SQL 慢查/索引类 -> 挂载【执行计划/索引推荐】卡片
    if any(k in q for k in ['sql', '索引', 'index', 'explain', '慢查', '优化']):
        explain_info = tool_results.get('explain') or {}
        miss_idx = explain_info.get('missing_index_suggestion')
        if miss_idx:
            cards.append({
                'card_type': 'SQL_SUGGESTION',
                'title': '🔍 一键复制索引优化 DDL',
                'ddl': miss_idx.get('suggested_index_ddl'),
                'improvement': miss_idx.get('estimated_improvement'),
                'desc': '系统评估添加此复合索引可使全表扫描降低 90%+ 开销。'
            })

    return cards


# =============================================================================
# 4. Copilot 对话核心入口（关键词证据路由与 Action Cards）
# =============================================================================

def _extract_sql(query: str) -> Optional[str]:
    fenced = re.search(r'```(?:sql)?\s*(.*?)```', query, re.I | re.S)
    candidate = fenced.group(1).strip() if fenced else query.strip()
    match = re.search(r'\b(select|with|update|delete|insert)\b.+', candidate, re.I | re.S)
    return match.group(0).strip() if match else None


def run_copilot_chat(query: str, config_id: Optional[int] = None,
                     history: Optional[List[Dict[str, str]]] = None, *, user=None) -> Dict[str, Any]:
    """
    Copilot 智能问答与工具流处理入口
    """
    if user is not None:
        visible_configs = DatabaseConfig.objects.visible_to(user)
        from monitor.auth import get_user_database_ids
        include_global_memories = get_user_database_ids(user) is None
    elif config_id is not None:
        # 兼容可信内部调用：只允许显式目标实例，绝不退化为全局扫描。
        visible_configs = DatabaseConfig.objects.filter(id=config_id)
        include_global_memories = False
    else:
        visible_configs = DatabaseConfig.objects.none()
        include_global_memories = False

    config = visible_configs.filter(id=config_id).first() if config_id else None
    context_data = {}
    tool_results = {}
    if config:
        context_data = _get_database_context(config)

    # 1. 资产与全局态势感知触发
    q = query.lower()
    if any(k in q for k in ['所有库', '纳管', '资产', '清单', '多少库', '有哪些库', '拓扑', 'inventory', 'database']):
        tool_results['global_inventory'] = get_global_managed_inventory(visible_configs)

    # 2. 告警全景、基线 vs 阈值、频发问题感知触发
    if any(k in q for k in ['告警', '基线', '3-sigma', 'sigma', '阈值', '频发', '常出', '恢复', 'alert', 'baseline']):
        tool_results['alerts_and_baselines'] = get_alerts_and_baseline_status(
            config=config, configs=visible_configs)

    # 3. 记忆宫殿 (Palace of Long-term Memory) 自动检索
    recalled_memories = recall_memory_palace(
        query, config=config, configs=visible_configs,
        include_global=include_global_memories)
    if recalled_memories:
        tool_results['recalled_memories_from_palace'] = recalled_memories

    # 4. 表空间/磁盘容量探查工具触发
    if any(k in q for k in ['空间', '表空间', '磁盘', '容量', 'tablespace', 'disk', '水位', '扩容']):
        tool_results['tablespace_and_capacity'] = get_tablespace_and_capacity_status(
            config=config, configs=visible_configs)

    if config:
            # 关键词证据路由：工具由服务端按意图触发，结果均带 available 证据状态。
            if any(k in q for k in ['ash', '等待', '阻塞', 'lock', '慢', '卡', '会话']):
                tool_results['ash'] = get_realtime_ash(config)
            if any(k in q for k in ['sql', 'explain', '计划', '索引', 'index', '优化']):
                sql_text = _extract_sql(query)
                tool_results['explain'] = (explain_sql(config, sql_text) if sql_text else {
                    'available': False,
                    'reason': '提问中没有可识别的 SQL 原文，无法匹配已采集执行计划',
                    'execution_plan_tree': [],
                    'missing_index_suggestion': None,
                })
            if any(k in q for k in ['预演', 'dryrun', 'dry-run', '评估']):
                tool_results['dryrun'] = {
                    'available': False,
                    'reason': 'Playbook 预演必须从已授权事故进入，以绑定服务端证据与审批链',
                }
            if 'tablespace_and_capacity' not in tool_results and any(k in q for k in ['空间', '表空间', '磁盘', '容量', 'tablespace', 'disk']):
                tool_results['tablespace_and_capacity'] = get_tablespace_and_capacity_status(config=config)

    # 智能构建交互动作卡片
    action_cards = _build_action_cards(query, config, tool_results)

    # 尝试多大模型路由器智能调度生成 (LLMRouterEngine)
    try:
        from monitor.llm.router import LLMRouterEngine
        messages = [{'role': 'system', 'content': COPILOT_SYSTEM_PROMPT}]

        # 注入历史记录（最近 6 轮）
        if history:
            for h in history[-6:]:
                if h.get('role') in ('user', 'assistant') and h.get('content'):
                    messages.append({'role': h['role'], 'content': h['content']})

        # 当前用户问题与工具产出上下文
        user_prompt = f"用户提问：{query}\n"
        if context_data:
            user_prompt += f"\n【当前目标数据库实时上下文与时序】:\n```json\n{json.dumps(context_data, ensure_ascii=False, indent=2)}\n```\n"
        if tool_results:
            user_prompt += f"\n【Copilot 证据路由观测结果】:\n```json\n{json.dumps(tool_results, ensure_ascii=False, indent=2)}\n```\n"

        messages.append({'role': 'user', 'content': user_prompt})

        # 智能匹配 copilot_chat 场景并执行多模型自动降级调用 (单凭据 15s 超时迅速故障转移)
        router_res = LLMRouterEngine.chat(messages, scene='copilot_chat', timeout=15)
        return {
            'answer': router_res['content'],
            'model': router_res['model'],
            'provider_name': router_res.get('provider_name', ''),
            'source': 'llm_router',
            'latency_ms': router_res['latency_ms'],
            'failover_traces': router_res.get('failover_traces', []),
            'context_used': bool(context_data),
            'tool_results': tool_results,
            'action_cards': action_cards,
        }
    except Exception as e:
        logger.warning("Copilot 多大模型路由调用异常或未配置，平滑降级至 DBA 专家规则引擎: %s", e)

    # 离线/降级模式：基于专家引擎与工具产出生成结构化回复
    fallback_res = _fallback_copilot_response(query, config, context_data, tool_results)
    fallback_res['action_cards'] = action_cards
    return fallback_res


def _fallback_copilot_response(query: str, config: Optional[DatabaseConfig], context_data: Dict[str, Any], tool_results: Dict[str, Any]) -> Dict[str, Any]:
    """离线/降级模式下的结构化回复引擎（带工具集调用结果）"""
    q_lower = query.lower()
    sections = []

    target_desc = f"数据库 **{config.name}** ({config.db_type})" if config else "当前未关联特定数据库"
    sections.append(f"### 🤖 DB-AIOps 智能运维助手 (专家工具链驱动模式)\n\n> 🎯 **目标对象**: {target_desc}\n")

    # 证据工具结果呈现
    if 'ash' in tool_results:
        ash = tool_results['ash']
        sections.append("#### ⏱️ 实时 ASH 等待事件探查结果 (Tool: `get_realtime_ash`)")
        if not ash.get('available'):
            sections.append(f"- **暂无可核验数据**：{ash.get('reason', 'ASH 样本不可用')}")
        else:
            sections.append(f"- **目标 SQL ID**: `{ash.get('sql_id') or '—'}`")
            sections.append(f"- **SQL 语句**: `{ash.get('sql_text') or '未采集原文'}`")
            sections.append(f"- **主要等待事件**: **`{ash.get('wait_event') or '—'}`** (等待类: `{ash.get('wait_class') or '—'}`)")
            sections.append(f"- **阻塞影响**: 最新样本中有 **{ash.get('blocked_session_count', 0)}** 个被阻塞会话；处置前须在事故证据链复核根阻塞者。")

    if 'explain' in tool_results:
        exp = tool_results['explain']
        sections.append("\n#### 🔬 执行计划诊断与索引推导 (Tool: `explain_sql`)")
        if not exp.get('available'):
            sections.append(f"- **暂无可核验数据**：{exp.get('reason', '执行计划不可用')}")
        miss = exp.get('missing_index_suggestion')
        if miss:
            ddl = miss.get('create_sql') or miss.get('suggested_index_ddl')
            if ddl:
                sections.append(f"- **规则式候选 DDL（须人工复核）**: \n```sql\n{ddl}\n```")

    if 'global_inventory' in tool_results:
        inv = tool_results['global_inventory']
        sections.append("#### 🌐 平台纳管数据库资产总览 (Tool: `get_global_managed_inventory`)")
        sections.append(f"- **总纳管实例数**: **{inv['total_databases_count']}** 个")
        sections.append("- **数据库分布**: " + "、".join([f"{k.upper()}: {v}个" for k, v in inv.get('db_types_distribution', {}).items()]))
        sections.append("\n| ID | 实例名称 | 类型 | 主机端口 | 状态 | 活跃告警 | 自动驾驶级别 |\n| :--- | :--- | :--- | :--- | :--- | :--- | :--- |")
        for d in inv.get('databases', []):
            sections.append(f"| {d['id']} | **{d['name']}** | `{d['db_type']}` | {d['host']}:{d['port']} | {d['status']} | {d['active_alerts_count']} | L{d['autonomy_level']} |")

    if 'tablespace_and_capacity' in tool_results:
        ts_data = tool_results['tablespace_and_capacity']
        sections.append("\n#### 💾 表空间与存储容量探测 (Tool: `get_tablespace_and_capacity_status`)")
        for db_rep in ts_data.get('databases_tablespace_report', []):
            if not db_rep.get('available'):
                sections.append(f"**实例 [{db_rep['db_name']}]**：暂无可核验数据（{db_rep.get('reason')}）。")
                continue
            sections.append(f"**实例 [{db_rep['db_name']}] 表空间明细** (高水位表空间数: `{db_rep['high_watermark_count']}`):")
            sections.append("| 表空间名称 | 已用容量 | 总容量 | 使用率 | 状态 | 自动扩展 |\n| :--- | :--- | :--- | :--- | :--- | :--- |")
            for t in db_rep.get('tablespaces', []):
                used_m = f"{t.get('used_mb', 0)}MB" if 'used_mb' in t else '—'
                tot_m = f"{t.get('total_mb', 0)}MB" if 'total_mb' in t else '—'
                pct = f"**{t.get('used_pct')}%**" if float(t.get('used_pct', 0)) >= 85 else f"{t.get('used_pct')}%"
                sections.append(f"| `{t.get('name')}` | {used_m} | {tot_m} | {pct} | {t.get('status', 'ONLINE')} | {t.get('autoextend', 'YES')} |")

    if 'alerts_and_baselines' in tool_results:
        ab = tool_results['alerts_and_baselines']
        sections.append("\n#### 🔔 告警全景与 3-Sigma 动态基线感知 (Tool: `get_alerts_and_baseline_status`)")
        sections.append(f"- **动态基线机制**: `{ab.get('baseline_mechanism')}`")
        sections.append(f"- **已训练活跃基线模型数**: **{ab.get('baseline_models_active')}** 个")
        if ab.get('active_alerts'):
            sections.append("\n**当前未恢复告警清单 (区分规则类型):**")
            sections.append("| 告警标题 | 实例 | 指标 Key | 告警机制 | 级别 | 触发时间 |\n| :--- | :--- | :--- | :--- | :--- | :--- |")
            for a in ab['active_alerts']:
                sections.append(f"| {a['title']} | **{a['db_name']}** | `{a['metric_key']}` | `{a['rule_category']}` | `{a['severity']}` | {a['created_at']} |")

    if 'recalled_memories_from_palace' in tool_results:
        mems = tool_results['recalled_memories_from_palace']
        sections.append("\n#### 🏛️ 记忆宫殿唤醒历史排障与偏好 (Memory Palace Recall)")
        for m in mems:
            sections.append(f"- **[{m['memory_type']}] {m['title']}** (宫殿坐标: `{m['locus_key']}`)\n  > {m['content']}")

    # 指标查询类
    if any(k in q_lower for k in ['状态', '指标', 'cpu', '内存', '连接', 'status', 'metric', 'qps']):
        metrics = context_data.get('latest_metrics', {})
        if metrics:
            sections.append("\n#### 📊 最新实时指标快照")
            rows = []
            for k, v in list(metrics.items())[:8]:
                rows.append(f"| `{k}` | **{v}** |")
            sections.append("| 指标项 | 当前数值 |\n| :--- | :--- |\n" + "\n".join(rows))

    # 告警与建议
    if any(k in q_lower for k in ['告警', '报错', '故障', 'alert', 'error']):
        alerts = context_data.get('recent_alerts', [])
        if alerts and 'alerts_and_baselines' not in tool_results:
            sections.append("\n#### 🚨 关联活动告警分析")
            for a in alerts:
                sections.append(f"- **[{a['severity'].upper()}]** {a['title']} (`{a['metric']}`) - *{a['time']}*")

    if len(sections) == 1:
        sections.append(f"""针对您的提问 **"{query}"**，Copilot 运维工具链已准备就绪：
- 🛠️ **实时探查**：输入 *“分析当前锁等待”* 触发 ASH 会话与阻塞下钻；
- 🔍 **SQL 优化**：输入 *“优化这条 SQL”* 自动生成执行计划与缺少索引推荐；
- ⚡ **自愈卡片**：在下方直接点击挂载的操作卡片即可一键发起 Dry-Run 预演与自愈执行。""")

    return {
        'answer': "\n".join(sections),
        'source': 'rule_fallback',
        'context_used': bool(context_data),
        'tool_results': tool_results,
    }


def generate_quick_health_assessment(config_id: int) -> Dict[str, Any]:
    """生成 5 维度一键智能体检评分"""
    config = DatabaseConfig.objects.filter(id=config_id).first()
    if not config:
        return {'error': 'Database config not found'}

    latest_log = MonitorLog.objects.filter(config=config).order_by('-create_time').first()
    alerts_count = AlertLog.objects.filter(config=config, status='active').count()

    metrics = {}
    if latest_log:
        try:
            metrics = json.loads(latest_log.message) if isinstance(latest_log.message, str) else latest_log.message
        except Exception:
            metrics = {}

    def _number(*keys):
        for key in keys:
            value = metrics.get(key)
            if value is not None and value != '':
                try:
                    return float(value)
                except (TypeError, ValueError):
                    continue
        return None

    # 只使用有口径的真实指标。绝对连接数必须除以上限后才能作为百分比。
    cpu_val = _number('cpu_usage', 'cpu_used_pct')
    conn_val = _number('conn_usage_pct')
    if conn_val is None:
        active = _number('threads_connected', 'active_connections', 'active_sessions')
        maximum = _number('max_connections', 'max_processes')
        if active is not None and maximum and maximum > 0:
            conn_val = active / maximum * 100
    disk_val = _number('disk_usage_pct', 'tablespace_used_pct')

    perf_score = None if cpu_val is None else max(0, int(100 - (cpu_val * 0.6)))
    conn_score = None if conn_val is None else max(0, int(100 - (conn_val * 0.5)))
    storage_score = None if disk_val is None else max(0, int(100 - (disk_val * 0.7)))
    alert_score = max(20, 100 - (alerts_count * 15))
    avail_score = None if latest_log is None else (100 if latest_log.status == 'UP' else 0)

    weighted = [(perf_score, .25), (conn_score, .20), (storage_score, .20),
                (alert_score, .20), (avail_score, .15)]
    known_weight = sum(weight for score, weight in weighted if score is not None)
    overall = (int(sum(score * weight for score, weight in weighted
                       if score is not None) / known_weight)
               if latest_log is not None and known_weight else None)
    grade = ('N/A' if overall is None else
             ('A' if overall >= 90 else ('B' if overall >= 80 else
              ('C' if overall >= 65 else 'D'))))
    risk_items = []
    if conn_val is not None and conn_val >= 85:
        risk_items.append({'title': '连接池接近饱和', 'value': round(conn_val, 2)})
    if disk_val is not None and disk_val >= 85:
        risk_items.append({'title': '容量水位较高', 'value': round(disk_val, 2)})

    return {
        'config_id': config.id,
        'db_name': config.name,
        'db_type': config.db_type,
        'overall_score': overall,
        'grade': grade,
        'dimensions': [
            {'name': '高可用性', 'score': avail_score, 'full': 100},
            {'name': '性能与负载', 'score': perf_score, 'full': 100},
            {'name': '连接健康', 'score': conn_score, 'full': 100},
            {'name': '容量规划', 'score': storage_score, 'full': 100},
            {'name': '告警压力', 'score': alert_score, 'full': 100},
        ],
        'risk_items': risk_items,
        'data_completeness': round(known_weight, 2),
        'degraded': latest_log is None or any(
            value is None for value in (cpu_val, conn_val, disk_val)),
        'summary': (f"综合体检评分为 {overall} 分 ({grade} 级)。当前活跃告警 {alerts_count} 条。"
                    if overall is not None else
                    f"暂无有效采集快照，不能生成健康分。当前活跃告警 {alerts_count} 条。"),
        'evaluated_at': timezone.now().isoformat()
    }
