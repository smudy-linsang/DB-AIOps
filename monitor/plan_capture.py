# -*- coding: utf-8 -*-
"""Phase 7A-08/7D-01: SQL 执行计划采集 + 计划突变检测。

capture(config, sql_digest, sql_text=None, source='auto', conn=None)
- Oracle: v$sql_plan (child 0) 树化, plan_hash 取 v$sqlstats.plan_hash_value
- MySQL:  EXPLAIN FORMAT=JSON <原文>, plan_hash=结构化 md5 (剔除代价/行数字段)
- PG:     EXPLAIN (FORMAT JSON) <原文> (禁 ANALYZE), plan_hash 同上
新计划 hash 与 is_current 不同 → 翻转 is_current 并按降噪铁律发 plan_change 事件
(phase7/40 §1: 仅 Top20 耗时 SQL 且劣化 >=2x 才扰民)。
安全铁律: 只读; MySQL/PG 仅接受单条 DML/SELECT; 任何失败静默返回 None。
"""
import hashlib
import json
import logging
import re

from django.utils import timezone

logger = logging.getLogger("monitor.plan_capture")

_ALLOWED_HEAD = re.compile(r'^\s*(select|insert|update|delete|replace|with|table)\b', re.I)

# 合法数据库标识符（用于 USE `db_name` 防注入；BUG-009）：
# 仅允许字母/数字/下划线/连字符/点/美元符，长度 1-64，禁止反引号/分号/空白等
_IDENTIFIER_RE = re.compile(r'^[A-Za-z0-9_.$-]{1,64}$')

# 结构 hash 需剔除的易变键 (代价/行数/过滤率类)
_VOLATILE_KEYS = {
    # mysql explain json
    'cost_info', 'rows_examined_per_scan', 'rows_produced_per_join', 'filtered',
    'read_cost', 'eval_cost', 'prefix_cost', 'data_read_per_join', 'query_cost',
    'sort_cost',
    # pg explain json
    'Total Cost', 'Startup Cost', 'Plan Rows', 'Plan Width',
}


def _strip_volatile(node):
    if isinstance(node, dict):
        return {k: _strip_volatile(v) for k, v in node.items() if k not in _VOLATILE_KEYS}
    if isinstance(node, list):
        return [_strip_volatile(x) for x in node]
    return node


def _structural_hash(plan_json) -> str:
    stripped = _strip_volatile(plan_json)
    return hashlib.md5(json.dumps(stripped, sort_keys=True, default=str)
                       .encode()).hexdigest()[:32]


def _sql_allowed(sql_text: str) -> bool:
    if not sql_text or not _ALLOWED_HEAD.match(sql_text):
        return False
    return ';' not in sql_text.rstrip().rstrip(';')


# ---- 逐库采集 ----
def _capture_mysql(cur, sql_text):
    cur.execute(f"EXPLAIN FORMAT=JSON {sql_text}")
    row = cur.fetchone()
    raw = list(row.values())[0] if isinstance(row, dict) else row[0]
    plan_json = json.loads(raw)
    cost = None
    try:
        cost = float(plan_json['query_block']['cost_info']['query_cost'])
    except Exception:
        pass
    try:
        cur.execute(f"EXPLAIN FORMAT=TREE {sql_text}")
        row = cur.fetchone()
        plan_text = list(row.values())[0] if isinstance(row, dict) else row[0]
    except Exception:
        plan_text = json.dumps(plan_json, indent=1)[:4000]
    return plan_json, plan_text, cost


def _capture_pg(cur, sql_text):
    cur.execute(f"EXPLAIN (FORMAT JSON) {sql_text}")
    plan_json = cur.fetchone()[0]
    if isinstance(plan_json, str):
        plan_json = json.loads(plan_json)
    plan_json = plan_json[0] if isinstance(plan_json, list) else plan_json
    cost = None
    try:
        cost = float(plan_json['Plan']['Total Cost'])
    except Exception:
        pass
    cur.execute(f"EXPLAIN {sql_text}")
    plan_text = "\n".join(r[0] for r in cur.fetchall())
    return plan_json, plan_text, cost


def _capture_oracle(cur, sql_id):
    cur.execute(
        "SELECT id, parent_id, depth, operation, options, object_name, cost, cardinality "
        "FROM v$sql_plan WHERE sql_id = :1 AND child_number = 0 ORDER BY id", [sql_id])
    rows = cur.fetchall()
    if not rows:
        return None, None, None, None
    nodes = [{'id': r[0], 'parent_id': r[1], 'depth': r[2] or 0,
              'operation': r[3], 'options': r[4], 'object_name': r[5],
              'cost': r[6], 'cardinality': r[7]} for r in rows]
    lines = []
    for n in nodes:
        op = f"{n['operation']}{' ' + n['options'] if n['options'] else ''}"
        obj = f" [{n['object_name']}]" if n['object_name'] else ''
        lines.append(f"{'  ' * n['depth']}{op}{obj}  (cost={n['cost']}, rows={n['cardinality']})")
    plan_text = "\n".join(lines)
    cost = nodes[0].get('cost')
    cur.execute("SELECT plan_hash_value FROM v$sqlstats WHERE sql_id = :1 "
                "FETCH FIRST 1 ROWS ONLY", [sql_id])
    row = cur.fetchone()
    plan_hash = str(row[0]) if row else _structural_hash(nodes)
    return {'nodes': nodes}, plan_text, float(cost) if cost is not None else None, plan_hash


def capture(config, sql_digest, sql_text=None, source='auto', conn=None, db_name=None):
    """采集一次计划并落库。返回 SqlPlan 或 None。conn 可复用外部连接。

    db_name: MySQL 家族 EXPLAIN 需要默认库上下文 (ASH 原文表名通常不带 schema)。
    """
    from monitor.db_connector import DbConnector
    from monitor.models import SqlPlan

    own_conn = conn is None
    try:
        if conn is None:
            conn = DbConnector.get_connection(config)
        cur = conn.cursor()
        try:
            if config.db_type in ('oracle', 'dm'):
                plan_json, plan_text, cost, plan_hash = _capture_oracle(cur, sql_digest)
                if plan_json is None:
                    return None
            elif config.db_type in ('mysql', 'tdsql', 'gbase'):
                if not _sql_allowed(sql_text or ''):
                    return None
                if db_name:
                    # 标识符白名单校验，防止 SQL 注入（BUG-009）
                    if not _IDENTIFIER_RE.match(str(db_name)):
                        logger.warning("[plan] 非法 db_name 已拒绝: %r", db_name)
                        return None
                    try:
                        cur.execute(f"USE `{db_name}`")
                    except Exception:
                        pass
                plan_json, plan_text, cost = _capture_mysql(cur, sql_text)
                plan_hash = _structural_hash(plan_json)
            elif config.db_type in ('pgsql', 'postgresql'):
                if not _sql_allowed(sql_text or ''):
                    return None
                plan_json, plan_text, cost = _capture_pg(cur, sql_text)
                plan_hash = _structural_hash(plan_json)
            else:
                return None
        finally:
            cur.close()

        prev = SqlPlan.objects.filter(config=config, sql_digest=sql_digest,
                                      is_current=True).first()
        if prev and prev.plan_hash == plan_hash:
            return prev  # 计划未变, 不重复落库
        # 原文随计划留存 (详情页/优化建议在 ASH 无样本时兜底读取)
        if sql_text and isinstance(plan_json, dict):
            plan_json = dict(plan_json, _sql_text=sql_text[:1000])
        plan = SqlPlan.objects.create(
            config=config, sql_digest=sql_digest, plan_hash=plan_hash,
            plan_json=plan_json, plan_text=plan_text or '',
            cost_total=cost, source=source, is_current=True)
        if prev:
            prev.is_current = False
            prev.save(update_fields=['is_current'])
            _maybe_emit_plan_change(config, sql_digest, prev, plan)
        return plan
    except Exception as e:
        logger.debug("[plan] 采集失败 %s/%s: %s", config.id, sql_digest, e)
        return None
    finally:
        if own_conn and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


# ---- 7D-01: 计划突变事件 (带降噪铁律) ----
def _digest_latency(config_id, sql_digest, since, until):
    """sql_stat 中该 digest 的平均单次耗时 ms; 无数据返回 None。"""
    try:
        from monitor.timeseries import get_timeseries_storage
        conn = get_timeseries_storage()._get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT SUM(elapsed_ms_delta)::float, SUM(exec_delta)::float FROM sql_stat "
            "WHERE db_config_id=%s AND sql_digest=%s AND time BETWEEN %s AND %s",
            (config_id, sql_digest, since, until))
        row = cur.fetchone()
        cur.close()
        if row and row[1]:
            return row[0] / row[1]
    except Exception:
        pass
    return None


def _in_top_elapsed(config_id, sql_digest, top_n=20) -> bool:
    try:
        from monitor.timeseries import get_timeseries_storage
        conn = get_timeseries_storage()._get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT sql_digest FROM sql_stat WHERE db_config_id=%s "
            "AND time > NOW() - interval '1 hour' "
            "GROUP BY sql_digest ORDER BY SUM(elapsed_ms_delta) DESC LIMIT %s",
            (config_id, top_n))
        tops = {r[0] for r in cur.fetchall()}
        cur.close()
        return sql_digest in tops
    except Exception:
        return False


def _maybe_emit_plan_change(config, sql_digest, old_plan, new_plan):
    """降噪铁律 (phase7/40 §1): Top20 耗时 且 劣化>=2x (或无前值但 after>500ms)。"""
    try:
        now = timezone.now()
        from datetime import timedelta
        before = _digest_latency(config.id, sql_digest, now - timedelta(minutes=70),
                                 now - timedelta(minutes=10))
        after = _digest_latency(config.id, sql_digest, now - timedelta(minutes=10), now)
        if not _in_top_elapsed(config.id, sql_digest):
            return
        degraded = ((before and after and after >= 2 * before)
                    or (before is None and after is not None and after > 500))
        if not degraded:
            return
        from monitor.redis_bus import emit_event
        emit_event({
            'config_id': config.id, 'db_type': config.db_type, 'source': 'collector',
            'signal': 'plan_change', 'metric_key': 'plan_hash',
            'value': round(after or 0, 1), 'threshold': round((before or 0) * 2, 1),
            'severity': 'warning',
            'occurred_at': timezone.now().isoformat(),
            'dedup_key': f"{config.id}:plan_change:{sql_digest[:16]}",
            'category': 'performance',
            'detail': {'sql_digest': sql_digest,
                       'old_plan_hash': old_plan.plan_hash,
                       'new_plan_hash': new_plan.plan_hash,
                       'old_cost': old_plan.cost_total, 'new_cost': new_plan.cost_total,
                       'latency_before_ms': round(before, 1) if before else None,
                       'latency_after_ms': round(after, 1) if after else None},
        })
        logger.info("[plan] %s digest=%s 计划突变事件已发", config.name, sql_digest[:12])
    except Exception as e:
        logger.debug("[plan] plan_change 事件失败: %s", e)
