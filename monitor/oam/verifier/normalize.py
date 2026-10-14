# -*- coding: utf-8 -*-
"""修复规格归一化：别名、单位、无害重排序（论文 §3.3 / §4.1）。

论文原文口径：*"不可执行案例与事件特定规格在固定归一化规则下比对，
归一化覆盖别名、单位、无害重排序，如 `work_mem` 增大到 128MB ≡ `work_mem=131072kB`"*。

## 三个容易抄错的地方

**① 归一化必须按方言分表。** 设计 §9.2 明确："`work_mem` / `sort_buffer_size`
等价参数跨方言不同名，归一化须分方言"，并警告"不分方言做归一化会把'对的修复'
判成错（也会把错的判成对）"。本模块的别名表以方言为键，且**比较永远发生在
同一个 case 内**（一个 case 只有一个方言），所以不会跨方言误判。

**② 归一化不是语义相似度。** 论文的根因匹配是"轻度归一化后精确字符串相等"。
本模块只做确定性的字面归一化（大小写、空白、标点、单位换算、赋值顺序），
**不做同义推理** —— 否则验证器就变成了一个"看起来聪明"的判定器，
而判定器必须可复现、可审计（设计 §8 第一条验收项）。

**③ 不放宽判据。** 论文的可执行案例判据是"不更慢且结果一致"
（`Lat(m̂;Σ) ≤ t_ref` 且 `Out(m̂) = Out(ref)`），**不是**"快多少"。
本模块不提供任何"性能提升需达 X%"的门槛 —— 设计 §8 专门有一条验收项防这个。
"""
import re

# --------------------------------------------------------------------------
# 单位换算。基准单位取 kB（论文示例：128MB ≡ 131072kB）。
# 只认整数 KB，不认 1.5MB 这类非整值 —— 换算不出整数说明规格本身不精确，
# 此时保留原样让比较判失败，而不是猜一个近似值。
# --------------------------------------------------------------------------
_UNIT_TO_KB = {'b': 1.0 / 1024, 'kb': 1.0, 'k': 1.0, 'mb': 1024.0,
               'm': 1024.0, 'gb': 1024.0 ** 2, 'g': 1024.0 ** 2,
               'tb': 1024.0 ** 3, 't': 1024.0 ** 3}
_SIZE_RE = re.compile(r'^(\d+(?:\.\d+)?)\s*([kmgt]?b?)$', re.IGNORECASE)


def normalize_size(value) -> str | None:
    """把带单位的内存尺寸归一到整数 kB；无法精确换算返回 None。"""
    if value is None:
        return None
    text = str(value).strip().replace(' ', '')
    if not text:
        return None
    m = _SIZE_RE.match(text)
    if not m:
        return None
    num, unit = m.group(1), m.group(2).lower()
    try:
        kb = float(num) * _UNIT_TO_KB.get(unit, 1.0)
    except (TypeError, ValueError):
        return None
    if abs(kb - round(kb)) > 1e-9:
        return None          # 非整 KB：拒绝猜测
    return f'{int(round(kb))}kb'


# --------------------------------------------------------------------------
# 参数别名表：方言 → {该方言下的写法: 规范概念名}
#
# 规范概念名跨方言同名（如 memory_per_sort），但**查找只在给定方言的表里做**，
# 于是 `work_mem=32MB`（pg）与 `sort_buffer_size=32MB`（mysql）会归到同一个
# 概念名 —— 而它们各自只与**同方言**的规格比较，不会互相冒充。
#
# 另一类别名是同方言内的写法差异（缩写、下划线/空格、中文名），由
# _canonical_name 统一处理，不进这张表。
# --------------------------------------------------------------------------
_PARAM_ALIASES = {
    'pgsql': {
        'work_mem': 'memory_per_sort',
        'sort_buffer_size': 'memory_per_sort',   # 同名跨系的兼容写法
        'hash_mem_multiplier': 'memory_per_hash',
        'temp_buffers': 'memory_per_sort',
        'shared_buffers': 'shared_buffer_pool',
        'effective_cache_size': 'effective_cache',
        'max_connections': 'max_connections',
        'statement_timeout': 'statement_timeout',
        'autovacuum': 'autovacuum_enabled',
    },
    'oracle': {
        'sort_area_size': 'memory_per_sort',
        'pga_aggregate_target': 'memory_per_sort',
        'sort_buffer_size': 'memory_per_sort',
        'work_mem': 'memory_per_sort',
        'hash_area_size': 'memory_per_hash',
        'db_cache_size': 'shared_buffer_pool',
        'sga_target': 'shared_buffer_pool',
        'buffer_pool': 'shared_buffer_pool',
        'max_connections': 'max_connections',
        'sessions': 'max_connections',
        'open_cursors': 'max_open_cursors',
        'undo_tablespace': 'undo_tablespace',
    },
    'mysql': {
        'sort_buffer_size': 'memory_per_sort',
        'tmp_table_size': 'memory_per_sort',
        'innodb_buffer_pool_size': 'shared_buffer_pool',
        'buffer_pool_size': 'shared_buffer_pool',
        'join_buffer_size': 'memory_per_hash',
        'max_connections': 'max_connections',
        'innodb_log_file_size': 'redo_log_size',
        'tmpdir': 'temp_space_path',
    },
    'dm': {
        'sort_area_size': 'memory_per_sort',
        'pga_aggregate_target': 'memory_per_sort',
        'sort_buffer_size': 'memory_per_sort',
        'work_mem': 'memory_per_sort',
        'buffer': 'shared_buffer_pool',
        'max_connections': 'max_connections',
        'sessions': 'max_connections',
    },
    'gbase': {
        'sort_buffer_size': 'memory_per_sort',
        'innodb_buffer_pool_size': 'shared_buffer_pool',
        'buffer_pool_size': 'shared_buffer_pool',
        'max_connections': 'max_connections',
    },
    'tdsql': {
        'work_mem': 'memory_per_sort',
        'sort_buffer_size': 'memory_per_sort',
        'hash_mem_multiplier': 'memory_per_hash',
        'shared_buffers': 'shared_buffer_pool',
        'max_connections': 'max_connections',
        'statement_timeout': 'statement_timeout',
    },
}

# 归一化后不参与"无害重排序"的固定前后缀（语句壳子）
_PREFIXES = (
    'alter system set', 'alter session set', 'set session', 'set global',
    'set local', 'alter table', 'create index', 'alter database',
    'sp_', 'pg_settings', 'select ',
)
_NOISE_TOKENS = ('；', ';', '。', '.', '，', ',', '　')


def _canonical_name(name: str, db_type: str) -> str:
    """参数名规范化：去空白/大小写/中文前缀，再过方言别名表。"""
    n = str(name or '').strip().strip('`"[]').lower()
    n = re.sub(r'^(pga|sga|session|global|local)[_.\-]', '', n)
    n = re.sub(r'[\s\-]+', '_', n)
    n = n.strip('_')
    aliases = _PARAM_ALIASES.get(db_type, {})
    return aliases.get(n, n)


_ASSIGN_RE = re.compile(r'([A-Za-z_][A-Za-z0-9_.\-]*)\s*=\s*([^\s,;]+)')


def normalize_fix(fix, db_type: str) -> str:
    """把修复规格归一化为可精确比较的规范串。

    支持三种输入形态：
      · dict  —— {'param': 'work_mem', 'value': '128MB'} 或 {'work_mem': '128MB'}
      · list  —— [('work_mem', '128MB'), ...]
      · str   —— "ALTER SYSTEM SET work_mem=128MB"

    归一化步骤：去语句壳 → 抽取 `名=值` 对 → 参数名走方言别名 → 值做单位换算
    → 排序（消除无害的赋值顺序差异）。
    """
    pairs = _extract_pairs(fix)
    if not pairs:
        # 没有 `名=值` 形态（纯文本建议），退化为字面归一化后原样比较。
        # 不做同义推理 —— 验证器必须可复现。
        return _literal(str(fix or ''))
    norm = []
    for name, value in pairs:
        canon = _canonical_name(name, db_type)
        norm_size = normalize_size(value)
        if norm_size is not None:
            norm.append(f'{canon}={norm_size}')
        else:
            norm.append(f'{canon}={_literal(value)}')
    return ';'.join(sorted(norm))


def _extract_pairs(fix) -> list:
    """把三种输入形态统一抽成 [(参数名, 值)]。"""
    if isinstance(fix, dict):
        if 'param' in fix:
            return [(fix.get('param', ''), fix.get('value', ''))]
        if 'assignments' in fix and isinstance(fix['assignments'], (list, tuple)):
            return [(a, b) for a, b in fix['assignments']]
        return list(fix.items())
    if isinstance(fix, (list, tuple)):
        return [tuple(item)[:2] for item in fix if isinstance(item, (list, tuple))]
    text = _strip_shell(str(fix or ''))
    return [(m.group(1), m.group(2)) for m in _ASSIGN_RE.finditer(text)]


def _strip_shell(text: str) -> str:
    t = _literal(text)
    for token in _NOISE_TOKENS:
        t = t.replace(token, ' ')
    return t


def _literal(text: str) -> str:
    """轻度字面归一化：转小写、压缩空白、去首尾标点。

    对应论文"lowercasing、空白与标点清理"。刻意**不删**中文标点以外的字符，
    也不做同义词替换 —— 那是语义相似度的地盘，不归验证器管。
    """
    t = str(text or '').strip().lower()
    t = re.sub(r'\s+', ' ', t)
    return t.strip(' ,;:.。；，')


def normalize_cause(text: str) -> str:
    """根因标签的轻度归一化（论文 §3.3）。

    只做小写化与空白/标点清理，**不做语义相似度匹配** —— 论文明确根因匹配是
    "轻度归一化后的精确字符串相等"。这正是标签空间必须是枚举的原因。
    """
    return re.sub(r'[\s_\-]+', '_', _literal(text)).strip('_')


__all__ = ['normalize_fix', 'normalize_cause', 'normalize_size',
           'canonical_param', '_PARAM_ALIASES']


def canonical_param(name: str, db_type: str) -> str:
    """对外暴露：取参数在某方言下的规范概念名。"""
    return _canonical_name(name, db_type)
