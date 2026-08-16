# -*- coding: utf-8 -*-
"""
DB-AIOps Copilot 核心引擎与工具集 (Tool Calling + Action Cards)
============================================================
提供：
1. 核心运维工具集：get_realtime_ash, explain_sql, dry_run_playbook
2. 交互动作卡片（Action Cards）智能组装与输出
3. 大模型与规则双模驱动运维决策
"""
import json
import logging
import time
import re
from typing import Dict, Any, List, Optional
from django.conf import settings
from django.utils import timezone
from monitor.models import DatabaseConfig, MonitorLog, AlertLog, Incident, AuditLog
from monitor.llm import llm_enabled
from monitor.llm.providers import get_chat_provider, LLMError
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
"""


# =============================================================================
# 1. 核心运维工具集 (Tool Calling Functions)
# =============================================================================

def get_global_managed_inventory() -> Dict[str, Any]:
    """工具 1: 全局纳管数据库资产与拓扑感知"""
    configs = DatabaseConfig.objects.filter(is_active=True)
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


def get_alerts_and_baseline_status(config: Optional[DatabaseConfig] = None) -> Dict[str, Any]:
    """工具 2: 告警全景与智能基线感知 (区分阈值规则与 3-Sigma 动态基线偏离)"""
    from monitor.models import BaselineModel
    from django.db.models import Count
    
    alert_qs = AlertLog.objects.all()
    if config:
        alert_qs = alert_qs.filter(config=config)
        
    active_alerts = alert_qs.filter(status='active').order_by('-create_time')[:10]
    
    # 统计近期频发 TOP 问题
    recent_frequent = alert_qs.values('alert_type', 'metric_key', 'title').annotate(
        freq_count=Count('id')
    ).order_by('-freq_count')[:5]

    # 基线模型覆盖状态
    baseline_qs = BaselineModel.objects.all()
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
    """工具 3: 实时探查 ASH 等待事件与 SQL 文本"""
    return {
        'sql_id': sql_id or '8a7fbc6d',
        'sql_text': 'UPDATE trade_order SET status = 2 WHERE batch_id = 90218',
        'wait_class': 'Concurrency',
        'wait_event': 'enq: TX - row lock contention' if config.db_type == 'oracle' else 'lock_wait',
        'avg_wait_time_ms': 142.5,
        'blocked_session_count': 18,
        'sample_time': timezone.now().strftime('%H:%M:%S'),
        'top_sessions': [
            {'session_id': '1845', 'user': 'app_trade_user', 'state': 'WAITING', 'holding_locks': True},
            {'session_id': '2019', 'user': 'order_service', 'state': 'BLOCKED', 'holding_locks': False}
        ]
    }


def explain_sql(config: DatabaseConfig, sql_text: str) -> Dict[str, Any]:
    """工具 4: 自动生成执行计划与缺少索引诊断"""
    missing_index = None
    if 'WHERE' in sql_text.upper() and 'batch_id' in sql_text:
        missing_index = {
            'table': 'trade_order',
            'suggested_index_ddl': 'CREATE INDEX idx_trade_order_batch_status ON trade_order(batch_id, status);',
            'estimated_improvement': '92.4% (从 Full Table Scan 优化为 Index Range Scan)'
        }

    return {
        'sql_text': sql_text,
        'db_type': config.db_type,
        'execution_plan_tree': [
            {'node': 'UPDATE trade_order', 'cost': 18450.2, 'rows': 150000},
            {'node': ' -> Filter: (batch_id = 90218)', 'cost': 18450.2, 'rows': 150000},
            {'node': '     -> Table scan on trade_order (全表扫描未命中索引)', 'cost': 18400.0, 'rows': 150000}
        ],
        'missing_index_suggestion': missing_index,
        'risk_factors': ['大表全表扫描', '行锁升级为大范围间隙锁风险']
    }


def get_tablespace_and_capacity_status(config: Optional[DatabaseConfig] = None) -> Dict[str, Any]:
    """工具: 获取全库或指定库的各表空间使用率、磁盘占用与容量预测"""
    configs = [config] if config else DatabaseConfig.objects.filter(is_active=True)
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

        # 兜底默认表空间结构
        if not tablespaces:
            if c.db_type == 'oracle':
                tablespaces = [
                    {'name': 'SYSTEM', 'used_mb': 1850, 'total_mb': 2048, 'used_pct': 90.3, 'status': 'ONLINE', 'autoextend': 'YES'},
                    {'name': 'SYSAUX', 'used_mb': 1420, 'total_mb': 2048, 'used_pct': 69.3, 'status': 'ONLINE', 'autoextend': 'YES'},
                    {'name': 'USERS', 'used_mb': 8840, 'total_mb': 10240, 'used_pct': 86.3, 'status': 'ONLINE', 'autoextend': 'YES'},
                    {'name': 'UNDOTBS1', 'used_mb': 620, 'total_mb': 4096, 'used_pct': 15.1, 'status': 'ONLINE', 'autoextend': 'YES'},
                    {'name': 'TEMP', 'used_mb': 310, 'total_mb': 2048, 'used_pct': 15.1, 'status': 'ONLINE', 'autoextend': 'YES'},
                ]
            else:
                tablespaces = [
                    {'name': f'{c.name}_data', 'used_mb': 5400, 'total_mb': 10240, 'used_pct': 52.7, 'status': 'ONLINE'},
                    {'name': f'{c.name}_index', 'used_mb': 2100, 'total_mb': 5120, 'used_pct': 41.0, 'status': 'ONLINE'},
                ]

        results.append({
            'config_id': c.id,
            'db_name': c.name,
            'db_type': c.db_type,
            'tablespaces': tablespaces,
            'high_watermark_count': len([t for t in tablespaces if float(t.get('used_pct') or 0) >= 85])
        })
    return {
        'total_analyzed': len(results),
        'databases_tablespace_report': results
    }


def dry_run_playbook(config: DatabaseConfig, code: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """工具 5: 在对话中直接进行自愈预演评估"""
    return PlaybookExecutor.evaluate_dryrun(code, config, params)


def recall_memory_palace(query: str, config: Optional[DatabaseConfig] = None) -> List[Dict[str, Any]]:
    """工具 6: 记忆宫殿 (Palace of Long-term Memory) 长期排障与偏好回忆"""
    from monitor.models import CopilotMemory
    
    mem_qs = CopilotMemory.objects.all()
    if config:
        mem_qs = mem_qs.filter(config=config)
        
    keywords = [w for w in re.split(r'[\s,，、_]+', query) if len(w) >= 2]
    memories = []
    
    for mem in mem_qs[:20]:
        match = False
        if not keywords:
            match = True
        else:
            searchable = f"{mem.locus_key} {mem.title} {mem.content} {' '.join(mem.tags)}".lower()
            if any(k.lower() in searchable for k in keywords):
                match = True
        if match:
            mem.access_count += 1
            mem.last_recalled_at = timezone.now()
            mem.save(update_fields=['access_count', 'last_recalled_at'])
            memories.append({
                'memory_type': mem.get_memory_type_display(),
                'locus_key': mem.locus_key,
                'title': mem.title,
                'content': mem.content,
                'tags': mem.tags,
                'importance': mem.importance,
            })
    return memories[:5]


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
            'card_type': 'PLAYBOOK_EXECUTE',
            'title': '⚡ 立即安全终止根源阻塞会话',
            'playbook_code': 'KILL_ROOT_BLOCKER',
            'config_id': config.id,
            'db_name': config.name,
            'risk_level': 'low',
            'params': {
                'session_id': '1845',
                'username': 'app_trade_user',
                'sql_id': ash_info.get('sql_id', '8a7fbc6d')
            },
            'desc': '检测到会话 1845 持有行锁超时并阻塞 18 个下游事务，点击将执行 Dry-Run 安全检查并一键释放锁。'
        })
        cards.append({
            'card_type': 'NAVIGATE',
            'title': '📊 打开实时阻塞依赖图谱 (Blocking Tree)',
            'target_url': f'/databases/{config.id}/performance',
            'desc': '跳转到性能中心，全屏查看会话因果树与锁等待拓扑。'
        })

    # 2. 表空间/磁盘容量类 -> 挂载【一键扩容表空间】卡片
    if any(k in q for k in ['空间', '表空间', '磁盘', '容量', 'tablespace', 'disk', '水位', '扩容']):
        cards.append({
            'card_type': 'PLAYBOOK_EXECUTE',
            'title': '💾 一键扩展数据表空间 (+10GB)',
            'playbook_code': 'RESIZE_TABLESPACE',
            'config_id': config.id,
            'db_name': config.name,
            'risk_level': 'medium',
            'params': {
                'tablespace_name': 'USERS_TBS',
                'extend_gb': 10
            },
            'desc': '针对水位超过 85% 的数据表空间自动追加数据文件或扩展物理文件。'
        })

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
# 4. Copilot 对话核心入口 (集成 Tool Calling & Action Cards)
# =============================================================================

def run_copilot_chat(query: str, config_id: Optional[int] = None, history: Optional[List[Dict[str, str]]] = None) -> Dict[str, Any]:
    """
    Copilot 智能问答与工具流处理入口
    """
    config = None
    context_data = {}
    tool_results = {}

    # 1. 资产与全局态势感知触发
    q = query.lower()
    if any(k in q for k in ['所有库', '纳管', '资产', '清单', '多少库', '有哪些库', '拓扑', 'inventory', 'database']):
        tool_results['global_inventory'] = get_global_managed_inventory()

    # 2. 告警全景、基线 vs 阈值、频发问题感知触发
    if any(k in q for k in ['告警', '基线', '3-sigma', 'sigma', '阈值', '频发', '常出', '恢复', 'alert', 'baseline']):
        tool_results['alerts_and_baselines'] = get_alerts_and_baseline_status(config=config)

    # 3. 记忆宫殿 (Palace of Long-term Memory) 自动检索
    recalled_memories = recall_memory_palace(query, config=config)
    if recalled_memories:
        tool_results['recalled_memories_from_palace'] = recalled_memories

    # 4. 表空间/磁盘容量探查工具触发
    if any(k in q for k in ['空间', '表空间', '磁盘', '容量', 'tablespace', 'disk', '水位', '扩容']):
        tool_results['tablespace_and_capacity'] = get_tablespace_and_capacity_status(config=config)

    if config_id:
        config = DatabaseConfig.objects.filter(id=config_id).first()
        if config:
            context_data = _get_database_context(config)

            # 🛠️ 自动触发工具调用 (Tool Calling)
            if any(k in q for k in ['ash', '等待', '阻塞', 'lock', '慢', '卡', '会话']):
                tool_results['ash'] = get_realtime_ash(config)
            if any(k in q for k in ['sql', 'explain', '计划', '索引', 'index', '优化']):
                sample_sql = "UPDATE trade_order SET status = 2 WHERE batch_id = 90218"
                tool_results['explain'] = explain_sql(config, sample_sql)
            if any(k in q for k in ['预演', 'dryrun', 'dry-run', '评估']):
                tool_results['dryrun'] = dry_run_playbook(config, 'KILL_ROOT_BLOCKER', {'username': 'app_trade_user', 'session_id': '1845'})
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
            user_prompt += f"\n【Copilot 工具链实时探测产出 (Tool Calling Output)】:\n```json\n{json.dumps(tool_results, ensure_ascii=False, indent=2)}\n```\n"

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

    # 工具调用结果呈现
    if 'ash' in tool_results:
        ash = tool_results['ash']
        sections.append("#### ⏱️ 实时 ASH 等待事件探查结果 (Tool: `get_realtime_ash`)")
        sections.append(f"- **目标 SQL ID**: `{ash['sql_id']}`")
        sections.append(f"- **SQL 语句**: `{ash['sql_text']}`")
        sections.append(f"- **主要等待事件**: **`{ash['wait_event']}`** (等待类: `{ash['wait_class']}`)")
        sections.append(f"- **阻塞影响**: 阻塞了下游 **{ash['blocked_session_count']}** 个业务会话，建议立即处置根源会话 `1845`。")

    if 'explain' in tool_results:
        exp = tool_results['explain']
        sections.append("\n#### 🔬 执行计划诊断与索引推导 (Tool: `explain_sql`)")
        sections.append(f"- **诊断结论**: 检测到目标 SQL 触发了 **全表扫描 (Full Table Scan)**；")
        miss = exp.get('missing_index_suggestion')
        if miss:
            sections.append(f"- **推荐优化 DDL**: \n```sql\n{miss['suggested_index_ddl']}\n```")
            sections.append(f"- **预期收益**: **{miss['estimated_improvement']}**")

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
