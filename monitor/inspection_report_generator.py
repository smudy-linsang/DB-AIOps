"""
巡检报告生成器 - Phase 5 P1-4
=============================

把 InspectionRun 的结果生成可读报告:
- HTML 报告(可视化)
- JSON 报告(机器可读)
- Markdown 报告(可贴到 Wiki)
- 纯文本报告(邮件/IM)

文件: monitor/inspection_report_generator.py
参考: PHASE5_DEVELOPMENT_DESIGN.md 第三部分 P1-4
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ============================================================================
# 报告生成器
# ============================================================================

class InspectionReportGenerator:
    """巡检报告生成器"""

    def __init__(self, run):
        """
        参数:
            run: InspectionRun 实例
        """
        self.run = run
        self._logger = logging.getLogger("monitor.inspection_report_generator")

    # ------------------------------------------------------------------
    # JSON 报告
    # ------------------------------------------------------------------

    def to_json(self, indent: int = 2) -> str:
        """生成 JSON 格式报告"""
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=indent)

    def to_dict(self) -> Dict[str, Any]:
        """报告结构化数据"""
        findings = []
        for f in self.run.findings.all().order_by("-severity", "item_code"):
            findings.append({
                "finding_id": f.finding_id,
                "item_code": f.item_code,
                "item_title": f.item_title,
                "status": f.status,
                "severity": f.severity,
                "summary": f.summary,
                "details": f.details,
                "detection_method": f.detection_method,
                "duration_ms": f.duration_ms,
                "confidence": f.confidence,
            })
        return {
            "report_id": f"RPT-{self.run.run_id}",
            "generated_at": datetime.now().isoformat(),
            "run": {
                "run_id": self.run.run_id,
                "level": self.run.level,
                "status": self.run.status,
                "started_at": self.run.started_at.isoformat() if self.run.started_at else None,
                "completed_at": self.run.completed_at.isoformat() if self.run.completed_at else None,
                "duration_sec": self.run.duration_sec,
                "health_score": self.run.health_score,
                "total_items": self.run.total_items,
                "ok_count": self.run.ok_count,
                "warning_count": self.run.warning_count,
                "critical_count": self.run.critical_count,
                "error_count": self.run.error_count,
            },
            "database": {
                "id": self.run.db_config.id,
                "name": self.run.db_config.name,
                "db_type": self.run.db_config.db_type,
                "host": self.run.db_config.host,
                "port": self.run.db_config.port,
            },
            "findings": findings,
            "summary": self._generate_summary(findings),
        }

    # ------------------------------------------------------------------
    # Markdown 报告
    # ------------------------------------------------------------------

    def to_markdown(self) -> str:
        """生成 Markdown 报告"""
        d = self.to_dict()
        md = []
        md.append(f"# 数据库巡检报告 - {d['database']['name']}")
        md.append("")
        md.append(f"- **报告ID**: {d['report_id']}")
        md.append(f"- **巡检时间**: {d['run']['started_at']} ~ {d['run']['completed_at']}")
        md.append(f"- **巡检级别**: {d['run']['level']}")
        md.append(f"- **耗时**: {d['run']['duration_sec']}s")
        md.append(f"- **数据库**: {d['database']['db_type']} @ {d['database']['host']}:{d['database']['port']}")
        md.append("")
        md.append("## 健康度评分")
        score = d['run']['health_score']
        emoji = "🟢" if score >= 90 else "🟡" if score >= 70 else "🔴"
        md.append(f"{emoji} **{score}** / 100")
        md.append("")
        md.append("## 概览")
        md.append("| 状态 | 数量 |")
        md.append("|------|------|")
        md.append(f"| ✅ 正常 | {d['run']['ok_count']} |")
        md.append(f"| ⚠️ 警告 | {d['run']['warning_count']} |")
        md.append(f"| 🔴 严重 | {d['run']['critical_count']} |")
        md.append(f"| ❌ 错误 | {d['run']['error_count']} |")
        md.append(f"| 总计 | {d['run']['total_items']} |")
        md.append("")
        # 摘要
        s = d['summary']
        md.append("## 摘要")
        md.append(f"- 严重问题: {s['critical']}")
        md.append(f"- 警告问题: {s['warning']}")
        md.append(f"- 自动可修复: {s['auto_fixable']}")
        md.append("")
        # 严重问题
        crits = [f for f in d['findings'] if f['status'] == 'critical']
        if crits:
            md.append("## 🔴 严重问题")
            for f in crits:
                md.append(f"### {f['item_title']}")
                md.append(f"- 编号: `{f['item_code']}`")
                md.append(f"- 摘要: {f['summary']}")
                md.append("")
        # 警告
        warns = [f for f in d['findings'] if f['status'] == 'warning']
        if warns:
            md.append("## ⚠️ 警告")
            for f in warns:
                md.append(f"### {f['item_title']}")
                md.append(f"- 编号: `{f['item_code']}`")
                md.append(f"- 摘要: {f['summary']}")
                md.append("")
        # 正常
        oks = [f for f in d['findings'] if f['status'] == 'ok']
        if oks:
            md.append(f"## ✅ 正常项 ({len(oks)} 项)")
            for f in oks[:10]:
                md.append(f"- {f['item_title']}")
            if len(oks) > 10:
                md.append(f"- ...还有 {len(oks) - 10} 项")
        md.append("")
        md.append("---")
        md.append(f"*由 DB Monitor 巡检引擎自动生成于 {d['generated_at']}*")
        return "\n".join(md)

    # ------------------------------------------------------------------
    # HTML 报告
    # ------------------------------------------------------------------

    def to_html(self) -> str:
        """生成 HTML 报告(简洁版,可邮件发送)"""
        d = self.to_dict()
        score = d['run']['health_score']
        if score >= 90:
            badge_color, badge_text = "#28a745", "健康"
        elif score >= 70:
            badge_color, badge_text = "#ffc107", "注意"
        else:
            badge_color, badge_text = "#dc3545", "风险"

        css = """
        <style>
        body { font-family: -apple-system, 'Microsoft YaHei', sans-serif; margin: 0; padding: 20px; background: #f5f5f5; }
        .container { max-width: 1100px; margin: 0 auto; background: white; padding: 30px; border-radius: 8px; }
        h1 { color: #2c3e50; border-bottom: 3px solid #3498db; padding-bottom: 10px; }
        h2 { color: #34495e; margin-top: 30px; }
        .meta { background: #ecf0f1; padding: 15px; border-radius: 5px; margin: 15px 0; }
        .meta-item { display: inline-block; margin-right: 20px; }
        .badge { display: inline-block; padding: 8px 20px; border-radius: 20px; color: white; font-size: 18px; font-weight: bold; }
        .summary { display: flex; justify-content: space-around; margin: 20px 0; }
        .summary-card { flex: 1; text-align: center; padding: 20px; margin: 0 5px; border-radius: 8px; }
        .ok { background: #d4edda; color: #155724; }
        .warning { background: #fff3cd; color: #856404; }
        .critical { background: #f8d7da; color: #721c24; }
        .error { background: #e2e3e5; color: #383d41; }
        .summary-card .num { font-size: 36px; font-weight: bold; display: block; }
        .finding { border-left: 4px solid #ccc; padding: 12px; margin: 8px 0; background: #fafafa; }
        .finding.critical { border-left-color: #dc3545; background: #fff5f5; }
        .finding.warning { border-left-color: #ffc107; background: #fffdf5; }
        .finding.ok { border-left-color: #28a745; background: #f5fff8; }
        .finding.error { border-left-color: #6c757d; background: #f8f8f8; }
        .finding-title { font-weight: bold; margin-bottom: 5px; }
        .finding-meta { color: #666; font-size: 12px; }
        table { width: 100%; border-collapse: collapse; margin: 15px 0; }
        th, td { padding: 8px; text-align: left; border-bottom: 1px solid #ecf0f1; }
        th { background: #34495e; color: white; }
        .footer { text-align: center; color: #999; margin-top: 30px; font-size: 12px; }
        </style>
        """
        html = []
        html.append(f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>巡检报告 {d['report_id']}</title>{css}</head><body>")
        html.append("<div class='container'>")
        html.append(f"<h1>📊 数据库巡检报告 - {d['database']['name']}</h1>")
        html.append("<div class='meta'>")
        html.append(f"<div class='meta-item'><b>报告ID:</b> {d['report_id']}</div>")
        html.append(f"<div class='meta-item'><b>数据库:</b> {d['database']['db_type']} @ {d['database']['host']}:{d['database']['port']}</div>")
        html.append(f"<div class='meta-item'><b>巡检级别:</b> {d['run']['level']}</div>")
        html.append(f"<div class='meta-item'><b>耗时:</b> {d['run']['duration_sec']}s</div>")
        html.append("</div>")
        html.append(f"<h2>健康度评分 <span class='badge' style='background:{badge_color}'>{score} - {badge_text}</span></h2>")
        html.append("<div class='summary'>")
        html.append(f"<div class='summary-card ok'><span class='num'>{d['run']['ok_count']}</span>✅ 正常</div>")
        html.append(f"<div class='summary-card warning'><span class='num'>{d['run']['warning_count']}</span>⚠️ 警告</div>")
        html.append(f"<div class='summary-card critical'><span class='num'>{d['run']['critical_count']}</span>🔴 严重</div>")
        html.append(f"<div class='summary-card error'><span class='num'>{d['run']['error_count']}</span>❌ 错误</div>")
        html.append("</div>")

        findings_sorted = sorted(d['findings'], key=lambda x: (
            {"critical": 0, "warning": 1, "error": 2, "ok": 3}.get(x['status'], 4),
            x['item_code']
        ))
        html.append("<h2>巡检发现详情</h2>")
        for f in findings_sorted:
            html.append(f"<div class='finding {f['status']}'>")
            html.append(f"<div class='finding-title'>{f['item_title']} <code>{f['item_code']}</code></div>")
            html.append(f"<div>{f['summary']}</div>")
            if f.get('details', {}).get('findings'):
                for fd in f['details']['findings'][:3]:
                    html.append(f"<div class='finding-meta'>• {fd.get('message', '')}</div>")
            html.append(f"<div class='finding-meta'>检测方法: {f['detection_method']} | 耗时: {f['duration_ms']}ms | 置信度: {f['confidence']}</div>")
            html.append("</div>")

        html.append(f"<div class='footer'>由 DB Monitor 巡检引擎自动生成于 {d['generated_at']}</div>")
        html.append("</div></body></html>")
        return "\n".join(html)

    # ------------------------------------------------------------------
    # 纯文本报告(邮件/IM)
    # ------------------------------------------------------------------

    def to_text(self) -> str:
        """生成纯文本报告"""
        d = self.to_dict()
        lines = []
        lines.append("=" * 70)
        lines.append(f"数据库巡检报告 - {d['database']['name']}")
        lines.append("=" * 70)
        lines.append(f"报告ID: {d['report_id']}")
        lines.append(f"巡检时间: {d['run']['started_at']} ~ {d['run']['completed_at']}")
        lines.append(f"数据库: {d['database']['db_type']} @ {d['database']['host']}:{d['database']['port']}")
        lines.append(f"巡检级别: {d['run']['level']}  耗时: {d['run']['duration_sec']}s")
        lines.append("")
        lines.append(f"健康度: {d['run']['health_score']} / 100")
        lines.append(f"正常 {d['run']['ok_count']}  警告 {d['run']['warning_count']}  严重 {d['run']['critical_count']}  错误 {d['run']['error_count']}")
        lines.append("")
        # 严重
        crits = [f for f in d['findings'] if f['status'] == 'critical']
        if crits:
            lines.append(f"--- 🔴 严重问题 ({len(crits)}) ---")
            for f in crits:
                lines.append(f"  [{f['item_code']}] {f['item_title']}")
                lines.append(f"    {f['summary']}")
            lines.append("")
        # 警告
        warns = [f for f in d['findings'] if f['status'] == 'warning']
        if warns:
            lines.append(f"--- ⚠️ 警告 ({len(warns)}) ---")
            for f in warns[:10]:
                lines.append(f"  [{f['item_code']}] {f['item_title']}: {f['summary']}")
            if len(warns) > 10:
                lines.append(f"  ... 还有 {len(warns) - 10} 条警告")
            lines.append("")
        # 错误
        errs = [f for f in d['findings'] if f['status'] == 'error']
        if errs:
            lines.append(f"--- ❌ 检测错误 ({len(errs)}) ---")
            for f in errs[:5]:
                lines.append(f"  [{f['item_code']}] {f['item_title']}: {f.get('details', {}).get('error', 'N/A')}")
        lines.append("")
        lines.append("=" * 70)
        lines.append(f"Generated by DB Monitor @ {d['generated_at']}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # 私有
    # ------------------------------------------------------------------

    def _generate_summary(self, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "critical": sum(1 for f in findings if f['status'] == 'critical'),
            "warning": sum(1 for f in findings if f['status'] == 'warning'),
            "ok": sum(1 for f in findings if f['status'] == 'ok'),
            "error": sum(1 for f in findings if f['status'] == 'error'),
            "auto_fixable": sum(1 for f in findings
                                if f.get('details', {}).get('findings')
                                and any('auto_fix' in str(fd) for fd in f['details']['findings'])),
        }


# ============================================================================
# 便捷函数
# ============================================================================

def generate_report(run, format: str = "html") -> str:
    """生成巡检报告

    参数:
        run: InspectionRun 实例
        format: html / json / markdown / text
    """
    gen = InspectionReportGenerator(run)
    if format == "html":
        return gen.to_html()
    if format == "json":
        return gen.to_json()
    if format == "markdown":
        return gen.to_markdown()
    if format == "text":
        return gen.to_text()
    raise ValueError(f"不支持的格式: {format}")


def save_report(run, format: str = "html", path: Optional[str] = None) -> str:
    """保存报告到文件"""
    import os
    content = generate_report(run, format)
    if not path:
        os.makedirs("reports/inspection", exist_ok=True)
        ext = {"html": "html", "json": "json", "markdown": "md", "text": "txt"}[format]
        path = f"reports/inspection/{run.run_id}.{ext}"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path
