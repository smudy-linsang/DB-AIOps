"""BUG-119: SqlPlan 同一 (config, sql_digest) 只能有一个 is_current=True。

模型文档一直声称该不变量，但此前无任何机制保证；并发采集与手工 EXPLAIN
会各插一条"当前计划"，详情页的 is_current 标记随之混乱。

仅靠 select_for_update 不足以修复 —— 行锁锁不住尚不存在的行（幻读），
必须由数据库的部分唯一索引兜底。

加约束前需清理历史脏数据：同一 digest 存在多条 is_current=True 时，
保留 captured_at 最新的一条（若并列则取 id 最大者），其余置 False。
"""
from django.db import migrations, models


def dedupe_current_plans(apps, schema_editor):
    SqlPlan = apps.get_model('monitor', 'SqlPlan')
    seen_keys = (SqlPlan.objects.filter(is_current=True)
                 .values_list('config_id', 'sql_digest').distinct())
    fixed = 0
    for config_id, sql_digest in seen_keys:
        rows = list(SqlPlan.objects
                    .filter(config_id=config_id, sql_digest=sql_digest, is_current=True)
                    .order_by('-captured_at', '-id')
                    .values_list('id', flat=True))
        if len(rows) > 1:
            SqlPlan.objects.filter(id__in=rows[1:]).update(is_current=False)
            fixed += len(rows) - 1
    if fixed:
        print(f"  [0021] 已清理 {fixed} 条重复的 is_current=True 计划记录")


def noop(apps, schema_editor):
    """回滚方向无需动作：去掉唯一约束不会产生冲突。"""


class Migration(migrations.Migration):

    dependencies = [
        ("monitor", "0020_auditlog_config_nullable"),
    ]

    operations = [
        migrations.RunPython(dedupe_current_plans, noop),
        migrations.AddConstraint(
            model_name="sqlplan",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_current", True)),
                fields=("config", "sql_digest"),
                name="uniq_current_plan_per_digest",
            ),
        ),
    ]
