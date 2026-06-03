"""
AWR 分析器 - Phase 5 P1-3
=========================

对 Oracle AWR(Automatic Workload Repository)数据进行深度分析:
1. Top Wait Events - 找出最消耗资源的等待事件
2. Top SQL - 找出最消耗资源的 SQL
3. Top Segments - 找出 I/O 最热的段
4. Instance Efficiency - 实例效率指标
5. Time Model - 时间模型分析
6. Health Check - AWR 健康度综合评估

设计:
- 完全独立,可独立调用
- 与 awr_report 配合使用,如不存在则降级到实时 v$ 查询
- 标准化输出 JSON 结构

文件: monitor/awr_analyzer.py
参考: PHASE5_DEVELOPMENT_DESIGN.md 第三部分 P1-3
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ============================================================================
# 数据类
# ============================================================================

@dataclass
class WaitEvent:
    name: str
    wait_class: str
    total_waits: int
    total_timeouts: int
    time_waited_sec: float
    average_wait_sec: float
    pct_of_total: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TopSql:
    sql_id: str
    executions: int
    elapsed_time_sec: float
    cpu_time_sec: float
    buffer_gets: int
    disk_reads: int
    rows_processed: int
    module: str = ""
    sql_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TopSegment:
    owner: str
    object_name: str
    object_type: str
    logical_reads: int
    physical_reads: int
    buffer_busy_waits: int
    row_lock_waits: int

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InstanceEfficiency:
    buffer_nowait_pct: float = 0.0
    dict_cache_pct: float = 0.0
    library_cache_pct: float = 0.0
    latch_hit_pct: float = 0.0
    soft_parse_pct: float = 0.0
    in_memory_sort_pct: float = 0.0
    parse_to_execute_pct: float = 0.0
    execute_to_parse_pct: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TimeModel:
    component: str
    time_sec: float
    pct_of_db_time: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AwrReport:
    db_id: str
    instance_name: str
    begin_snap_id: int
    end_snap_id: int
    begin_time: str
    end_time: str
    db_time_sec: float = 0.0
    elapsed_time_sec: float = 0.0
    top_wait_events: List[WaitEvent] = field(default_factory=list)
    top_sql: List[TopSql] = field(default_factory=list)
    top_segments: List[TopSegment] = field(default_factory=list)
    instance_efficiency: Optional[InstanceEfficiency] = None
    time_model: List[TimeModel] = field(default_factory=list)
    health_score: float = 0.0
    health_issues: List[str] = field(default_factory=list)
    recommendations: List[str] = field(default_factory=list)
    generated_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "db_id": self.db_id,
            "instance_name": self.instance_name,
            "begin_snap_id": self.begin_snap_id,
            "end_snap_id": self.end_snap_id,
            "begin_time": self.begin_time,
            "end_time": self.end_time,
            "db_time_sec": self.db_time_sec,
            "elapsed_time_sec": self.elapsed_time_sec,
            "top_wait_events": [e.to_dict() for e in self.top_wait_events],
            "top_sql": [s.to_dict() for s in self.top_sql],
            "top_segments": [s.to_dict() for s in self.top_segments],
            "instance_efficiency": self.instance_efficiency.to_dict() if self.instance_efficiency else None,
            "time_model": [t.to_dict() for t in self.time_model],
            "health_score": self.health_score,
            "health_issues": self.health_issues,
            "recommendations": self.recommendations,
            "generated_at": self.generated_at,
        }


# ============================================================================
# AWR 分析器
# ============================================================================

class AwrAnalyzer:
    """AWR 数据分析器

    用法:
        analyzer = AwrAnalyzer(db_connector, db_config)
        report = analyzer.analyze_recent(hours=1)
    """

    def __init__(self, db_connector, db_config):
        self.db_connector = db_connector
        self.db_config = db_config
        self._logger = logging.getLogger("monitor.awr_analyzer")

    def analyze_recent(self, hours: int = 1, top_n: int = 10) -> AwrReport:
        """分析最近 N 小时"""
        snap = self._find_snapshot_window(hours)
        if not snap:
            self._logger.warning("未找到合适的 AWR 快照,降级到实时 v$ 查询")
            return self._fallback_realtime(hours, top_n)
        return self.analyze_snapshots(snap[0], snap[1], top_n)

    def analyze_snapshots(self, begin_snap_id: int, end_snap_id: int,
                         top_n: int = 10) -> AwrReport:
        """分析指定两个快照之间"""
        report = AwrReport(
            db_id=str(self.db_config.id),
            instance_name=self.db_config.instance_name or "",
            begin_snap_id=begin_snap_id,
            end_snap_id=end_snap_id,
            begin_time="",
            end_time="",
            generated_at=datetime.now().isoformat(),
        )
        conn = self.db_connector.connect()
        try:
            cur = conn.cursor()
            # 1) 快照元数据
            cur.execute("""
                SELECT snap_id, begin_interval_time, end_interval_time, instance_number
                FROM dba_hist_snapshot
                WHERE snap_id IN (:1, :2)
            """, [begin_snap_id, end_snap_id])
            snaps = {r[0]: {"begin": r[1], "end": r[2], "inst": r[3]}
                     for r in cur.fetchall()}
            if begin_snap_id in snaps:
                report.begin_time = str(snaps[begin_snap_id]["begin"])
            if end_snap_id in snaps:
                report.end_time = str(snaps[end_snap_id]["end"])
            bid = begin_snap_id
            eid = end_snap_id

            # 2) DB Time
            cur.execute("""
                SELECT value
                FROM dba_hist_sys_time_model
                WHERE snap_id = :1
                  AND stat_name = 'DB time'
            """, [eid])
            e_db_time = (cur.fetchone() or [0])[0] or 0
            cur.execute("""
                SELECT value
                FROM dba_hist_sys_time_model
                WHERE snap_id = :1
                  AND stat_name = 'DB time'
            """, [bid])
            b_db_time = (cur.fetchone() or [0])[0] or 0
            report.db_time_sec = float(e_db_time - b_db_time) / 1e6

            # 3) Elapsed Time
            cur.execute("""
                SELECT end_interval_time - begin_interval_time
                FROM dba_hist_snapshot
                WHERE snap_id = :1
            """, [bid])
            row = cur.fetchone()
            if row:
                # 可能是 timedelta
                elapsed = row[0]
                if hasattr(elapsed, "total_seconds"):
                    report.elapsed_time_sec = elapsed.total_seconds()
                else:
                    report.elapsed_time_sec = float(elapsed)

            # 4) Top Wait Events
            cur.execute("""
                SELECT event_name, wait_class,
                       total_waits - LAG(total_waits)
                          OVER (PARTITION BY event_name ORDER BY snap_id) AS waits_delta,
                       time_waited_micro - LAG(time_waited_micro)
                          OVER (PARTITION BY event_name ORDER BY snap_id) AS time_delta
                FROM dba_hist_system_event
                WHERE snap_id BETWEEN :1 AND :2
                  AND wait_class != 'Idle'
                ORDER BY time_delta DESC NULLS LAST
                FETCH FIRST :3 ROWS ONLY
            """, [bid, eid, top_n])
            total_time = 0
            waits: List[WaitEvent] = []
            for r in cur.fetchall():
                t = (r[3] or 0) / 1e6
                total_time += t
                waits.append(WaitEvent(
                    name=r[0] or "", wait_class=r[1] or "",
                    total_waits=r[2] or 0,
                    total_timeouts=0,
                    time_waited_sec=round(t, 3),
                    average_wait_sec=0.0,
                ))
            for w in waits:
                w.pct_of_total = round(w.time_waited_sec / total_time * 100, 1) if total_time else 0
            report.top_wait_events = waits

            # 5) Top SQL
            cur.execute("""
                SELECT sql_id,
                       executions_delta,
                       elapsed_time_delta/1e6,
                       cpu_time_delta/1e6,
                       buffer_gets_delta,
                       disk_reads_delta,
                       rows_processed_delta,
                       module,
                       SUBSTR(sql_text, 1, 400)
                FROM dba_hist_sqlstat s
                JOIN dba_hist_sqltext t USING (sql_id)
                WHERE snap_id = :1
                  AND executions_delta > 0
                ORDER BY elapsed_time_delta DESC NULLS LAST
                FETCH FIRST :2 ROWS ONLY
            """, [eid, top_n])
            report.top_sql = [
                TopSql(
                    sql_id=r[0] or "", executions=r[1] or 0,
                    elapsed_time_sec=round(r[2] or 0, 3),
                    cpu_time_sec=round(r[3] or 0, 3),
                    buffer_gets=r[4] or 0, disk_reads=r[5] or 0,
                    rows_processed=r[6] or 0, module=r[7] or "",
                    sql_text=r[8] or "",
                ) for r in cur.fetchall()
            ]

            # 6) Top Segments
            cur.execute("""
                SELECT owner, object_name, object_type,
                       logical_reads_delta, physical_reads_delta,
                       buffer_busy_waits_delta, row_lock_waits_delta
                FROM dba_hist_seg_stat
                WHERE snap_id = :1
                ORDER BY logical_reads_delta DESC NULLS LAST
                FETCH FIRST :2 ROWS ONLY
            """, [eid, top_n])
            report.top_segments = [
                TopSegment(
                    owner=r[0] or "", object_name=r[1] or "",
                    object_type=r[2] or "",
                    logical_reads=r[3] or 0, physical_reads=r[4] or 0,
                    buffer_busy_waits=r[5] or 0, row_lock_waits=r[6] or 0,
                ) for r in cur.fetchall()
            ]

            # 7) Instance Efficiency
            cur.execute("""
                SELECT name, value
                FROM dba_hist_sysstat
                WHERE snap_id = :1
                  AND name IN (
                    'session logical reads', 'db block gets', 'consistent gets',
                    'db block gets from cache', 'consistent gets from cache',
                    'gc cr blocks received', 'gc current blocks received',
                    'parse count (total)', 'parse count (hard)',
                    'execute count', 'sorts (memory)', 'sorts (disk)'
                  )
            """, [eid])
            stats = {r[0]: (r[1] or 0) for r in cur.fetchall()}
            cur.execute("""
                SELECT name, value
                FROM dba_hist_sysstat
                WHERE snap_id = :1
                  AND name IN (
                    'parse count (total)', 'parse count (hard)',
                    'execute count'
                  )
            """, [bid])
            b_stats = {r[0]: (r[1] or 0) for r in cur.fetchall()}
            report.instance_efficiency = self._calc_efficiency(stats, b_stats)

            # 8) Time Model
            cur.execute("""
                SELECT stat_name, value
                FROM dba_hist_sys_time_model
                WHERE snap_id IN (:1, :2)
                  AND stat_name IN (
                    'DB time','DB CPU','sql execute elapsed time',
                    'parse time elapsed','hard parse elapsed time',
                    'PL/SQL execution elapsed time','connection management call elapsed time',
                    'sequence load elapsed time','background elapsed time',
                    'background cpu time'
                  )
            """, [bid, eid])
            raw = {}
            for r in cur.fetchall():
                raw.setdefault(r[0], []).append(r[1] or 0)
            total = 0
            tm_list: List[TimeModel] = []
            for k, v in raw.items():
                delta = (v[-1] - v[0]) / 1e6
                tm_list.append(TimeModel(component=k, time_sec=round(delta, 3)))
                total += delta
            for t in tm_list:
                t.pct_of_db_time = round(t.time_sec / total * 100, 1) if total else 0
            tm_list.sort(key=lambda x: x.time_sec, reverse=True)
            report.time_model = tm_list

            # 9) 健康度评估
            self._evaluate_health(report)

        finally:
            self.db_connector.close()
        return report

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _find_snapshot_window(self, hours: int) -> Optional[Tuple[int, int]]:
        """根据时间窗口找 begin/end snap_id"""
        conn = self.db_connector.connect()
        try:
            cur = conn.cursor()
            cur.execute("""
                SELECT snap_id, end_interval_time
                FROM dba_hist_snapshot
                WHERE end_interval_time <= SYSDATE
                ORDER BY end_interval_time DESC
                FETCH FIRST 50 ROWS ONLY
            """)
            rows = cur.fetchall()
            if len(rows) < 2:
                return None
            latest_id = rows[0][0]
            latest_time = rows[0][1]
            target = latest_time - timedelta(hours=hours)
            for sid, etime in rows[1:]:
                if etime <= target:
                    return (sid, latest_id)
            return (rows[1][0], latest_id)
        finally:
            self.db_connector.close()

    def _calc_efficiency(self, e_stats: Dict[str, float],
                         b_stats: Dict[str, float]) -> InstanceEfficiency:
        eff = InstanceEfficiency()
        try:
            slr = e_stats.get("session logical reads", 0) - b_stats.get("session logical reads", 0)
            dbg = e_stats.get("db block gets", 0) - b_stats.get("db block gets", 0)
            cg = e_stats.get("consistent gets", 0) - b_stats.get("consistent gets", 0)
            cache_total = e_stats.get("db block gets from cache", 0) + e_stats.get("consistent gets from cache", 0)
            total_block = dbg + cg
            eff.buffer_nowait_pct = round((cache_total / total_block * 100) if total_block else 100, 2)
            parses = e_stats.get("parse count (total)", 0) - b_stats.get("parse count (total)", 0)
            hard = e_stats.get("parse count (hard)", 0) - b_stats.get("parse count (hard)", 0)
            if parses:
                eff.soft_parse_pct = round((parses - hard) / parses * 100, 2)
            execs = e_stats.get("execute count", 0) - b_stats.get("execute count", 0)
            if parses:
                eff.execute_to_parse_pct = round((execs / parses - 1) * 100, 2) if execs else 0
            smem = e_stats.get("sorts (memory)", 0)
            sdisk = e_stats.get("sorts (disk)", 0)
            stotal = smem + sdisk
            if stotal:
                eff.in_memory_sort_pct = round(smem / stotal * 100, 2)
        except Exception as e:
            self._logger.warning("计算效率指标失败: %s", e)
        return eff

    def _evaluate_health(self, report: AwrReport):
        """根据指标评估健康度并生成建议"""
        score = 100.0
        issues: List[str] = []
        recs: List[str] = []

        # 1) 等待事件分析
        for w in report.top_wait_events[:3]:
            if w.time_waited_sec > 60 and w.pct_of_total > 30:
                score -= 5
                if "log file sync" in w.name.lower():
                    issues.append(f"日志同步等待高({w.time_waited_sec:.0f}s)")
                    recs.append("检查 LGWR 性能,减少事务提交频率或增加 LOG_BUFFER")
                elif "db file sequential read" in w.name.lower():
                    issues.append(f"单块读等待高({w.time_waited_sec:.0f}s)")
                    recs.append("检查热表的索引设计,考虑增加 KEEP 池缓存")
                elif "db file scattered read" in w.name.lower():
                    issues.append(f"多块读等待高({w.time_waited_sec:.0f}s)")
                    recs.append("检查全表扫描 SQL,可能缺少合适索引")
                elif "latch" in w.name.lower():
                    issues.append(f"闩锁争用高({w.time_waited_sec:.0f}s)")
                    recs.append("分析 shared pool/cursor 争用")
                elif "enq" in w.name.lower():
                    issues.append(f"队列锁等待高({w.time_waited_sec:.0f}s)")
                    recs.append("分析锁竞争来源,优化事务隔离")

        # 2) DB Time / Elapsed Time
        if report.elapsed_time_sec > 0:
            load = report.db_time_sec / report.elapsed_time_sec
            if load > 4:
                score -= 15
                issues.append(f"DB Time 负载过高 ({load:.1f}x)")
                recs.append("CPU 严重饱和,需扩容或优化 TOP SQL")
            elif load > 2:
                score -= 8
                issues.append(f"DB Time 负载较高 ({load:.1f}x)")
                recs.append("数据库负载偏高,需优化 TOP SQL")

        # 3) Instance Efficiency
        if report.instance_efficiency:
            ie = report.instance_efficiency
            if ie.soft_parse_pct < 90:
                score -= 5
                issues.append(f"软解析率低 ({ie.soft_parse_pct:.1f}%)")
                recs.append("应用层应使用绑定变量减少硬解析")
            if ie.in_memory_sort_pct < 95:
                score -= 3
                issues.append(f"内存排序率低 ({ie.in_memory_sort_pct:.1f}%)")
                recs.append("考虑增大 PGA_AGGREGATE_TARGET")
            if ie.buffer_nowait_pct < 99:
                score -= 3
                issues.append(f"Buffer Cache 命中率低 ({ie.buffer_nowait_pct:.1f}%)")
                recs.append("考虑增大 DB_CACHE_SIZE")

        # 4) Top SQL 异常
        for sql in report.top_sql[:3]:
            if sql.elapsed_time_sec > 300:
                score -= 3
                issues.append(f"SQL {sql.sql_id[:10]}... 单次执行 {sql.elapsed_time_sec:.0f}s")
                recs.append(f"对 sql_id={sql.sql_id} 进行 SQL Tuning Advisor 分析")

        # 5) Top Segments 异常
        for seg in report.top_segments[:3]:
            if seg.physical_reads > 100000:
                score -= 2
                recs.append(f"热点段 {seg.owner}.{seg.object_name} 物理读高({seg.physical_reads}),考虑 KEEP 池")

        report.health_score = max(0.0, round(score, 1))
        report.health_issues = issues
        report.recommendations = recs

    def _fallback_realtime(self, hours: int, top_n: int) -> AwrReport:
        """无 AWR 快照时使用实时 v$ 视图"""
        report = AwrReport(
            db_id=str(self.db_config.id),
            instance_name=self.db_config.instance_name or "",
            begin_snap_id=0, end_snap_id=0,
            begin_time="", end_time="",
            generated_at=datetime.now().isoformat(),
        )
        conn = self.db_connector.connect()
        try:
            cur = conn.cursor()
            # 实时等待事件
            cur.execute("""
                SELECT event, wait_class, total_waits, time_waited_micro/1e6 AS tw
                FROM v$system_event
                WHERE wait_class != 'Idle'
                ORDER BY time_waited_micro DESC
                FETCH FIRST :1 ROWS ONLY
            """, [top_n])
            total = sum(r[3] or 0 for r in cur.fetchall()) or 1
            report.top_wait_events = [
                WaitEvent(
                    name=r[0] or "", wait_class=r[1] or "",
                    total_waits=r[2] or 0, total_timeouts=0,
                    time_waited_sec=round(r[3] or 0, 3),
                    average_wait_sec=0.0,
                    pct_of_total=round((r[3] or 0) / total * 100, 1),
                ) for r in cur.fetchall()
            ]
            report.health_issues.append("降级模式:无 AWR 快照,使用实时 v$ 数据")
            report.health_score = 50.0
        finally:
            self.db_connector.close()
        return report


# ============================================================================
# 便捷函数
# ============================================================================

def analyze_awr(db_config, hours: int = 1) -> Dict[str, Any]:
    """快速 AWR 分析入口"""
    from monitor.db_connector import get_connector
    connector = get_connector(db_config)
    analyzer = AwrAnalyzer(connector, db_config)
    report = analyzer.analyze_recent(hours=hours)
    return report.to_dict()
