import os
code = '# -*- coding: utf-8 -*-\n' + '''
import json
import statistics
import logging
from datetime import datetime, timedelta
from collections import defaultdict
from django.utils import timezone
from django.core.cache import cache
from monitor.models import MonitorLog, DatabaseConfig

logger = logging.getLogger(__name__)

class IntelligentBaselineEngine:
    CACHE_PREFIX = "baseline_v2_"
    CACHE_TTL = 300

    def __init__(self, config, history_days=14):
        self.config = config
        self.history_days = history_days

    def get_history_logs(self, days=None):
        if days is None: days = self.history_days
        start_time = timezone.now() - timedelta(days=days)
        return MonitorLog.objects.filter(config=self.config, status="UP", create_time__gte=start_time).order_by("create_time")

    def parse_log_data(self, log):
        try: return json.loads(log.message)
        except: return {}

    def get_metric_time_series(self, metric_key, days=None):
        logs = self.get_history_logs(days)
        time_series = []
        for log in logs:
            data = self.parse_log_data(log)
            if metric_key in data and data[metric_key] is not None:
                try:
                    val = float(data[metric_key])
                    time_series.append((log.create_time, val))
                except: pass
        return time_series

    def calculate_baseline(self, metric_key, days=None):
        cache_key = f"{self.CACHE_PREFIX}{self.config.id}_{metric_key}_basic_{days}"
        cached = cache.get(cache_key)
        if cached: return cached
        time_series = self.get_metric_time_series(metric_key, days)
        if len(time_series) < 3: return None
        values = [v for _, v in time_series]
        mean_val = statistics.mean(values)
        std_val = statistics.stdev(values) if len(values) > 1 else 0
        sorted_vals = sorted(values)
        p95 = sorted_vals[int(len(sorted_vals) * 0.95)]
        p99 = sorted_vals[int(len(sorted_vals) * 0.99)]
        result = {"mean": round(mean_val, 2), "std": round(std_val, 2), "min": round(min(values), 2), "max": round(max(values), 2), "p95": round(p95, 2), "p99": round(p99, 2), "sample_count": len(values), "normal_range": [round(mean_val - 2 * std_val, 2), round(mean_val + 2 * std_val, 2)]}
        cache.set(cache_key, result, self.CACHE_TTL)
        return result

    def calculate_periodic_baseline(self, metric_key, period_type="hour"):
        cache_key = f"{self.CACHE_PREFIX}{self.config.id}_{metric_key}_{period_type}"
        cached = cache.get(cache_key)
        if cached: return cached
        time_series = self.get_metric_time_series(metric_key)
        if len(time_series) < 10: return None
        period_values = {}
        for timestamp, value in time_series:
            if period_type == "hour": pk = timestamp.hour
            elif period_type == "dow": pk = timestamp.isoweekday() % 7
            elif period_type == "hour_dow": pk = (timestamp.isoweekday() % 7) * 24 + timestamp.hour
            else: pk = 0
            if pk not in period_values: period_values[pk] = []
            period_values[pk].append(value)
        periods = {}
        for pk, vals in period_values.items():
            if len(vals) >= 3:
                periods[pk] = {"mean": round(statistics.mean(vals), 2), "std": round(statistics.stdev(vals) if len(vals) > 1 else 0, 2), "p95": round(sorted(vals)[int(len(vals) * 0.95)], 2), "count": len(vals), "min": round(min(vals), 2), "max": round(max(vals), 2)}
        result = {"period_type": period_type, "periods": periods, "sample_count": len(time_series)}
        cache.set(cache_key, result, self.CACHE_TTL)
        return result

    def detect_trend(self, metric_key, window_hours=24):
        time_series = self.get_metric_time_series(metric_key, days=7)
        if len(time_series) < window_hours: return {"trend": "unknown", "confidence": 0}
        values = [v for _, v in time_series[-window_hours:]]
        first_half = values[:len(values)//2]
        second_half = values[len(values)//2:]
        avg_first = statistics.mean(first_half)
        avg_second = statistics.mean(second_half)
        change_pct = ((avg_second - avg_first) / avg_first * 100) if avg_first != 0 else 0
        if abs(change_pct) < 5: trend = "stable"
        elif change_pct > 0: trend = "increasing"
        else: trend = "decreasing"
        return {"trend": trend, "change_pct": round(change_pct, 2), "confidence": round(min(abs(change_pct) / 20 * 100, 100), 2)}

    def calculate_anomaly_score(self, current_value, baseline):
        if baseline is None: return 0
        mean = baseline.get("mean", 0)
        std = baseline.get("std", 0)
        if std == 0: return 0 if current_value == mean else 50
        z_score = abs(current_value - mean) / std
        if z_score <= 2: return round(z_score * 10, 2)
        elif z_score <= 3: return round(20 + (z_score - 2) * 30, 2)
        return round(min(50 + (z_score - 3) * 25, 100), 2)

    def detect_anomaly(self, current_value, baseline):
        if baseline is None: return False, None, None, 0
        mean = baseline.get("mean", 0)
        std = baseline.get("std", 0)
        p99 = baseline.get("p99", 0)
        score = self.calculate_anomaly_score(current_value, baseline)
        if current_value > p99: return True, "high", "critical", score
        if current_value > mean + 2 * std: return True, "high", "warning", score
        if current_value < mean - 2 * std and mean > 0: return True, "low", "warning", score
        return False, None, None, score

    def get_current_period_baseline(self, metric_key, timestamp=None):
        if timestamp is None: timestamp = timezone.now()
        baseline = self.calculate_periodic_baseline(metric_key, "hour_dow")
        if not baseline: return self.calculate_baseline(metric_key)
        current_key = (timestamp.isoweekday() % 7) * 24 + timestamp.hour
        if current_key in baseline.get("periods", {}):
            pd = baseline["periods"][current_key]
            return {"mean": pd["mean"], "std": pd["std"], "p95": pd["p95"], "p99": round(pd["p95"] * 1.1, 2), "min": pd["min"], "max": pd["max"], "normal_range": [round(pd["mean"] - 2 * pd["std"], 2), round(pd["mean"] + 2 * pd["std"], 2)]}
        return self.calculate_baseline(metric_key)

    def check_current_against_baseline(self, current_data, use_periodic=True):
        anomalies = []
        for metric_key, current_value in current_data.items():
            if not isinstance(current_value, (int, float)): continue
            if isinstance(current_value, (list, dict)): continue
            baseline = self.get_current_period_baseline(metric_key) if use_periodic else self.calculate_baseline(metric_key)
            if baseline:
                is_anomaly, anomaly_type, severity, score = self.detect_anomaly(current_value, baseline)
                if is_anomaly:
                    anomalies.append({"metric": metric_key, "current_value": current_value, "baseline_mean": baseline.get("mean"), "baseline_std": baseline.get("std"), "normal_range": baseline.get("normal_range"), "anomaly_type": anomaly_type, "severity": severity, "anomaly_score": score})
        return anomalies

    def get_full_baseline_report(self, days=None):
        logs = self.get_history_logs(days)
        if not logs: return {"error": "No data"}
        all_keys = set()
        for log in logs:
            data = self.parse_log_data(log)
            all_keys.update(data.keys())
        exclude_keys = {"version", "error", "warning_list", "locks", "tablespaces", "database_sizes", "cluster_nodes", "shards", "message"}
        metric_keys = all_keys - exclude_keys
        report = {"config_name": self.config.name, "db_type": self.config.db_type, "analysis_period_days": days or self.history_days, "sample_count": len(logs), "metrics": {}, "analysis_time": timezone.now().isoformat()}
        for key in metric_keys:
            baseline = self.calculate_baseline(key, days)
            if baseline:
                report["metrics"][key] = baseline
                trend = self.detect_trend(key)
                report["metrics"][key]["trend"] = trend
        return report
'''
with open(r'D:\DB_Monitor\monitor\intelligent_baseline_engine.py', 'w', encoding='utf-8') as f:
    f.write(code)
print('Done')
