"""
Phase 5 知识沉淀初始化脚本
=========================

为新部署环境一次性注入:
1. 演示案例数据 (5 个常见案例)
2. 知识库条目 (Phase 5 巡检项 + 解决方案映射)
3. 检查数据完整性

用法:
    python manage.py shell < init_phase5_knowledge.py
    或
    python init_phase5_knowledge.py
"""

import os
import django

# Django 初始化
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "dbmonitor.settings")
django.setup()

from monitor.case_rag import init_demo_cases, get_case_count
from monitor.inspection_knowledge_base import KNOWLEDGE_BASE, KnowledgeBaseManager
from monitor.models import InspectionItem, AlertCase, InspectionIssuePattern


def init_all():
    print("=" * 60)
    print("Phase 5 知识沉淀初始化")
    print("=" * 60)

    # 1) 演示案例
    print("\n[1/4] 注入演示案例...")
    try:
        added = init_demo_cases()
        print(f"  ✓ 已注入 {added} 个演示案例")
        print(f"  ✓ 当前案例总数: {get_case_count()}")
    except Exception as e:
        print(f"  ✗ 案例注入失败: {e}")

    # 2) 知识库条目
    print("\n[2/4] 同步知识库条目...")
    kb_count = KnowledgeBaseManager.count()
    print(f"  ✓ 知识库条目数: {kb_count}")
    for code in list(KNOWLEDGE_BASE.keys())[:5]:
        kb = KNOWLEDGE_BASE[code]
        print(f"  - {code}: {kb.get('category', 'general')} - {len(kb.get('root_causes', []))} 根因 "
              f"/ {len(kb.get('best_practices', []))} 最佳实践")

    # 3) 同步巡检项到 DB
    print("\n[3/4] 同步巡检项定义到 DB...")
    try:
        from monitor.inspection_registry import ALL_ITEMS
        synced = 0
        for it in ALL_ITEMS:
            obj, created = InspectionItem.objects.update_or_create(
                item_code=it.get("item_id"),
                defaults={
                    "title": it.get("title", ""),
                    "category": it.get("category", ""),
                    "level": it.get("level", "daily"),
                    "severity": it.get("severity", "info"),
                    "applicable_db_types": it.get("applicable_db_types", []),
                    "detect_method": it.get("detect_method", ""),
                    "threshold": it.get("threshold", {}),
                    "recommendation": it.get("recommendation", ""),
                    "auto_fixable": it.get("auto_fixable", False),
                    "auto_fix_method": it.get("auto_fix_method", ""),
                },
            )
            synced += 1
        print(f"  ✓ 已同步 {synced} 个巡检项到 DB")
    except Exception as e:
        print(f"  ✗ 巡检项同步失败: {e}")

    # 4) 检查问题模式表
    print("\n[4/4] 检查问题模式识别表...")
    pattern_count = InspectionIssuePattern.objects.count()
    print(f"  ✓ 问题模式数: {pattern_count}")

    print("\n" + "=" * 60)
    print("Phase 5 知识沉淀初始化完成")
    print("=" * 60)


if __name__ == "__main__":
    init_all()
