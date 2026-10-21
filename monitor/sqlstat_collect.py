# -*- coding: utf-8 -*-
"""MySQL 系（mysql / tdsql / gbase）SQL digest 采集与差分。见 TDSQL_SLOWSQL_DESIGN.md §2。

为什么单独成模块：原实现内嵌在 start_monitor，有两个结构性缺陷（TDSQL_SLOWSQL_REVIEW.md）：

- **T-03**：对 TDSQL groupshard，proxy 每轮把 performance_schema 查询路由到某一个 set，
  且每轮可能不同；原实现对"不同 set 的累计值"做减法，写进 sql_stat 的增量是垃圾。
- **T-12**：每轮只取**累计**耗时 Top 100，新近变慢但历史累计小的 SQL 进不来。

这里的规则只有一条：**凡累计计数器，先按 (实例, set, digest) 差分，再跨 set 求和**。
跨 set 求和发生在读取侧（sql_stat 按 set 分行存储），采集侧永远不跨 set 做减法。

TDSQL 按 set 取数依据腾讯云官方文档《透传 SQL》：
``/*sets:allsets*/<SQL>`` 发往全部 set，结果集多一列 ``info``（set 名）；
``/*sets:<set_name>*/<SQL>`` 发往指定 set。透传时 proxy 不解析 SQL。
"""
import logging
import re
import threading
import time

from monitor import degrade

logger = logging.getLogger(__name__)

# 计数器：输出键 -> performance_schema 列。时间类为皮秒。
_COUNTERS = (
    ('exec', 'COUNT_STAR'),
    ('elapsed_ps', 'SUM_TIMER_WAIT'),
    ('lock_ps', 'SUM_LOCK_TIME'),
    ('cpu_ps', 'SUM_CPU_TIME'),
    ('rows_sent', 'SUM_ROWS_SENT'),
    ('rows_examined', 'SUM_ROWS_EXAMINED'),
    ('tmp_tables', 'SUM_CREATED_TMP_TABLES'),
    ('tmp_disk', 'SUM_CREATED_TMP_DISK_TABLES'),
    ('sort_merge', 'SUM_SORT_MERGE_PASSES'),
    ('sort_rows', 'SUM_SORT_ROWS'),
    ('scan', 'SUM_SELECT_SCAN'),
    ('full_join', 'SUM_SELECT_FULL_JOIN'),
    ('no_index', 'SUM_NO_INDEX_USED'),
    ('errors', 'SUM_ERRORS'),
)
# 8.0.28+ 才有 SUM_CPU_TIME，8.0.3+ 才有 QUERY_SAMPLE_TEXT；MariaDB 系两者都没有
_OPTIONAL_COLS = ('SUM_CPU_TIME',)

_PS_PER_MS = 10 ** 9
_SET_NAME_RE = re.compile(r'^[A-Za-z0-9_\-]{1,64}$')

BASIS_PER_SET = 'per_set'        # 已按 set 取数（透传可用）
BASIS_SINGLE = 'single'          # 单节点（MySQL / GBase / TDSQL 直连 set / noshard 无透传）
BASIS_UNROUTED = 'unrouted'      # TDSQL groupshard 但透传不可用：数据只来自 proxy 随机路由到的某个 set


# 会话设置与元数据查询（含本平台自己的采集语句）不是业务慢 SQL：不进入 sql_stat。
# DIGEST_TEXT 已去掉注释，无法靠注释标记识别自身语句，只能按语句形态排除。
_NOISE_FILTER = (
    " AND DIGEST_TEXT NOT LIKE 'SET %'"
    " AND DIGEST_TEXT NOT LIKE 'SHOW %'"
    " AND DIGEST_TEXT NOT LIKE 'USE %'"
    " AND DIGEST_TEXT NOT LIKE 'SELECT @@%'"
    " AND DIGEST_TEXT NOT LIKE '%performance_schema%'"
)


def _select_sql(full: bool, window_sec: int, limit: int) -> str:
    counter_cols = [col for _, col in _COUNTERS if full or col not in _OPTIONAL_COLS]
    extra = ", LEFT(QUERY_SAMPLE_TEXT, 2000) AS sample_text" if full else ""
    # window_sec / limit 已由 appconf 校验为整数，这里再 int() 一次防御；透传时 proxy 不解析 SQL，
    # 不用占位符可以避免依赖驱动侧插值与 proxy 行为的组合
    return (
        "SELECT DIGEST AS digest, SCHEMA_NAME AS schema_name, "
        "LEFT(DIGEST_TEXT, 2000) AS digest_text, FIRST_SEEN AS first_seen, "
        "NOW(6) AS server_now, "
        + ", ".join(f"{c} AS {c.lower()}" for c in counter_cols)
        + extra
        + " FROM performance_schema.events_statements_summary_by_digest"
        f" WHERE DIGEST IS NOT NULL AND LAST_SEEN >= NOW() - INTERVAL {int(window_sec)} SECOND"
        + _NOISE_FILTER
        + f" ORDER BY SUM_TIMER_WAIT DESC LIMIT {int(limit)}"
    )


def _num(v):
    try:
        return int(v or 0)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return 0


def _row_get(r, key):
    """DictCursor 的列名大小写随服务端而异，统一按小写取。"""
    if key in r:
        return r[key]
    lk = key.lower()
    for k, v in r.items():
        if str(k).lower() == lk:
            return v
    return None


def _normalize_row(r, set_name):
    counters = {}
    for out, col in _COUNTERS:
        v = _row_get(r, col.lower())
        counters[out] = _num(v) if v is not None else 0
    return {
        'set_name': set_name or '',
        'digest': str(_row_get(r, 'digest') or '')[:64],
        'schema_name': _row_get(r, 'schema_name'),
        'digest_text': _row_get(r, 'digest_text'),
        'sample_text': _row_get(r, 'sample_text'),
        'first_seen': _row_get(r, 'first_seen'),
        'server_now': _row_get(r, 'server_now'),
        'counters': counters,
        'has_cpu': _row_get(r, 'sum_cpu_time') is not None,
    }


class _Caps:
    """按实例缓存能力探测结果（列是否齐全、透传是否可用）。线程安全。"""

    def __init__(self):
        self._lock = threading.Lock()
        self._full_cols = {}     # config_id -> bool
        self._passthrough = {}   # config_id -> (mode, probed_at)

    def full_cols(self, cid):
        with self._lock:
            return self._full_cols.get(cid, True)

    def set_full_cols(self, cid, ok):
        with self._lock:
            self._full_cols[cid] = ok

    def passthrough(self, cid):
        with self._lock:
            mode, at = self._passthrough.get(cid, (None, 0))
            # 判定为"不可用"的结论 1 小时后重新探测（proxy 升级、权限调整后能自动恢复）
            if mode == 'none' and time.time() - at > 3600:
                return None
            return mode

    def set_passthrough(self, cid, mode):
        with self._lock:
            self._passthrough[cid] = (mode, time.time())


CAPS = _Caps()


def _execute_rows(cur, sql):
    cur.execute(sql)
    return list(cur.fetchall() or [])


def _query_with_cols(cur, cid, prefix, window_sec, limit):
    """按列能力执行；完整列报错（未知列）则降级到基础列并记住。"""
    full = CAPS.full_cols(cid)
    try:
        return _execute_rows(cur, prefix + _select_sql(full, window_sec, limit)), full
    except Exception as e:
        if not full:
            raise
        msg = str(e)
        # 1054 Unknown column：SUM_CPU_TIME（<8.0.28）或 QUERY_SAMPLE_TEXT（MariaDB）缺失
        if '1054' in msg or 'Unknown column' in msg or 'unknown column' in msg:
            degrade.note('sqlstat.digest_columns',
                         'performance_schema 缺少 SUM_CPU_TIME/QUERY_SAMPLE_TEXT，退回基础列', e)
            CAPS.set_full_cols(cid, False)
            return _execute_rows(cur, prefix + _select_sql(False, window_sec, limit)), False
        raise


def fetch_digest_rows(cur, config, tdsql_sets=None, tdsql_mode=None,
                      window_sec=900, limit=500):
    """取 MySQL 系 digest 累计值。

    返回 (rows, meta)。rows 为 _normalize_row 结构；
    meta = {'basis': ..., 'sets': [...], 'warning': str|None}
    """
    cid = config.id
    if config.db_type != 'tdsql':
        raw, _ = _query_with_cols(cur, cid, '', window_sec, limit)
        return [_normalize_row(r, '') for r in raw], {'basis': BASIS_SINGLE, 'sets': [], 'warning': None}

    mode = CAPS.passthrough(cid)
    set_names = [s for s in (tdsql_sets or []) if s and _SET_NAME_RE.match(str(s))]

    # ① allsets：一次往返拿到全部 set，info 列给出 set 名
    if mode in (None, 'allsets'):
        try:
            raw, _ = _query_with_cols(cur, cid, '/*sets:allsets*/', window_sec, limit)
            if not raw:
                # 窗口内无活跃 SQL：不能据此判断透传是否可用，保持既有结论
                basis = BASIS_PER_SET if mode == 'allsets' else BASIS_SINGLE
                return [], {'basis': basis, 'sets': [], 'warning': None}
            if any(_row_get(r, 'info') for r in raw):
                CAPS.set_passthrough(cid, 'allsets')
                rows = [_normalize_row(r, str(_row_get(r, 'info') or '')) for r in raw]
                sets = sorted({r['set_name'] for r in rows if r['set_name']})
                return rows, {'basis': BASIS_PER_SET, 'sets': sets, 'warning': None}
            if not set_names:
                # 没有 info 列、也不知道有哪些 set：大概率是直连了某个 set 或测试用的原生 MySQL
                # （注释被当成普通注释忽略），结果就是单节点结果，不再多查一次
                return _single_or_unrouted(raw, tdsql_mode, 'allsets 结果无 info 列')
        except Exception as e:
            degrade.note('sqlstat.tdsql_allsets', '透传 /*sets:allsets*/ 不可用，尝试逐 set 透传', e)

    # ② 逐 set 透传
    if set_names and mode in (None, 'allsets', 'perset'):
        rows, failed = [], []
        for name in set_names:
            try:
                raw, _ = _query_with_cols(cur, cid, f'/*sets:{name}*/', window_sec, limit)
                rows.extend(_normalize_row(r, name) for r in raw)
            except Exception as e:
                failed.append(name)
                degrade.note('sqlstat.tdsql_perset', f'透传到 {name} 失败', e)
        if len(failed) < len(set_names):
            CAPS.set_passthrough(cid, 'perset')
            warning = (f'以下 set 透传失败，统计不完整：{", ".join(failed)}' if failed else None)
            return rows, {'basis': BASIS_PER_SET, 'sets': [s for s in set_names if s not in failed],
                          'warning': warning}

    # ③ 不带透传：proxy 自行路由
    CAPS.set_passthrough(cid, 'none')
    raw, _ = _query_with_cols(cur, cid, '', window_sec, limit)
    return _single_or_unrouted(raw, tdsql_mode, '透传不可用')


def _single_or_unrouted(raw, tdsql_mode, why):
    rows = [_normalize_row(r, '') for r in raw]
    if tdsql_mode == 'groupshard':
        warning = (f'{why}：分布式实例的 SQL 统计只来自 proxy 路由到的某一个 set，'
                   '跨 set 的执行次数与耗时不完整，环比结论不可信')
        degrade.note('sqlstat.tdsql_unrouted', warning)
        return rows, {'basis': BASIS_UNROUTED, 'sets': [], 'warning': warning}
    return rows, {'basis': BASIS_SINGLE, 'sets': [], 'warning': None}


class DeltaTracker:
    """按 (实例, set, digest) 对累计计数器差分。进程内状态；重启后第一轮不产出数据。"""

    MAX_KEYS_PER_CONFIG = 50000

    def __init__(self):
        self._lock = threading.Lock()
        self._prev = {}          # cid -> {(set, digest): counters}
        self._prev_now = {}      # cid -> {set: server_now}
        self._touched = {}       # cid -> {(set, digest): poll_seq}
        self._seq = 0

    def compute(self, cid, rows):
        """返回增量行列表（每行含 set_name / digest / 各 *_delta）。"""
        out = []
        with self._lock:
            self._seq += 1
            prev = self._prev.setdefault(cid, {})
            prev_now = self._prev_now.setdefault(cid, {})
            touched = self._touched.setdefault(cid, {})
            seen_sets = set()
            for r in rows:
                key = (r['set_name'], r['digest'])
                cur_c = r['counters']
                p = prev.get(key)
                delta = None
                if p is not None:
                    d = {k: cur_c[k] - p.get(k, 0) for k in cur_c}
                    # 回退（重启 / TRUNCATE）：任何计数器变小 → 本轮跳过，不写负值
                    if all(v >= 0 for v in d.values()) and d['exec'] > 0:
                        delta = d
                else:
                    # 缓存里没有：只有能证明它是上一轮之后才出现的，才把当前值整体当增量
                    last_now = prev_now.get(r['set_name'])
                    fs = r.get('first_seen')
                    if last_now is not None and fs is not None and cur_c['exec'] > 0:
                        try:
                            if fs > last_now:
                                delta = dict(cur_c)
                        except TypeError:
                            delta = None
                prev[key] = cur_c
                touched[key] = self._seq
                seen_sets.add(r['set_name'])
                if delta is not None:
                    out.append(_delta_row(r, delta))
            for s in seen_sets:
                sn = next((r.get('server_now') for r in rows
                           if r['set_name'] == s and r.get('server_now') is not None), None)
                if sn is not None:
                    prev_now[s] = sn
            if len(prev) > self.MAX_KEYS_PER_CONFIG:
                # 淘汰最久未出现的一半
                victims = sorted(touched.items(), key=lambda kv: kv[1])[:len(prev) // 2]
                for k, _ in victims:
                    prev.pop(k, None)
                    touched.pop(k, None)
        return out


def _ms(ps):
    return int(round(ps / _PS_PER_MS))


def _delta_row(r, d):
    return {
        'sql_digest': r['digest'][:32],
        'set_name': r['set_name'],
        'db_name': r.get('schema_name'),
        'exec_delta': d['exec'],
        'elapsed_ms_delta': _ms(d['elapsed_ps']),
        'lock_ms_delta': _ms(d['lock_ps']),
        'cpu_ms_delta': _ms(d['cpu_ps']),
        'rows_delta': d['rows_sent'],
        'reads_delta': d['rows_examined'],
        'tmp_tables_delta': d['tmp_tables'],
        'tmp_disk_delta': d['tmp_disk'],
        'sort_merge_delta': d['sort_merge'],
        'sort_rows_delta': d['sort_rows'],
        'scan_delta': d['scan'],
        'full_join_delta': d['full_join'],
        'no_index_delta': d['no_index'],
        'errors_delta': d['errors'],
        'sql_text_sample': (r.get('digest_text') or '')[:2000] or None,
        'sql_example': (r.get('sample_text') or '')[:2000] or None,
        '_elapsed_ps': d['elapsed_ps'],
    }


TRACKER = DeltaTracker()


def top_per_set(rows, topn):
    """每个 set 内按本轮耗时增量取 Top N（修 T-12：排序依据是本轮增量而非历史累计）。"""
    by_set = {}
    for r in rows:
        by_set.setdefault(r['set_name'], []).append(r)
    out = []
    for _, lst in by_set.items():
        lst.sort(key=lambda x: x['_elapsed_ps'], reverse=True)
        out.extend(lst[:topn])
    for r in out:
        r.pop('_elapsed_ps', None)
    return out


def collect(cur, config, data=None, tracker=None):
    """一轮完整采集：取数 → 差分 → 每 set Top N。返回 (delta_rows, meta)。"""
    from monitor import appconf
    tracker = tracker or TRACKER
    data = data or {}
    sets = [s.get('set_name') for s in (data.get('tdsql_sets') or []) if isinstance(s, dict)]
    rows, meta = fetch_digest_rows(
        cur, config, tdsql_sets=sets, tdsql_mode=data.get('tdsql_mode'),
        window_sec=appconf.get('SQLSTAT_ACTIVE_WINDOW_SEC'),
        limit=appconf.get('SQLSTAT_FETCH_LIMIT'))
    deltas = tracker.compute(config.id, rows)
    return top_per_set(deltas, appconf.get('SQLSTAT_TOPN')), meta
