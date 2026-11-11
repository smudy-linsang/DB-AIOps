# -*- coding: utf-8 -*-
"""DB-023：010B 下半 34 项巡检检测器。

只实现任务单列出的缺实现项。不改可复用项，不挂
``INS-ORA-OPT-STATS-GATHER`` 与 ``INS-MYSQL-BINLOG-DELAY``。

约定：
- 方言不匹配时不连接目标库，直接 ``skip``。
- 形态不适用（非 RAC / 非 Data Guard / 非 Exadata / 非 DSC / 非 DataWatch /
  主库没有 WAL receiver 等）也是 ``skip``，原因与方言 skip 分开写。
- 查不到清单、查询抛错，状态是 ``error``。不得把空结果写成健康或「没有宕机」。
- 注册表阈值的 ``error`` 档不能占用结果状态 ``error``（那个状态表示检测过程失败）。
  阈值三档记在 ``metrics['threshold_band']``：``warn`` / ``error`` / ``critical``。
  结果状态只把 ``critical`` 档写成 ``critical``，``warn`` 与 ``error`` 档写成 ``warning``。
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from typing import TYPE_CHECKING

from monitor.degrade import note as degrade_note

if TYPE_CHECKING:
    from monitor.inspection_executor import DetectionContext, DetectionResult

_ORACLE = ('oracle',)
_MYSQL = ('mysql', 'tdsql')
_PG = ('pgsql',)
_DM = ('dm',)
_GBASE = ('gbase',)

_DEFAULT_DIRECTORIES = {
    'DATA_PUMP_DIR', 'XMLDIR', 'ORACLE_OCM_CONFIG_DIR', 'OPATCH_INST_DIR',
    'OPATCH_LOG_DIR', 'OPATCH_SCRIPT_DIR', 'ORACLE_BASE', 'ORACLE_HOME',
    'XSDDIR', 'SDO_DIR_ADMIN', 'SDO_DIR_WORK', 'DBMS_OPTIM_LOGDIR',
    'JAVA$JOX$CUJS$DIRECTORY$',
}

_INTERVAL_RE = re.compile(
    r'^[+-]?(?P<d>\d+)\s+(?P<h>\d+):(?P<m>\d+):(?P<s>\d+(?:\.\d+)?)$'
)
_LINK_RE_CACHE = {}

SQL_DM_ARCH_STATUS = (
    "SELECT ARCH_TYPE, ARCH_DEST, ARCH_STATUS, ARCH_SRC FROM V$ARCH_STATUS"
)
SQL_FREEZE = """
SELECT n.nspname AS schema,
       c.relname AS name,
       age(c.relfrozenxid) AS freeze_age
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind = 'r'
  AND c.relfrozenxid <> 0
  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
ORDER BY freeze_age DESC
LIMIT 50
"""
SQL_BLOAT = """
SELECT schema, name, kind, bloat_pct
FROM (
    SELECT n.nspname AS schema,
           c.relname AS name,
           CASE c.relkind WHEN 'r' THEN 'table' WHEN 'i' THEN 'index'
                ELSE c.relkind END AS kind,
           CASE
               WHEN c.relpages = 0 OR c.reltuples <= 0 THEN 0
               ELSE ROUND((
                   100.0 * GREATEST(
                       c.relpages * current_setting('block_size')::numeric
                       - c.reltuples * COALESCE((
                           SELECT SUM(s.avg_width)
                           FROM pg_stats s
                           WHERE s.schemaname = n.nspname
                             AND s.tablename = CASE
                                 WHEN c.relkind = 'i' THEN t.relname
                                 ELSE c.relname END
                       ), 100),
                       0
                   ) / NULLIF(c.relpages * current_setting('block_size')::numeric, 0)
               )::numeric, 1)
           END AS bloat_pct
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    LEFT JOIN pg_index ix ON c.relkind = 'i' AND ix.indexrelid = c.oid
    LEFT JOIN pg_class t ON t.oid = ix.indrelid
    WHERE c.relkind IN ('r', 'i')
      AND n.nspname NOT IN ('pg_catalog', 'information_schema')
      AND c.relpages > 0
) est
WHERE bloat_pct > 50
"""


class NotApplicable(Exception):
    """当前实例的形态不在本项范围内。不是查询失败。"""


def parse_oracle_interval(value):
    """把 Data Guard 的 ``+DD HH:MI:SS`` 解析成秒。

    不对其直接 ``int()``：库里的 apply lag 通常是间隔字符串。
    """
    if value is None:
        return None
    if hasattr(value, 'total_seconds') and not isinstance(value, str):
        return float(value.total_seconds())
    if isinstance(value, bool):
        raise ValueError(f'无法解析 Data Guard 时间间隔: {value!r}')
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    match = _INTERVAL_RE.match(text)
    if match:
        seconds = (
            int(match.group('d')) * 86400
            + int(match.group('h')) * 3600
            + int(match.group('m')) * 60
            + float(match.group('s'))
        )
        return -seconds if text.startswith('-') else seconds
    if re.fullmatch(r'[+-]?\d+(?:\.\d+)?', text):
        return float(text)
    raise ValueError(f'无法解析 Data Guard 时间间隔: {text}')


def gtid_missing_count(retrieved, executed):
    """已取到但尚未执行的事务个数。按区间做差，不把区间展开成整数集合。"""
    got = _parse_gtid_set(retrieved)
    done = _parse_gtid_set(executed)
    missing = 0
    for uuid, intervals in got.items():
        missing += _interval_length(_subtract_intervals(intervals, done.get(uuid, [])))
    return missing


def _parse_gtid_set(text):
    result = {}
    if not text:
        return result
    for part in str(text).split(','):
        part = part.strip()
        if not part or ':' not in part:
            continue
        uuid, _, rest = part.partition(':')
        intervals = result.setdefault(uuid.strip().lower(), [])
        for piece in rest.split(':'):
            piece = piece.strip()
            if not piece:
                continue
            if '-' in piece:
                start, end = piece.split('-', 1)
                intervals.append((int(start), int(end)))
            else:
                num = int(piece)
                intervals.append((num, num))
    return result


def _subtract_intervals(source, remove):
    chunks = list(source)
    for start, end in remove:
        nxt = []
        for left, right in chunks:
            if end < left or start > right:
                nxt.append((left, right))
                continue
            if left < start:
                nxt.append((left, start - 1))
            if right > end:
                nxt.append((end + 1, right))
        chunks = nxt
    return [(left, right) for left, right in chunks if left <= right]


def _interval_length(intervals):
    return sum(right - left + 1 for left, right in intervals)


def _num(value, default=None):
    if value is None or value == '':
        return default
    if isinstance(value, bool):
        return float(int(value))
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _colnames(cur, width):
    desc = getattr(cur, 'description', None) or []
    names = []
    for col in desc:
        if isinstance(col, str):
            names.append(col.lower())
        elif col:
            names.append(str(col[0]).lower())
    if len(names) < width:
        names.extend(f'c{i}' for i in range(len(names), width))
    return names


def _fetchall(cur):
    rows = cur.fetchall() or []
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return [{str(k).lower(): v for k, v in row.items()} for row in rows]
    width = len(rows[0])
    cols = _colnames(cur, width)
    return [dict(zip(cols, row)) for row in rows]


def _query(cur, sql):
    cur.execute(sql)
    return _fetchall(cur)


def _missing_view(exc):
    text = str(exc).lower()
    return (
        'ora-00942' in text
        or 'does not exist' in text
        or '不存在' in text
        or '-2106' in text
        or '无效的表或视图' in text
    )


def _threshold(ctx, item_id):
    item = ctx.item or {}
    if item.get('threshold'):
        return item['threshold']
    from monitor.inspection_registry import ALL_ITEMS
    for it in ALL_ITEMS:
        if it.get('item_id') == item_id:
            return it.get('threshold') or {}
    return {}


def _set_band(result, band, summary, finding_type):
    result.metrics['threshold_band'] = band
    result.summary = summary
    if band == 'ok':
        result.status = 'ok'
        result.severity = 'info'
        return
    if band == 'critical':
        result.status = 'critical'
        result.severity = 'critical'
    else:
        result.status = 'warning'
        result.severity = 'error' if band == 'error' else 'warning'
    result.findings.append({
        'type': finding_type,
        'band': band,
        'message': summary,
    })


def _apply_high(result, value, threshold, summary, finding_type, metric_key):
    value = float(value)
    result.metrics[metric_key] = value
    if threshold.get('critical') is not None and value >= float(threshold['critical']):
        band = 'critical'
    elif threshold.get('error') is not None and value >= float(threshold['error']):
        band = 'error'
    elif threshold.get('warn') is not None and value >= float(threshold['warn']):
        band = 'warn'
    else:
        band = 'ok'
    _set_band(result, band, summary, finding_type)


def _apply_low(result, value, threshold, summary, finding_type, metric_key):
    """数值越小越差。临界值本身算越界；等于 warn 下限视为达标。"""
    value = float(value)
    result.metrics[metric_key] = value
    crit = threshold.get('critical')
    err = threshold.get('error')
    warn = threshold.get('warn')
    if crit is not None and value <= float(crit):
        band = 'critical'
    elif err is not None and value <= float(err):
        band = 'error'
    elif warn is not None and value < float(warn):
        band = 'warn'
    else:
        band = 'ok'
    _set_band(result, band, summary, finding_type)


def _close_quiet(ctx, cur, method, created):
    if cur is not None:
        try:
            cur.close()
        except Exception as exc:
            degrade_note(f'inspection.{method}.cursor_close', '关闭游标失败', exc)
    try:
        if ctx.db_connector is not None:
            ctx.db_connector.close()
    except Exception as exc:
        degrade_note(f'inspection.{method}.close', '关闭连接失败', exc)
    if created:
        ctx.db_connector = None


@contextmanager
def _cursor(ctx, method):
    from monitor.db_connector import get_connector

    created = ctx.db_connector is None
    if created:
        ctx.db_connector = get_connector(ctx.db_config)
    cur = None
    try:
        conn = ctx.db_connector.connect()
        cur = conn.cursor()
    except Exception:
        _close_quiet(ctx, cur, method, created)
        raise
    try:
        yield cur
    finally:
        _close_quiet(ctx, cur, method, created)


def _run(ctx, item_id, title, method, allowed, fn):
    from monitor.inspection_executor import DetectionResult

    item = ctx.item or {}
    result = DetectionResult(
        item_id=item.get('item_id', item_id),
        item_title=item.get('title', title),
        detection_method=method,
    )
    if (ctx.db_type or '') not in allowed:
        shown = ctx.db_type or '未知'
        result.status = 'skip'
        result.summary = f"不适用当前方言: {shown} (仅支持 {', '.join(allowed)})"
        return result
    try:
        with _cursor(ctx, method) as cur:
            fn(ctx, result, cur)
    except NotApplicable as exc:
        result.status = 'skip'
        result.summary = str(exc)
        result.error = None
        result.findings = []
    except AssertionError:
        raise
    except Exception as exc:
        degrade_note(f'inspection.{method}', '检测查询失败', exc)
        result.status = 'error'
        result.error = f'{type(exc).__name__}: {exc}'
        result.summary = f'检测失败: {exc}'
    return result


def _show_map(rows):
    """SHOW 语句两列结果。兼容 DictCursor 和没有列名的元组游标。"""
    found = {}
    for row in rows:
        name = row.get('variable_name')
        value = row.get('value')
        if name is None and ('c0' in row or 'variable_name' not in row):
            name = row.get('c0', row.get('variable_name'))
            value = row.get('c1', row.get('value'))
        if name is not None:
            found[str(name).lower()] = value
    return found


def _cell(row, *names):
    for name in names:
        if name in row and row[name] is not None:
            return row[name]
    for key, value in row.items():
        for name in names:
            if name in key and value is not None:
                return value
    return None


def _truthy_on(value):
    return str(value or '').strip().upper() in ('ON', '1', 'TRUE', 'YES', 'Y')


def _healthy_token(value):
    text = str(value or '').strip().upper()
    return any(token in text for token in ('ONLINE', 'ACTIVE', 'HEALTHY', 'NORMAL', 'VALID', 'OPEN', 'OK', 'ENABLE'))


def _link_used(name, texts):
    short = str(name or '').split('.')[0].upper()
    if not short:
        return False
    pattern = _LINK_RE_CACHE.get(short)
    if pattern is None:
        pattern = re.compile(r'@' + re.escape(short) + r'(?:[^A-Z0-9_]|$)', re.I)
        _LINK_RE_CACHE[short] = pattern
    full = '@' + str(name).upper()
    for text in texts:
        upper = str(text or '').upper()
        if full in upper or pattern.search(upper):
            return True
    return False


def _listener_host(value):
    match = re.search(r'HOST\s*=\s*([^)\s]+)', str(value or ''), re.I)
    return match.group(1).strip().lower() if match else ''


def _spread_pct(values):
    nums = [num for num in (_num(v) for v in values) if num is not None]
    if len(nums) < 2:
        return None
    peak = max(nums)
    floor = min(nums)
    if peak <= 0:
        return 0.0
    return (peak - floor) / peak * 100.0


def _dg_configured(role, dests):
    if 'STANDBY' in str(role or '').upper():
        return True
    return any(str(row.get('target') or '').upper() == 'STANDBY' for row in dests)


def _lob_pct(ctx, result, cur):
    rows = _query(cur, """
        SELECT owner, table_name, column_name, pctversion
        FROM dba_lobs
        WHERE pctversion > 10
          AND owner NOT IN (
              'SYS','SYSTEM','XDB','CTXSYS','MDSYS','OLAPSYS','WMSYS',
              'ORDSYS','ORDDATA','DVSYS','LBACSYS','GSMADMIN_INTERNAL'
          )
    """)
    bad = [row for row in rows if (_num(row.get('pctversion')) or 0) > 10]
    th = _threshold(ctx, 'INS-ORA-LOB-PCT')
    _apply_high(
        result, len(bad), th,
        f'PCTVERSION>10 的 LOB {len(bad)} 个',
        'lob_pctversion', 'lob_pctversion_count',
    )
    result.metrics['samples'] = bad[:20]


def _dblinks(ctx, result, cur):
    links = _query(cur, "SELECT owner, db_link, username, host FROM dba_db_links")
    texts = _query(cur, """
        SELECT sql_text FROM v$sql
        WHERE UPPER(sql_text) LIKE '%@%' AND ROWNUM <= 5000
    """)
    used_text = [row.get('sql_text') for row in texts]
    unused = []
    for link in links:
        name = link.get('db_link') or ''
        if not _link_used(name, used_text):
            unused.append(link)
    th = _threshold(ctx, 'INS-ORA-DBLINK')
    _apply_high(
        result, len(unused), th,
        f'库缓存样本中未见引用的 DBLINK {len(unused)} 个（样本上限 5000 条 SQL）',
        'dblink', 'unused_dblinks',
    )
    result.metrics['samples'] = unused[:20]


def _directories(ctx, result, cur):
    rows = _query(cur, "SELECT owner, directory_name, directory_path FROM dba_directories")
    abnormal = []
    for row in rows:
        name = str(row.get('directory_name') or '').upper()
        path = str(row.get('directory_path') or '')
        suspicious = (
            '..' in path
            or path.startswith(('/tmp', '/var/tmp', '/dev', '/etc', '/root', '/proc'))
        )
        if name not in _DEFAULT_DIRECTORIES or suspicious:
            abnormal.append(row)
    th = _threshold(ctx, 'INS-ORA-DIR-OBJ')
    _apply_high(
        result, len(abnormal), th,
        f'非默认或路径可疑的 Directory {len(abnormal)} 个',
        'directory', 'abnormal_directories',
    )
    result.metrics['samples'] = abnormal[:20]


def _rman_age(ctx, result, cur):
    rows = _query(cur, """
        SELECT (SYSDATE - MAX(end_time)) * 24 AS age_hours
        FROM v$rman_backup_job_details
        WHERE input_type = 'DB FULL' AND status LIKE 'COMPLETED%'
    """)
    age = _num(rows[0].get('age_hours')) if rows else None
    if age is None:
        result.metrics['age_hours'] = None
        _set_band(
            result, 'critical',
            '无已完成的 RMAN 全库备份',
            'rman_backup_age',
        )
        return
    th = _threshold(ctx, 'INS-ORA-RMAN-AGE')
    _apply_high(
        result, age, th,
        f'最近一次 RMAN 全库备份距今 {age:.1f} 小时',
        'rman_backup_age', 'age_hours',
    )


def _dg_lag(ctx, result, cur):
    role_rows = _query(cur, "SELECT database_role FROM v$database")
    dests = _query(cur, """
        SELECT dest_id, target, status FROM v$archive_dest_status
    """)
    role = role_rows[0].get('database_role') if role_rows else ''
    if not _dg_configured(role, dests):
        raise NotApplicable('未配置 Data Guard，不适用')
    lags = _query(cur, """
        SELECT name, value, unit FROM v$dataguard_stats
        WHERE LOWER(name) = 'apply lag'
    """)
    if not lags:
        raise RuntimeError('已配置 Data Guard，但 v$dataguard_stats 没有 apply lag')
    seconds = parse_oracle_interval(lags[0].get('value'))
    if seconds is None:
        raise RuntimeError('apply lag 值为空，不能当成 0 秒')
    th = _threshold(ctx, 'INS-ORA-DG-LAG')
    _apply_high(
        result, seconds, th,
        f'Data Guard apply lag {seconds:.0f} 秒',
        'dg_lag', 'lag_sec',
    )


def _dg_status(ctx, result, cur):
    role_rows = _query(cur, "SELECT database_role, protection_mode, switchover_status FROM v$database")
    dests = _query(cur, """
        SELECT dest_id, status, error AS dest_error, target
        FROM v$archive_dest_status
    """)
    role = role_rows[0].get('database_role') if role_rows else ''
    if not _dg_configured(role, dests):
        raise NotApplicable('未配置 Data Guard，不适用')
    processes = _query(cur, "SELECT process, status FROM v$managed_standby")
    grade = 0
    reasons = []
    standby_dests = [d for d in dests if str(d.get('target') or '').upper() == 'STANDBY']
    for dest in standby_dests:
        status = str(dest.get('status') or '').upper()
        error = str(dest.get('dest_error') or '').strip()
        if error or 'ERROR' in status:
            grade = max(grade, 3)
            reasons.append(f"备库目的地 {dest.get('dest_id')} 错误: {error or status}")
        elif status in ('INACTIVE', 'DEFERRED', 'DISABLED'):
            grade = max(grade, 2)
            reasons.append(f"备库目的地 {dest.get('dest_id')} 状态 {status}")
        elif status not in ('VALID', ''):
            grade = max(grade, 1)
            reasons.append(f"备库目的地 {dest.get('dest_id')} 状态 {status}")
    if 'STANDBY' in str(role or '').upper():
        mrp = [p for p in processes if str(p.get('process') or '').upper().startswith('MRP')]
        if not mrp:
            grade = 3
            reasons.append('物理备库没有 MRP 进程')
        else:
            mrp_status = str(mrp[0].get('status') or '').upper()
            if mrp_status == 'WAIT_FOR_LOG':
                grade = max(grade, 1)
                reasons.append('MRP 正在等待日志')
            elif mrp_status != 'APPLYING_LOG':
                grade = max(grade, 2)
                reasons.append(f'MRP 状态 {mrp_status}')
    th = _threshold(ctx, 'INS-ORA-DG-STATUS')
    summary = '；'.join(reasons) if reasons else 'Data Guard 状态正常'
    _apply_high(result, grade, th, summary, 'dg_status', 'grade')
    result.metrics['database_role'] = role


def _rac_vip(ctx, result, cur):
    counts = _query(cur, "SELECT COUNT(*) AS cnt FROM gv$instance")
    count = int(_num(counts[0].get('cnt'), 0) or 0) if counts else 0
    if count <= 1:
        raise NotApplicable('非 RAC（gv$instance 不足 2 个实例），VIP 漂移不适用')
    rows = _query(cur, """
        SELECT i.inst_id, i.instance_name, i.host_name, i.status,
               p.value AS local_listener
        FROM gv$instance i
        LEFT JOIN gv$parameter p
          ON p.inst_id = i.inst_id AND p.name = 'local_listener'
    """)
    hosts = []
    missing = []
    for row in rows:
        host = _listener_host(row.get('local_listener'))
        status = str(row.get('status') or '').upper()
        if host:
            hosts.append(host)
        elif status == 'OPEN':
            missing.append(row.get('instance_name') or row.get('inst_id'))
    grade = 0
    reasons = []
    if len(hosts) != len(set(hosts)):
        grade = 3
        reasons.append(f'多个实例的 local_listener 指向同一 HOST: {sorted(set(hosts))}')
    if missing:
        grade = max(grade, 1)
        reasons.append(f'OPEN 实例缺少 local_listener: {missing}')
    th = _threshold(ctx, 'INS-ORA-RAC-VIP')
    summary = '；'.join(reasons) if reasons else 'RAC 实例 VIP（local_listener HOST）互不重复'
    _apply_high(result, grade, th, summary, 'rac_vip', 'grade')
    result.metrics['instances'] = len(rows)


def _redolog_size(ctx, result, cur):
    rows = _query(cur, "SELECT group# AS log_group, bytes FROM v$log")
    sizes = [_num(row.get('bytes')) for row in rows]
    sizes = [size for size in sizes if size is not None]
    if not sizes:
        raise RuntimeError('v$log 没有日志组，不能判断 redo 大小')
    min_mb = min(sizes) / 1024 / 1024
    th = _threshold(ctx, 'INS-ORA-REDOLOG-SIZE')
    _apply_low(
        result, min_mb, th,
        f'最小 redo 日志 {min_mb:.0f} MB（建议不低于 1024 MB）',
        'redolog_size', 'min_mb',
    )


def _open_files(ctx, result, cur):
    limited = _query(cur, """
        SELECT current_utilization
        FROM v$resource_limit
        WHERE LOWER(resource_name) IN ('files', 'open_files')
    """)
    util = _num(limited[0].get('current_utilization')) if limited else None
    source = 'v$resource_limit'
    if util is None:
        summed = _query(cur, """
            SELECT (SELECT COUNT(*) FROM v$datafile)
                 + (SELECT COUNT(*) FROM v$tempfile)
                 + (SELECT COUNT(*) FROM v$logfile)
                 + (SELECT COUNT(*) FROM v$controlfile) AS open_files
            FROM dual
        """)
        util = _num(summed[0].get('open_files'), 0) if summed else 0
        source = 'datafile+tempfile+logfile+controlfile'
    th = _threshold(ctx, 'INS-ORA-OPEN-FILE')
    _apply_high(
        result, util, th,
        f'打开文件数 {util:.0f}（来源 {source}）',
        'open_files', 'open_files',
    )
    result.metrics['source'] = source


def _lob_cache(ctx, result, cur):
    rows = _query(cur, """
        SELECT name, value FROM v$sysstat
        WHERE name IN ('lob reads', 'physical reads direct (lob)')
    """)
    stats = {str(row.get('name') or '').lower(): _num(row.get('value'), 0) for row in rows}
    reads = stats.get('lob reads', 0) or 0
    physical = stats.get('physical reads direct (lob)', 0) or 0
    result.metrics['lob_reads'] = reads
    result.metrics['lob_physical_reads'] = physical
    if reads <= 0:
        result.status = 'ok'
        result.summary = '无 LOB 读取，命中率没有样本'
        result.metrics['threshold_band'] = 'ok'
        result.metrics['hit_pct'] = None
        return
    hit = max(0.0, (1 - physical / reads) * 100)
    th = _threshold(ctx, 'INS-ORA-LOB-CACHE')
    _apply_low(
        result, hit, th,
        f'LOB Cache 命中率 {hit:.1f}%',
        'lob_cache', 'hit_pct',
    )


def _ash_memory(ctx, result, cur):
    rows = _query(cur, """
        SELECT bytes FROM v$sgastat WHERE name = 'ASH buffers'
    """)
    ash_bytes = _num(rows[0].get('bytes')) if rows else None
    if ash_bytes is None:
        raise RuntimeError('v$sgastat 没有 ASH buffers，不能用平台会话采样代替')
    sized = _query(cur, """
        SELECT b.ksppstvl AS value
        FROM x$ksppi a, x$ksppcv b
        WHERE a.indx = b.indx AND a.ksppinm = '_ash_size'
    """)
    ash_size = _num(sized[0].get('value')) if sized else None
    if not ash_size:
        raise RuntimeError('读不到 _ash_size，不能计算 ASH 使用率')
    pct = ash_bytes / ash_size * 100
    th = _threshold(ctx, 'INS-ORA-ASH-USAGE')
    _apply_high(
        result, pct, th,
        f'ASH buffers 占 _ash_size 的 {pct:.1f}%',
        'ash_memory', 'ash_pct',
    )
    result.metrics['ash_bytes'] = ash_bytes
    result.metrics['ash_size'] = ash_size


def _rman_problems(rows):
    cfg = {}
    for row in rows:
        cfg[str(row.get('name') or '').upper()] = str(row.get('value') or '').upper()
    problems = []
    retention = cfg.get('RETENTION POLICY', '')
    if retention and (
        'NONE' in retention
        or re.search(r'REDUNDANCY\s+0\b', retention)
        or re.search(r'WINDOW OF 0\b', retention)
    ):
        problems.append('保留策略为 NONE 或 0')
    if cfg.get('BACKUP OPTIMIZATION', 'OFF') != 'ON':
        problems.append('BACKUP OPTIMIZATION 未开启')
    compression = cfg.get('COMPRESSION ALGORITHM', '')
    if not any(token in compression for token in ('BASIC', 'LOW', 'MEDIUM', 'HIGH')):
        problems.append('未配置压缩算法')
    device = cfg.get('DEFAULT DEVICE TYPE', 'DISK')
    if device not in ('DISK', 'SBT_TAPE', 'SBT'):
        problems.append(f'备份设备类型异常: {device or "空"}')
    for name, value in cfg.items():
        if 'EXCLUDE' in name and any(token in value for token in ('SYSTEM', 'UNDOTBS', 'SYSAUX')):
            problems.append('RMAN 排除了系统表空间')
            break
    return problems


def _rman_config(ctx, result, cur):
    rows = _query(cur, "SELECT name, value FROM v$rman_configuration")
    problems = _rman_problems(rows)
    th = _threshold(ctx, 'INS-ORA-RMAN-CONF')
    summary = '；'.join(problems) if problems else 'RMAN 保留策略与优化配置未见列出的问题'
    _apply_high(result, len(problems), th, summary, 'rman_config', 'problem_count')
    result.metrics['problems'] = problems


def _audit_trail(ctx, result, cur):
    rows = _query(cur, "SELECT value FROM v$parameter WHERE name = 'audit_trail'")
    unified_rows = _query(cur, "SELECT value FROM v$option WHERE parameter = 'Unified Auditing'")
    trail = str((rows[0].get('value') if rows else '') or '').upper().replace(' ', '')
    unified = _truthy_on(unified_rows[0].get('value') if unified_rows else '')
    if trail in ('DB', 'DB,EXTENDED', 'XML', 'XML,EXTENDED') or unified:
        grade = 0
        summary = f'审计已启用（audit_trail={trail or "空"}, unified={unified}）'
    elif trail == 'OS':
        grade = 1
        summary = '审计写在 OS，不是建议的 audit_trail=DB'
    elif trail in ('NONE', ''):
        grade = 3
        summary = '审计关闭（audit_trail=NONE，且统一审计未开）'
    else:
        grade = 1
        summary = f'审计参数未识别: {trail}'
    th = _threshold(ctx, 'INS-ORA-AUDIT-TRAIL')
    _apply_high(result, grade, th, summary, 'audit_trail', 'grade')
    result.metrics['audit_trail'] = trail
    result.metrics['unified_auditing'] = unified


def _pga_leak(ctx, result, cur):
    rows = _query(cur, """
        SELECT name, value FROM v$pgastat
        WHERE name IN ('total PGA allocated', 'aggregate PGA target parameter')
    """)
    stats = {str(row.get('name') or '').lower(): _num(row.get('value')) for row in rows}
    allocated = stats.get('total pga allocated')
    target = stats.get('aggregate pga target parameter')
    if not target:
        mem_rows = _query(cur, "SELECT value FROM v$parameter WHERE name = 'memory_target'")
        memory_target = _num(mem_rows[0].get('value'), 0) if mem_rows else 0
        if memory_target and memory_target > 0:
            raise NotApplicable('PGA 由 memory_target 托管，无独立 pga_aggregate_target，不适用')
        raise RuntimeError('pga_aggregate_target 为空，不能计算分配比例')
    if allocated is None:
        raise RuntimeError('v$pgastat 没有 total PGA allocated')
    pct = allocated / target * 100
    th = _threshold(ctx, 'INS-ORA-PGA-LEAK')
    _apply_high(
        result, pct, th,
        f'PGA 已分配占 target 的 {pct:.1f}%',
        'pga_leak', 'pga_pct',
    )


def _io_distribution(ctx, result, cur):
    rows = _query(cur, """
        SELECT d.name AS file_name,
               NVL(f.phyrds, 0) AS phyrds,
               NVL(f.phywrts, 0) AS phywrts
        FROM v$filestat f
        JOIN v$datafile d ON d.file# = f.file#
    """)
    usage = []
    for row in rows:
        io = (_num(row.get('phyrds'), 0) or 0) + (_num(row.get('phywrts'), 0) or 0)
        usage.append((io, row.get('file_name')))
    total = sum(io for io, _name in usage)
    if total <= 0 or not usage:
        result.status = 'ok'
        result.summary = '没有数据文件物理 IO 样本'
        result.metrics['threshold_band'] = 'ok'
        result.metrics['hot_files'] = 0
        return
    average = total / len(usage)
    hot = [(io, name) for io, name in usage if io >= 2 * average and io > 0]
    th = _threshold(ctx, 'INS-ORA-IO-STAT')
    _apply_high(
        result, len(hot), th,
        f'IO 不低于均值 2 倍的数据文件 {len(hot)} 个',
        'io_distribution', 'hot_files',
    )
    result.metrics['samples'] = [
        {'file_name': name, 'io': io} for io, name in sorted(hot, reverse=True)[:10]
    ]


def _exadata(ctx, result, cur):
    try:
        cells = _query(cur, "SELECT cell_name, status FROM v$cell")
    except Exception as exc:
        if _missing_view(exc):
            raise NotApplicable('非 Exadata，不适用（不是特性关闭）')
        raise
    if not cells:
        raise NotApplicable('非 Exadata（v$cell 无存储单元），不是特性关闭')
    params = _query(cur, """
        SELECT name, value FROM v$parameter WHERE name = 'cell_offload_processing'
    """)
    offload = str(params[0].get('value') if params else '').upper()
    problems = []
    if offload in ('FALSE', 'OFF', '0'):
        problems.append('cell_offload_processing 关闭')
    offline = [
        row for row in cells
        if 'ONLINE' not in str(row.get('status') or '').upper()
    ]
    problems.extend(
        f"存储单元 {row.get('cell_name')} 状态 {row.get('status')}" for row in offline
    )
    th = _threshold(ctx, 'INS-ORA-EXADATA')
    summary = '；'.join(problems) if problems else 'Exadata cell 在线且 cell offload 开启'
    _apply_high(result, len(problems), th, summary, 'exadata', 'problem_count')
    result.metrics['cell_count'] = len(cells)
    result.metrics['cell_offload_processing'] = offload


def _gtid(ctx, result, cur):
    variables = _query(cur, "SHOW GLOBAL VARIABLES LIKE 'gtid%'")
    found = _show_map(variables)
    mode = str(found.get('gtid_mode') or '').upper()
    if mode in ('', 'OFF', 'OFF_PERMISSIVE'):
        raise NotApplicable(f'GTID 未开启（gtid_mode={mode or "空"}），不适用一致性检查')
    try:
        replicas = _query(cur, 'SHOW SLAVE STATUS')
    except Exception as first:
        degrade_note('inspection.detect_gtid_consistency.slave_status', 'SHOW SLAVE STATUS 失败，改试 REPLICA', first)
        replicas = _query(cur, 'SHOW REPLICA STATUS')
    if not replicas:
        raise NotApplicable('当前实例没有复制从库状态，无法对比主从 GTID 集合')
    row = replicas[0]
    retrieved = _cell(row, 'retrieved_gtid_set')
    executed = _cell(row, 'executed_gtid_set')
    auto_position = _cell(row, 'auto_position')
    missing = gtid_missing_count(retrieved, executed)
    note = ''
    if missing == 0 and str(auto_position) in ('0', '0.0', 'False', 'false'):
        missing = 1
        note = '；GTID 集合一致，但 Auto_Position 未开启'
    th = _threshold(ctx, 'INS-MYSQL-GTID')
    _apply_high(
        result, missing, th,
        f'已取到未执行的 GTID 事务 {missing} 个{note}',
        'gtid', 'missing_gtids',
    )
    result.metrics['gtid_mode'] = mode


def _semi_sync(ctx, result, cur):
    variables = _query(cur, "SHOW GLOBAL VARIABLES LIKE 'rpl_semi_sync%'")
    if not variables:
        raise NotApplicable('未安装半同步插件，不适用')
    enabled = any(
        _truthy_on(value) and 'enabled' in key
        for key, value in _show_map(variables).items()
    )
    if not enabled:
        raise NotApplicable('半同步未启用，不适用生效检查')
    status_rows = _query(cur, "SHOW GLOBAL STATUS LIKE 'Rpl_semi_sync%'")
    status_map = _show_map(status_rows)
    working = any(
        _truthy_on(value) and value is not None and 'status' in key
        for key, value in status_map.items()
    )
    no_tx = _num(status_map.get('rpl_semi_sync_master_no_tx'), 0) or 0
    if not working:
        grade = 3
        summary = '半同步已配置但状态不是 ON'
    elif no_tx > 0:
        grade = 1
        summary = f'半同步在线，但有 {no_tx:.0f} 个事务退化为异步'
    else:
        grade = 0
        summary = '半同步已生效'
    th = _threshold(ctx, 'INS-MYSQL-SEMI-SYNC')
    _apply_high(result, grade, th, summary, 'semi_sync', 'grade')


def _event_scheduler(ctx, result, cur):
    rows = _query(cur, "SHOW GLOBAL VARIABLES LIKE 'event_scheduler'")
    value = str(_show_map(rows).get('event_scheduler') or '').upper()
    counted = _query(cur, """
        SELECT COUNT(*) AS cnt FROM information_schema.events
        WHERE UPPER(status) = 'ENABLED'
    """)
    enabled_events = int(_num(counted[0].get('cnt'), 0) or 0) if counted else 0
    if value == 'ON':
        grade = 0
        summary = f'event_scheduler=ON，启用中的事件 {enabled_events} 个'
    elif value == 'DISABLED':
        grade = 3 if enabled_events else 2
        summary = f'event_scheduler=DISABLED，启用中的事件 {enabled_events} 个'
    elif value == 'OFF':
        grade = 3 if enabled_events else 1
        summary = f'event_scheduler=OFF，启用中的事件 {enabled_events} 个'
    else:
        grade = 1
        summary = f'event_scheduler 未识别: {value or "空"}'
    th = _threshold(ctx, 'INS-MYSQL-EVENT')
    _apply_high(result, grade, th, summary, 'event_scheduler', 'grade')
    result.metrics['enabled_events'] = enabled_events


def _dangerous_fk(ctx, result, cur):
    rows = _query(cur, """
        SELECT constraint_schema, table_name, constraint_name, delete_rule, update_rule
        FROM information_schema.referential_constraints
        WHERE UPPER(delete_rule) = 'CASCADE' OR UPPER(update_rule) = 'CASCADE'
    """)
    cascades = [
        row for row in rows
        if str(row.get('delete_rule') or '').upper() == 'CASCADE'
        or str(row.get('update_rule') or '').upper() == 'CASCADE'
    ]
    th = _threshold(ctx, 'INS-MYSQL-FK-CASCADE')
    _apply_high(
        result, len(cascades), th,
        f'ON DELETE/UPDATE CASCADE 外键 {len(cascades)} 个',
        'dangerous_fk', 'cascade_fks',
    )
    result.metrics['samples'] = cascades[:20]


def _tmp_disk(ctx, result, cur):
    rows = _query(cur, "SHOW GLOBAL STATUS LIKE 'Created_tmp%'")
    stats = {key: _num(value, 0) for key, value in _show_map(rows).items()}
    tables = stats.get('created_tmp_tables', 0) or 0
    disk = stats.get('created_tmp_disk_tables', 0) or 0
    if tables <= 0 and disk <= 0:
        result.status = 'ok'
        result.summary = '尚无内部临时表'
        result.metrics.update({'threshold_band': 'ok', 'tmp_disk_ratio': 0, 'created_tmp_tables': 0})
        return
    ratio = (disk / tables * 100) if tables > 0 else 100.0
    th = _threshold(ctx, 'INS-MYSQL-TMP-DISK')
    _apply_high(
        result, ratio, th,
        f'Created_tmp_disk_tables / Created_tmp_tables = {ratio:.1f}%',
        'tmp_disk', 'tmp_disk_ratio',
    )


def _wal_receiver(ctx, result, cur):
    rows = _query(cur, 'SELECT pg_is_in_recovery() AS in_recovery')
    in_recovery = rows[0].get('in_recovery') if rows else False
    if str(in_recovery).lower() in ('f', 'false', '0', 'none', '') or in_recovery is False:
        raise NotApplicable('当前不是备库，无 WAL receiver，不适用')
    receivers = _query(cur, """
        SELECT status, pid, sender_host FROM pg_stat_wal_receiver
    """)
    if not receivers:
        grade = 3
        summary = '备库没有 WAL receiver 行'
    else:
        status = str(receivers[0].get('status') or '').lower()
        if status == 'streaming':
            grade = 0
            summary = 'WAL receiver 正在 streaming'
        elif status in ('starting', 'restarting'):
            grade = 1
            summary = f'WAL receiver 状态 {status}'
        elif status == 'stopping':
            grade = 2
            summary = 'WAL receiver 正在停止'
        else:
            grade = 2
            summary = f'WAL receiver 状态 {status or "空"}'
    th = _threshold(ctx, 'INS-PG-WAL-RECV')
    _apply_high(result, grade, th, summary, 'wal_receiver', 'grade')


def _inactive_slots(ctx, result, cur):
    rows = _query(cur, """
        SELECT slot_name, slot_type, active FROM pg_replication_slots
    """)
    inactive = []
    for row in rows:
        active = row.get('active')
        if active is False or str(active).lower() in ('f', 'false', '0', 'off'):
            inactive.append(row)
    th = _threshold(ctx, 'INS-PG-INACTIVE-SLOT')
    _apply_high(
        result, len(inactive), th,
        f'active=false 的复制槽 {len(inactive)} 个',
        'inactive_slot', 'inactive_slots',
    )
    result.metrics['samples'] = inactive[:20]


def _freeze_age(ctx, result, cur):
    rows = _query(cur, SQL_FREEZE)
    ages = [_num(row.get('freeze_age')) for row in rows]
    ages = [age for age in ages if age is not None]
    oldest = max(ages) if ages else 0
    th = _threshold(ctx, 'INS-PG-VACUUM-FREEZE')
    _apply_high(
        result, oldest, th,
        f'最大 age(relfrozenxid) = {oldest:.0f}',
        'freeze_age', 'freeze_age',
    )
    result.metrics['samples'] = rows[:5]


def _bloat(ctx, result, cur):
    rows = _query(cur, SQL_BLOAT)
    bloated = [row for row in rows if (_num(row.get('bloat_pct')) or 0) > 50]
    th = _threshold(ctx, 'INS-PG-BLOAT')
    _apply_high(
        result, len(bloated), th,
        f'估算膨胀率超过 50% 的表或索引 {len(bloated)} 个',
        'bloat', 'bloated_objects',
    )
    result.metrics['samples'] = bloated[:20]


def _extensions(ctx, result, cur):
    rows = _query(cur, """
        SELECT e.extname, e.extversion, a.default_version
        FROM pg_extension e
        LEFT JOIN pg_available_extensions a ON a.name = e.extname
    """)
    outdated = []
    uncompared = []
    for row in rows:
        installed = row.get('extversion')
        default = row.get('default_version')
        if default is None or installed is None:
            uncompared.append(row)
        elif str(installed) != str(default):
            outdated.append(row)
    th = _threshold(ctx, 'INS-PG-EXT-OUTDATED')
    summary = f'低于本机 default_version 的扩展 {len(outdated)} 个'
    if uncompared:
        summary += f'；{len(uncompared)} 个没有默认版本，不能证明过期'
    _apply_high(result, len(outdated), th, summary, 'extension', 'outdated_extensions')
    result.metrics['uncompared'] = len(uncompared)


def _statement_timeout_value(text):
    match = re.search(r'statement_timeout\s*=\s*(\d+)', str(text or ''), re.I)
    return int(match.group(1)) if match else None


def _statement_timeout(ctx, result, cur):
    rows = _query(cur, """
        SELECT name, setting, source, boot_val, reset_val
        FROM pg_settings
        WHERE name = 'statement_timeout'
    """)
    if not rows:
        raise RuntimeError('pg_settings 没有 statement_timeout')
    source = str(rows[0].get('source') or '').lower()
    session_setting = int(_num(rows[0].get('setting'), 0) or 0)
    injected = source in ('session', 'client', 'command line')
    app_setting = session_setting
    partial = False
    if injected:
        result.metrics['ignored_session_setting'] = session_setting
        try:
            file_rows = _query(cur, """
                SELECT name, setting FROM pg_file_settings
                WHERE name = 'statement_timeout' AND error IS NULL
            """)
        except Exception as exc:
            degrade_note('inspection.detect_statement_timeout.file', '读不到 pg_file_settings', exc)
            file_rows = None
        try:
            role_rows = _query(cur, """
                SELECT setdatabase, setrole, unnest(setconfig) AS cfg
                FROM pg_db_role_setting
            """)
        except Exception as exc:
            degrade_note('inspection.detect_statement_timeout.role', '读不到 pg_db_role_setting', exc)
            role_rows = None
        if file_rows is None and role_rows is None:
            raise RuntimeError(
                '平台连接注入了 statement_timeout，且无权读取配置文件与角色设置，不能证明应用层超时'
            )
        file_setting = None
        if file_rows:
            file_setting = int(_num(file_rows[0].get('setting'), 0) or 0)
        role_values = []
        for row in role_rows or []:
            parsed = _statement_timeout_value(row.get('cfg'))
            if parsed is not None:
                role_values.append(parsed)
        if file_setting is not None:
            app_setting = file_setting
        elif any(value > 0 for value in role_values):
            app_setting = max(role_values)
            partial = len(role_values) > 0
        else:
            app_setting = 0
    if app_setting > 0 and not partial:
        grade = 0
        summary = f'应用层 statement_timeout={app_setting} ms（source={source}）'
    elif app_setting > 0 and partial:
        grade = 1
        summary = f'仅角色或库级设置了 statement_timeout={app_setting} ms'
    elif injected:
        grade = 3
        summary = '数据库与角色均未设置 statement_timeout（看不到应用自己的连接参数）'
    else:
        grade = 3
        summary = 'statement_timeout 配置为 0'
    th = _threshold(ctx, 'INS-PG-STMT-TIMEOUT')
    _apply_high(result, grade, th, summary, 'statement_timeout', 'grade')
    result.metrics['app_statement_timeout_ms'] = app_setting
    result.metrics['source'] = source


def _dsc_nodes(ctx, result, cur):
    rows = _query(cur, 'SELECT DSC_NODES FROM V$DATABASE')
    nodes = _num(rows[0].get('dsc_nodes')) if rows else None
    if nodes is None:
        raise RuntimeError('DSC_NODES 为空，不能判定节点状态')
    if nodes <= 1:
        raise NotApplicable(
            f'DSC_NODES={nodes:.0f}，非多节点 DSC，不适用（不用 V$INSTANCE 行数代替节点数）'
        )
    try:
        detail = _query(cur, 'SELECT EP_SEQNO, EP_NAME, EP_STATUS FROM V$DSC_EP_INFO')
        status_key = 'ep_status'
    except Exception as first:
        if not _missing_view(first):
            raise
        degrade_note('inspection.detect_dsc_nodes.ep_info', 'V$DSC_EP_INFO 不可读，改试 V$DSC_INSTANCES', first)
        try:
            detail = _query(cur, 'SELECT INST_ID, INST_NAME, STATUS FROM V$DSC_INSTANCES')
            status_key = 'status'
        except Exception as second:
            raise RuntimeError(
                f'DSC_NODES={nodes:.0f} 但节点明细视图不可读，不能把节点数当成全部健康: {second}'
            ) from second
    unhealthy = 0
    for row in detail:
        status = row.get(status_key)
        if str(status or '').strip().upper() not in ('OPEN', 'NORMAL', 'OK', 'ACTIVE', 'RUNNING'):
            unhealthy += 1
    if len(detail) < nodes:
        unhealthy += int(nodes - len(detail))
    th = _threshold(ctx, 'INS-DM-DSC')
    _apply_high(
        result, unhealthy, th,
        f'DSC 未见健康状态的节点 {unhealthy} 个（DSC_NODES={nodes:.0f}）',
        'dsc', 'unhealthy_nodes',
    )
    result.metrics['dsc_nodes'] = nodes


def _standby_arch_rows(rows):
    kept = []
    for row in rows:
        arch_type = str(row.get('arch_type') or '').upper()
        if arch_type and arch_type not in ('LOCAL', 'NONE', 'N/A'):
            kept.append(row)
    return kept


def _arch_bad(row):
    status = str(row.get('arch_status') or '').upper()
    if any(token in status for token in ('ERROR', 'INVALID', 'FAIL', 'STOP', 'DISABLE', 'INACTIVE')):
        return True
    if status in ('0', 'N', 'NO'):
        return True
    return not _healthy_token(status)


def _dw_standby(ctx, result, cur):
    role_rows = _query(cur, 'SELECT ROLE$ FROM V$DATABASE')
    _query(cur, 'SELECT MODE$ FROM V$INSTANCE')
    dests = _query(cur, SQL_DM_ARCH_STATUS)
    role = role_rows[0].get('role$') if role_rows else None
    standby = _standby_arch_rows(dests)
    if not standby:
        raise NotApplicable('未配置 DataWatch 备库，不适用（不把 PRIMARY 标成健康）')
    bad = [row for row in standby if _arch_bad(row)]
    if bad:
        grade = 3
        summary = f"DataWatch 归档目的地异常 {len(bad)} 个"
    else:
        grade = 0
        summary = f'DataWatch 备库归档状态正常（{len(standby)} 个目的地，ROLE$={role}）'
    th = _threshold(ctx, 'INS-DM-DW-STANDBY')
    _apply_high(result, grade, th, summary, 'dw_standby', 'grade')
    result.metrics['role'] = role


def _dm_arch(ctx, result, cur):
    mode_rows = _query(cur, 'SELECT ARCH_MODE FROM V$DATABASE')
    dests = _query(cur, SQL_DM_ARCH_STATUS)
    files = _query(cur, 'SELECT COUNT(*) AS cnt FROM V$ARCH_FILE')
    mode = str(mode_rows[0].get('arch_mode') if mode_rows else '').upper()
    file_count = int(_num(files[0].get('cnt'), 0) or 0) if files else 0
    if mode in ('N', 'NO', '0', 'OFF', 'NOARCHIVELOG', ''):
        grade = 3
        summary = f'归档未开启（ARCH_MODE={mode or "空"}）'
    else:
        bad = [row for row in dests if _arch_bad(row)]
        if bad:
            grade = 2
            summary = f'归档已开启，但 {len(bad)} 个目的地状态异常'
        elif not dests:
            grade = 1
            summary = '归档已开启，但 V$ARCH_STATUS 没有目的地'
        else:
            grade = 0
            summary = f'归档开启，目的地 {len(dests)} 个，归档文件 {file_count} 个'
    th = _threshold(ctx, 'INS-DM-ARCH')
    _apply_high(result, grade, th, summary, 'dm_arch', 'grade')
    result.metrics['arch_mode'] = mode
    result.metrics['arch_files'] = file_count


def _dm_rlog(ctx, result, cur):
    dests = _query(cur, SQL_DM_ARCH_STATUS)
    realtime = [
        row for row in dests
        if str(row.get('arch_type') or '').upper() in ('REALTIME', 'TIMELY', 'RLOG')
    ]
    if not realtime:
        raise NotApplicable('无实时 RLOG 同步对象，不适用（不使用 V$RLOG 偏移或水位冒充同步进度）')
    bad = [row for row in realtime if _arch_bad(row)]
    if bad:
        grade = 3
        summary = f'实时 RLOG 目的地异常 {len(bad)} 个'
    else:
        grade = 0
        summary = f'实时 RLOG 目的地正常 {len(realtime)} 个'
    th = _threshold(ctx, 'INS-DM-RLOG')
    _apply_high(result, grade, th, summary, 'dm_rlog', 'grade')


def _gbase_nodes(cur):
    rows = _query(cur, """
        SELECT NODEID, NODENAME, NODETYPE, STATUS, CPU_USED, MEM_USED
        FROM gcluster_v$node_info
    """)
    if not rows:
        raise RuntimeError('未取得 gcluster_v$node_info 节点清单，不能把空结果当成均衡或无宕机')
    return rows


def _gbase_balance(ctx, result, cur):
    rows = _gbase_nodes(cur)
    if len(rows) < 2:
        raise NotApplicable('节点数不足 2，负载均衡不适用')
    cpu = _spread_pct([row.get('cpu_used') for row in rows])
    mem = _spread_pct([row.get('mem_used') for row in rows])
    spreads = [value for value in (cpu, mem) if value is not None]
    if not spreads:
        raise RuntimeError('节点清单没有 CPU_USED/MEM_USED，不能判定负载均衡')
    spread = max(spreads)
    th = _threshold(ctx, 'INS-GBASE-BALANCE')
    _apply_high(
        result, spread, th,
        f'节点负载极差 {spread:.1f}%（CPU {cpu if cpu is not None else "无"} / MEM {mem if mem is not None else "无"}）',
        'gbase_balance', 'spread_pct',
    )


def _gbase_distribution(ctx, result, cur):
    rows = _query(cur, """
        SELECT NODEID, SUM(DATA_SIZE) AS bytes
        FROM gcluster_v$table_distribution
        GROUP BY NODEID
    """)
    if not rows:
        raise RuntimeError('未取得数据分布，不能把空结果当成均衡')
    if len(rows) < 2:
        raise NotApplicable('节点数不足 2，数据分布均衡不适用')
    spread = _spread_pct([row.get('bytes') for row in rows])
    if spread is None:
        raise RuntimeError('分布结果没有 DATA_SIZE，不能判定均衡')
    th = _threshold(ctx, 'INS-GBASE-DIST')
    _apply_high(
        result, spread, th,
        f'节点数据量极差 {spread:.1f}%',
        'gbase_distribution', 'spread_pct',
    )


def _gbase_node_down(ctx, result, cur):
    rows = _gbase_nodes(cur)
    down = []
    for row in rows:
        status = str(row.get('status') or '')
        if not any(token in status.upper() for token in ('ONLINE', 'ACTIVE', 'HEALTHY', 'NORMAL')):
            down.append(row)
    th = _threshold(ctx, 'INS-GBASE-NODE-DOWN')
    _apply_high(
        result, len(down), th,
        f'非在线节点 {len(down)} 个',
        'gbase_node_down', 'down_nodes',
    )
    result.metrics['samples'] = down[:20]


class Batch010bDetector:
    """DB-023 的 34 个检测方法。签名与既有 GenericDetector 相同。"""

    @staticmethod
    def detect_lob_pctversion(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-LOB-PCT', 'LOB PCTVERSION 异常', 'detect_lob_pctversion', _ORACLE, _lob_pct)

    @staticmethod
    def detect_dblinks(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-DBLINK', '残留 DBLINK', 'detect_dblinks', _ORACLE, _dblinks)

    @staticmethod
    def detect_directory_objects(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-DIR-OBJ', 'Directory 对象', 'detect_directory_objects', _ORACLE, _directories)

    @staticmethod
    def detect_rman_backup_age(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-RMAN-AGE', 'RMAN 备份距今', 'detect_rman_backup_age', _ORACLE, _rman_age)

    @staticmethod
    def detect_dg_lag(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-DG-LAG', 'DataGuard Lag', 'detect_dg_lag', _ORACLE, _dg_lag)

    @staticmethod
    def detect_dg_status(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-DG-STATUS', 'DataGuard 状态', 'detect_dg_status', _ORACLE, _dg_status)

    @staticmethod
    def detect_rac_vip(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-RAC-VIP', 'RAC VIP 状态', 'detect_rac_vip', _ORACLE, _rac_vip)

    @staticmethod
    def detect_redolog_size(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-REDOLOG-SIZE', 'Redo Log 大小', 'detect_redolog_size', _ORACLE, _redolog_size)

    @staticmethod
    def detect_open_files(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-OPEN-FILE', '打开文件数', 'detect_open_files', _ORACLE, _open_files)

    @staticmethod
    def detect_lob_cache_hit(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-LOB-CACHE', 'LOB CACHE 读取', 'detect_lob_cache_hit', _ORACLE, _lob_cache)

    @staticmethod
    def detect_ash_memory(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-ASH-USAGE', 'ASH 内存使用', 'detect_ash_memory', _ORACLE, _ash_memory)

    @staticmethod
    def detect_rman_config(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-RMAN-CONF', 'RMAN 配置', 'detect_rman_config', _ORACLE, _rman_config)

    @staticmethod
    def detect_audit_trail(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-AUDIT-TRAIL', '审计日志', 'detect_audit_trail', _ORACLE, _audit_trail)

    @staticmethod
    def detect_pga_leak(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-PGA-LEAK', 'PGA 泄漏', 'detect_pga_leak', _ORACLE, _pga_leak)

    @staticmethod
    def detect_io_distribution(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-IO-STAT', 'IO 分布', 'detect_io_distribution', _ORACLE, _io_distribution)

    @staticmethod
    def detect_exadata_features(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-ORA-EXADATA', 'Exadata 特性', 'detect_exadata_features', _ORACLE, _exadata)

    @staticmethod
    def detect_gtid_consistency(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-MYSQL-GTID', 'GTID 一致性', 'detect_gtid_consistency', _MYSQL, _gtid)

    @staticmethod
    def detect_semi_sync(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-MYSQL-SEMI-SYNC', '半同步复制状态', 'detect_semi_sync', _MYSQL, _semi_sync)

    @staticmethod
    def detect_event_scheduler(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-MYSQL-EVENT', 'Event 调度器', 'detect_event_scheduler', _MYSQL, _event_scheduler)

    @staticmethod
    def detect_dangerous_fk(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-MYSQL-FK-CASCADE', '危险外键级联', 'detect_dangerous_fk', _MYSQL, _dangerous_fk)

    @staticmethod
    def detect_tmp_disk_ratio(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-MYSQL-TMP-DISK', '磁盘临时表比例', 'detect_tmp_disk_ratio', _MYSQL, _tmp_disk)

    @staticmethod
    def detect_wal_receiver(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-PG-WAL-RECV', 'WAL Receiver 状态', 'detect_wal_receiver', _PG, _wal_receiver)

    @staticmethod
    def detect_inactive_repl_slot(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-PG-INACTIVE-SLOT', '失效复制槽', 'detect_inactive_repl_slot', _PG, _inactive_slots)

    @staticmethod
    def detect_freeze_age(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-PG-VACUUM-FREEZE', 'Freeze Age', 'detect_freeze_age', _PG, _freeze_age)

    @staticmethod
    def detect_bloat(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-PG-BLOAT', '表/索引膨胀', 'detect_bloat', _PG, _bloat)

    @staticmethod
    def detect_extension_versions(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-PG-EXT-OUTDATED', '扩展版本', 'detect_extension_versions', _PG, _extensions)

    @staticmethod
    def detect_statement_timeout(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-PG-STMT-TIMEOUT', 'Statement Timeout', 'detect_statement_timeout', _PG, _statement_timeout)

    @staticmethod
    def detect_dsc_nodes(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-DM-DSC', 'DSC 节点状态', 'detect_dsc_nodes', _DM, _dsc_nodes)

    @staticmethod
    def detect_dw_standby(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-DM-DW-STANDBY', '主备同步状态', 'detect_dw_standby', _DM, _dw_standby)

    @staticmethod
    def detect_dm_arch(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-DM-ARCH', '归档状态', 'detect_dm_arch', _DM, _dm_arch)

    @staticmethod
    def detect_dm_rlog(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-DM-RLOG', 'RLOG 同步状态', 'detect_dm_rlog', _DM, _dm_rlog)

    @staticmethod
    def detect_gbase_node_balance(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-GBASE-BALANCE', '节点负载均衡', 'detect_gbase_node_balance', _GBASE, _gbase_balance)

    @staticmethod
    def detect_gbase_distribution(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-GBASE-DIST', '数据分布均衡', 'detect_gbase_distribution', _GBASE, _gbase_distribution)

    @staticmethod
    def detect_gbase_node_down(ctx: DetectionContext) -> DetectionResult:
        return _run(ctx, 'INS-GBASE-NODE-DOWN', '节点宕机', 'detect_gbase_node_down', _GBASE, _gbase_node_down)


DB023_DETECTORS = {
    'INS-ORA-LOB-PCT': Batch010bDetector.detect_lob_pctversion,
    'INS-ORA-DBLINK': Batch010bDetector.detect_dblinks,
    'INS-ORA-DIR-OBJ': Batch010bDetector.detect_directory_objects,
    'INS-ORA-RMAN-AGE': Batch010bDetector.detect_rman_backup_age,
    'INS-ORA-DG-LAG': Batch010bDetector.detect_dg_lag,
    'INS-ORA-DG-STATUS': Batch010bDetector.detect_dg_status,
    'INS-ORA-RAC-VIP': Batch010bDetector.detect_rac_vip,
    'INS-ORA-REDOLOG-SIZE': Batch010bDetector.detect_redolog_size,
    'INS-ORA-OPEN-FILE': Batch010bDetector.detect_open_files,
    'INS-ORA-LOB-CACHE': Batch010bDetector.detect_lob_cache_hit,
    'INS-ORA-ASH-USAGE': Batch010bDetector.detect_ash_memory,
    'INS-ORA-RMAN-CONF': Batch010bDetector.detect_rman_config,
    'INS-ORA-AUDIT-TRAIL': Batch010bDetector.detect_audit_trail,
    'INS-ORA-PGA-LEAK': Batch010bDetector.detect_pga_leak,
    'INS-ORA-IO-STAT': Batch010bDetector.detect_io_distribution,
    'INS-ORA-EXADATA': Batch010bDetector.detect_exadata_features,
    'INS-MYSQL-GTID': Batch010bDetector.detect_gtid_consistency,
    'INS-MYSQL-SEMI-SYNC': Batch010bDetector.detect_semi_sync,
    'INS-MYSQL-EVENT': Batch010bDetector.detect_event_scheduler,
    'INS-MYSQL-FK-CASCADE': Batch010bDetector.detect_dangerous_fk,
    'INS-MYSQL-TMP-DISK': Batch010bDetector.detect_tmp_disk_ratio,
    'INS-PG-WAL-RECV': Batch010bDetector.detect_wal_receiver,
    'INS-PG-INACTIVE-SLOT': Batch010bDetector.detect_inactive_repl_slot,
    'INS-PG-VACUUM-FREEZE': Batch010bDetector.detect_freeze_age,
    'INS-PG-BLOAT': Batch010bDetector.detect_bloat,
    'INS-PG-EXT-OUTDATED': Batch010bDetector.detect_extension_versions,
    'INS-PG-STMT-TIMEOUT': Batch010bDetector.detect_statement_timeout,
    'INS-DM-DSC': Batch010bDetector.detect_dsc_nodes,
    'INS-DM-DW-STANDBY': Batch010bDetector.detect_dw_standby,
    'INS-DM-ARCH': Batch010bDetector.detect_dm_arch,
    'INS-DM-RLOG': Batch010bDetector.detect_dm_rlog,
    'INS-GBASE-BALANCE': Batch010bDetector.detect_gbase_node_balance,
    'INS-GBASE-DIST': Batch010bDetector.detect_gbase_distribution,
    'INS-GBASE-NODE-DOWN': Batch010bDetector.detect_gbase_node_down,
}

if len(DB023_DETECTORS) != 34:
    raise RuntimeError(f'DB-023 应登记 34 项，当前 {len(DB023_DETECTORS)}')
