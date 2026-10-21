# -*- coding: utf-8 -*-
"""运行期配置单一事实源。

三条铁律（都是本轮踩过的坑）：
1. **调用时求值**，不在模块导入时固化 —— 否则配置项写了不生效（BUG-138），
   且 override_settings 无效导致安全逻辑无法被测试覆盖。
2. **默认值只在此处定义一次** —— 散落各处的 getattr 默认值会漂移（BUG-127
   的 ASH_INTERVAL_SEC 一处 5 一处 15，直接导致 AAS 计算偏差 3 倍）。
3. **启动时校验**，非法值立刻失败，不拖到运行期。
"""
from dataclasses import dataclass
from typing import Any, Callable

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


@dataclass(frozen=True)
class Spec:
    name: str
    default: Any
    cast: Callable[[Any], Any]
    validate: Callable[[Any], bool] = lambda v: True
    hint: str = ''


SPECS = {
    # 采集与哨兵
    'ASH_INTERVAL_SEC':        Spec('ASH_INTERVAL_SEC', 5, int, lambda v: 1 <= v <= 300,
                                    '1–300 秒；与 session_ash_1m 的 sample_gap_sec 语义绑定'),
    'SENTINEL_INTERVAL_SEC':   Spec('SENTINEL_INTERVAL_SEC', 8, int, lambda v: 1 <= v <= 300),
    'SENTINEL_FAIL_THRESHOLD': Spec('SENTINEL_FAIL_THRESHOLD', 3, int, lambda v: v >= 1),
    'SENTINEL_CONN_MAX_AGE_SEC': Spec('SENTINEL_CONN_MAX_AGE_SEC', 1800, int, lambda v: v >= 0),
    # 目标库与时序库
    'TARGET_DB_STATEMENT_TIMEOUT_MS': Spec('TARGET_DB_STATEMENT_TIMEOUT_MS', 5000, int,
                                           lambda v: 500 <= v <= 60000),
    'TIMESCALEDB_POOL_MAX':    Spec('TIMESCALEDB_POOL_MAX', 16, int, lambda v: 1 <= v <= 200),
    'TIMESCALEDB_ENABLED':     Spec('TIMESCALEDB_ENABLED', True, bool),
    'USE_REDIS_CACHE':         Spec('USE_REDIS_CACHE', False, bool),
    'ES_ENABLED':              Spec('ES_ENABLED', True, bool),
    # 安全
    'LOGIN_MAX_ATTEMPTS':      Spec('LOGIN_MAX_ATTEMPTS', 5, int, lambda v: v >= 1),
    'LOGIN_FAIL_WINDOW_SEC':   Spec('LOGIN_FAIL_WINDOW_SEC', 600, int, lambda v: v >= 30),
    'LOGIN_LOCKOUT_SEC':       Spec('LOGIN_LOCKOUT_SEC', 900, int, lambda v: v >= 30),
    'LOGIN_MAX_ATTEMPTS_PER_USER': Spec('LOGIN_MAX_ATTEMPTS_PER_USER', 20, int, lambda v: v >= 1),
    'TRUSTED_PROXY_DEPTH':     Spec('TRUSTED_PROXY_DEPTH', 1, int, lambda v: v >= 1),
    'API_KEY_TTL_SEC':         Spec('API_KEY_TTL_SEC', 90 * 86400, int, lambda v: v >= 300),
    'AUTH_ALLOW_COOKIE_TOKEN': Spec('AUTH_ALLOW_COOKIE_TOKEN', False, bool),
    'READINESS_REQUIRE_WORKERS': Spec('READINESS_REQUIRE_WORKERS', False, bool),
    'WEBHOOK_ALLOWED_HOSTS':    Spec('WEBHOOK_ALLOWED_HOSTS',
                                     ('oapi.dingtalk.com', 'qyapi.weixin.qq.com'),
                                     tuple, lambda v: bool(v)),
    'PLAYBOOK_AUTO_LOW_RISK':  Spec('PLAYBOOK_AUTO_LOW_RISK', True, bool),
    'PLAYBOOK_AUTO_CIRCUIT_BREAK': Spec('PLAYBOOK_AUTO_CIRCUIT_BREAK', 3, int,
                                         lambda v: v >= 1),
    'INCIDENT_P1_EXECUTE_FIRST': Spec('INCIDENT_P1_EXECUTE_FIRST', False, bool),
    'PROCESS_LEASE_TTL_SEC':    Spec('PROCESS_LEASE_TTL_SEC', 30, int,
                                     lambda v: 10 <= v <= 300),
    'PROCESS_LEASE_RENEW_SEC':  Spec('PROCESS_LEASE_RENEW_SEC', 10, int,
                                     lambda v: 1 <= v <= 100),
    # Phase 8 LLM 大模型配置
    'LLM_ENABLED':             Spec('LLM_ENABLED', False, bool),
    'LLM_PROVIDER':            Spec('LLM_PROVIDER', 'openai_compat', str),
    'LLM_BASE_URL':            Spec('LLM_BASE_URL', 'http://localhost:11434/v1', str),
    'LLM_API_KEY':             Spec('LLM_API_KEY', 'ollama', str),
    'LLM_MODEL':               Spec('LLM_MODEL', 'qwen2.5:14b-instruct', str),
    'LLM_TEMPERATURE':         Spec('LLM_TEMPERATURE', 0.1, float, lambda v: 0.0 <= v <= 2.0),
    'LLM_MAX_TOKENS':          Spec('LLM_MAX_TOKENS', 2048, int, lambda v: v >= 64),
    'LLM_TIMEOUT_SEC':         Spec('LLM_TIMEOUT_SEC', 25, int, lambda v: v >= 1),
    'LLM_PROXY_URL':           Spec('LLM_PROXY_URL', '', str),
    'LLM_ALLOWED_ENDPOINT_HOSTS': Spec('LLM_ALLOWED_ENDPOINT_HOSTS', (), tuple),
    'AGENT_ENABLED':           Spec('AGENT_ENABLED', False, bool),
    'EMBED_ENABLED':           Spec('EMBED_ENABLED', False, bool),
    # Phase 8C Agent 循环预算
    'AGENT_MAX_STEPS':         Spec('AGENT_MAX_STEPS', 6, int, lambda v: 1 <= v <= 50,
                                    'ReAct 循环步数上限；与 AGENT_BUDGET_SEC 共同约束'),
    'AGENT_BUDGET_SEC':        Spec('AGENT_BUDGET_SEC', 120, int, lambda v: 5 <= v <= 3600,
                                    '单次排查的时间预算（秒）'),
    # ===== V3.0 OAM（见 V3.0_DBAGENT_REDESIGN.md）=====
    # 下面 4 个超参是**论文原文没给值**的（§0.1 五：L_tool / β / G / ALIGN_WINDOW），
    # 设计要求"一律先登记进 SPECS 再给默认值"，故在此定义唯一默认。
    'OAM_ENABLED':             Spec('OAM_ENABLED', False, bool, hint='OAM 总开关；与既有 AGENT_ENABLED 各自独立、并存'),
    'OAM_MAX_TURNS':           Spec('OAM_MAX_TURNS', 12, int, lambda v: 1 <= v <= 50,
                                    'Think-Act-Observe 最大轮数；耗尽则输出升级报告转人工'),
    'OAM_KB_TOP_K':            Spec('OAM_KB_TOP_K', 5, int, lambda v: 1 <= v <= 20,
                                    '知识库检索返回条数；论文口径 k=5'),
    'OAM_REWARD_ALIGN_WINDOW': Spec('OAM_REWARD_ALIGN_WINDOW', 2, int, lambda v: 1 <= v <= 10,
                                    'r_tool 的计划-执行对齐窗口（轮）。论文只说"若干轮窗口"未给值'),
    'OAM_REWARD_OVER_PROBE':   Spec('OAM_REWARD_OVER_PROBE', 4, int, lambda v: 1 <= v <= 50,
                                    '单步奖励的过度探查阈值 L_tool：单轮工具调用数超过它则 r_stepwise 置零'),
    # §9.3：贝叶斯单步奖励带历史频率偏差，小语料上更严重，故对低频根因做加性平滑
    'OAM_PRIOR_SMOOTHING':     Spec('OAM_PRIOR_SMOOTHING', 1.0, float, lambda v: v >= 0,
                                    '根因先验的加性平滑系数；0 表示不平滑（不推荐）'),
    # §7 P3：KB 新鲜度。论文部署失败主因第一位是"知识库过时"（19%），须有机制而非靠人记得
    'OAM_KB_STALENESS_WARN_DAYS': Spec('OAM_KB_STALENESS_WARN_DAYS', 90, int,
                                       lambda v: 1 <= v <= 3650,
                                       'KB 条目超过该天数未人工复核即视为过期，报告须标注'),
    'OAM_RETRIEVAL_MISS_SIM':  Spec('OAM_RETRIEVAL_MISS_SIM', 0.12, float,
                                    lambda v: 0.0 < v < 1.0,
                                    '知识检索最高相似度低于此值视为未命中，写 OamRetrievalMiss 留痕；'
                                    '默认 0.12 是冒烟语料实测标定值（同义查询 0.17~0.42、无关查询 0.00），'
                                    '换语义嵌入后须重标'),
    # §4.1：回放环境边界。长序列会让 MMA 的 F1 崩塌（论文 Table 3a：500 点降到 3.2%）
    'OAM_MAX_SERIES_POINTS':   Spec('OAM_MAX_SERIES_POINTS', 500, int, lambda v: 10 <= v <= 10000,
                                    '单条时序指标在快照中的最大采样点数；超出的分析请求返回确定性错误而非静默截断'),
    'OAM_EVIDENCE_MIN_REFS':   Spec('OAM_EVIDENCE_MIN_REFS', 1, int, lambda v: v >= 0,
                                    '报告根因主张至少需要多少个证据引用才通过证据审计'),
    # ===== 慢 SQL / SQL 统计（见 TDSQL_SLOWSQL_DESIGN.md）=====
    'SQLSTAT_TOPN':            Spec('SQLSTAT_TOPN', 200, int, lambda v: 10 <= v <= 5000,
                                    '每个 set 每轮写入 sql_stat 的 digest 上限（按本轮耗时增量排序）'),
    'SQLSTAT_FETCH_LIMIT':     Spec('SQLSTAT_FETCH_LIMIT', 500, int, lambda v: 10 <= v <= 10000,
                                    '每个 set 每轮从 performance_schema 拉取的活跃 digest 上限'),
    'SQLSTAT_ACTIVE_WINDOW_SEC': Spec('SQLSTAT_ACTIVE_WINDOW_SEC', 900, int,
                                      lambda v: 60 <= v <= 86400,
                                      '只拉取 LAST_SEEN 落在该窗口内的 digest；应 >= 2 倍采集间隔'),
    'SLOW_SQL_AVG_MS':         Spec('SLOW_SQL_AVG_MS', 1000, int, lambda v: 1 <= v <= 3600000,
                                    '平均单次耗时达到该毫秒数即判为慢 SQL（慢 SQL 列表 is_slow 与 D5 告警共用）'),
    'PERF_EXPLAIN_ANALYZE_ENABLED': Spec('PERF_EXPLAIN_ANALYZE_ENABLED', False, bool,
                                         hint='EXPLAIN ANALYZE 会在目标库真实执行查询；默认关闭，按需开启'),
    'PERF_EXPLAIN_ANALYZE_TIMEOUT_MS': Spec('PERF_EXPLAIN_ANALYZE_TIMEOUT_MS', 5000, int,
                                            lambda v: 100 <= v <= 60000,
                                            'EXPLAIN ANALYZE 的语句超时上限；超时时计划会被截断并标注'),
    'SQL_REGRESSION_AVG_RATIO': Spec('SQL_REGRESSION_AVG_RATIO', 1.5, float, lambda v: v > 1.0,
                                     '环比退化判定：平均耗时达到基线的该倍数'),
    'SQL_REGRESSION_MIN_DELTA_MS': Spec('SQL_REGRESSION_MIN_DELTA_MS', 5.0, float, lambda v: v >= 0,
                                        '环比退化判定：平均耗时的最小绝对差值（毫秒），防止亚毫秒抖动误报'),
}


def get(name: str):
    """读取配置（调用时求值）。"""
    spec = SPECS.get(name)
    if spec is None:
        raise KeyError(f'未登记的配置项: {name}（请先在 appconf.SPECS 中声明）')
    raw = getattr(settings, name, spec.default)
    try:
        value = spec.cast(raw)
    except Exception as e:
        raise ImproperlyConfigured(f'{name}={raw!r} 类型非法: {e}') from e
    if not spec.validate(value):
        raise ImproperlyConfigured(
            f'{name}={value!r} 取值非法。{spec.hint or "请查阅 appconf.SPECS"}')
    return value


def validate_all() -> list:
    """启动自检：返回错误消息列表，空列表表示全部合法。"""
    errors = []
    for name in SPECS:
        try:
            get(name)
        except ImproperlyConfigured as e:
            errors.append(str(e))
    return errors
