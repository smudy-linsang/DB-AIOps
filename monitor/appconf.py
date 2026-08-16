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
    # 安全
    'LOGIN_MAX_ATTEMPTS':      Spec('LOGIN_MAX_ATTEMPTS', 5, int, lambda v: v >= 1),
    'LOGIN_FAIL_WINDOW_SEC':   Spec('LOGIN_FAIL_WINDOW_SEC', 600, int, lambda v: v >= 30),
    'LOGIN_LOCKOUT_SEC':       Spec('LOGIN_LOCKOUT_SEC', 900, int, lambda v: v >= 30),
    'LOGIN_MAX_ATTEMPTS_PER_USER': Spec('LOGIN_MAX_ATTEMPTS_PER_USER', 20, int, lambda v: v >= 1),
    'TRUSTED_PROXY_DEPTH':     Spec('TRUSTED_PROXY_DEPTH', 1, int, lambda v: v >= 1),
    'API_KEY_TTL_SEC':         Spec('API_KEY_TTL_SEC', 90 * 86400, int, lambda v: v >= 300),
    # Phase 8 LLM 大模型配置
    'LLM_ENABLED':             Spec('LLM_ENABLED', False, bool),
    'LLM_PROVIDER':            Spec('LLM_PROVIDER', 'openai_compat', str),
    'LLM_BASE_URL':            Spec('LLM_BASE_URL', 'http://localhost:11434/v1', str),
    'LLM_API_KEY':             Spec('LLM_API_KEY', 'ollama', str),
    'LLM_MODEL':               Spec('LLM_MODEL', 'qwen2.5:14b-instruct', str),
    'LLM_TEMPERATURE':         Spec('LLM_TEMPERATURE', 0.1, float, lambda v: 0.0 <= v <= 2.0),
    'LLM_MAX_TOKENS':          Spec('LLM_MAX_TOKENS', 2048, int, lambda v: v >= 64),
    'LLM_TIMEOUT_SEC':         Spec('LLM_TIMEOUT_SEC', 25, int, lambda v: v >= 1),
    'AGENT_ENABLED':           Spec('AGENT_ENABLED', False, bool),
    'EMBED_ENABLED':           Spec('EMBED_ENABLED', False, bool),
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
