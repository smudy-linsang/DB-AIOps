# -*- coding: utf-8 -*-
"""SQL 优化建议：通用索引 / 改写规则 + TDSQL 分布式规则。纯函数，无 I/O。

见 TDSQL_SLOWSQL_DESIGN.md §6。修 TDSQL_SLOWSQL_REVIEW.md 的：
- T-05：原 IndexAdvisor 用正则抓列名，不认表别名 —— 把 ``users.city`` 建到 ``orders`` 上（DDL 必失败）；
  对 ``LIKE '%x%'`` 推 B-tree（无效）；函数包列不提示改写；同时给出冗余的单列与复合建议。
- T-06：完全没有分片键相关的判断，而这是 TDSQL 分布式实例最常见、代价最大的问题。

TDSQL 规则依据腾讯云官方文档：不带 shardkey 的 SQL 路由到所有节点；分表之间推荐用分表键相等做 JOIN；
单表默认存放在第一个 set；广播表（shardkey=noshardkey_allset）每个 set 存全量。

解析器只分析**最外层**语句（子查询只做标注不深入），宁可少说也不说错：
列归属不确定时不出索引建议。
"""
import re

# ------------------------------------------------------------------ 分词

_TOKEN_RE = re.compile(r"""
    (?P<ws>\s+)
  | (?P<bq>`(?:[^`]|``)*`)
  | (?P<str>'(?:[^'\\]|\\.|'')*'|"(?:[^"\\]|\\.|"")*")
  | (?P<num>\d+(?:\.\d+)?(?:e[+-]?\d+)?)
  | (?P<ph>\?)
  | (?P<op><=>|<=|>=|<>|!=|=|<|>)
  | (?P<punct>[(),.;*])
  | (?P<word>[A-Za-z_$@][\w$@]*)
  | (?P<other>.)
""", re.X | re.S)

_CLAUSE_KW = ('FROM', 'WHERE', 'GROUP', 'HAVING', 'ORDER', 'LIMIT', 'UNION', 'WINDOW', 'FOR', 'INTO')
_JOIN_KW = ('JOIN', 'INNER', 'LEFT', 'RIGHT', 'CROSS', 'STRAIGHT_JOIN', 'NATURAL', 'OUTER', 'FULL')


class Tok:
    __slots__ = ('kind', 'val', 'up')

    def __init__(self, kind, val):
        self.kind = kind
        self.val = val
        self.up = val.upper() if kind == 'word' else val

    def __repr__(self):
        return f'{self.kind}:{self.val}'


def tokenize(sql):
    sql = re.sub(r'/\*.*?\*/', ' ', sql or '', flags=re.S)
    sql = re.sub(r'(--\s|#)[^\n]*', ' ', sql)
    out = []
    for m in _TOKEN_RE.finditer(sql):
        k = m.lastgroup
        if k == 'ws':
            continue
        v = m.group(k)
        if k == 'bq':
            out.append(Tok('ident', v[1:-1].replace('``', '`')))
        else:
            out.append(Tok(k, v))
    return out


def _is_kw(t, *kws):
    return t is not None and t.kind == 'word' and t.up in kws


def _split_top(tokens, pred):
    """按深度 0 上满足 pred 的 token 切分（BETWEEN ... AND 的 AND 不切）。"""
    parts, cur, depth, between = [], [], 0, False
    for t in tokens:
        if t.val == '(':
            depth += 1
        elif t.val == ')':
            depth -= 1
        if depth == 0 and _is_kw(t, 'BETWEEN'):
            between = True
        if depth == 0 and pred(t):
            if between and _is_kw(t, 'AND'):
                between = False
                cur.append(t)
                continue
            parts.append(cur)
            cur = []
            continue
        cur.append(t)
    parts.append(cur)
    return [p for p in parts if p]


def _clauses(tokens):
    """把顶层 SELECT 切成子句。返回 {'SELECT': [...], 'FROM': [...], ...}。"""
    res, cur_name, cur, depth = {}, None, [], 0
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.val == '(':
            depth += 1
        elif t.val == ')':
            depth -= 1
        name = None
        if depth == 0 and t.kind == 'word':
            if t.up == 'SELECT' and cur_name is None:
                name = 'SELECT'
            elif t.up in ('FROM', 'WHERE', 'HAVING', 'LIMIT', 'UNION', 'WINDOW', 'INTO'):
                name = t.up
            elif t.up in ('GROUP', 'ORDER') and i + 1 < len(tokens) and _is_kw(tokens[i + 1], 'BY'):
                name = t.up
                i += 1
            elif t.up == 'FOR' and i + 1 < len(tokens) and _is_kw(tokens[i + 1], 'UPDATE', 'SHARE'):
                name = 'FOR'
        if name:
            if cur_name:
                res.setdefault(cur_name, cur)
            if name == 'UNION':
                res['UNION'] = True
                break
            cur_name, cur = name, []
        else:
            cur.append(t)
        i += 1
    if cur_name and cur_name not in res:
        res[cur_name] = cur
    return res


# ------------------------------------------------------------------ 解析

def _colref(toks):
    """单个列引用：ident | ident.ident | ident.ident.ident。返回 (qualifier, col) 或 None。"""
    names = []
    for j, t in enumerate(toks):
        if j % 2 == 0:
            if t.kind not in ('ident', 'word'):
                return None
            names.append(t.val)
        elif t.val != '.':
            return None
    if not names or len(toks) % 2 == 0:
        return None
    if names[0].upper() in _RESERVED:
        return None
    if len(names) == 1:
        return None, names[0]
    return names[-2], names[-1]


_RESERVED = {'NULL', 'TRUE', 'FALSE', 'AND', 'OR', 'NOT', 'SELECT', 'CASE', 'WHEN', 'THEN',
             'ELSE', 'END', 'EXISTS', 'INTERVAL', 'DISTINCT', 'CURRENT_TIMESTAMP', 'CURRENT_DATE',
             'NOW', 'BINARY', 'MICROSECOND', 'SECOND', 'MINUTE', 'HOUR', 'DAY', 'WEEK', 'MONTH',
             'QUARTER', 'YEAR', 'ASC', 'DESC', 'UNKNOWN'}


def _has_colref(toks):
    for j, t in enumerate(toks):
        if t.kind == 'ident':
            return True
        if t.kind == 'word' and t.up not in _RESERVED:
            nxt = toks[j + 1] if j + 1 < len(toks) else None
            if not (nxt is not None and nxt.val == '('):   # 函数名不算列
                return True
    return False


def _parse_tables(from_toks):
    """FROM 子句 → (tables, on_conds)。tables: [{'name','db','alias','derived'}]"""
    tables, ons = [], []
    # 以逗号与 JOIN 关键字切分表项
    items, cur, depth, i = [], [], 0, 0
    toks = from_toks
    while i < len(toks):
        t = toks[i]
        if t.val == '(':
            depth += 1
        elif t.val == ')':
            depth -= 1
        if depth == 0 and (t.val == ',' or _is_kw(t, *_JOIN_KW)):
            if cur:
                items.append(cur)
            cur = []
            while i + 1 < len(toks) and _is_kw(toks[i + 1], *_JOIN_KW):
                i += 1
            i += 1
            continue
        cur.append(t)
        i += 1
    if cur:
        items.append(cur)
    for it in items:
        on_idx = next((k for k, t in enumerate(it) if _is_kw(t, 'ON', 'USING')), None)
        ref = it if on_idx is None else it[:on_idx]
        if on_idx is not None:
            ons.append((it[on_idx].up, it[on_idx + 1:]))
        if not ref:
            continue
        if ref[0].val == '(':
            alias = ref[-1].val if ref[-1].kind in ('ident', 'word') else None
            tables.append({'name': None, 'db': None, 'alias': alias, 'derived': True})
            continue
        # tbl | db.tbl [AS] alias [USE/FORCE/IGNORE INDEX (...)]
        names, k = [], 0
        while k < len(ref) and ref[k].kind in ('ident', 'word'):
            names.append(ref[k].val)
            if k + 1 < len(ref) and ref[k + 1].val == '.':
                k += 2
                continue
            k += 1
            break
        rest = ref[k:]
        alias = None
        if rest and _is_kw(rest[0], 'AS'):
            rest = rest[1:]
        if rest and rest[0].kind in ('ident', 'word') and rest[0].up not in (
                'USE', 'FORCE', 'IGNORE', 'PARTITION'):
            alias = rest[0].val
        if not names:
            continue
        tables.append({'name': names[-1], 'db': names[-2] if len(names) > 1 else None,
                       'alias': alias, 'derived': False})
    return tables, ons


class Parsed:
    def __init__(self):
        self.kind = 'other'
        self.tables = []
        self.preds = []          # {'q','col','kind','func','value','source'}
        self.joins = []          # ((q,col),(q,col))
        self.or_groups = []      # [[(q,col),...]]
        self.order_by = []       # [{'q','col','dir'}]
        self.group_by = []
        self.limit = None        # {'offset': int|'?'|None, 'count': ...}
        self.select_cols = []    # [(q,col)]
        self.select_star = False
        self.has_subquery = False
        self.union = False
        self.not_in_subquery = False
        self.order_by_rand = False
        self.count_distinct = False
        self.aggregates = False
        self.using_cols = []     # JOIN ... USING (col)

    # 列归属：返回表名（小写）或 None
    def resolve(self, q, col):
        base = [t for t in self.tables if not t['derived']]
        if q:
            for t in self.tables:
                if (t['alias'] or '').lower() == q.lower() or (t['name'] or '').lower() == q.lower():
                    return None if t['derived'] else t['name'].lower()
            return None
        if len(base) == 1 and len(self.tables) == 1:
            return base[0]['name'].lower()
        return None


def _classify_pred(toks, source, p):
    """单个合取项 → 谓词（写入 p.preds / p.joins / p.or_groups）。"""
    if not toks:
        return
    if any(t.val == '(' for t in toks) and any(_is_kw(t, 'SELECT') for t in toks):
        p.has_subquery = True
    # 括号包起来的整体：剥一层
    if toks[0].val == '(' and toks[-1].val == ')':
        depth, ok = 0, True
        for j, t in enumerate(toks):
            depth += t.val == '('
            depth -= t.val == ')'
            if depth == 0 and j < len(toks) - 1:
                ok = False
                break
        if ok:
            inner = toks[1:-1]
            ors = _split_top(inner, lambda t: _is_kw(t, 'OR'))
            if len(ors) > 1:
                cols = []
                for o in ors:
                    for a in _split_top(o, lambda t: _is_kw(t, 'AND')):
                        cr = _lhs_col(a)
                        if cr:
                            cols.append(cr)
                p.or_groups.append(cols)
                return
            for a in _split_top(inner, lambda t: _is_kw(t, 'AND')):
                _classify_pred(a, source, p)
            return
    ors = _split_top(toks, lambda t: _is_kw(t, 'OR'))
    if len(ors) > 1:
        cols = []
        for o in ors:
            cr = _lhs_col(o)
            if cr:
                cols.append(cr)
        p.or_groups.append(cols)
        return
    # 找顶层比较符 / 关键字
    depth = 0
    for j, t in enumerate(toks):
        if t.val == '(':
            depth += 1
        elif t.val == ')':
            depth -= 1
        if depth:
            continue
        neg = j > 0 and _is_kw(toks[j - 1], 'NOT')
        lhs = toks[:j - 1] if neg else toks[:j]
        rhs = toks[j + 1:]
        if t.kind == 'op':
            lc, rc = _colref(lhs), _colref(rhs)
            if lc and rc:
                p.joins.append((lc, rc))
                return
            col, other, func = _side(lhs, rhs)
            if col is None:
                return
            if t.val in ('!=', '<>'):
                kind = 'neq'
            elif t.val in ('=', '<=>'):
                kind = 'eq'
            else:
                kind = 'range'
            if func:
                kind = 'func'
            p.preds.append({'q': col[0], 'col': col[1], 'kind': kind, 'func': func,
                            'value': _text(other), 'source': source})
            return
        if _is_kw(t, 'IN'):
            col, func = _lhs_col_func(lhs)
            if col is None:
                return
            sub = any(_is_kw(x, 'SELECT') for x in rhs)
            if sub:
                p.has_subquery = True
                if neg:
                    p.not_in_subquery = True
            p.preds.append({'q': col[0], 'col': col[1],
                            'kind': 'func' if func else ('neq' if neg else 'in'),
                            'func': func, 'value': _text(rhs)[:80], 'source': source})
            return
        if _is_kw(t, 'LIKE'):
            col, func = _lhs_col_func(lhs)
            if col is None:
                return
            p.preds.append({'q': col[0], 'col': col[1],
                            'kind': 'func' if func else ('neq' if neg else 'like'),
                            'func': func, 'value': _text(rhs), 'source': source})
            return
        if _is_kw(t, 'BETWEEN'):
            col, func = _lhs_col_func(lhs)
            if col is None:
                return
            p.preds.append({'q': col[0], 'col': col[1], 'kind': 'func' if func else 'range',
                            'func': func, 'value': _text(rhs), 'source': source})
            return
        if _is_kw(t, 'IS'):
            col, func = _lhs_col_func(toks[:j])
            if col is None:
                return
            isnot = j + 1 < len(toks) and _is_kw(toks[j + 1], 'NOT')
            p.preds.append({'q': col[0], 'col': col[1],
                            'kind': 'neq' if isnot else ('func' if func else 'eq'),
                            'func': func, 'value': 'NULL', 'source': source})
            return


def _text(toks):
    return ' '.join(t.val if t.kind != 'ident' else f'`{t.val}`' for t in toks)


def _lhs_col(toks):
    for j, t in enumerate(toks):
        if t.kind == 'op' or _is_kw(t, 'IN', 'LIKE', 'BETWEEN', 'IS', 'NOT'):
            col, _ = _lhs_col_func(toks[:j])
            return col
    return None


def _lhs_col_func(toks):
    """左侧：列引用，或 FUNC(列引用, ...)。返回 ((q,col)|None, func|None)。"""
    cr = _colref(toks)
    if cr:
        return cr, None
    if len(toks) >= 3 and toks[0].kind == 'word' and toks[1].val == '(' and toks[-1].val == ')':
        inner = toks[2:-1]
        args = _split_top(inner, lambda t: t.val == ',')
        for a in args:
            c = _colref(a)
            if c:
                return c, toks[0].up
    # 列参与运算：col + 1 = ?
    if toks and _colref(toks[:1]) and len(toks) > 1 and toks[1].val in ('+', '-', '*', '/'):
        return _colref(toks[:1]), 'EXPR'
    return None, None


def _side(lhs, rhs):
    """比较两侧谁是列。返回 ((q,col), 另一侧 tokens, func)。"""
    c, f = _lhs_col_func(lhs)
    if c and not _has_colref(rhs):
        return c, rhs, f
    c, f = _lhs_col_func(rhs)
    if c and not _has_colref(lhs):
        return c, lhs, f
    return None, None, None


def _parse_order(toks, p, target):
    for item in _split_top(toks, lambda t: t.val == ','):
        direction = 'ASC'
        if item and _is_kw(item[-1], 'ASC', 'DESC'):
            direction = item[-1].up
            item = item[:-1]
        if item and _is_kw(item[0], 'RAND') and len(item) >= 3:
            p.order_by_rand = True
            continue
        cr = _colref(item)
        target.append({'q': cr[0], 'col': cr[1], 'dir': direction} if cr else
                      {'q': None, 'col': None, 'dir': direction, 'expr': _text(item)})


def _parse_limit(toks):
    vals = [t for t in toks if t.kind in ('num', 'ph')]
    norm = [int(float(t.val)) if t.kind == 'num' else '?' for t in vals]
    has_offset_kw = any(_is_kw(t, 'OFFSET') for t in toks)
    if len(norm) == 2:
        if has_offset_kw:
            return {'count': norm[0], 'offset': norm[1]}
        return {'offset': norm[0], 'count': norm[1]}
    if len(norm) == 1:
        return {'offset': None, 'count': norm[0]}
    return None


def parse(sql):
    toks = tokenize(sql)
    p = Parsed()
    if not toks:
        return p
    first = toks[0].up if toks[0].kind == 'word' else ''
    if first == 'WITH':
        # 跳过 CTE 定义，定位主语句
        depth = 0
        for j, t in enumerate(toks):
            depth += t.val == '('
            depth -= t.val == ')'
            if depth == 0 and _is_kw(t, 'SELECT', 'UPDATE', 'DELETE') and j > 0 \
                    and toks[j - 1].val == ')':
                toks = toks[j:]
                first = t.up
                break
        p.has_subquery = True
    if first in ('UPDATE', 'DELETE'):
        return _parse_dml(toks, first, p)
    if first != 'SELECT':
        return p
    p.kind = 'select'
    cl = _clauses(toks)
    p.union = bool(cl.get('UNION'))
    sel = cl.get('SELECT') or []
    if sel and _is_kw(sel[0], 'DISTINCT'):
        sel = sel[1:]
    tables, ons = _parse_tables(cl.get('FROM') or [])
    p.tables = tables
    for item in _split_top(sel, lambda t: t.val == ','):
        if any(t.val == '(' for t in item):
            if any(_is_kw(t, 'SELECT') for t in item):
                p.has_subquery = True
            fn = item[0].up if item and item[0].kind == 'word' else ''
            if fn in ('COUNT', 'SUM', 'AVG', 'MIN', 'MAX', 'GROUP_CONCAT'):
                p.aggregates = True
                if fn == 'COUNT' and any(_is_kw(t, 'DISTINCT') for t in item):
                    p.count_distinct = True
            continue
        if item and item[-1].val == '*':
            p.select_star = True
            continue
        if len(item) >= 3 and _is_kw(item[-2], 'AS'):
            item = item[:-2]
        elif len(item) >= 2 and item[-1].kind in ('ident', 'word') and _colref(item[:-1]):
            item = item[:-1]
        cr = _colref(item)
        if cr:
            p.select_cols.append(cr)
    for kind, cond in ons:
        if kind == 'USING':
            p.using_cols.extend(t.val for t in cond if t.kind in ('ident', 'word'))
            continue
        for a in _split_top(cond, lambda t: _is_kw(t, 'AND')):
            _classify_pred(a, 'on', p)
    for a in _split_top(cl.get('WHERE') or [], lambda t: _is_kw(t, 'AND')):
        _classify_pred(a, 'where', p)
    _parse_order(cl.get('GROUP') or [], p, p.group_by)
    _parse_order(cl.get('ORDER') or [], p, p.order_by)
    if cl.get('GROUP'):
        p.aggregates = True
    if cl.get('LIMIT'):
        p.limit = _parse_limit(cl['LIMIT'])
    return p


def _parse_dml(toks, first, p):
    p.kind = first.lower()
    # UPDATE t [AS a] SET ... WHERE ... / DELETE FROM t WHERE ...
    words = toks[1:]
    if first == 'DELETE':
        while words and _is_kw(words[0], 'LOW_PRIORITY', 'QUICK', 'IGNORE'):
            words = words[1:]
        if words and _is_kw(words[0], 'FROM'):
            words = words[1:]
    else:
        while words and _is_kw(words[0], 'LOW_PRIORITY', 'IGNORE'):
            words = words[1:]
    stop = next((k for k, t in enumerate(words) if _is_kw(t, 'SET', 'WHERE', 'ORDER', 'LIMIT')),
                len(words))
    tables, _ = _parse_tables(words[:stop])
    p.tables = tables
    wi = next((k for k, t in enumerate(words) if _is_kw(t, 'WHERE')), None)
    if wi is not None:
        rest = words[wi + 1:]
        end = next((k for k, t in enumerate(rest) if _is_kw(t, 'ORDER', 'LIMIT')), len(rest))
        for a in _split_top(rest[:end], lambda t: _is_kw(t, 'AND')):
            _classify_pred(a, 'where', p)
    return p


# ------------------------------------------------------------------ 规则

_SEV_ORDER = {'high': 0, 'medium': 1, 'low': 2, 'info': 3}


def _advice(code, severity, category, title, detail, evidence=None, ddl=None,
            rewrite=None, expected=None):
    return {'code': code, 'severity': severity, 'category': category, 'title': title,
            'detail': detail, 'evidence': evidence or {}, 'ddl': ddl, 'rewrite': rewrite,
            'expected': expected}


def _q(name):
    return f'`{name}`'


def _index_name(table, cols):
    return ('idx_' + '_'.join(c for c in cols))[:60]


def _leading_wildcard(value):
    v = (value or '').strip()
    return len(v) >= 2 and v[0] in ("'", '"') and v[1] in ('%', '_')


def _index_columns(idx):
    return list(idx.get('columns') or []) if isinstance(idx, dict) else list(idx)


def _primary_cols(existing):
    for idx in existing or []:
        if isinstance(idx, dict) and str(idx.get('name', '')).upper() == 'PRIMARY':
            return [c.lower() for c in _index_columns(idx)]
    return []


def _covered_by(existing, cols):
    """已有索引的最左前缀覆盖了建议列 → 返回该索引的列，否则 None。

    existing: [[列, ...], ...] 或 [{'name': 'PRIMARY', 'columns': [...]}, ...]
    """
    want = [c.lower() for c in cols]
    for idx in existing or []:
        have = [c.lower() for c in _index_columns(idx)]
        if have[:len(want)] == want:
            return _index_columns(idx)
    return None


def index_advice(p, profile=None, existing_indexes=None, max_cols=5):
    """按表给出复合索引建议（等值 → 排序/分组 → 范围）。"""
    out = []
    if p.kind != 'select' and p.kind not in ('update', 'delete'):
        return out
    if profile and profile.get('available') and profile.get('ratios'):
        r = profile['ratios']
        eff = r.get('scan_efficiency')
        if eff is not None and eff <= 10 and not (r.get('no_index_share') or 0) \
                and not (r.get('full_scan_share') or 0):
            return [_advice('I-00', 'info', 'index', '当前执行效率良好，无需新增索引',
                            f"每次执行扫描 / 返回行比为 {eff}，且没有全表扫描与未用索引的执行",
                            {'scan_efficiency': eff})]
    base = [t for t in p.tables if not t['derived']]
    multi = len(p.tables) > 1
    first_table = base[0]['name'].lower() if base else None
    by_table = {}
    unresolved = []
    for pr in p.preds:
        t = p.resolve(pr['q'], pr['col'])
        if t is None:
            unresolved.append(pr['col'])
            continue
        by_table.setdefault(t, []).append(pr)
    # JOIN 关联列：非首表（被驱动表）的关联列当作等值列
    join_cols = {}
    for (lq, lc), (rq, rc) in p.joins:
        lt, rt = p.resolve(lq, lc), p.resolve(rq, rc)
        for t, c in ((lt, lc), (rt, rc)):
            if t and t != first_table:
                join_cols.setdefault(t, []).append(c)
    for t in base:
        name = t['name'].lower()
        preds = by_table.get(name, [])
        eq = [pr['col'] for pr in preds if pr['kind'] in ('eq', 'in')]
        eq += join_cols.get(name, [])
        ranges = [pr for pr in preds if pr['kind'] == 'range'
                  or (pr['kind'] == 'like' and not _leading_wildcard(pr['value']))]
        cols = list(dict.fromkeys(eq))
        has_in = any(pr['kind'] == 'in' for pr in preds)
        note = []
        sort_cols = []
        if (not multi or name == first_table) and not has_in:
            src = p.order_by or p.group_by
            if src and all(o.get('col') and p.resolve(o['q'], o['col']) == name for o in src):
                dirs = {o['dir'] for o in src}
                if len(dirs) == 1:
                    sort_cols = [o['col'] for o in src]
        range_col = ranges[0]['col'] if ranges else None
        if sort_cols and (p.limit or not range_col):
            cols += [c for c in sort_cols if c not in cols]
            if range_col and range_col not in cols:
                cols.append(range_col)
                note.append(f'范围列 {range_col} 放在排序列之后，只用于过滤，换取免排序（有 LIMIT 时通常更划算）')
        elif range_col:
            if range_col not in cols:
                cols.append(range_col)
            if sort_cols:
                note.append('范围条件之后的排序无法利用索引，仍会有 filesort')
        if any(pr['kind'] == 'like' and not _leading_wildcard(pr['value'])
               and pr['value'].strip() == '?' for pr in preds):
            note.append('LIKE 的模式是参数：只有前缀匹配（如 \'abc%\'）才能用上该索引')
        cols = cols[:max_cols]
        if not cols:
            continue
        hit = _covered_by((existing_indexes or {}).get(name), cols)
        if hit is not None:
            out.append(_advice('I-03', 'info', 'index', f'表 {t["name"]} 已有可用索引',
                               f"已有索引 ({', '.join(hit)}) 的最左前缀覆盖建议列 ({', '.join(cols)})；"
                               '若仍慢，用 EXPLAIN ANALYZE 确认是否真的走了该索引',
                               {'existing': hit, 'wanted': cols}))
            continue
        ddl = (f"ALTER TABLE {_q(t['name'])} ADD INDEX {_q(_index_name(t['name'], cols))} "
               f"({', '.join(_q(c) for c in cols)});")
        detail = f"按 等值列 → 排序/分组列 → 范围列 的顺序建立复合索引：({', '.join(cols)})"
        if note:
            detail += '。' + '；'.join(note)
        expected = ('消除全表扫描' + ('与排序' if sort_cols and sort_cols[0] in cols else ''))
        out.append(_advice('I-01', 'high' if (profile and _scan_bad(profile)) else 'medium',
                           'index', f'为表 {t["name"]} 建立复合索引', detail,
                           {'columns': cols}, ddl=ddl, expected=expected))
        # InnoDB 二级索引隐含主键列：覆盖索引里不必重复（未知主键时按惯例跳过名为 id 的列）
        pk = _primary_cols((existing_indexes or {}).get(name)) or ['id']
        sel = [c for q, c in p.select_cols if p.resolve(q, c) == name and c.lower() not in pk]
        if sel and not p.select_star and len(set(cols) | set(sel)) <= max_cols + 1 \
                and all(p.resolve(q, c) == name for q, c in p.select_cols):
            cov = cols + [c for c in sel if c not in cols]
            if cov != cols:
                out.append(_advice(
                    'I-02', 'low', 'index', f'可选：表 {t["name"]} 的覆盖索引',
                    f"在 I-01 基础上追加查询列 ({', '.join(c for c in cov if c not in cols)})，"
                    '可免回表；代价是索引更宽、写放大更大',
                    {'columns': cov},
                    ddl=(f"ALTER TABLE {_q(t['name'])} ADD INDEX {_q(_index_name(t['name'], cov))} "
                         f"({', '.join(_q(c) for c in cov)});"),
                    expected='消除回表'))
    if unresolved and multi:
        out.append(_advice('I-09', 'info', 'index', '部分条件列无法确定所属表',
                           f"多表查询中的无前缀列 ({', '.join(dict.fromkeys(unresolved))}) 无法确定属于哪张表，"
                           '没有为它们给出索引建议；请在 SQL 中使用 表别名.列 的写法',
                           {'columns': list(dict.fromkeys(unresolved))}))
    return out


def _scan_bad(profile):
    r = profile.get('ratios') or {}
    return (r.get('scan_efficiency') or 0) >= 100 or (r.get('full_scan_share') or 0) >= 0.5


def rewrite_advice(p, sample_sql=None):
    out = []
    for pr in p.preds:
        if pr['kind'] == 'func':
            fn = pr['func'] or '函数'
            hint = ''
            if fn in ('DATE', 'DATE_FORMAT', 'YEAR', 'MONTH', 'TO_DAYS', 'UNIX_TIMESTAMP'):
                hint = f"例如 {fn}({pr['col']}) = ? 改写为 {pr['col']} >= ? AND {pr['col']} < ?（区间）"
            elif fn in ('LOWER', 'UPPER', 'TRIM'):
                hint = '统一在写入侧规整大小写 / 空白，或使用函数索引（MySQL 8.0.13+）'
            elif fn == 'EXPR':
                hint = f"把运算移到等号另一侧，例如 {pr['col']} + 1 = ? 改写为 {pr['col']} = ? - 1"
            else:
                hint = '把函数作用到常量一侧，或使用函数索引（MySQL 8.0.13+）'
            out.append(_advice('R-01', 'high', 'rewrite', f'列 {pr["col"]} 被 {fn}() 包裹，无法使用索引',
                               hint, {'column': pr['col'], 'function': fn}, rewrite=hint,
                               expected='使该条件可以走索引'))
        elif pr['kind'] == 'like' and _leading_wildcard(pr['value']):
            out.append(_advice('R-02', 'high', 'rewrite', f'列 {pr["col"]} 的 LIKE 以通配符开头',
                               f"LIKE {pr['value']} 无法使用 B-tree 索引（不要为它建普通索引）；"
                               '可改为前缀匹配、使用全文索引（FULLTEXT / ngram），或把检索交给搜索引擎',
                               {'column': pr['col'], 'pattern': pr['value']}))
        elif pr['kind'] == 'neq' and pr['source'] == 'where':
            out.append(_advice('R-08', 'low', 'rewrite', f'列 {pr["col"]} 上的否定条件选择性差',
                               '!= / NOT IN / NOT LIKE / IS NOT NULL 通常无法有效利用索引，'
                               '如业务允许，改写为正向条件', {'column': pr['col']}))
    lim = p.limit or {}
    off = lim.get('offset')
    if isinstance(off, int) and off >= 10000:
        out.append(_advice('R-03', 'high', 'rewrite', f'深分页 LIMIT {off}, {lim.get("count")}',
                           f'数据库要先读取并丢弃 {off} 行；改为键集分页（WHERE id > 上一页最后 id ORDER BY id LIMIT n）',
                           {'offset': off}))
    elif off == '?':
        out.append(_advice('R-03', 'low', 'rewrite', '分页偏移量是参数',
                           '若偏移量可能很大（深分页），改为键集分页', {'offset': '?'}))
    for grp in p.or_groups:
        cols = {c for _, c in grp}
        if len(cols) > 1:
            out.append(_advice('R-04', 'medium', 'rewrite', f'跨列 OR 条件：{" / ".join(sorted(cols))}',
                               '跨不同列的 OR 往往导致全表扫描；改写为 UNION ALL 分支，或保证每列都有索引以触发 index_merge',
                               {'columns': sorted(cols)}))
    if p.not_in_subquery:
        out.append(_advice('R-05', 'medium', 'rewrite', 'NOT IN (子查询)',
                           '子查询结果含 NULL 时 NOT IN 语义陷阱且难以优化，改写为 NOT EXISTS 或 LEFT JOIN ... IS NULL'))
    if p.select_star and p.kind == 'select':
        out.append(_advice('R-06', 'low', 'rewrite', 'SELECT *',
                           '只取需要的列：减少回表、网络传输，并使覆盖索引成为可能'))
    if p.order_by_rand:
        out.append(_advice('R-07', 'high', 'rewrite', 'ORDER BY RAND()',
                           '会对全部结果集生成随机数再排序；改为按主键随机区间取样'))
    return out


def evidence_advice(profile):
    """来自实测画像（sql_profile）的配置与执行层建议。"""
    out = []
    if not profile or not profile.get('available'):
        return out
    codes = {f['code']: f for f in profile.get('findings', [])}
    if 'TMP_DISK' in codes:
        out.append(_advice('E-01', 'medium', 'config', '内部临时表落盘',
                           codes['TMP_DISK']['text'] + '；优先为 GROUP BY / ORDER BY 列建索引避免临时表，'
                           '其次评估 tmp_table_size / max_heap_table_size',
                           codes['TMP_DISK']['evidence']))
    if 'SORT_MERGE' in codes:
        out.append(_advice('E-02', 'medium', 'config', '排序发生多轮归并',
                           codes['SORT_MERGE']['text'] + '；优先用索引消除排序，其次评估 sort_buffer_size（会话级调整，勿全局放大）',
                           codes['SORT_MERGE']['evidence']))
    if 'LOCK_WAIT' in codes:
        out.append(_advice('E-03', 'high', 'concurrency', '锁等待占比高',
                           codes['LOCK_WAIT']['text'] + '；在阻塞分析中定位持锁会话，缩短事务、避免热点行并发更新',
                           codes['LOCK_WAIT']['evidence']))
    if 'FULL_JOIN' in codes:
        out.append(_advice('E-04', 'high', 'index', '无索引 JOIN',
                           codes['FULL_JOIN']['text'] + '；为被驱动表的关联列建立索引（见 I-01）',
                           codes['FULL_JOIN']['evidence']))
    return out


# ------------------------------------------------------------------ TDSQL 分布式规则

def shard_meta_from_collect(data):
    """从采集结果（checkers/tdsql.py 的 tdsql_shard_tables）构造 {表名小写: {'type','shardkey','db'}}。"""
    meta = {}
    for t in (data or {}).get('tdsql_shard_tables') or []:
        db_table = str(t.get('db_table') or '')
        if not db_table:
            continue
        db, _, tbl = db_table.rpartition('.')
        sk = str(t.get('shardkey') or '')
        kind = 'broadcast' if sk == 'noshardkey_allset' else ('shard' if sk else None)
        if kind:
            meta[tbl.lower()] = {'type': kind, 'shardkey': None if kind == 'broadcast' else sk,
                                 'db': db or None}
    return meta


def tdsql_advice(p, shard_meta, set_count=None, distribution=None):
    """groupshard 实例的分布式规则 D-01 ~ D-08。shard_meta 为空时只给倾斜规则。"""
    out = []
    n = set_count or 'N'
    shard_meta = shard_meta or {}
    base = [t for t in p.tables if not t['derived']]

    def meta(t):
        return shard_meta.get((t['name'] or '').lower())

    routed = {}   # 表名 -> 是否能按 shardkey 路由
    eq_cols = {(p.resolve(pr['q'], pr['col']), (pr['col'] or '').lower())
               for pr in p.preds if pr['kind'] in ('eq', 'in')}
    for t in base:
        m = meta(t)
        if m and m['type'] == 'shard':
            routed[t['name'].lower()] = (t['name'].lower(), m['shardkey'].lower()) in eq_cols
    # 传递：分表的 shardkey 与"已定值的列"等值关联 → 同样可路由（同 shardkey 值必在同一 set）
    changed = True
    while changed:
        changed = False
        for (lq, lc), (rq, rc) in p.joins:
            ends = ((p.resolve(lq, lc), lc.lower()), (p.resolve(rq, rc), rc.lower()))
            for (ta, ca), (tb, cb) in (ends, ends[::-1]):
                ma = shard_meta.get(ta or '')
                if not ma or ma['type'] != 'shard' or routed.get(ta) or ca != ma['shardkey'].lower():
                    continue
                if (tb, cb) in eq_cols:
                    routed[ta] = True
                    eq_cols.add((ta, ca))
                    changed = True
    for t in base:
        m = meta(t)
        if not m or m['type'] != 'shard':
            continue
        name = t['name'].lower()
        sk = m['shardkey'].lower()
        preds = [pr for pr in p.preds if (pr['col'] or '').lower() == sk
                 and p.resolve(pr['q'], pr['col']) == name]
        bad = [pr for pr in preds if pr['kind'] in ('func', 'range', 'neq')]
        if routed.get(name):
            continue
        if bad:
            pr = bad[0]
            why = {'func': f'被 {pr["func"]}() 包裹', 'range': '只有范围条件',
                   'neq': '是否定条件'}[pr['kind']]
            out.append(_advice('D-07', 'high', 'distributed',
                               f'分表 {t["name"]} 的 shardkey `{m["shardkey"]}` {why}，无法路由',
                               f'shardkey 按哈希分布，{why}时 proxy 无法定位 set，等同于广播到全部 {n} 个 set',
                               {'table': t['name'], 'shardkey': m['shardkey'], 'predicate': pr['value']},
                               rewrite=f'改为对 `{m["shardkey"]}` 的等值或 IN 条件'))
        elif p.kind in ('update', 'delete'):
            out.append(_advice('D-06', 'high', 'distributed',
                               f'{p.kind.upper()} 分表 {t["name"]} 未带 shardkey `{m["shardkey"]}`',
                               f'将在全部 {n} 个 set 上执行并走分布式事务；请在 WHERE 中带上 shardkey 的等值条件',
                               {'table': t['name'], 'shardkey': m['shardkey']}))
        else:
            out.append(_advice('D-01', 'high', 'distributed',
                               f'分表 {t["name"]} 的查询未带 shardkey `{m["shardkey"]}`，广播到全部 {n} 个 set',
                               '官方文档：不带 shardkey 的 SQL 会路由到所有节点，资源消耗大。'
                               f'请在 WHERE 中加入 `{m["shardkey"]}` = ? 或 IN (...)',
                               {'table': t['name'], 'shardkey': m['shardkey']},
                               rewrite=f'... WHERE `{m["shardkey"]}` = ? AND ...',
                               expected=f'从 {n} 个 set 扫描降为 1 个'))
    # JOIN 规则
    shard_tabs = {t['name'].lower(): t for t in base if (meta(t) or {}).get('type') == 'shard'}
    single_tabs = [t for t in base if meta(t) is None and shard_meta]
    if len(shard_tabs) >= 2:
        sk_of = {k: shard_meta[k]['shardkey'].lower() for k in shard_tabs}
        ok_pairs = set()
        for (lq, lc), (rq, rc) in p.joins:
            lt, rt = p.resolve(lq, lc), p.resolve(rq, rc)
            if lt in sk_of and rt in sk_of and lc.lower() == sk_of[lt] and rc.lower() == sk_of[rt]:
                ok_pairs.add(frozenset((lt, rt)))
        names = sorted(shard_tabs)
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                if frozenset((a, b)) not in ok_pairs:
                    out.append(_advice('D-02', 'high', 'distributed',
                                       f'分表 {shard_tabs[a]["name"]} 与 {shard_tabs[b]["name"]} 的 JOIN 不是 shardkey 相等',
                                       '官方建议多个分表之间使用分表键相等的条件做 JOIN；否则需要跨 set 关联，'
                                       '数据在 proxy 汇聚。改为按 shardkey 关联，或把较小的表改为广播表',
                                       {'tables': [shard_tabs[a]['name'], shard_tabs[b]['name']],
                                        'shardkeys': [sk_of[a], sk_of[b]]}))
    if shard_tabs and single_tabs:
        for t in single_tabs:
            out.append(_advice('D-03', 'medium', 'distributed',
                               f'分表与单表 {t["name"]} 关联',
                               '单表只存放在第一个 set，与分表关联时无法下推到各 set；'
                               '若该表较小且读多写少，建议改为广播表（shardkey=noshardkey_allset）',
                               {'table': t['name']}))
    broadcast_query = any(v is False for v in routed.values())
    if broadcast_query and p.order_by and p.limit:
        off = (p.limit or {}).get('offset')
        out.append(_advice('D-04', 'high' if isinstance(off, int) and off >= 1000 else 'medium',
                           'distributed', '跨 set 的 ORDER BY ... LIMIT',
                           f'每个 set 都要返回 偏移量+条数 行，再由 proxy 归并排序；偏移量越大，'
                           f'{n} 个 set 一起放大。优先带上 shardkey，或改为键集分页',
                           {'offset': off, 'count': (p.limit or {}).get('count')}))
    if broadcast_query and p.aggregates:
        out.append(_advice('D-05', 'medium' if p.count_distinct else 'low', 'distributed',
                           '跨 set 聚合',
                           '各 set 先局部聚合，再由 proxy 二次聚合' +
                           ('；COUNT(DISTINCT) 需要把去重明细汇聚到 proxy，代价很高' if p.count_distinct else ''),
                           {'count_distinct': p.count_distinct}))
    if distribution and distribution.get('is_skewed'):
        out.append(_advice('D-08', 'medium', 'distributed',
                           f'set 间负载倾斜：{distribution["hottest_set"]} 承担了 '
                           f'{(distribution["sets"][0]["share"] or 0) * 100:.0f}% 的耗时',
                           f'倾斜度 {distribution["skew"]}（≥ 2 视为热点 set）。检查 shardkey 取值分布'
                           '是否集中（热点用户 / 热点商户），或该 SQL 是否只命中少数 shardkey 值',
                           {'skew': distribution['skew'], 'hottest_set': distribution['hottest_set']}))
    return out


# ------------------------------------------------------------------ 入口

def advise(sql_text, db_type='mysql', sample_sql=None, profile=None, existing_indexes=None,
           tdsql_mode=None, shard_meta=None, set_count=None, distribution=None):
    """统一入口。sql_text 用 digest 文本或带字面量样例均可；有样例时优先用样例（能判断前导通配、深分页）。"""
    text = sample_sql or sql_text
    p = parse(text)
    out = []
    if p.kind in ('select', 'update', 'delete'):
        out += index_advice(p, profile, existing_indexes)
        out += rewrite_advice(p, sample_sql)
    out += evidence_advice(profile)
    if db_type == 'tdsql' and tdsql_mode == 'groupshard':
        out += tdsql_advice(p, shard_meta, set_count, distribution)
    # 前导通配的列不该出现在索引建议里（R-02 已解释原因）
    bad_like = {pr['col'] for pr in p.preds if pr['kind'] == 'like' and _leading_wildcard(pr['value'])}
    out = [a for a in out if not (a['code'] in ('I-01', 'I-02')
                                  and set(a['evidence'].get('columns', [])) <= bad_like)]
    out.sort(key=lambda a: _SEV_ORDER.get(a['severity'], 9))
    return {'parsed': p.kind != 'other', 'statement': p.kind, 'advice': out,
            'tables': [t['name'] for t in p.tables if t['name']]}
