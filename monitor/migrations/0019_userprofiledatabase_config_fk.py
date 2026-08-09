"""BUG-130: UserProfileDatabase.config_id 由裸 IntegerField 改为真外键。

原实现删除 DatabaseConfig 后授权记录残留；若新实例复用同一自增 ID
（手工 setval / 数据迁移导入），旧授权会意外套用到新实例上 —— 静默越权。

实现要点：物理列名保持 `config_id` 不变，所以**不能**用
RemoveField + AddField（那会 DROP 列，把所有授权数据一起删掉）。
这里用 SeparateDatabaseAndState：
  - 状态侧：告诉 Django 该字段现在是 ForeignKey
  - 数据库侧：只清理孤儿行 + 补加外键约束，不动列本身
"""
from django.db import migrations, models
import django.db.models.deletion


def drop_orphans(apps, schema_editor):
    """删除指向已不存在实例的授权行（否则外键约束无法建立）。"""
    UserProfileDatabase = apps.get_model('monitor', 'UserProfileDatabase')
    DatabaseConfig = apps.get_model('monitor', 'DatabaseConfig')
    valid_ids = set(DatabaseConfig.objects.values_list('id', flat=True))
    orphans = UserProfileDatabase.objects.exclude(config_id__in=valid_ids)
    count = orphans.count()
    if count:
        orphans.delete()
        print(f"  [0019] 已清理 {count} 条指向已删除实例的残留授权行")


def noop(apps, schema_editor):
    """回滚方向无需动作。"""


def add_fk(apps, schema_editor):
    """仅 PostgreSQL 执行原生 DDL；SQLite（unit 测试库）无 ADD CONSTRAINT 语法，
    跳过即可 —— FK 语义由状态侧模型保证，真实外键行为属 integration 层职责。"""
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(
        "ALTER TABLE monitor_userprofiledatabase "
        "ADD CONSTRAINT monitor_upd_config_id_fk "
        "FOREIGN KEY (config_id) REFERENCES monitor_databaseconfig (id) "
        "ON DELETE CASCADE")


def drop_fk(apps, schema_editor):
    if schema_editor.connection.vendor != 'postgresql':
        return
    schema_editor.execute(
        "ALTER TABLE monitor_userprofiledatabase "
        "DROP CONSTRAINT IF EXISTS monitor_upd_config_id_fk")


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0018_remove_agenttrace_steps_and_more'),
    ]

    operations = [
        # 1) 先清孤儿行（在状态切换前用旧字段名 config_id 访问）
        migrations.RunPython(drop_orphans, noop),

        # 2) 状态侧改成外键；数据库侧只加约束，列原样保留
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.AlterUniqueTogether(
                    name='userprofiledatabase',
                    unique_together=set(),
                ),
                migrations.RemoveField(
                    model_name='userprofiledatabase',
                    name='config_id',
                ),
                migrations.AddField(
                    model_name='userprofiledatabase',
                    name='config',
                    field=models.ForeignKey(
                        db_column='config_id',
                        default=None,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name='+',
                        to='monitor.databaseconfig',
                        verbose_name='可访问数据库',
                    ),
                    preserve_default=False,
                ),
                migrations.AlterUniqueTogether(
                    name='userprofiledatabase',
                    unique_together={('profile', 'config')},
                ),
            ],
            database_operations=[
                migrations.RunPython(add_fk, drop_fk),
            ],
        ),
    ]
