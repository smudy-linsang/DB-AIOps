# -*- coding: utf-8 -*-
"""sql_stat 读取侧：窗口聚合、按 set 拆分、按小时序列。见 TDSQL_SLOWSQL_DESIGN.md §4、§5、§7。

采集侧按 (set, digest) 分行存储增量；**跨 set 求和只发生在这里**。
所有函数都接收一个已借出的时序库游标（``get_timeseries_storage().cursor()``，BUG-101），
本模块不自己持有连接。
"""
import json
import logging

logger = logging.getLogger(__name__)

# 聚合字段：(输出键, sql_stat 列)
AGG_FIELDS = (
    ('exec', 'exec_delta'),
    ('elapsed_ms', 'elapsed_ms_delta'),
    ('lock_ms', 'lock_ms_delta'),
    ('cpu_ms', 'cpu_ms_delta'),
    ('rows_sent', 'rows_delta'),
    ('rows_examined', 'reads_delta'),
    ('tmp_tables', 'tmp_tables_delta'),
    ('tmp_disk', 'tmp_disk_delta'),
    ('sort_merge', 'sort_merge_delta'),
    ('sort_rows', 'sort_rows_delta'),
    ('scan', 'scan_delta'),
    ('full_join', 'full_join_delta'),
    ('no_index', 'no_index_delta'),
    ('errors', 'errors_delta'),
)

_SUMS = ", ".join(f"COALESCE(SUM({col}), 0)" for _, col in AGG_FIELDS)


def _to_agg(vals):
    return {k: int(v or 0) for (k, _), v in zip(AGG_FIELDS, vals)}


def window_by_digest(cur, config_id, fd, td, limit=200, digests=None):
    """窗口内按 digest 聚合（跨 set 求和），按总耗时降序。

    返回 {digest: agg}，agg 额外含 set_count / db_name / last_at。保持插入顺序 = 排序。
    """
    params = [config_id, fd, td]
    extra = ''
    if digests:
        extra = ' AND sql_digest = ANY(%s)'
        params.append(list(digests))
    params.append(int(limit))
    cur.execute(
        f"SELECT sql_digest, {_SUMS}, COUNT(DISTINCT set_name), MAX(db_name), MAX(time) "
        "FROM sql_stat WHERE db_config_id=%s AND time >= %s AND time < %s" + extra +
        " GROUP BY sql_digest ORDER BY 3 DESC LIMIT %s", params)
    out = {}
    n = len(AGG_FIELDS)
    for row in cur.fetchall():
        agg = _to_agg(row[1:1 + n])
        agg['set_count'] = int(row[1 + n] or 0)
        agg['db_name'] = row[2 + n]
        agg['last_at'] = row[3 + n]
        out[row[0]] = agg
    return out


def window_by_set(cur, config_id, fd, td, digests):
    """同一批 digest 按 set 拆分。返回 {digest: {set_name: agg}}。"""
    if not digests:
        return {}
    cur.execute(
        f"SELECT sql_digest, COALESCE(set_name, ''), {_SUMS} FROM sql_stat "
        "WHERE db_config_id=%s AND time >= %s AND time < %s AND sql_digest = ANY(%s) "
        "GROUP BY 1, 2", (config_id, fd, td, list(digests)))
    out = {}
    for row in cur.fetchall():
        out.setdefault(row[0], {})[row[1]] = _to_agg(row[2:])
    return out


def texts_for(cur, config_id, digests, since):
    """每个 digest 最近一条 digest 文本与带字面量样例。返回 {digest: (text, example)}。"""
    if not digests:
        return {}
    cur.execute(
        "SELECT DISTINCT ON (sql_digest) sql_digest, sql_text_sample, sql_example "
        "FROM sql_stat WHERE db_config_id=%s AND sql_digest = ANY(%s) AND time >= %s "
        "AND (sql_text_sample IS NOT NULL OR sql_example IS NOT NULL) "
        "ORDER BY sql_digest, time DESC", (config_id, list(digests), since))
    return {r[0]: (r[1], r[2]) for r in cur.fetchall()}


def hourly_series(cur, config_id, fd, td, digests):
    """按小时聚合执行次数与耗时。返回 {digest: [(hour_start, exec, elapsed_ms), ...]}。"""
    if not digests:
        return {}
    cur.execute(
        "SELECT sql_digest, time_bucket('1 hour', time) AS h, "
        "COALESCE(SUM(exec_delta), 0), COALESCE(SUM(elapsed_ms_delta), 0) FROM sql_stat "
        "WHERE db_config_id=%s AND time >= %s AND time < %s AND sql_digest = ANY(%s) "
        "GROUP BY 1, 2 ORDER BY 1, 2", (config_id, fd, td, list(digests)))
    out = {}
    for d, h, ex, ms in cur.fetchall():
        out.setdefault(d, []).append((h, int(ex or 0), int(ms or 0)))
    return out


def latest_collect_data(config):
    """最近一轮成功采集的结果（MonitorLog.message 存的是 JSON）。取不到返回 {}。"""
    from monitor.models import MonitorLog
    log = (MonitorLog.objects.filter(config=config, status='UP')
           .order_by('-create_time').only('message').first())
    if log is None:
        return {}
    try:
        data = json.loads(log.message or '{}')
        return data if isinstance(data, dict) else {}
    except (TypeError, ValueError) as e:
        from monitor import degrade
        degrade.note('sqlstat.latest_collect_data', 'MonitorLog.message 不是合法 JSON', e)
        return {}


def basis_warning(config, set_names, data=None):
    """读取侧的数据可信度判断：groupshard 但数据没有按 set 区分 → 不可信。"""
    if getattr(config, 'db_type', None) != 'tdsql':
        return None
    data = data if data is not None else latest_collect_data(config)
    if data.get('tdsql_mode') == 'groupshard' and not any(s for s in set_names):
        return ('该分布式实例的 SQL 统计没有按 set 采集（透传不可用），数据只来自 proxy '
                '路由到的某一个 set，执行次数、耗时与环比结论都不可信。请按 TDSQL_SLOWSQL_DESIGN.md '
                '§8 的 V-1 ~ V-5 检查透传配置。')
    return None
