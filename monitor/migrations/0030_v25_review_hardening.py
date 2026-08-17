from django.db import migrations, models


DEFAULT_TEMPLATES = (
    {
        'code': 'KILL_ROOT_BLOCKER',
        'name': '安全终止根源阻塞会话',
        'db_types': ['oracle', 'mysql', 'pgsql', 'dm', 'tdsql', 'gbase'],
        'risk_level': 'low',
        'min_autonomy_level': 1,
        'description': '精准终止持有排他锁并阻塞下游多个事务的长事务会话，快速释放阻塞链',
        'steps_payload': [{'action': 'KILL_SESSION', 'target': 'session_id'}],
        'rollback_payload': [{'action': 'LOG', 'msg': '会话已终止，无需回滚'}],
    },
    {
        'code': 'RESIZE_TABLESPACE',
        'name': '数据表空间自动扩容',
        'db_types': ['oracle', 'dm'],
        'risk_level': 'medium',
        'min_autonomy_level': 2,
        'description': '对水位超过 85% 的表空间追加数据文件或扩展物理文件',
        'steps_payload': [{'action': 'EXTEND_DATAFILE', 'size_gb': 10}],
        'rollback_payload': [{'action': 'LOG', 'msg': '数据文件已扩展'}],
    },
    {
        'code': 'FLUSH_QUERY_CACHE',
        'name': '释放临时表与缓存重置',
        'db_types': ['mysql', 'pgsql'],
        'risk_level': 'low',
        'min_autonomy_level': 1,
        'description': '清理临时表空间并刷新缓存，释放锁定的系统资源',
        'steps_payload': [{'action': 'FLUSH_TEMP', 'target': 'temp_pool'}],
        'rollback_payload': [],
    },
)

DEFAULT_ROUTES = (
    ('global_default', '全局默认兜底', '未单独配置场景时使用'),
    ('copilot_chat', 'Copilot 专家日常对话', 'DBA 日常问答与证据检索'),
    ('rca_deep_reasoning', 'RCA 3.0 根因深度推理', '重大事故多跳因果分析'),
    ('sql_explain_opt', 'SQL 执行计划与索引优化', '已采集执行计划与索引候选分析'),
    ('incident_warroom', '排障作战室自愈决策', '事故证据绑定的预演与审批'),
)


def encrypt_existing_keys_and_seed_templates(apps, schema_editor):
    from monitor.crypto import encrypt_password

    Credential = apps.get_model('monitor', 'LLMProviderCredential')
    for credential in Credential.objects.exclude(api_key='').iterator():
        if not credential.api_key.startswith('enc:'):
            credential.api_key = encrypt_password(credential.api_key)
            credential.save(update_fields=['api_key'])
    # 代理改为部署期配置，删除历史 Web 可写代理，避免继续转发模型凭据。
    Credential.objects.exclude(proxy_url='').update(proxy_url='')

    Template = apps.get_model('monitor', 'PlaybookTemplate')
    for item in DEFAULT_TEMPLATES:
        code = item['code']
        defaults = {key: value for key, value in item.items() if key != 'code'}
        defaults['is_active'] = True
        Template.objects.get_or_create(code=code, defaults=defaults)

    Route = apps.get_model('monitor', 'LLMSceneRoutingRule')
    for code, name, description in DEFAULT_ROUTES:
        Route.objects.get_or_create(
            scene_code=code,
            defaults={'scene_name': name, 'description': description},
        )


class Migration(migrations.Migration):
    dependencies = [('monitor', '0029_processlease')]

    operations = [
        migrations.AlterField(
            model_name='llmprovidercredential',
            name='api_key',
            field=models.CharField(
                blank=True, default='', max_length=512,
                verbose_name='AES-GCM 加密存储的 API Key'),
        ),
        migrations.AlterField(
            model_name='llmprovidercredential',
            name='proxy_url',
            field=models.CharField(
                blank=True, default='', max_length=255,
                help_text='仅为迁移兼容保留；运行时忽略，代理由部署期 LLM_PROXY_URL 管理',
                verbose_name='已停用的历史代理 URL'),
        ),
        migrations.RunPython(
            encrypt_existing_keys_and_seed_templates,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
