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
    DamengChecker,
    GbaseChecker,
    TDSQLChecker,
    CHECKER_MAP,
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
        'dm': DamengChecker,
        'gbase': GbaseChecker,
        'tdsql': TDSQLChecker,
        'redis': RedisChecker,
        'mongo': None,  # TODO: MongoDB 支持
    }

    def handle(self, *args, **options):
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

        # 立即执行一次
        self.monitor_job()

        try:
            scheduler.start()
        except (KeyboardInterrupt, SystemExit):
            print("\n监控进程已停止")

    def _run_single_check(self, config):
        """在独立线程中执行单个数据库的采集，超时后自动记录 DOWN"""
        # 每个线程需要独立关闭复用的 Django DB 连接，避免跨线程复用问题
        connection.close_if_unusable_or_obsolete()
        checker_class = self.CHECKER_MAP.get(config.db_type)
        if checker_class:
            checker = checker_class(self)
            checker.check(config)
        elif config.db_type == 'mongo':
            print(f"  -- 跳过暂不支持的类型：{config.name} (MongoDB)")
        else:
            print(f"  -- 跳过未知类型：{config.name} ({config.db_type})")
        # 采集完毕后主动释放本线程的数据库连接，防止连接池耗尽
        from django.db import close_old_connections
        close_old_connections()

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

    def process_result(self, config, current_status, data):
        """统一结果处理和告警逻辑（v3.0：Phase 2 智能引擎集成）"""

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
                for key in ['dw_replication_health', 'dsc_cluster_health',
                            'gbase_cluster_health', 'tdsql_cluster_health']:
                    if key in data:
                        es_docs.append({
                            "_index": get_metrics_index_name(),
                            "config_id": config.id,
                            "db_type": config.db_type,
                            "db_name": config.name,
                            "metric_name": key,
                            "value": data[key],
                            "status": current_status,
                            "timestamp": datetime.datetime.now().isoformat(),
                        })

                if es_docs:
                    bulk_index_metrics(es_docs)
        except Exception as es_err:
            print(f"  [ES] 写入 Elasticsearch 失败: {es_err}")

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
