# -*- coding: utf-8 -*-
"""EXPLAIN ANALYZE：算子层耗时分解（MySQL 8.0.18+ / TXSQL）。见 TDSQL_SLOWSQL_DESIGN.md §5.3。

EXPLAIN ANALYZE **会在目标库真实执行查询**，所以这里的硬约束一条都不能省：

1. 只接受单条 SELECT / WITH...SELECT（``guard_select``），拒绝任何写、锁、INTO、副作用函数
2. 只读事务：``START TRANSACTION READ ONLY`` ... ``ROLLBACK``
   （本地 MySQL 8.0.46 实测：只读事务下 ``EXPLAIN ANALYZE UPDATE`` 报 1792）
3. 双重超时：会话 ``max_execution_time`` + 语句内 ``/*+ MAX_EXECUTION_TIME(n) */``
4. TDSQL：透传到**一个** set 执行（``/*sets:<name>*/``），不在全部 set 上跑

**超时截断**（本地实测发现）：``max_execution_time`` 触发时 EXPLAIN ANALYZE **不报错**，
而是返回一棵被截断的计划 —— 实测根节点 ``rows=0``、``actual time=497ms`` 恰好卡在 500ms
超时上。若不识别，界面会把"跑了 0.5 秒被掐断"展示成"这条 SQL 只要 0.5 秒"。
因此根节点实际耗时达到超时的 95% 即标记 ``truncated``。
"""
import re

_SET_NAME_RE = re.compile(r'^[A-Za-z0-9_\-]{1,64}$')
TRUNCATE_RATIO = 0.95

# 出现在引号 / 反引号之外即拒绝的关键字（按词边界、忽略大小写）
_FORBIDDEN = (
    'INSERT', 'UPDATE', 'DELETE', 'REPLACE', 'MERGE', 'CALL', 'LOAD', 'HANDLER', 'DO',
    'SET', 'LOCK', 'UNLOCK', 'GRANT', 'REVOKE', 'CREATE', 'DROP', 'ALTER', 'TRUNCATE',
    'RENAME', 'INTO', 'SHARE', 'PREPARE', 'EXECUTE', 'DEALLOCATE', 'KILL', 'FLUSH',
    'RESET', 'PURGE', 'INSTALL', 'UNINSTALL', 'SHUTDOWN', 'RESTART', 'START', 'COMMIT',
    'ROLLBACK', 'SAVEPOINT', 'XA', 'BEGIN',
)
# 有副作用或纯粹浪费目标库时间的函数
_FORBIDDEN_FUNCS = ('SLEEP', 'BENCHMARK', 'GET_LOCK', 'RELEASE_LOCK', 'RELEASE_ALL_LOCKS',
                    'LOAD_FILE', 'SOURCE_POS_WAIT', 'MASTER_POS_WAIT', 'WAIT_FOR_EXECUTED_GTID_SET')


def _strip_quoted(sql):
    """把引号 / 反引号内容替换成占位，便于只在"代码部分"检查关键字。"""
    out, i, n = [], 0, len(sql)
    while i < n:
        ch = sql[i]
        if ch in ("'", '"', '`'):
            q = ch
            i += 1
            while i < n:
                if sql[i] == '\\' and q != '`':
                    i += 2
                    continue
                if sql[i] == q:
                    if i + 1 < n and sql[i + 1] == q:   # '' 转义
                        i += 2
                        continue
                    break
                i += 1
            out.append(' _Q_ ')
            i += 1
            continue
        out.append(ch)
        i += 1
    return ''.join(out)


def _strip_comments(sql):
    sql = re.sub(r'/\*.*?\*/', ' ', sql, flags=re.S)
    sql = re.sub(r'(--\s|#)[^\n]*', ' ', sql)
    return sql


def guard_select(sql):
    """返回 (ok, reason, cleaned_sql)。cleaned_sql 已去注释与末尾分号。"""
    if not sql or not sql.strip():
        return False, '没有 SQL 原文', None
    cleaned = _strip_comments(sql).strip().rstrip(';').strip()
    code = _strip_quoted(cleaned)
    if ';' in code:
        return False, '只允许单条语句', None
    if '?' in code:
        return False, ('只有参数化的 digest 文本（含 ?），没有带字面量的样例，无法执行 EXPLAIN ANALYZE；'
                       '请在详情页手工粘贴一条真实 SQL'), None
    m = re.match(r'^\s*\(?\s*(\w+)', code)
    first = (m.group(1).upper() if m else '')
    if first not in ('SELECT', 'WITH'):
        return False, f'只允许 SELECT 查询（当前语句以 {first or "?"} 开头）', None
    if not re.search(r'\bSELECT\b', code, re.I):
        return False, '只允许 SELECT 查询', None
    for kw in _FORBIDDEN:
        if re.search(rf'\b{kw}\b', code, re.I):
            return False, f'语句包含不允许的关键字 {kw}（EXPLAIN ANALYZE 会真实执行语句）', None
    for fn in _FORBIDDEN_FUNCS:
        if re.search(rf'\b{fn}\s*\(', code, re.I):
            return False, f'语句包含不允许的函数 {fn}()', None
    return True, '', cleaned


def with_time_hint(sql, timeout_ms):
    """在顶层 SELECT 后注入 MAX_EXECUTION_TIME 提示。WITH 开头的语句跳过（提示只对顶层 SELECT
    生效，放进 CTE 会被忽略），依赖会话级 max_execution_time。"""
    if re.match(r'^\s*SELECT\b', sql, re.I):
        return re.sub(r'^\s*SELECT\b', f'SELECT /*+ MAX_EXECUTION_TIME({int(timeout_ms)}) */',
                      sql, count=1, flags=re.I)
    return sql


# ---------------------------------------------------------------- 解析
_NUM = r'[\d.]+(?:e[+-]?\d+)?'
_NODE_RE = re.compile(
    r'^(?P<indent>\s*)->\s+(?P<op>.*?)'
    rf'(?:\s+\(cost=(?P<cost>{_NUM})(?:\.\.{_NUM})?\s+rows=(?P<est>{_NUM})\))?'
    rf'(?:\s+\((?:actual time=(?P<first>{_NUM})\.\.(?P<last>{_NUM})\s+rows=(?P<rows>{_NUM})'
    r'\s+loops=(?P<loops>\d+)|(?P<never>never executed))\))?\s*$', re.I)


def _f(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def parse_tree(text):
    """把 EXPLAIN ANALYZE 的 TREE 文本解析为算子树。

    返回 {'root': node, 'nodes': [扁平列表，前序]}。node 字段：
    op / cost / est_rows / actual_rows / loops / first_ms / last_ms / total_ms / self_ms / never / children
    total_ms = last_ms × loops（MySQL 的 actual time 是每次循环的平均值）；
    self_ms = total_ms − 子算子 total_ms 之和（近似，下限 0）。
    """
    if not text:
        return {'root': None, 'nodes': []}
    text = text.replace('\\n', '\n')
    stack, nodes, root = [], [], None
    for line in text.splitlines():
        if '->' not in line:
            continue
        m = _NODE_RE.match(line.rstrip())
        if not m:
            continue
        depth = len(m.group('indent').replace('\t', '    ')) // 4
        loops = int(m.group('loops')) if m.group('loops') else (0 if m.group('never') else None)
        last = _f(m.group('last'))
        node = {
            'op': m.group('op').strip(), 'depth': depth,
            'cost': _f(m.group('cost')), 'est_rows': _f(m.group('est')),
            'actual_rows': _f(m.group('rows')), 'loops': loops,
            'first_ms': _f(m.group('first')), 'last_ms': last,
            'total_ms': (last * loops) if (last is not None and loops) else (0.0 if m.group('never') else None),
            'never': bool(m.group('never')), 'children': [],
        }
        while stack and stack[-1]['depth'] >= depth:
            stack.pop()
        if stack:
            stack[-1]['children'].append(node)
        elif root is None:
            root = node
        stack.append(node)
        nodes.append(node)
    for n in nodes:
        if n['total_ms'] is None:
            continue
        child = sum(c['total_ms'] or 0 for c in n['children'])
        n['self_ms'] = round(max(n['total_ms'] - child, 0.0), 3)
    return {'root': root, 'nodes': nodes}


def summarize(tree, timeout_ms=None, top=5):
    """热点算子 + 截断判定。"""
    root = tree.get('root')
    nodes = tree.get('nodes') or []
    if root is None:
        return {'parsed': False, 'hotspots': [], 'truncated': False, 'total_ms': None}
    total = root.get('total_ms') or 0
    hot = sorted([n for n in nodes if n.get('self_ms')], key=lambda n: n['self_ms'], reverse=True)[:top]
    truncated = bool(timeout_ms and total >= timeout_ms * TRUNCATE_RATIO)
    return {
        'parsed': True,
        'total_ms': round(total, 3),
        'truncated': truncated,
        'truncated_note': ('计划在语句超时处被截断（EXPLAIN ANALYZE 超时不报错而是返回部分结果），'
                           '各算子耗时为下限，真实耗时更长') if truncated else None,
        'hotspots': [{'op': n['op'], 'self_ms': n['self_ms'],
                      'share': round(n['self_ms'] / total, 3) if total else None,
                      'actual_rows': n['actual_rows'], 'est_rows': n['est_rows'],
                      'loops': n['loops'],
                      'row_estimate_off': _estimate_off(n)} for n in hot],
    }


def _estimate_off(n):
    """估算行数与实际行数相差 10 倍以上 → 统计信息可能过期（论文 MMA 的同类规则）。"""
    est, act = n.get('est_rows'), n.get('actual_rows')
    if est is None or act is None or n.get('loops') in (None, 0):
        return None
    hi, lo = max(est, act), max(min(est, act), 1.0)
    return round(hi / lo, 1) if hi / lo >= 10 else None


def _tree_for_json(node):
    if node is None:
        return None
    return {k: v for k, v in node.items() if k != 'children'} | {
        'children': [_tree_for_json(c) for c in node['children']]}


# ---------------------------------------------------------------- 执行
class ExplainRefused(Exception):
    """守卫拒绝执行（不是目标库错误）。"""


def run(config, sql_text, set_name=None, timeout_ms=5000):
    """在目标库执行 EXPLAIN ANALYZE。调用方负责开关与权限校验。

    返回 {'plan_text', 'tree', 'summary', 'set_name', 'timeout_ms', 'sql'}。
    守卫拒绝抛 ExplainRefused；目标库错误原样抛出。
    """
    from monitor.db_connector import DbConnector
    if config.db_type not in ('mysql', 'tdsql', 'gbase'):
        raise ExplainRefused('EXPLAIN ANALYZE 目前只支持 MySQL 系（MySQL / TDSQL）')
    ok, reason, cleaned = guard_select(sql_text)
    if not ok:
        raise ExplainRefused(reason)
    prefix = ''
    if config.db_type == 'tdsql' and set_name:
        if not _SET_NAME_RE.match(set_name):
            raise ExplainRefused('set 名不合法')
        prefix = f'/*sets:{set_name}*/'
    stmt = prefix + 'EXPLAIN ANALYZE ' + with_time_hint(cleaned, timeout_ms)
    conn = DbConnector.get_connection(config, statement_timeout_ms=timeout_ms, readonly=True)
    try:
        cur = conn.cursor()
        try:
            cur.execute(prefix + 'START TRANSACTION READ ONLY')
            try:
                cur.execute(stmt)
                rows = cur.fetchall() or []
            finally:
                try:
                    cur.execute(prefix + 'ROLLBACK')
                except Exception as e:
                    from monitor import degrade
                    degrade.note('sql_explain.rollback', '只读事务回滚失败', e)
        finally:
            cur.close()
    finally:
        DbConnector.close_connection(conn)
    parts = []
    for r in rows:
        if isinstance(r, dict):
            parts.extend(str(v) for k, v in r.items() if str(k).lower() != 'info')
        else:
            parts.append(str(r[0]))
    text = '\n'.join(parts)
    tree = parse_tree(text)
    return {'plan_text': text, 'tree': _tree_for_json(tree['root']),
            'summary': summarize(tree, timeout_ms), 'set_name': set_name or None,
            'timeout_ms': timeout_ms, 'sql': cleaned}
