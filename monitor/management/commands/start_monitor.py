# -*- coding: utf-8 -*-
"""
全栈数据库监控守护进程 (v3.0 - Phase 2 智能增强版)

架构改进 (v3.0):
- Checker 类拆分为独立模块 (monitor/checkers/)
- 导入 OracleChecker, MySQLChecker, PostgreSQLChecker,
  DamengChecker, GbaseChecker, TDSQLChecker
- 仅保留 Command 调度器逻辑
"""

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
import datetime
import json
import time

from apscheduler.schedulers.blocking import BlockingScheduler
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection

from monitor.alert_manager import AlertManager
from monitor.alert_engine import AlertEngine  # Phase 3 智能告警引擎
from monitor.baseline_engine import BaselineEngine
from monitor.rca_engine import RCAEngine
from monitor.capacity_engine import CapacityEngine
from monitor.health_engine import HealthEngine
from monitor.models import DatabaseConfig, MonitorLog
from monitor.api_views import get_effective_alert_config

# Checker 类迁移至独立模块
from monitor.checkers import (
    BaseDBChecker,
    OracleChecker,
    MySQLChecker,
    PostgreSQLChecker,
    GbaseChecker,
    TDSQLChecker,
    CHECKER_MAP as _CHECKER_MAP,
    get_checker,
    COLLECT_TIMEOUT_SEC,
    COLLECT_WORKERS,
    TBS_THRESHOLD,
    LOCK_TIME_THRESHOLD,
    CONN_THRESHOLD_PCT,
    ENABLE_PHASE2_ENGINES,
    CAPACITY_CHECK_INTERVAL_HOURS,
    HEALTH_CHECK_INTERVAL_HOURS,
)

# 达梦 DM8 驱动为可选依赖
try:
    from monitor.checkers import DamengChecker
except ImportError:
    DamengChecker = None

# v3.0: 是否使用 Celery 异步采集 (优先使用 Celery，不可用时回退 ThreadPool)
USE_CELERY = getattr(settings, 'MONITOR_USE_CELERY', False)


# ==========================================
# Redis 预留存根
# ==========================================
class RedisChecker(BaseDBChecker):
    """Redis 监控 - 预留实现"""

    def check(self, config):
        # TODO: 需要安装 redis-py
        self.cmd.process_result(config, 'DOWN', {
            "error": "Redis 监控尚未实现，需要安装 redis-py"
        })


# ==========================================
# 主命令类
# ==========================================
class Command(BaseCommand):
    help = '全能数据库监控守护进程 (v3.0 - Phase 2 智能增强版 + 模块化Checkers)'

    # 数据库类型 -> 检查器映射
    CHECKER_MAP = {
        'oracle': OracleChecker,
        'mysql': MySQLChecker,
        'pgsql': PostgreSQLChecker,
        'gbase': GbaseChecker,
        'tdsql': TDSQLChecker,
        'redis': RedisChecker,
        'mongo': None,  # TODO: MongoDB 支持
    }
    if DamengChecker is not None:
        CHECKER_MAP['dm'] = DamengChecker

    def add_arguments(self, parser):
        parser.add_argument(
            '--once', action='store_true',
            help='只执行一轮采集后退出（调试/手动触发用）',
        )

    def handle(self, *args, **options):
        if options.get('once'):
            print(f"[{datetime.datetime.now()}] 单轮采集模式 (--once)")
            self.monitor_job()
            return

        print(f"[{datetime.datetime.now()}] 全栈监控守护进程 v3.0 (Phase 2 智能增强版 + 模块化Checkers) 已启动")
        print(f">> 支持的数据库：Oracle, MySQL, PostgreSQL, 达梦, Gbase8a, TDSQL")
        print(f">> Phase 2 智能特性：168时间槽基线 | RCA根因分析 | 容量预测 | 健康评分")
        print(f">> Checker 模块化: monitor/checkers/ (base, oracle, mysql, pgsql, dm, gbase, tdsql)")

        if ENABLE_PHASE2_ENGINES:
            print(f">> Phase 2 引擎: 已启用")
        else:
            print(f">> Phase 2 引擎: 已禁用 (设置 ENABLE_PHASE2_ENGINES=True 启用)")

        scheduler = BlockingScheduler()
        scheduler.add_job(self.monitor_job, 'interval', seconds=60)
        # 告警运维：自动升级扫描 + 聚合冲刷（接通 AlertManager 升级/聚合能力）
        scheduler.add_job(self.alert_housekeeping_job, 'interval', seconds=60)

        # 立即执行一次
        self.monitor_job()

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            print("\n监控进程已停止")

    def alert_housekeeping_job(self):
        """周期告警运维：对每个活跃库执行自动升级扫描 + 聚合冲刷。"""
        from monitor.alert_manager import AlertManager
        from monitor.models import DatabaseConfig
        for cfg in DatabaseConfig.objects.filter(is_active=True):
            try:
                am = AlertManager(cfg)
                n = am.run_escalation_scan()
                am.flush_expired_aggregations()
                if n:
                    print(f"[ALERT-OPS] {cfg.name}: 升级 {n} 条告警")
            except Exception as e:
                logger.warning("[ALERT-OPS] %s 告警运维失败: %s", cfg.name, e)
        # W4 自监控：失联组件扫描（复用 AlertManager 告警链路）
        try:
            from monitor.self_monitor import run_heartbeat_check
            n_stale = run_heartbeat_check()
            if n_stale:
                print(f"[ALERT-OPS] 自监控: {n_stale} 个组件失联")
        except Exception as e:
            logger.warning("[ALERT-OPS] 自监控扫描失败: %s", e)

    def _run_single_check(self, config):
        """在独立线程中执行单个数据库的采集，超时后自动记录 DOWN"""
        # 每个线程需要独立关闭复用的 Django DB 连接，避免跨线程复用问题
        connection.close_if_unusable_or_obsolete()
        try:
            checker_class = self.CHECKER_MAP.get(config.db_type)
            if checker_class:
                checker = checker_class(self)
                checker.check(config)
            elif config.db_type == 'mongo':
                print(f"  -- 跳过暂不支持的类型：{config.name} (MongoDB)")
            else:
                print(f"  -- 跳过未知类型：{config.name} ({config.db_type})")
        finally:
            # 强制关闭本 worker 线程的 Django 连接。
            # ThreadPoolExecutor 每轮新建线程, close_old_connections() 只关超过
            # CONN_MAX_AGE 的连接, 关不掉本轮刚建的; 线程随执行器销毁后连接残留,
            # 逐轮累积会耗尽 PostgreSQL 连接 (too many clients)。connection 为
            # 线程本地对象, close() 只影响当前线程, 不会误伤其他线程/主线程。
            connection.close()

    def _celery_dispatch_job(self, configs):
        """使用 Celery 异步分发采集任务（v3.0 推荐模式）"""
        from monitor.tasks import collect_single_db

        print(f"  [Celery] 分发 {len(configs)} 个采集任务...")
        async_results = {}
        for cfg in configs:
            try:
                result = collect_single_db.delay(cfg.id)
                async_results[cfg] = result
            except Exception as e:
                print(f"  [Celery] 分发 [{cfg.name}] 失败: {e}")
                self.process_result(cfg, 'DOWN', {'error': f'Celery分发失败: {str(e)}'})

        # 等待结果（带超时）
        for cfg, result in async_results.items():
            try:
                status, data = result.get(timeout=COLLECT_TIMEOUT_SEC)
                self.process_result(cfg, status, data)
            except Exception as e:
                print(f"  [Celery] [{cfg.name}] 获取结果异常: {e}")
                self.process_result(cfg, 'DOWN', {'error': f'Celery获取结果超时/异常: {str(e)}'})

    def _threadpool_job(self, configs):
        """使用 ThreadPoolExecutor 本地线程采集（回退模式）"""
        print(f"  [ThreadPool] 并发采集 {len(configs)} 个数据库...")
        with ThreadPoolExecutor(max_workers=min(COLLECT_WORKERS, len(configs))) as executor:
            futures = {executor.submit(self._run_single_check, cfg): cfg for cfg in configs}
            for future, cfg in futures.items():
                try:
                    future.result(timeout=COLLECT_TIMEOUT_SEC)
                except FuturesTimeoutError:
                    print(f"  [TIMEOUT] [{cfg.name}] 采集超时 ({COLLECT_TIMEOUT_SEC}s)，记录 DOWN")
                    self.process_result(cfg, 'DOWN', {'error': f'采集超时 {COLLECT_TIMEOUT_SEC}s'})
                except Exception as e:
                    print(f"  [{cfg.name}] 采集线程异常：{e}")
                    self.process_result(cfg, 'DOWN', {'error': f'采集线程异常：{str(e)}'})

    def monitor_job(self):
        """统一巡检入口：支持 Celery 异步模式和 ThreadPool 本地模式（v3.0）"""
        print(f"\n[{datetime.datetime.now()}] --- 开始新一轮巡检 ---")
        connection.close_if_unusable_or_obsolete()

        configs = list(DatabaseConfig.objects.filter(is_active=True))
        if not configs:
            print("  没有活跃的数据库配置，跳过本轮巡检")
            return

        if USE_CELERY:
            self._celery_dispatch_job(configs)
        else:
            self._threadpool_job(configs)

        # W4 自监控：本轮采集完成，上报采集器心跳
        from monitor.self_monitor import report
        report('collector', {'db_count': len(configs)})

    def process_result(self, config, current_status, data):
        """统一结果处理和告警逻辑（v3.0：Phase 2 智能引擎集成）"""

        # 计数器型指标先算增量, 使其进入 MonitorLog 供检测/验证使用
        if current_status == 'UP':
            self._compute_counter_deltas(config, data)

        def notify(title, body):
            self.send_alert(config, title, body)

        am = AlertManager(config, notify)

        # --- 1. 连通性告警 ---
        am.fire_or_resolve(
            condition=(current_status == 'DOWN'),
            alert_type='down', metric_key='',
            fire_title='[DOWN] 故障告警',
            fire_body=f"数据库无法连接\n错误：{data.get('error', '未知错误')}",
            resolve_title='[RECOVERED] 恢复通知',
            resolve_body='数据库已重新恢复连接',
            severity='critical',
        )

        # --- 2. 业务监控（仅 UP 状态）---
        if current_status == 'UP':

            # A. 表空间容量告警（动态三级阈值）
            tbs_cfg = get_effective_alert_config(config, 'tablespace_usage_pct')
            tbs_warn_val = (tbs_cfg.get('warn_threshold') if tbs_cfg else None) or TBS_THRESHOLD
            tbs_err_val = (tbs_cfg.get('error_threshold') if tbs_cfg else None) or TBS_THRESHOLD + 5
            tbs_crit_val = (tbs_cfg.get('critical_threshold') if tbs_cfg else None) or TBS_THRESHOLD + 10
            tablespaces = data.get('tablespaces', [])
            tbs_critical = [t['name'] for t in tablespaces if (t.get('used_pct') or 0) > tbs_crit_val]
            tbs_error = [t['name'] for t in tablespaces if tbs_err_val < (t.get('used_pct') or 0) <= tbs_crit_val]
            tbs_warn_ = [t['name'] for t in tablespaces if tbs_warn_val < (t.get('used_pct') or 0) <= tbs_err_val]
            tbs_any_hit = tbs_critical or tbs_error or tbs_warn_
            if tbs_any_hit:
                tbs_sev = 'critical' if tbs_critical else ('error' if tbs_error else 'warning')
                tbs_detail = (
                    (f"三级(>{tbs_crit_val}%): {', '.join(tbs_critical)}\n" if tbs_critical else '') +
                    (f"二级(>{tbs_err_val}%): {', '.join(tbs_error)}\n" if tbs_error else '') +
                    (f"一级(>{tbs_warn_val}%): {', '.join(tbs_warn_)}" if tbs_warn_ else '')
                ).strip()
                am.fire_or_resolve(
                    condition=True,
                    alert_type='tablespace', metric_key='',
                    fire_title=f'[{tbs_sev.upper()}] 容量告警',
                    fire_body=f"表空间使用率告警：\n{tbs_detail}",
                    resolve_title='[RECOVERED] 容量恢复',
                    resolve_body='所有表空间使用率已降至阈值以下',
                    severity=tbs_sev,
                )
            else:
                am.fire_or_resolve(
                    condition=False,
                    alert_type='tablespace', metric_key='',
                    fire_title='[WARNING] 容量告警',
                    fire_body='',
                    resolve_title='[RECOVERED] 容量恢复',
                    resolve_body='所有表空间使用率已降至阈值以下',
                )

            # B. 连接数使用率告警（动态三级阈值）
            conn_usage = data.get('conn_usage_pct', 0)
            conn_cfg = get_effective_alert_config(config, 'conn_usage_pct')
            conn_warn_val = (conn_cfg.get('warn_threshold') if conn_cfg else None) or CONN_THRESHOLD_PCT
            conn_err_val = (conn_cfg.get('error_threshold') if conn_cfg else None) or CONN_THRESHOLD_PCT + 10
            conn_crit_val = (conn_cfg.get('critical_threshold') if conn_cfg else None) or CONN_THRESHOLD_PCT + 20
            conn_sev = ('critical' if conn_usage > conn_crit_val
                        else 'error' if conn_usage > conn_err_val
                        else 'warning')
            am.fire_or_resolve(
                condition=(conn_usage > conn_warn_val),
                alert_type='connection', metric_key='conn_usage_pct',
                fire_title=f'[{conn_sev.upper()}] 连接数告警',
                fire_body=(f"连接数使用率已达 {conn_usage}%（一级>{conn_warn_val}% 二级>{conn_err_val}% 三级>{conn_crit_val}%）\n"
                           f"当前连接：{data.get('active_connections', 0)}\n"
                           f"最大连接：{data.get('max_connections', 0)}"),
                resolve_title='[RECOVERED] 连接数恢复',
                resolve_body=f"连接数使用率已恢复正常（当前 {conn_usage}%）",
                severity=conn_sev,
            )

            # C. 锁等待告警
            current_locks = data.get('locks', [])
            am.fire_or_resolve(
                condition=bool(current_locks),
                alert_type='lock', metric_key='',
                fire_title='[CRITICAL] 性能告警：锁等待',
                fire_body=self._build_lock_msg(current_locks),
                resolve_title='[RECOVERED] 锁等待解除',
                resolve_body='数据库阻塞已全部解除',
                severity='critical',
            )
            if current_locks:
                print(f"  [LOCK] {len(current_locks)} 个阻塞会话")

            # ======================================
            # D. Phase 2: 智能引擎分析
            # ======================================
            if ENABLE_PHASE2_ENGINES:
                self._run_phase2_analysis(config, data, am)

        # --- 3. 记录监控日志 ---
        MonitorLog.objects.create(
            config=config,
            status=current_status,
            message=json.dumps(data, ensure_ascii=False, default=str)
        )

        # --- 3.2 Phase 6A: 采集尾部三层检测 → 发事件到 Stream ---
        if current_status == 'UP':
            try:
                self._emit_phase6_events(config, data)
            except Exception as e6:
                print(f"  [6A] 事件检测失败: {e6}")

        # --- 3.3 Phase 7A: sql_stat 快照增量 + cpu_cores + 计划采集 ---
        if current_status == 'UP':
            try:
                self._collect_perf_extras(config)
            except Exception as e7:
                print(f"  [7A] 性能采集失败: {e7}")

        # --- 3.5 SSE 实时推送指标更新 ---
        try:
            from monitor.sse_views import publish_metric_event
            numeric_metrics = {k: v for k, v in data.items()
                               if isinstance(v, (int, float)) and not isinstance(v, bool)}
            if numeric_metrics:
                publish_metric_event(config.id, config.name, config.db_type, numeric_metrics)
        except Exception:
            pass

        # --- 4. 同步写入 TimescaleDB（指标时序数据）---
        try:
            from monitor.timeseries import get_timeseries_storage
            ts = get_timeseries_storage()
            if ts.enabled:
                # 提取数值型指标写入 TimescaleDB
                numeric_metrics = {}
                for key, value in data.items():
                    if isinstance(value, (int, float)) and value is not None and not isinstance(value, bool):
                        numeric_metrics[key] = float(value)
                if numeric_metrics:
                    ts.write_metrics_batch(config.id, numeric_metrics, status=current_status)
                # 写入采集快照
                ts.write_snapshot(config.id, current_status, data)
        except Exception as ts_err:
            print(f"  [TSDB] 写入 TimescaleDB 失败: {ts_err}")

        # --- 5. 异步写入 Elasticsearch（告警搜索索引）---
        try:
            from monitor.elasticsearch_engine import (
                bulk_index_metrics,
                get_es_client,
                get_metrics_index_name,
            )
            es_client = get_es_client()
            if es_client:
                # 构建 ES 文档列表
                es_docs = []
                for key, value in data.items():
                    if isinstance(value, (int, float)) and value is not None and not isinstance(value, bool):
                        es_docs.append({
                            "_index": get_metrics_index_name(),
                            "config_id": config.id,
                            "db_type": config.db_type,
                            "db_name": config.name,
                            "metric_name": key,
                            "value": float(value),
                            "status": current_status,
                            "timestamp": datetime.datetime.now().isoformat(),
                        })
                # 添加集群级指标
                # ES 索引 value 字段是 float 映射，字符串健康状态需转数值评分，
                # 原始文本保留在 value_text（数值语义: 越低越差, 便于 down 方向告警）
                health_score_map = {
                    'HEALTHY': 100, 'NORMAL': 100, 'OK': 100,
                    'DEGRADED': 50, 'WARNING': 50,
                    'UNHEALTHY': 25,
                    'CRITICAL': 0, 'DOWN': 0,
                    'UNKNOWN': -1, 'N/A': -1,
                }
                for key in ['dw_replication_health', 'dsc_cluster_health',
                            'gbase_cluster_health', 'tdsql_cluster_health']:
                    if key in data:
                        raw = data[key]
                        if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                            numeric = float(raw)
                        else:
                            numeric = float(health_score_map.get(
                                str(raw).upper().strip(), -1))
                        es_docs.append({
                            "_index": get_metrics_index_name(),
                            "config_id": config.id,
                            "db_type": config.db_type,
                            "db_name": config.name,
                            "metric_name": key,
                            "value": numeric,
                            "value_text": str(raw),
                            "status": current_status,
                            "timestamp": datetime.datetime.now().isoformat(),
                        })

                if es_docs:
                    bulk_index_metrics(es_docs)
        except Exception as es_err:
            print(f"  [ES] 写入 Elasticsearch 失败: {es_err}")

    def _emit_phase6_events(self, config, data):
        """Phase 6A: 采集尾部跑 L1/L2/L3 检测, 命中即发事件到 Redis Stream。

        L1 硬阈值(连接/空间/复制) + L2 自适应基线 + L3 复合规则(死锁增量在此算)。
        锁阻塞由哨兵 ASH 负责, 此处不重复。
        """
        from monitor.detectors import detect_l1, detect_l2, detect_l3
        from monitor.detectors.config_drift import detect_config_drift
        from monitor.redis_bus import emit_event

        events = []
        try:
            events += detect_l1(config, data)
        except Exception as e:
            print(f"  [6A-L1] {e}")
        try:
            events += detect_config_drift(config, data)
        except Exception as e:
            print(f"  [6A-drift] {e}")
        try:
            events += detect_l2(config, data)
        except Exception as e:
            print(f"  [6A-L2] {e}")
        try:
            # 基线均值供 L3 (从 data 里取不到, 用简化: 传空, L3 里 mean 缺失则跳过突增规则)
            events += detect_l3(config, data, baseline_means=self._collect_baseline_means(config, data))
        except Exception as e:
            print(f"  [6A-L3] {e}")

        for e in events:
            try:
                emit_event(e)
            except Exception as ee:
                print(f"  [6A-emit] {ee}")
        if events:
            print(f"  [6A] {config.name}: 发出 {len(events)} 个事件 "
                  f"({','.join(sorted({x['signal'] for x in events}))})")

    def _compute_counter_deltas(self, config, data):
        """计数器型指标算本轮增量 (进程内缓存上一轮值)。

        slow_queries/innodb_deadlocks 是累计计数器, 绝对值无法判"突增";
        增量进 MonitorLog 后供 L3 检测与验证回路使用。计数器回绕(重启)跳过一轮。
        """
        if not hasattr(self, '_counter_prev'):
            self._counter_prev = {}
        prev = self._counter_prev.setdefault(config.id, {})
        for key, out_key in (('slow_queries', 'slow_queries_delta'),
                             ('innodb_deadlocks', '_deadlock_delta_5min')):
            cur = data.get(key)
            if isinstance(cur, (int, float)):
                p = prev.get(key)
                if p is not None and cur >= p:
                    data[out_key] = cur - p
                prev[key] = cur

    # ================= Phase 7A-07/08: SQL 统计快照 + 计划采集 =================
    _SQLSTAT_TOPN = 100

    def _collect_perf_extras(self, config):
        """sql_stat 快照增量 + cpu_cores 回填 + 每小时 Top SQL 计划采集 (phase7/10 §8/§9)。"""
        if config.db_type not in ('mysql', 'tdsql', 'gbase', 'pgsql', 'oracle', 'dm'):
            return
        from monitor.db_connector import DbConnector
        conn = None
        try:
            conn = DbConnector.get_connection(config)
            cur = conn.cursor()
            try:
                rows = self._snapshot_sqlstat(config, cur)
                if rows:
                    from monitor.timeseries import get_timeseries_storage
                    get_timeseries_storage().write_sql_stats(config.id, rows)
                self._refresh_cpu_cores(config, cur)
            finally:
                cur.close()
            self._hourly_plan_capture(config, conn)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

    def _snapshot_sqlstat(self, config, cur):
        """逐库拉 TopN digest 累计值 → 与上轮差分 → 返回增量行。回绕(重启)跳过该轮。"""
        from monitor.sqlfingerprint import unified_digest
        snap = {}  # digest -> (exec, elapsed_ms, rows, reads, text, db_name)
        if config.db_type in ('mysql', 'tdsql', 'gbase'):
            cur.execute(
                "SELECT DIGEST, SCHEMA_NAME, COUNT_STAR, SUM_TIMER_WAIT, "
                "SUM_ROWS_SENT, SUM_ROWS_EXAMINED, LEFT(DIGEST_TEXT,500) AS txt "
                "FROM performance_schema.events_statements_summary_by_digest "
                "WHERE DIGEST IS NOT NULL "
                f"ORDER BY SUM_TIMER_WAIT DESC LIMIT {self._SQLSTAT_TOPN}")
            for r in cur.fetchall():
                d = unified_digest(config.db_type, r['DIGEST'], None)
                snap[d] = (int(r['COUNT_STAR']), int(r['SUM_TIMER_WAIT']) // 10**9,
                           int(r['SUM_ROWS_SENT']), int(r['SUM_ROWS_EXAMINED']),
                           r['txt'], r['SCHEMA_NAME'])
        elif config.db_type in ('pgsql', 'postgresql'):
            try:
                cur.execute(
                    "SELECT queryid, calls, total_exec_time, rows, "
                    "shared_blks_hit + shared_blks_read AS reads, LEFT(query,500) "
                    "FROM pg_stat_statements WHERE queryid IS NOT NULL "
                    f"ORDER BY total_exec_time DESC LIMIT {self._SQLSTAT_TOPN}")
                for qid, calls, ms, rows_, reads, txt in cur.fetchall():
                    d = unified_digest(config.db_type, str(qid), txt)
                    snap[d] = (int(calls), int(ms), int(rows_), int(reads), txt, None)
            except Exception:
                # pg_stat_statements 未安装 (7D-04 环境准备) → 静默跳过
                try:
                    cur.connection.rollback()
                except Exception:
                    pass
                return []
        elif config.db_type in ('oracle', 'dm'):
            cur.execute(
                "SELECT sql_id, executions, elapsed_time, rows_processed, buffer_gets, "
                "sql_text FROM v$sqlstats "
                f"ORDER BY elapsed_time DESC FETCH FIRST {self._SQLSTAT_TOPN} ROWS ONLY")
            for sql_id, execs, us, rows_, reads, txt in cur.fetchall():
                snap[str(sql_id)] = (int(execs or 0), int(us or 0) // 1000,
                                     int(rows_ or 0), int(reads or 0),
                                     (txt or '')[:500], None)
        if not snap:
            return []

        if not hasattr(self, '_sqlstat_prev'):
            self._sqlstat_prev = {}
        prev = self._sqlstat_prev.setdefault(config.id, {})
        out = []
        for digest, (execs, ms, rows_, reads, txt, dbn) in snap.items():
            p = prev.get(digest)
            if p is not None:
                d_exec = execs - p[0]
                if d_exec > 0 and ms >= p[1]:  # 回绕保护
                    out.append({'sql_digest': digest, 'db_name': dbn,
                                'exec_delta': d_exec, 'elapsed_ms_delta': ms - p[1],
                                'rows_delta': max(rows_ - p[2], 0),
                                'reads_delta': max(reads - p[3], 0),
                                'sql_text_sample': txt})
            prev[digest] = (execs, ms, rows_, reads)
        if len(prev) > 2000:  # 缓存兜底
            self._sqlstat_prev[config.id] = dict(list(prev.items())[-1000:])
        return out

    def _refresh_cpu_cores(self, config, cur):
        """采集 CPU 核数并回填 config.cpu_cores (采集值优先, 手工值兜底)。"""
        cores = None
        try:
            if config.db_type in ('oracle', 'dm'):
                cur.execute("SELECT value FROM v$parameter WHERE name='cpu_count'")
                row = cur.fetchone()
                cores = int(row[0]) if row and row[0] else None
        except Exception:
            return
        if cores and cores != config.cpu_cores:
            config.cpu_cores = cores
            config.save(update_fields=['cpu_cores'])

    def _hourly_plan_capture(self, config, conn):
        """每小时对该实例近 1h 耗时 Top10 digest 自动采集执行计划 (phase7/10 §9)。"""
        import time as _t
        if not hasattr(self, '_last_plan_at'):
            self._last_plan_at = {}
        if _t.time() - self._last_plan_at.get(config.id, 0) < 3600:
            return
        self._last_plan_at[config.id] = _t.time()
        try:
            from monitor.timeseries import get_timeseries_storage
            with get_timeseries_storage().cursor() as tcur:
                if tcur is None:
                    return
                tcur.execute(
                    "SELECT sql_digest, MAX(db_name) FROM sql_stat WHERE db_config_id=%s "
                    "AND time > NOW() - interval '1 hour' "
                    "GROUP BY sql_digest ORDER BY SUM(elapsed_ms_delta) DESC LIMIT 10",
                    (config.id,))
                digests = tcur.fetchall()
        except Exception:
            return
        if not digests:
            return
        from monitor.plan_capture import capture
        for d, stat_db in digests:
            text, ash_db = self._raw_sql_for_digest(config.id, d)
            capture(config, d, sql_text=text, source='auto', conn=conn,
                    db_name=ash_db or stat_db)

    def _raw_sql_for_digest(self, config_id, digest):
        """从 ASH 样本取该 digest 最近一条原文与库名 (带字面量, EXPLAIN 用)。"""
        try:
            from monitor.timeseries import get_timeseries_storage
            with get_timeseries_storage().cursor() as cur:
                if cur is None:
                    return None, None
                cur.execute(
                    "SELECT sql_text, db_name FROM session_sample WHERE db_config_id=%s "
                    "AND sql_digest=%s AND sql_text IS NOT NULL AND sql_text <> '' "
                    "ORDER BY time DESC LIMIT 1", (config_id, digest))
                row = cur.fetchone()
                return (row[0], row[1]) if row else (None, None)
        except Exception:
            return None, None

    def _collect_baseline_means(self, config, data):
        """为 L3 提供关键指标基线均值 (当前时间槽, 每实例每小时缓存一次)。失败返回空。"""
        try:
            from monitor.baseline_engine import BaselineEngine
            if not hasattr(self, '_baseline_mean_cache'):
                self._baseline_mean_cache = {}
            cache = self._baseline_mean_cache.setdefault(config.id, {'at': 0, 'means': {}})
            if time.time() - cache['at'] > 3600:
                eng = BaselineEngine(config)
                slot = eng._get_current_time_slot()
                means = {}
                for k in ('threads_connected', 'threads_running', 'active_connections'):
                    if k not in data:
                        continue
                    try:
                        by_slot = eng.calculate_baseline_for_metric(k) or {}
                        model = by_slot.get(slot)
                        if model and getattr(model, 'mean', 0):
                            means[k] = model.mean
                    except Exception:
                        pass
                cache['means'] = means
                cache['at'] = time.time()
            return dict(cache['means'])
        except Exception:
            return {}

    def _run_phase2_analysis(self, config, data, am):
        """
        Phase 2 智能引擎分析

        包含:
        - 168时间槽动态基线异常检测
        - RCA根因分析
        - 容量预测 (定期)
        - 健康评分 (定期)
        """
        _p2_start = datetime.datetime.now()

        # --- D1. 基线异常检测 (168时间槽 + 三重条件 + Phase 3智能告警收敛) ---
        try:
            baseline_engine = BaselineEngine(config)
            # Phase 3: 初始化智能告警引擎（必须传入 baseline_engine）
            alert_engine = AlertEngine(config, baseline_engine)

            anomalies = baseline_engine.check_current_against_baseline(data)

            # 增量更新基线模型（Welford 在线算法，O(1)复杂度）
            try:
                baseline_engine.update_baseline(data)
            except Exception as ub_err:
                print(f"  [BASELINE-UPDATE] 基线更新失败: {ub_err}")

            anomaly_keys = set()
            for metric_name, current_val, baseline, anomaly_type, sev, _reason in anomalies:
                anomaly_keys.add(metric_name)

                direction_str = 'up' if anomaly_type == 'high' else 'down'

                # 获取该指标的振幅百分比告警配置
                amp_cfg = get_effective_alert_config(config, metric_name)
                amp_severity = None
                amp_pct = None
                if amp_cfg and amp_cfg.get('rule_type') == 'baseline_amplitude' and baseline.mean != 0:
                    deviation_pct = abs(current_val - baseline.mean) / abs(baseline.mean) * 100
                    amp_pct = round(deviation_pct, 1)
                    crit_pct = amp_cfg.get('critical_amplitude_pct')
                    err_pct = amp_cfg.get('error_amplitude_pct')
                    warn_pct = amp_cfg.get('warn_amplitude_pct')
                    direction_ok = (
                        amp_cfg.get('direction') == 'both'
                        or (amp_cfg.get('direction') == 'up' and anomaly_type == 'high')
                        or (amp_cfg.get('direction') == 'down' and anomaly_type == 'low')
                    )
                    if direction_ok:
                        if crit_pct is not None and deviation_pct >= crit_pct:
                            amp_severity = 'critical'
                        elif err_pct is not None and deviation_pct >= err_pct:
                            amp_severity = 'error'
                        elif warn_pct is not None and deviation_pct >= warn_pct:
                            amp_severity = 'warning'

                # Phase 3: 使用 AlertEngine.should_alert() 进行收敛判断
                # should_alert 返回 (bool, Optional[AlertEvent])
                should_fire, alert_event = alert_engine.should_alert(metric_name, current_val, direction_str)

                # 振幅配置命中时，用振幅等级替换原始等级
                final_severity = amp_severity or (alert_event.severity if alert_event else None) or sev

                if should_fire or amp_severity:
                    normal_range = f"{baseline.normal_min:.2f} ~ {baseline.normal_max:.2f}"
                    direction_label = '暴涨' if anomaly_type == 'high' else '骤降'
                    sev_label = final_severity or sev
                    emoji = '[CRITICAL]' if sev_label in ('critical', 'emergency') else '[WARNING]'
                    amp_line = f"振幅偏离：{amp_pct}%\n" if amp_pct is not None else ''
                    body = (
                        f"指标：{metric_name}\n"
                        f"当前值：{current_val}\n"
                        f"基线均值：{baseline.mean:.2f} ± {baseline.std:.2f}\n"
                        f"正常范围：{normal_range}\n"
                        f"{amp_line}"
                        f"偏离类型：{direction_label}\n"
                        f"告警等级：{sev_label}\n"
                        f"建议：检查是否有异常业务行为或潜在故障"
                    )
                    am.fire(
                        alert_type='baseline', metric_key=metric_name,
                        title=f'{emoji} 基线异常：{metric_name}', description=body,
                        severity=sev_label,
                    )
                    print(f"  [BASELINE] {metric_name}={current_val} 偏离（{direction_label}） [{sev_label}]")
                else:
                    print(f"  [BASELINE-CONVERGE] {metric_name}={current_val} 检测到异常但处于收敛窗口内")

            # 对本轮已恢复的基线异常发送恢复通知
            from monitor.models import AlertLog
            active_baseline = AlertLog.objects.filter(
                config=config, alert_type='baseline', status='active'
            )
            for al in active_baseline:
                if al.metric_key not in anomaly_keys:
                    am.resolve(
                        alert_type='baseline', metric_key=al.metric_key,
                        recovery_title=f'[RECOVERED] 基线恢复：{al.metric_key}',
                        recovery_body=f'指标 {al.metric_key} 已恢复至正常范围',
                    )

        except Exception as e:
            print(f"  [WARNING] 基线检测异常：{e}")

        # --- D2. RCA 根因分析 ---
        _d2_start = datetime.datetime.now()
        print(f"  [P2-TIMING] {config.name} D1 baseline={(_d2_start-_p2_start).total_seconds():.1f}s")
        try:
            rca_engine = RCAEngine(config)
            rca_report = rca_engine.analyze(data)

            if rca_report.get('diagnoses'):
                for diag in rca_report['diagnoses']:
                    if diag['severity'] == 'critical':
                        body = (
                            f"规则ID：{diag['rule_id']}\n"
                            f"问题描述：{diag['description']}\n\n"
                            f"建议措施：\n" + "\n".join(f"• {s}" for s in diag['suggestions'])
                        )
                        am.fire(
                            alert_type='rca', metric_key=diag['rule_id'],
                            title=f"[CRITICAL] RCA根因：{diag['name']}",
                            description=body,
                            severity='critical',
                        )
                        print(f"  [RCA] {diag['rule_id']} - {diag['name']}")

            # 复合故障告警
            if rca_report.get('compound_diagnoses'):
                for compound in rca_report['compound_diagnoses']:
                    body = (
                        f"复合故障：{compound['name']}\n"
                        f"关联规则：{', '.join(compound['requires'])}\n\n"
                        f"建议措施：\n" + "\n".join(f"• {s}" for s in compound['suggestions'])
                    )
                    am.fire(
                        alert_type='rca_compound', metric_key=compound['id'],
                        title=f"[CRITICAL] 复合故障：{compound['name']}",
                        description=body,
                        severity='critical',
                    )
                    print(f"  [RCA-COMPOUND] {compound['id']} - {compound['name']}")

        except Exception as e:
            print(f"  [WARNING] RCA分析异常：{e}")

        # --- D3. 健康评分 (每小时一次) ---
        _d3_start = datetime.datetime.now()
        print(f"  [P2-TIMING] {config.name} D2 rca={(_d3_start-_d2_start).total_seconds():.1f}s")
        try:
            from django.core.cache import cache
            health_cache_key = f"health_score_{config.id}"
            last_health_check = cache.get(health_cache_key)

            if last_health_check is None:  # 首次检查或缓存过期
                health_engine = HealthEngine(config)
                health_report = health_engine.calculate(data)

                # 持久化健康评分到数据库
                health_engine.save_result(health_report)

                # 缓存1小时
                cache.set(health_cache_key, health_report, 3600)

                # 评分低于C级发送告警
                if health_report['grade'] in ('D', 'F'):
                    emoji = '[CRITICAL]' if health_report['grade'] == 'F' else '[WARNING]'
                    body = (
                        f"健康评分：{health_report['overall_score']} 分\n"
                        f"等级：{health_report['grade']} ({health_report['grade_description']})\n\n"
                        f"各维度得分：\n" + "\n".join(
                            f"• {dim}: {d['score']}"
                            for dim, d in health_report['dimensions'].items()
                        ) + "\n\n"
                        f"改进建议：\n" + "\n".join(f"• {r}" for r in health_report['recommendations'])
                    )
                    am.fire(
                        alert_type='health', metric_key='health_score',
                        title=f"{emoji} 数据库健康评分 {health_report['grade']}级",
                        description=body,
                        severity='critical' if health_report['grade'] == 'F' else 'warning',
                    )
                    print(f"  [HEALTH] 评分={health_report['overall_score']} {health_report['grade']}级")
                else:
                    # BUG-020: 评分恢复至 C 级及以上时，解除历史健康告警
                    am.resolve(
                        'health', 'health_score',
                        recovery_title='[RECOVERED] 数据库健康评分已恢复',
                        recovery_body=(
                            f"健康评分恢复至 {health_report['overall_score']} 分 "
                            f"({health_report['grade']}级)，告警解除"
                        ),
                    )
                    print(f"  [HEALTH] 评分={health_report['overall_score']} {health_report['grade']}级 (正常)")

        except Exception as e:
            print(f"  [WARNING] 健康评分异常：{e}")

        # --- D4. 容量预测 (每天一次) ---
        _d4_start = datetime.datetime.now()
        print(f"  [P2-TIMING] {config.name} D3 health={(_d4_start-_d3_start).total_seconds():.1f}s")
        try:
            from django.core.cache import cache
            capacity_cache_key = f"capacity_forecast_{config.id}"
            last_capacity_check = cache.get(capacity_cache_key)

            if last_capacity_check is None:  # 首次检查或缓存过期
                capacity_engine = CapacityEngine(config)
                capacity_report = capacity_engine.analyze_all_metrics()

                # 持久化容量预测结果到数据库
                try:
                    capacity_engine.save_predictions(capacity_report)
                except Exception as sp_err:
                    print(f"  [CAPACITY-SAVE] 预测结果保存失败: {sp_err}")

                # 缓存24小时
                cache.set(capacity_cache_key, capacity_report, 86400)

                if capacity_report.get('alerts'):
                    for alert in capacity_report['alerts']:
                        emoji = '[EMERGENCY]' if alert['severity'] == 'emergency' else \
                                '[CRITICAL]' if alert['severity'] == 'critical' else '[WARNING]'
                        body = (
                            f"类型：{alert['type']}\n"
                            f"当前值：{alert['current']}%\n"
                            f"预测值：{alert['predicted']}%\n"
                            f"消息：{alert['message']}"
                        )
                        am.fire(
                            alert_type='capacity', metric_key=alert['type'],
                            title=f"{emoji} 容量预测告警",
                            description=body,
                            severity=alert['severity'],
                        )
                        print(f"  [CAPACITY] {alert['type']} - {alert['message']}")

        except Exception as e:
            print(f"  [WARNING] 容量预测异常：{e}")

        # --- D5. 慢查询检测 (每10分钟) ---
        _d5_start = datetime.datetime.now()
        print(f"  [P2-TIMING] {config.name} D4 capacity={(_d5_start-_d4_start).total_seconds():.1f}s")
        try:
            from django.core.cache import cache
            slow_query_cache_key = f"slow_query_check_{config.id}"
            if cache.get(slow_query_cache_key) is None:
                from monitor.slow_query_engine import SlowQueryEngine
                sq_engine = SlowQueryEngine(config)
                slow_queries = sq_engine.collect_slow_queries_from_db(time_range='1h', limit=10)
                if slow_queries:
                    top_slow = slow_queries[0]
                    body = (
                        f"检测到 {len(slow_queries)} 条慢查询\n"
                        f"最慢查询耗时：{top_slow.get('total_time_sec', 0):.2f}s\n"
                        f"执行次数：{top_slow.get('exec_count', 0)}\n"
                        f"SQL摘要：{top_slow.get('query', '')[:200]}"
                    )
                    am.fire(
                        alert_type='slow_query', metric_key='slow_queries',
                        title='[WARNING] 慢查询检测',
                        description=body, severity='warning',
                    )
                    print(f"  [SLOW_QUERY] 检测到 {len(slow_queries)} 条慢查询")
                # 缓存10分钟
                cache.set(slow_query_cache_key, True, 600)
        except Exception as e:
            print(f"  [WARNING] 慢查询检测异常：{e}")

        # --- D6. 配置检查 (每天一次) ---
        _d6_start = datetime.datetime.now()
        print(f"  [P2-TIMING] {config.name} D5 slow_query={(_d6_start-_d5_start).total_seconds():.1f}s")
        try:
            from django.core.cache import cache
            config_check_cache_key = f"config_check_{config.id}"
            if cache.get(config_check_cache_key) is None:
                from monitor.config_advisor import ConfigAdvisor
                from monitor.db_connector import DbConnector
                advisor = ConfigAdvisor(config)
                try:
                    check_conn = DbConnector.get_connection(config)
                    report = advisor.check_configuration(check_conn)
                finally:
                    try: DbConnector.close_connection(check_conn)
                    except Exception: pass
                if report and hasattr(report, 'results') and report.results:
                    critical_issues = [r for r in report.results if getattr(r, 'severity', '') in ('critical', 'high')]
                    if critical_issues:
                        body = f"发现 {len(critical_issues)} 个高优先级配置问题:\n"
                        for issue in critical_issues[:5]:
                            body += f"\u2022 {getattr(issue, 'parameter', '')}: {getattr(issue, 'suggestion', '')}\n"
                        am.fire(
                            alert_type='config', metric_key='config_check',
                            title='[WARNING] 数据库配置检查',
                            description=body, severity='warning',
                        )
                        print(f"  [CONFIG] 发现 {len(critical_issues)} 个配置问题")
                    else:
                        # BUG-020: 无高优先级配置问题时，解除历史配置告警
                        am.resolve(
                            'config', 'config_check',
                            recovery_title='[RECOVERED] 数据库配置检查已恢复',
                            recovery_body='配置检查未发现高优先级问题，告警解除',
                        )
                else:
                    # BUG-020: 检查通过（无结果）同样解除历史配置告警
                    am.resolve(
                        'config', 'config_check',
                        recovery_title='[RECOVERED] 数据库配置检查已恢复',
                        recovery_body='配置检查通过，告警解除',
                    )
                # 缓存24小时
                cache.set(config_check_cache_key, True, 86400)
        except Exception as e:
            print(f"  [WARNING] 配置检查异常：{e}")

        _p2_end = datetime.datetime.now()
        print(f"  [P2-TIMING] {config.name} D6 config+total={(_p2_end-_p2_start).total_seconds():.1f}s")

    def _build_lock_msg(self, locks):
        """构建锁等待告警消息"""
        msg = "检测到严重的数据库阻塞（Lock Wait）：\n\n"
        for l in locks:
            msg += (
                f"--------------------------------\n"
                f"凶手 (Blocker): {l.get('blocker_user', 'N/A')} ({l.get('blocker_id', 'N/A')})\n"
                f"受害 (Waiter) : {l.get('waiter_user', 'N/A')} ({l.get('waiter_id', 'N/A')})\n"
                f"已阻塞时   : {l.get('seconds', 0)} 秒\n"
            )
        msg += "--------------------------------\n注意：时长仍在增加，请DBA立即检查！"
        return msg

    def send_alert(self, config, title, body):
        """统一告警出口：邮件 + 钉钉（如已配置）"""
        from monitor.notifications import send_email_alert, send_dingtalk_alert

        full_body = (
            f"数据库：{config.name}\n"
            f"地址：{config.host}:{config.port}\n"
            f"类型：{config.get_db_type_display()}\n"
            f"时间：{datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"\n{body}"
        )
        send_email_alert(title, full_body)
        send_dingtalk_alert(title, full_body)

    # 保留旧名称兼容性
    def send_alert_email(self, config, title_prefix, error_msg):
        self.send_alert(config, title_prefix, error_msg)
