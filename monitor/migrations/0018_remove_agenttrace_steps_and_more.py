# 4NF 规范化迁移：先建子表 -> 数据搬迁 -> 再删除多值 JSON 列
# （重排 Django 自动生成的操作顺序，保证存量数据不丢失）

import django.db.models.deletion
from django.db import migrations, models


def _copy_scalar(apps, parent_name, field, child_name, fk, value_cast=str):
    Parent = apps.get_model('monitor', parent_name)
    Child = apps.get_model('monitor', child_name)
    rows = []
    for p in Parent.objects.all():
        vals = getattr(p, field, None) or []
        seen = set()
        for v in vals:
            if v is None or v == '':
                continue
            key = value_cast(v)
            if key in seen:
                continue
            seen.add(key)
            rows.append(Child(**{fk: p, 'value': key}) if value_cast is str
                        else Child(**{fk: p, 'config_id': int(v)}))
    Child.objects.bulk_create(rows)


def _copy_ordered(apps, parent_name, field, child_name, fk, phase=None):
    Parent = apps.get_model('monitor', parent_name)
    Child = apps.get_model('monitor', child_name)
    rows = []
    for p in Parent.objects.all():
        vals = getattr(p, field, None) or []
        for i, item in enumerate(vals):
            kwargs = {fk: p, 'seq': i, 'payload': item}
            if phase is not None:
                kwargs['phase'] = phase
            rows.append(Child(**kwargs))
    Child.objects.bulk_create(rows)


def forward(apps, schema_editor):
    # 标量多值
    _copy_scalar(apps, 'NotificationRule', 'alert_types', 'NotificationRuleAlertType', 'rule')
    _copy_scalar(apps, 'NotificationRule', 'severities', 'NotificationRuleSeverity', 'rule')
    _copy_scalar(apps, 'NotificationRule', 'channels', 'NotificationRuleChannel', 'rule')
    _copy_scalar(apps, 'UserProfile', 'allowed_databases', 'UserProfileDatabase', 'profile', value_cast=int)
    _copy_scalar(apps, 'MetricDefinition', 'db_types', 'MetricDefinitionDbType', 'metric')
    _copy_scalar(apps, 'ReportRecord', 'recipients', 'ReportRecordRecipient', 'report')
    _copy_scalar(apps, 'AlertCase', 'commands_used', 'AlertCaseCommand', 'case')
    _copy_scalar(apps, 'AlertCase', 'tags', 'AlertCaseTag', 'case')
    _copy_scalar(apps, 'AlertCase', 'references', 'AlertCaseReference', 'case')
    _copy_scalar(apps, 'BusinessImpactAssessment', 'health_affected_dimensions', 'BiaDimension', 'assessment')
    _copy_scalar(apps, 'InspectionItem', 'applicable_db_types', 'InspectionItemDbType', 'item')
    _copy_scalar(apps, 'InspectionItem', 'references', 'InspectionItemReference', 'item')
    _copy_scalar(apps, 'Playbook', 'applicable_db_types', 'PlaybookDbType', 'playbook')
    # 有序/复合多值
    _copy_ordered(apps, 'BusinessImpactAssessment', 'affected_systems', 'BiaSystem', 'assessment')
    _copy_ordered(apps, 'Incident', 'plans', 'IncidentPlan', 'incident')
    _copy_ordered(apps, 'Playbook', 'precheck', 'PlaybookStep', 'playbook', phase='precheck')
    _copy_ordered(apps, 'Playbook', 'steps', 'PlaybookStep', 'playbook', phase='steps')
    _copy_ordered(apps, 'Playbook', 'rollback', 'PlaybookStep', 'playbook', phase='rollback')
    _copy_ordered(apps, 'RemediationPlan', 'steps', 'RemediationPlanStep', 'plan')
    _copy_ordered(apps, 'PlaybookRun', 'step_results', 'PlaybookRunStepResult', 'run')
    _copy_ordered(apps, 'AgentTrace', 'steps', 'AgentTraceStep', 'trace')


def reverse(apps, schema_editor):
    # 回退不恢复 JSON 数据（列已删），仅保证可逆执行不报错
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('monitor', '0017_llmcalllog_rulestat_alertcase_embedding_indexed_and_more'),
    ]

    operations = [
        # ---- 1. 先创建子表 ----
        migrations.CreateModel(name='AgentTraceStep', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('seq', models.IntegerField(default=0)), ('payload', models.JSONField(default=dict)),
            ('trace', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='step_items', to='monitor.agenttrace')),
        ], options={'ordering': ['seq'], 'unique_together': {('trace', 'seq')}}),
        migrations.CreateModel(name='AlertCaseCommand', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('value', models.TextField()),
            ('case', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='command_set', to='monitor.alertcase')),
        ], options={'unique_together': {('case', 'value')}}),
        migrations.CreateModel(name='AlertCaseReference', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('value', models.TextField()),
            ('case', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reference_set', to='monitor.alertcase')),
        ], options={'unique_together': {('case', 'value')}}),
        migrations.CreateModel(name='AlertCaseTag', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('value', models.CharField(max_length=50)),
            ('case', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='tag_set', to='monitor.alertcase')),
        ], options={'unique_together': {('case', 'value')}}),
        migrations.CreateModel(name='BiaDimension', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('value', models.CharField(max_length=50)),
            ('assessment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dimension_set', to='monitor.businessimpactassessment')),
        ], options={'unique_together': {('assessment', 'value')}}),
        migrations.CreateModel(name='BiaSystem', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('seq', models.IntegerField(default=0)), ('payload', models.JSONField(default=dict)),
            ('assessment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='system_set', to='monitor.businessimpactassessment')),
        ], options={'ordering': ['seq'], 'unique_together': {('assessment', 'seq')}}),
        migrations.CreateModel(name='IncidentPlan', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('seq', models.IntegerField(default=0)), ('payload', models.JSONField(default=dict)),
            ('incident', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='plan_items', to='monitor.incident')),
        ], options={'ordering': ['seq'], 'unique_together': {('incident', 'seq')}}),
        migrations.CreateModel(name='InspectionItemDbType', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('value', models.CharField(max_length=20)),
            ('item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dbtype_set', to='monitor.inspectionitem')),
        ], options={'unique_together': {('item', 'value')}}),
        migrations.CreateModel(name='InspectionItemReference', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('value', models.TextField()),
            ('item', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reference_set', to='monitor.inspectionitem')),
        ], options={'unique_together': {('item', 'value')}}),
        migrations.CreateModel(name='MetricDefinitionDbType', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('value', models.CharField(max_length=20)),
            ('metric', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dbtype_set', to='monitor.metricdefinition')),
        ], options={'unique_together': {('metric', 'value')}}),
        migrations.CreateModel(name='NotificationRuleAlertType', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('value', models.CharField(max_length=50)),
            ('rule', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='alerttype_set', to='monitor.notificationrule')),
        ], options={'unique_together': {('rule', 'value')}}),
        migrations.CreateModel(name='NotificationRuleChannel', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('value', models.CharField(max_length=20)),
            ('rule', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='channel_set', to='monitor.notificationrule')),
        ], options={'unique_together': {('rule', 'value')}}),
        migrations.CreateModel(name='NotificationRuleSeverity', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('value', models.CharField(max_length=20)),
            ('rule', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='severity_set', to='monitor.notificationrule')),
        ], options={'unique_together': {('rule', 'value')}}),
        migrations.CreateModel(name='PlaybookDbType', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('value', models.CharField(max_length=20)),
            ('playbook', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='dbtype_set', to='monitor.playbook')),
        ], options={'unique_together': {('playbook', 'value')}}),
        migrations.CreateModel(name='PlaybookRunStepResult', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('seq', models.IntegerField(default=0)), ('payload', models.JSONField(default=dict)),
            ('run', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='step_result_items', to='monitor.playbookrun')),
        ], options={'ordering': ['seq'], 'unique_together': {('run', 'seq')}}),
        migrations.CreateModel(name='PlaybookStep', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('phase', models.CharField(max_length=12)), ('seq', models.IntegerField(default=0)),
            ('payload', models.JSONField(default=dict)),
            ('playbook', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='step_items', to='monitor.playbook')),
        ], options={'ordering': ['phase', 'seq'], 'unique_together': {('playbook', 'phase', 'seq')}}),
        migrations.CreateModel(name='RemediationPlanStep', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('seq', models.IntegerField(default=0)), ('payload', models.JSONField(default=dict)),
            ('plan', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='step_items', to='monitor.remediationplan')),
        ], options={'ordering': ['seq'], 'unique_together': {('plan', 'seq')}}),
        migrations.CreateModel(name='ReportRecordRecipient', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('value', models.CharField(max_length=200)),
            ('report', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recipient_set', to='monitor.reportrecord')),
        ], options={'unique_together': {('report', 'value')}}),
        migrations.CreateModel(name='UserProfileDatabase', fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('config_id', models.IntegerField(db_index=True)),
            ('profile', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='alloweddatabase_set', to='monitor.userprofile')),
        ], options={'unique_together': {('profile', 'config_id')}}),
        # ---- 2. 数据搬迁 ----
        migrations.RunPython(forward, reverse),
        # ---- 3. 删除多值 JSON 列 ----
        migrations.RemoveField(model_name='agenttrace', name='steps'),
        migrations.RemoveField(model_name='alertcase', name='commands_used'),
        migrations.RemoveField(model_name='alertcase', name='references'),
        migrations.RemoveField(model_name='alertcase', name='tags'),
        migrations.RemoveField(model_name='businessimpactassessment', name='affected_systems'),
        migrations.RemoveField(model_name='businessimpactassessment', name='health_affected_dimensions'),
        migrations.RemoveField(model_name='incident', name='plans'),
        migrations.RemoveField(model_name='inspectionitem', name='applicable_db_types'),
        migrations.RemoveField(model_name='inspectionitem', name='references'),
        migrations.RemoveField(model_name='metricdefinition', name='db_types'),
        migrations.RemoveField(model_name='notificationrule', name='alert_types'),
        migrations.RemoveField(model_name='notificationrule', name='channels'),
        migrations.RemoveField(model_name='notificationrule', name='severities'),
        migrations.RemoveField(model_name='playbook', name='applicable_db_types'),
        migrations.RemoveField(model_name='playbook', name='precheck'),
        migrations.RemoveField(model_name='playbook', name='rollback'),
        migrations.RemoveField(model_name='playbook', name='steps'),
        migrations.RemoveField(model_name='playbookrun', name='step_results'),
        migrations.RemoveField(model_name='remediationplan', name='steps'),
        migrations.RemoveField(model_name='reportrecord', name='recipients'),
        migrations.RemoveField(model_name='userprofile', name='allowed_databases'),
    ]
