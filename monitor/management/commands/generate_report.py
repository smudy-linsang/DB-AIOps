# -*- coding: utf-8 -*-
"""
定时报表生成命令

支持日报、周报、月报三种类型，生成 HTML 格式的巡检报告。

用法:
    python manage.py generate_report --type daily
    python manage.py generate_report --type weekly
    python manage.py generate_report --type monthly
"""

import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone

from monitor.models import (
    DatabaseConfig, MonitorLog, AlertLog, HealthScore,
    PredictionResult, ReportRecord,
)


class Command(BaseCommand):
    help = '生成定时报表（日报/周报/月报）'

    def add_arguments(self, parser):
        parser.add_argument('--type', type=str, default='daily',
                            choices=['daily', 'weekly', 'monthly'],
                            help='报表类型: daily/weekly/monthly')

    def handle(self, *args, **options):
        report_type = options['type']
        now = timezone.now()

        if report_type == 'daily':
            period_start = (now - datetime.timedelta(days=1)).date()
            period_end = now.date()
            title = f"数据库巡检日报 - {period_end.strftime('%Y-%m-%d')}"
        elif report_type == 'weekly':
            period_start = (now - datetime.timedelta(days=7)).date()
            period_end = now.date()
            title = f"数据库巡检周报 - {period_start.strftime('%m/%d')}~{period_end.strftime('%m/%d')}"
        else:
            period_start = (now - datetime.timedelta(days=30)).date()
            period_end = now.date()
            title = f"数据库巡检月报 - {period_end.strftime('%Y-%m')}"

        # 收集数据
        configs = DatabaseConfig.objects.filter(is_active=True)
        html = self._generate_html(title, report_type, period_start, period_end, configs)

        # 保存报表记录
        report = ReportRecord.objects.create(
            report_type=report_type,
            title=title,
            content_html=html,
            period_start=period_start,
            period_end=period_end,
            recipients=[],
            status='generated',
        )

        self.stdout.write(self.style.SUCCESS(
            f"报表生成完成: {title} (id={report.id})"
        ))

    def _generate_html(self, title, report_type, period_start, period_end, configs):
        """生成 HTML 报表内容"""
        # 统计数据
        total_dbs = configs.count()
        up_count = 0
        down_count = 0

        db_rows = ''
        for config in configs:
            latest = MonitorLog.objects.filter(config=config).order_by('-create_time').first()
            status = latest.status if latest else 'UNKNOWN'
            if status == 'UP':
                up_count += 1
            else:
                down_count += 1

            # 健康评分
            hs = HealthScore.objects.filter(config=config).order_by('-score_date').first()
            health_str = f"{hs.total_score}分({hs.grade})" if hs else '-'

            # 活跃告警
            active_alerts = AlertLog.objects.filter(config=config, status='active').count()

            # 容量预测
            pred = PredictionResult.objects.filter(config=config).first()
            pred_str = ''
            if pred and pred.predicted_warn_date:
                pred_str = f"预计 {pred.predicted_warn_date} 触达告警线"

            status_color = '#52c41a' if status == 'UP' else '#f5222d'
            db_rows += f'''
            <tr>
                <td>{config.name}</td>
                <td>{config.get_db_type_display()}</td>
                <td>{config.host}:{config.port}</td>
                <td style="color:{status_color};font-weight:bold">{status}</td>
                <td>{health_str}</td>
                <td>{active_alerts}</td>
                <td style="color:#fa8c16">{pred_str}</td>
            </tr>'''

        # 告警统计
        alert_stats = AlertLog.objects.filter(
            create_time__date__gte=period_start,
            create_time__date__lte=period_end,
        ).count()
        critical_alerts = AlertLog.objects.filter(
            create_time__date__gte=period_start,
            create_time__date__lte=period_end,
            severity='critical',
        ).count()

        html = f'''<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{title}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; margin: 40px; background: #f5f7fa; }}
.container {{ max-width: 1200px; margin: 0 auto; background: #fff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.08); }}
h1 {{ color: #1a3a5c; border-bottom: 3px solid #1890ff; padding-bottom: 10px; }}
h2 {{ color: #333; margin-top: 30px; }}
.summary {{ display: flex; gap: 20px; margin: 20px 0; }}
.stat-card {{ background: #f0f5ff; padding: 20px; border-radius: 6px; text-align: center; flex: 1; }}
.stat-card .number {{ font-size: 32px; font-weight: bold; color: #1890ff; }}
.stat-card .label {{ color: #666; margin-top: 5px; }}
table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
th {{ background: #fafafa; padding: 12px; text-align: left; border-bottom: 2px solid #e8e8e8; }}
td {{ padding: 10px 12px; border-bottom: 1px solid #f0f0f0; }}
.footer {{ margin-top: 30px; color: #999; font-size: 12px; text-align: center; }}
</style>
</head>
<body>
<div class="container">
<h1>{title}</h1>
<p>统计周期: {period_start.strftime('%Y-%m-%d')} ~ {period_end.strftime('%Y-%m-%d')}</p>

<div class="summary">
    <div class="stat-card">
        <div class="number">{total_dbs}</div>
        <div class="label">监控数据库总数</div>
    </div>
    <div class="stat-card">
        <div class="number" style="color:#52c41a">{up_count}</div>
        <div class="label">在线</div>
    </div>
    <div class="stat-card">
        <div class="number" style="color:#f5222d">{down_count}</div>
        <div class="label">离线</div>
    </div>
    <div class="stat-card">
        <div class="number">{alert_stats}</div>
        <div class="label">告警总数</div>
    </div>
    <div class="stat-card">
        <div class="number" style="color:#f5222d">{critical_alerts}</div>
        <div class="label">严重告警</div>
    </div>
</div>

<h2>数据库详情</h2>
<table>
<thead>
<tr><th>名称</th><th>类型</th><th>地址</th><th>状态</th><th>健康评分</th><th>活跃告警</th><th>容量预测</th></tr>
</thead>
<tbody>
{db_rows}
</tbody>
</table>

<div class="footer">
    DB-AIOps 智能运维平台 自动生成 | {timezone.now().strftime('%Y-%m-%d %H:%M')}
</div>
</div>
</body>
</html>'''
        return html
