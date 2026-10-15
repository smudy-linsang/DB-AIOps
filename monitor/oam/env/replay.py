# -*- coding: utf-8 -*-
"""可回放环境：冻结快照 → 工具调用 → 观测（论文 §5.3.1）。

## 这个环境能证明什么、不能证明什么（照论文原文写死）

论文 §5.3.1 承认：*"Our replay design **cannot synthesize truly novel telemetry
beyond what was captured. We mitigate this with noise injection.** Future work
will integrate stochastic generators and fault injectors to expand coverage."*

所以本环境的可信范围是：**快照已采集到的遥测 + 噪声注入扰动**。
它**不具备**的能力（不要试图绕过）：
  · 合成快照中不存在的新指标、新等待事件、新错误码；
  · 合成未被采集的时段。

因此评测结论只能支持"**相对已覆盖场景没有回归**"，
**不能**支持"未见过的故障也能处理"。后者只能靠故障注入台（P3）。
这正是本项目三轮复审里"指标看着好、实际不成立"的同一种病 ——
所以设计 §8 把"评测与覆盖度同屏"列为验收项：没有 `coverage_report`
就不算完成评测，`root_causes_missing` 非空时报告必须显式标注
"该成绩不覆盖以下根因"。

## 确定性

噪声按 `case_id` 派生种子，所以同一用例重跑得到同一串扰动 ——
设计 §8 第一条验收项"同一 case_id + policy_version 重跑结果一致"依赖这一点。
"""
import hashlib
import logging
import random
from datetime import datetime

from monitor import appconf
from monitor.oam.env.snapshot import OamSnapshot, SnapshotRangeError
from monitor.oam.env.tool_proxy import ToolProxy
from monitor.oam.labels import labels_for

logger = logging.getLogger('monitor.oam')

# 论文 Table 3b 的算子数分档，用作覆盖度报告的口径
PLAN_OPERATOR_BUCKETS = ((1, 30), (30, 60), (60, 90))


def _seed_for(case_id: str) -> int:
    return int(hashlib.sha256((case_id or '').encode('utf-8')).hexdigest()[:16], 16)


def _record_miss(env, params, result) -> None:
    """把一次未命中写进 `OamRetrievalMiss`。

    无 Django 配置时（纯函数自测）静默跳过：写库是运行环境的事，
    不该让感知层在裸环境下崩溃。
    """
    try:
        from monitor.oam.audit import persist_miss
    except Exception:
        return
    payload = result.miss_payload() if hasattr(result, 'miss_payload') else {}
    persist_miss(
        trajectory_id=getattr(env, 'trajectory_id', '') or '',
        kb_category=str(params.get('category') or payload.get('category') or ''),
        query_text=str(params.get('query') or ''),
        db_type=env.db_type or '',
        top_similarity=getattr(result, 'top_similarity', None),
    )


class ReplayEnv:
    """回放环境。可信范围 = 快照已采集到的遥测 + 噪声注入扰动。"""

    # 论文的缓解手段就是噪声注入；随机生成器与故障注入被明确列为未来工作。
    NOISE = {'gaussian_sigma_ratio': 0.05, 'jitter_seconds': 30, 'dropout_ratio': 0.02}

    def __init__(self, snapshot: OamSnapshot, label_space=None, kb_index=None,
                 mode: str = 'replay', trajectory_id: str = ''):
        self.snapshot = snapshot
        self.db_type = snapshot.db_type
        self.kb_index = kb_index
        self.trajectory_id = trajectory_id or snapshot.case_id
        self._rng = random.Random(_seed_for(snapshot.case_id))
        self.proxy = ToolProxy(ctx=self, mode=mode, handlers=_build_handlers(self))
        # 该用例所在的标签空间（评测"覆盖度"要拿它算缺口）
        self.label_space = set(label_space or
                               {lb.label_id for lb in labels_for(self.db_type)})

    # ------------------------------------------------------------------ 调
    def catalog(self) -> list:
        return self.proxy.catalog()

    def call(self, tool: str, params: dict):
        return self.proxy.call(tool, params)

    def check_window(self, since, until):
        """超出快照窗口的请求返回确定性错误，不静默截断。"""
        if not self.snapshot.window:
            raise SnapshotRangeError('快照未声明窗口，拒绝按窗口范围的分析请求')
        w_start, w_end = self.snapshot.window
        if since < w_start or until > w_end:
            raise SnapshotRangeError(
                f'请求范围 {since}~{until} 超出快照窗口 {w_start}~{w_end}')
        return True

    # ------------------------------------------------------- 噪声注入
    def perturb(self, series: list) -> list:
        """对时序注入论文口径的噪声扰动。

        三个成分：高斯噪声（σ = ratio × 序列标准差）、时间抖动、随机丢点。
        **只扰动已有采样点，不新增/不外插** —— 这是本环境能力边界的直接体现。

        时间戳支持 `datetime` 与数值两种：快照入库后读回来是 ISO 字符串
        （`JSONField` 存不下 datetime），而回放路径常直接传 datetime，
        两种都要能抖动，否则这里会炸成 `datetime + float`。
        """
        if not series:
            return list(series)
        values = [float(v) for _, v in series]
        sigma = self.NOISE['gaussian_sigma_ratio'] * _std(values)
        out = []
        for ts, v in series:
            if self._rng.random() < self.NOISE['dropout_ratio']:
                continue                      # 丢点
            jitter = self._rng.uniform(-self.NOISE['jitter_seconds'],
                                       self.NOISE['jitter_seconds'])
            noise = self._rng.gauss(0, sigma) if sigma > 0 else 0.0
            out.append((_jitter_ts(ts, jitter), v + noise))
        out.sort(key=lambda p: _sort_key(p[0]))
        return out

    # ---------------------------------------------------------- 覆盖度
    def coverage_report(self, covered_root_causes=None) -> dict:
        """每次评测**必须**随成绩一起输出覆盖度，缺口不许留空。

        设计 §8："评测报告里没有 `coverage_report` 就不算完成评测。
        成功率必须与覆盖度同屏呈现，`root_causes_missing` 非空时报告须显式
        标注'该成绩不覆盖以下根因'"。
        """
        covered = set(covered_root_causes or [])
        missing = sorted(self.label_space - covered)
        return {
            'db_types': [self.db_type],
            'root_causes_covered': sorted(covered),
            'root_causes_missing': missing,
            'label_space_size': len(self.label_space),
            'covered_ratio': (round(len(covered & self.label_space) / len(self.label_space), 4)
                              if self.label_space else 0.0),
            'plan_operator_buckets': self.plan_operator_buckets(),
            'kpi_series_points': self.snapshot.series_lengths(),
            'noise_injection': dict(self.NOISE),
            'capability_boundary': (
                '回放环境只能复现快照已采集的遥测并加噪声注入，无法合成新遥测；'
                '故本成绩只支持"已覆盖场景无回归"，不支持"未见过的故障也能处理"'),
        }

    def plan_operator_buckets(self) -> dict:
        counts = {f'{lo}-{hi}': 0 for lo, hi in PLAN_OPERATOR_BUCKETS}
        counts['>90'] = 0
        for plan in self.snapshot.plans or []:
            n = _count_operators(plan.get('plan_text', ''))
            for lo, hi in PLAN_OPERATOR_BUCKETS:
                if lo <= n <= hi:
                    counts[f'{lo}-{hi}'] += 1
                    break
            else:
                counts['>90'] += 1
        return counts


def _std(values: list) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((v - mean) ** 2 for v in values) / (len(values) - 1)) ** 0.5


def _jitter_ts(ts, jitter_seconds: float):
    """按时间戳类型施加抖动。datetime 走 timedelta，数值走秒数。"""
    from datetime import timedelta
    if isinstance(ts, datetime):
        return ts + timedelta(seconds=jitter_seconds)
    if isinstance(ts, (int, float)):
        return ts + jitter_seconds
    return ts


def _sort_key(ts):
    return ts.timestamp() if isinstance(ts, datetime) else (
        ts if isinstance(ts, (int, float)) else 0)


def _count_operators(plan_text: str) -> int:
    """粗略统计计划算子数：按行首缩进层级计数。用于覆盖度分档，不需要精确。"""
    if not plan_text:
        return 0
    n = 0
    for line in str(plan_text).splitlines():
        s = line.strip()
        if s and not s.startswith('--') and s != 'QUERY PLAN':
            n += 1
    return n


# ==========================================================================
# 工具实现：全部只从冻结快照取数。快照没有的信号一律返回 None → NO_DATA。
# ==========================================================================
def _bucket(snapshot, name):
    return getattr(snapshot, name, None)


def _build_handlers(env: ReplayEnv) -> dict:
    """构造 13 个只读工具的快照取数实现。"""

    def kb_search(ctx, params):
        if env.kb_index is None:
            return None            # 知识库未接入 → NO_DATA，不编造
        # `db_version` 目前恒为空：OamSnapshot 没登记版本字段。空版本按
        # `version_in_range` 的口径**不拦**（拦了会变成"永远检索未命中"），
        # 但一旦快照补上版本就会自动生效，不必再改这里。
        result = env.kb_index.search(
            params.get('query', ''),
            db_type=ctx.db_type,
            db_version=getattr(ctx.snapshot, 'db_version', '') or '',
            category=params.get('category') or '',
            top_k=appconf.get('OAM_KB_TOP_K'))
        # 检索未命中必须留痕（设计 §7 P3）：论文部署失败原因里"检索未命中"占 12%，
        # 排名靠前且早于"KB 过时"被发现 —— 不记就无从改进索引与类目划分。
        # 这里只记"事实"，不据此改阈值或补默认值。
        try:
            if getattr(result, 'missed', False):
                _record_miss(ctx, params, result)
        except Exception as e:
            from monitor import degrade
            degrade.note('oam.kb.retrieval_miss', '检索未命中留痕失败', e)
        return result

    def os_metrics(ctx, params):
        node = _bucket(ctx.snapshot, 'config_meta') or {}
        group = params.get('group') or 'cpu'
        data = (node.get('os_metrics') or {}).get(group)
        return data or None

    def session_profile(ctx, params):
        rows = _bucket(ctx.snapshot, 'session_samples') or []
        if not rows:
            return None
        window = params.get('window_min')
        if window and ctx.snapshot.window:
            # 窗口外的样本不该出现在观测里；越界走确定性错误而不是静默丢弃。
            ctx.check_window(ctx.snapshot.window[0], ctx.snapshot.window[1])
        return {'samples': rows[:200], 'total': len(rows)}

    def lock_chain(ctx, params):
        rows = _bucket(ctx.snapshot, 'session_samples') or []
        if not rows:
            # 快照里根本没有会话样本 ⇒ 证据不足，必须走 NO_DATA。
            # 早先写成 `{...} or None`：字典恒为真值，`or None` 从不生效，
            # 于是"没采到样本"被报成"查过了，没有阻塞链"这种**看起来成功**的结果。
            # 后果不只是显示问题 —— 证据审计会把 `tool:db.lock_chain` 记为
            # "有工具观测支撑"，策略可以据此为任意根因主张背书（R25-02 同型）。
            return None
        chains = [r for r in rows if r.get('blocking')]
        return {'blocking_chains': chains[:50], 'total': len(chains)}

    def top_sql(ctx, params):
        rows = [r for r in (_bucket(ctx.snapshot, 'session_samples') or [])
                if r.get('sql_id')]
        if not rows:
            return None
        limit = params.get('limit', 10)
        rows = sorted(rows, key=lambda r: -(r.get('elapsed') or 0))[:limit]
        return {'top_sql': rows}

    def slow_query(ctx, params):
        rows = [r for r in (_bucket(ctx.snapshot, 'logs') or [])
                if r.get('kind') == 'slow_query']
        if not rows:
            return None
        limit = params.get('limit', 10)
        return {'slow_queries': rows[:limit]}

    def io_wait(ctx, params):
        keys = ('physical_reads', 'redo_wait_ms', 'log_file_sync', 'io_wait_pct',
                'blk_read', 'disk_queue_depth')
        return _metric_subset(ctx, keys)

    def log_surge(ctx, params):
        keys = ('redo_mb_per_sec', 'binlog_mb_per_sec', 'log_switch_per_hour',
                'wal_bytes')
        return _metric_subset(ctx, keys)

    def replication_status(ctx, params):
        rows = [r for r in (_bucket(ctx.snapshot, 'logs') or [])
                if r.get('kind') == 'replication']
        return {'replication': rows} if rows else None

    def capacity_trend(ctx, params):
        keys = ('tablespace_used_pct', 'disk_used_pct', 'datafile_count',
                'growth_pct_per_day')
        return _metric_subset(ctx, keys)

    def schema_fields(ctx, params):
        schema = _bucket(ctx.snapshot, 'schema_meta') or {}
        table = params.get('table')
        if table:
            row = (schema.get('tables') or {}).get(table)
            return {'table': table, 'columns': row} if row else None
        return {'tables': schema.get('tables') or {}} if schema.get('tables') else None

    def statistics(ctx, params):
        schema = _bucket(ctx.snapshot, 'schema_meta') or {}
        stats = schema.get('statistics') or {}
        table = params.get('table')
        if table:
            row = stats.get(table)
            return {'table': table, 'statistics': row} if row else None
        return {'statistics': stats} if stats else None

    def guc_config(ctx, params):
        cfg = (_bucket(ctx.snapshot, 'config_meta') or {}).get('parameters') or {}
        name = params.get('name')
        if name:
            return {'name': name, 'value': cfg[name]} if name in cfg else None
        return {'parameters': cfg} if cfg else None

    def resource_policy(ctx, params):
        cfg = _bucket(ctx.snapshot, 'config_meta') or {}
        pol = cfg.get('resource_policy') or {}
        pool = params.get('pool')
        if pool:
            return {'pool': pool, 'policy': pol[pool]} if pool in pol else None
        return {'resource_policy': pol} if pol else None

    def _metric_subset(ctx, keys):
        """按时序指标取一段最新值。缺失的键不补 0 —— 缺就是缺。"""
        out = {}
        for key in keys:
            vals = env.perturb(ctx.snapshot.series(key))
            if vals:
                out[key] = {'points': vals[-60:], 'latest': vals[-1][1]}
        return out or None

    return {
        'kb.search': kb_search,
        'os.metrics': os_metrics,
        'db.session_profile': session_profile,
        'db.top_sql': top_sql,
        'db.slow_query': slow_query,
        'db.lock_chain': lock_chain,
        'db.io_wait': io_wait,
        'db.log_surge': log_surge,
        'db.replication_status': replication_status,
        'meta.schema_fields': schema_fields,
        'meta.statistics': statistics,
        'meta.guc_config': guc_config,
        'meta.resource_policy': resource_policy,
    }


__all__ = ['ReplayEnv', 'PLAN_OPERATOR_BUCKETS']
