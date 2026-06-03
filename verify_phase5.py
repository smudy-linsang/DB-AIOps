"""
Phase 5 完整验证脚本
===================
逐个验证所有 Phase 5 模块:
1. 数据模型
2. RCA 引擎
3. 影响评估
4. 方案生成器
5. 上下文聚合器
6. 巡检执行器
7. AWR 分析器
8. 报告生成器
9. 知识库
10. 案例库 RAG
11. 自动修复
12. 调度器
"""

import os
import sys
import traceback
from datetime import datetime

# 切换到项目根目录
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(PROJECT_ROOT)
sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dbmonitor.settings")
import django
django.setup()


# ==========================================
# 验证列表
# ==========================================

def verify_module(name, import_fn, instance_check=None):
    """验证单个模块"""
    print(f"\n[验证] {name} ... ", end="")
    try:
        result = import_fn()
        if instance_check:
            instance_check(result)
        print("[OK]")
        return True
    except Exception as e:
        print(f"[FAIL]: {e}")
        traceback.print_exc()
        return False


def run_all():
    print("=" * 70)
    print(f"Phase 5 完整验证 - {datetime.now().isoformat()}")
    print("=" * 70)

    results = {}

    # 1. 数据模型
    def t01():
        from monitor.models import (
            AlertCase, RemediationPlan, BusinessImpactAssessment,
            InspectionItem, InspectionRun, InspectionFinding,
            InspectionIssuePattern,
        )
        return {"models": 7, "names": [m.__name__ for m in [
            AlertCase, RemediationPlan, BusinessImpactAssessment,
            InspectionItem, InspectionRun, InspectionFinding,
            InspectionIssuePattern,
        ]]}
    results["P0-1 数据模型"] = verify_module("P0-1 数据模型 (7 个新模型)", t01)

    # 2. 上下文聚合器
    def t02():
        from monitor.context_aggregator import ContextAggregator, aggregate_alert_context
        return ContextAggregator.__doc__[:50] if ContextAggregator.__doc__ else "OK"
    results["P0-2 上下文聚合器"] = verify_module("P0-2 上下文聚合器", t02)

    # 3. RCA 引擎
    def t03():
        from monitor.rca_engine_v2 import RCAEngineV2, RULES_V2, get_rule_count
        return {"rule_count": get_rule_count(), "engine": RCAEngineV2.__name__}
    results["P0-3 RCA 2.0"] = verify_module("P0-3 RCA 2.0 引擎", t03)

    # 4. 影响评估
    def t04():
        from monitor.impact_engine import (
            HealthImpactCalculator, BusinessImpactAssessor,
            ImpactAssessment, assess_impact,
        )
        return HealthImpactCalculator.__name__
    results["P0-4 影响评估"] = verify_module("P0-4 影响评估引擎", t04)

    # 5. 方案生成器
    def t05():
        from monitor.remediation_planner import (
            PlanStep, PlanScenario, RemediationPlanV2,
            RemediationPlanner, PLAN_TEMPLATES,
        )
        return {"templates": len(PLAN_TEMPLATES)}
    results["P0-5 方案生成器"] = verify_module("P0-5 方案生成器", t05)

    # 6. API 端点
    def t06():
        from monitor.api_views_phase5 import (
            AlertRCADetailView, AlertRCAQuickView,
            InspectionRunListView, InspectionRunTriggerView,
            InspectionRunDetailView, InspectionItemListView,
            AlertCaseListView, AlertCaseSearchView,
            InspectionIssuePatternListView, Phase5StatsView,
        )
        return {"views": 10}
    results["P0-6 API 端点"] = verify_module("P0-6 Phase 5 API 端点 (10 个 View)", t06)

    # 7. 巡检注册表
    def t07():
        from monitor.inspection_registry import (
            ALL_ITEMS, get_total_count, get_count_by_level,
            get_count_by_db_type,
        )
        return {
            "total": get_total_count(),
            "by_level": get_count_by_level(),
            "by_db": get_count_by_db_type(),
        }
    results["P1-1 巡检注册表"] = verify_module("P1-1 巡检规则库", t07)

    # 8. 巡检执行器
    def t08():
        from monitor.inspection_executor import (
            InspectionExecutor, DetectionContext, DetectionResult,
            GenericDetector, DETECTOR_REGISTRY, get_detector_count,
        )
        return {"detectors": get_detector_count()}
    results["P1-2 巡检执行器"] = verify_module("P1-2 巡检执行器+检测方法", t08)

    # 9. 调度器
    def t09():
        from monitor.inspection_scheduler import (
            schedule_daily_inspection, schedule_weekly_inspection,
            schedule_monthly_inspection, INSPECTION_BEAT_SCHEDULE,
            trigger_inspection_now, run_inline,
        )
        return {"schedules": len(INSPECTION_BEAT_SCHEDULE)}
    results["P1-2 调度器"] = verify_module("P1-2 巡检调度器", t09)

    # 10. AWR 分析
    def t10():
        from monitor.awr_analyzer import (
            AwrAnalyzer, AwrReport, WaitEvent, TopSql, TopSegment,
            InstanceEfficiency, TimeModel, analyze_awr,
        )
        return AwrAnalyzer.__name__
    results["P1-3 AWR 分析"] = verify_module("P1-3 AWR 分析器", t10)

    # 11. 报告生成器
    def t11():
        from monitor.inspection_report_generator import (
            InspectionReportGenerator, generate_report, save_report,
        )
        return InspectionReportGenerator.__name__
    results["P1-4 报告生成器"] = verify_module("P1-4 巡检报告生成器", t11)

    # 12. 知识库
    def t12():
        from monitor.inspection_knowledge_base import (
            KNOWLEDGE_BASE, PatternRecognizer, TrendAnalyzer,
            KnowledgeBaseManager, recognize_patterns, suggest_solution,
            get_kb_count,
        )
        return {"kb_count": get_kb_count()}
    results["P1-5 知识库"] = verify_module("P1-5 巡检知识库+模式识别", t12)

    # 13. 案例库 RAG
    def t13():
        from monitor.case_rag import (
            CaseRag, CaseMatch, RagResult, SymptomSignature,
            search_cases, get_case_count,
        )
        return CaseRag.__name__
    results["P2-1 案例库 RAG"] = verify_module("P2-1 案例库 RAG", t13)

    # 14. 自动修复
    def t14():
        from monitor.auto_fix_loop import (
            AutoFixEngine, FixRule, FixResult, FixRules,
            FixRisk, FIX_RULES, try_fix_finding, get_fix_rule_count,
        )
        return {"rules": get_fix_rule_count()}
    results["P2-2 自动修复"] = verify_module("P2-2 自动修复闭环", t14)

    # 15. 前端页面
    def t15():
        import os
        files = [
            "frontend/src/pages/AlertDetail.jsx",
            "frontend/src/pages/InspectionCenter.jsx",
            "frontend/src/pages/InspectionDetail.jsx",
        ]
        for f in files:
            path = os.path.join(PROJECT_ROOT, f)
            if not os.path.exists(path):
                raise FileNotFoundError(f)
        return {"files": len(files)}
    results["前端页面"] = verify_module("前端页面 (3 个新增页面)", t15)

    # 16. 路由注册
    def t16():
        path = os.path.join(PROJECT_ROOT, "frontend/src/App.jsx")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        for kw in ["AlertDetail", "InspectionCenter", "InspectionDetail"]:
            if kw not in content:
                raise AssertionError(f"App.jsx 中未找到 {kw}")
        return "已注册"
    results["前端路由"] = verify_module("前端路由注册", t16)

    # 17. API 服务
    def t17():
        path = os.path.join(PROJECT_ROOT, "frontend/src/services/api.js")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        for kw in ["alertRcaAPI", "inspectionAPI"]:
            if kw not in content:
                raise AssertionError(f"api.js 中未找到 {kw}")
        return "已添加"
    results["前端 API"] = verify_module("前端 API 服务", t17)

    # 18. 菜单集成
    def t18():
        path = os.path.join(PROJECT_ROOT, "frontend/src/components/EMLayout.jsx")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if "/inspection" not in content:
            raise AssertionError("EMLayout.jsx 中未添加巡检菜单")
        if "CheckCircleOutlined" not in content:
            raise AssertionError("未导入 CheckCircleOutlined 图标")
        return "已添加"
    results["导航菜单"] = verify_module("导航菜单集成", t18)

    # 19. 知识沉淀脚本
    def t19():
        path = os.path.join(PROJECT_ROOT, "init_phase5_knowledge.py")
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        return "存在"
    results["初始化脚本"] = verify_module("知识沉淀初始化脚本", t19)

    # 20. 迁移文件
    def t20():
        path = os.path.join(PROJECT_ROOT, "monitor/migrations/0013_alertcase_businessimpactassessment_and_more.py")
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        return "存在"
    results["数据库迁移"] = verify_module("数据库迁移文件", t20)

    # ==========================================
    # 汇总
    # ==========================================
    print("\n" + "=" * 70)
    print("验证结果汇总")
    print("=" * 70)
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    for name, ok in results.items():
        marker = "[OK]" if ok else "[FAIL]"
        print(f"  {marker} {name}")
    print(f"\n通过: {passed}/{total}")
    if passed == total:
        print("\n>>> 所有 Phase 5 模块验证通过!")
    else:
        print(f"\n>>> {total - passed} 个模块验证失败,需修复")
    return passed == total


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
