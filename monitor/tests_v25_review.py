# -*- coding: utf-8 -*-
"""v2.5 独立复审缺陷的正式回归防线。"""
import json
import os
import importlib
from datetime import timedelta
from unittest.mock import Mock, patch

from django.conf import settings
from django.test import Client, TestCase, override_settings, tag
from django.utils import timezone

from monitor.auth import Perm, RoleCode
from monitor.crypto import encrypt_password
from monitor.models import DatabaseConfig
from monitor.tests_bugfix import login, make_db, make_user


@tag('unit')
@override_settings(LLM_ENABLED=False, AGENT_ENABLED=False, EMBED_ENABLED=False)
class CopilotInventoryDataScopeTests(TestCase):
    def setUp(self):
        self.mine = make_db('copilot-mine', port=3306)
        self.secret = DatabaseConfig.objects.create(
            name='核心交易库-生产', db_type='oracle', host='10.88.88.88',
            port=1521, username='u', password=encrypt_password('p'), is_active=True)
        self.user = make_user(
            'copilotscoped', RoleCode.DBA, [Perm.METRICS_VIEW],
            allowed_dbs=[self.mine.id])
        self.client = Client()
        login(self.client, self.user)

    def test_inventory_must_not_leak_out_of_scope_databases(self):
        response = self.client.post(
            '/api/v1/copilot/chat/',
            data=json.dumps({'query': '我们一共纳管了哪些库？给我资产清单'}),
            content_type='application/json')
        body = response.content.decode('utf-8')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('核心交易库-生产', body)
        self.assertNotIn('10.88.88.88', body)
        self.assertIn('copilot-mine', body)

    def test_visible_to_queryset_is_the_single_scope_primitive(self):
        visible = DatabaseConfig.objects.visible_to(self.user)
        self.assertEqual(list(visible.values_list('id', flat=True)), [self.mine.id])


@tag('unit')
class CopilotToolsEvidenceTests(TestCase):
    def setUp(self):
        self.cfg = DatabaseConfig.objects.create(
            name='全新纳管-零数据', db_type='oracle', host='10.9.9.9', port=1521,
            username='u', password=encrypt_password('p'), is_active=True)

    def test_ash_tool_must_not_invent_sessions(self):
        from monitor.copilot import get_realtime_ash
        ash = get_realtime_ash(self.cfg)
        self.assertFalse(ash['available'])
        self.assertIsNone(ash['sql_text'])
        self.assertEqual(ash['blocked_session_count'], 0)
        self.assertEqual(ash['top_sessions'], [])

    def test_ash_tool_uses_real_samples(self):
        from monitor.copilot import get_realtime_ash
        observed = timezone.now()
        storage = type('Storage', (), {
            'query_session_samples': lambda _self, _config_id, _since: [{
                'time': observed, 'session_id': '77', 'user_name': 'bank_app',
                'state': 'WAITING', 'is_blocked': True, 'blocker_id': '11',
                'sql_id': 'real-sql', 'sql_text': 'select * from account',
                'wait_class': 'Concurrency', 'wait_event': 'row lock',
                'wait_secs': 2,
            }],
        })()
        with patch('monitor.timeseries.get_timeseries_storage', return_value=storage):
            ash = get_realtime_ash(self.cfg)
        self.assertTrue(ash['available'])
        self.assertEqual(ash['sql_id'], 'real-sql')
        self.assertEqual(ash['blocked_session_count'], 1)
        self.assertEqual(ash['top_sessions'][0]['session_id'], '77')

    def test_tablespace_tool_must_not_invent_high_watermark(self):
        from monitor.copilot import get_tablespace_and_capacity_status
        report = get_tablespace_and_capacity_status(
            self.cfg)['databases_tablespace_report'][0]
        self.assertFalse(report['available'])
        self.assertEqual(report['high_watermark_count'], 0)
        self.assertEqual(report['tablespaces'], [])

    def test_explain_tool_must_not_invent_execution_plan(self):
        from monitor.copilot import explain_sql
        plan = explain_sql(self.cfg, 'UPDATE orders SET status=2 WHERE batch_id=1')
        self.assertFalse(plan['available'])
        self.assertEqual(plan['execution_plan_tree'], [])


@tag('unit')
@override_settings(LLM_ENABLED=False, AGENT_ENABLED=False, EMBED_ENABLED=False)
class CopilotSelectedConfigAndMemoryTests(TestCase):
    def setUp(self):
        from monitor.models import CopilotMemory, MonitorLog
        self.mine = make_db('selected-mine')
        self.other = make_db('selected-other', port=3307)
        MonitorLog.objects.create(
            config=self.mine, status='UP',
            message=json.dumps({'tablespaces': [{'name': 'MINE_TS', 'used_pct': 20}]}))
        MonitorLog.objects.create(
            config=self.other, status='UP',
            message=json.dumps({'tablespaces': [{'name': 'OTHER_SECRET_TS', 'used_pct': 99}]}))
        CopilotMemory.objects.create(
            config=self.mine, locus_key='容量', title='我的容量经验', content='MINE_MEMORY')
        CopilotMemory.objects.create(
            config=self.other, locus_key='容量', title='他库秘密', content='OTHER_SECRET_MEMORY')
        self.user = make_user(
            'selected-scope', RoleCode.DBA, [Perm.METRICS_VIEW],
            allowed_dbs=[self.mine.id])
        self.client = Client()
        login(self.client, self.user)

    def test_tablespace_tool_respects_selected_database(self):
        response = self.client.post(
            '/api/v1/copilot/chat/',
            data=json.dumps({'query': '检查表空间容量', 'config_id': self.mine.id}),
            content_type='application/json')
        payload = response.json()
        reports = payload['tool_results']['tablespace_and_capacity']['databases_tablespace_report']
        self.assertEqual([item['config_id'] for item in reports], [self.mine.id])
        self.assertNotIn('OTHER_SECRET_TS', response.content.decode())

    def test_memory_recall_must_respect_scope(self):
        response = self.client.post(
            '/api/v1/copilot/chat/', data=json.dumps({'query': '回忆容量经验'}),
            content_type='application/json')
        body = response.content.decode()
        self.assertIn('MINE_MEMORY', body)
        self.assertNotIn('OTHER_SECRET_MEMORY', body)


@tag('unit')
class LlmCredentialSecurityTests(TestCase):
    def test_api_key_must_not_be_stored_in_plaintext(self):
        from django.db import connection
        from monitor.models import LLMProviderCredential
        raw = 'sk-live-SUPERSECRET-abcdef'  # secret-scan: allow 测试夹具
        credential = LLMProviderCredential.objects.create(
            name='t', provider_type='openai', base_url='https://api.openai.com',
            api_key=raw, model_name='gpt-4')
        with connection.cursor() as cursor:
            cursor.execute(
                'SELECT api_key FROM llm_provider_credential WHERE id=%s',
                [credential.id])
            stored = cursor.fetchone()[0]
        self.assertNotEqual(stored, raw)
        self.assertTrue(stored.startswith('enc:'))
        credential.refresh_from_db()
        self.assertEqual(credential.get_api_key(), raw)

    def test_data_migration_encrypts_legacy_key_and_clears_web_proxy(self):
        from django.apps import apps
        from monitor.models import LLMProviderCredential
        credential = LLMProviderCredential.objects.create(
            name='legacy', provider_type='custom', base_url='https://api.openai.com',
            model_name='legacy-model')
        LLMProviderCredential.objects.filter(id=credential.id).update(
            api_key='legacy-plaintext-key',  # secret-scan: allow 测试夹具
            proxy_url='http://127.0.0.1:7890')
        migration = importlib.import_module(
            'monitor.migrations.0030_v25_review_hardening')
        migration.encrypt_existing_keys_and_seed_templates(apps, None)
        credential.refresh_from_db()
        self.assertTrue(credential.api_key.startswith('enc:'))
        self.assertEqual(credential.get_api_key(), 'legacy-plaintext-key')
        self.assertEqual(credential.proxy_url, '')

    @override_settings(LLM_ALLOWED_ENDPOINT_HOSTS=('model.bank.local',))
    def test_endpoint_policy_blocks_ssrf_and_allows_deployment_allowlist(self):
        from monitor.llm.security import LLMEndpointValidationError, validate_llm_base_url
        for bad in (
            'http://api.openai.com/v1',
            'https://169.254.169.254/latest/meta-data',
            'https://user:password@api.openai.com/v1',
            'https://127.0.0.1/v1',
        ):
            with self.subTest(url=bad), self.assertRaises(LLMEndpointValidationError):
                validate_llm_base_url(bad)
        self.assertEqual(
            validate_llm_base_url('https://model.bank.local/v1'),
            'https://model.bank.local/v1')

    @override_settings(
        LLM_ALLOWED_ENDPOINT_HOSTS=('generativelanguage.googleapis.com',),
        LLM_PROXY_URL='')
    def test_gemini_key_is_sent_only_in_header(self):
        from monitor.llm.providers import OpenAICompatProvider
        response = Mock(status_code=200, text='')
        response.json.return_value = {
            'candidates': [{'content': {'parts': [{'text': 'pong'}]}}],
            'usageMetadata': {},
        }
        provider = OpenAICompatProvider(
            base_url='https://generativelanguage.googleapis.com/v1beta',
            api_key='gemini-secret',  # secret-scan: allow 测试夹具
            model='gemini-test')
        with patch('requests.post', return_value=response) as post:
            provider.chat([{'role': 'user', 'content': 'ping'}], json_mode=False)
        url = post.call_args.args[0]
        headers = post.call_args.kwargs['headers']
        self.assertNotIn('gemini-secret', url)
        self.assertEqual(headers['x-goog-api-key'], 'gemini-secret')
        self.assertFalse(post.call_args.kwargs['allow_redirects'])


@tag('unit')
@override_settings(LLM_ALLOWED_ENDPOINT_HOSTS=('model.bank.local',))
class LlmRbacAndConfigTests(TestCase):
    def setUp(self):
        self.viewer = make_user(
            'llm-viewer', RoleCode.READONLY, [Perm.ALERT_CONFIG_VIEW])
        self.manager = make_user(
            'llm-manager', RoleCode.CONFIG_OPERATOR,
            [Perm.ALERT_CONFIG_VIEW, Perm.ALERT_CONFIG_MANAGE])

    def test_view_permission_cannot_create_credential(self):
        client = Client()
        login(client, self.viewer)
        response = client.post(
            '/api/v1/llm/credentials/', data=json.dumps({
                'name': 'blocked', 'base_url': 'https://model.bank.local/v1',
                'model_name': 'bank-model',
            }), content_type='application/json')
        self.assertEqual(response.status_code, 403)

    def test_manage_permission_updates_db_without_mutating_settings(self):
        from monitor.models import LLMProviderCredential
        client = Client()
        login(client, self.manager)
        old_base = settings.LLM_BASE_URL
        response = client.put(
            '/api/v1/llm/config/', data=json.dumps({
                'llm_enabled': True, 'llm_provider': 'custom',
                'llm_base_url': 'https://model.bank.local/v1',
                'llm_model': 'bank-model',
                'llm_api_key': 'sk-bank-secret',  # secret-scan: allow 测试夹具
            }), content_type='application/json')
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(settings.LLM_BASE_URL, old_base)
        credential = LLMProviderCredential.objects.get(name='系统默认')
        self.assertTrue(credential.api_key.startswith('enc:'))
        self.assertEqual(credential.proxy_url, '')


@tag('unit')
class RedlineAndPlaybookMechanismTests(TestCase):
    def test_ci_clean_checkout_scans_parent_commit(self):
        from scripts import lint_redlines

        def fake_git(*args):
            if args == ('diff', 'HEAD', '--name-status'):
                return ''
            if args == ('rev-parse', '--verify', 'HEAD^'):
                return 'parent-sha\n'
            return ''

        with patch.dict(os.environ, {'CI': 'true'}), patch.object(
                lint_redlines, '_git', side_effect=fake_git):
            self.assertEqual(lint_redlines._diff_args(), ('HEAD^', 'HEAD'))

    def test_dryrun_does_not_bootstrap_on_read_path(self):
        from monitor.models import PlaybookTemplate
        from monitor.playbook_engine_v2 import PlaybookExecutor
        cfg = make_db('playbook-no-write')
        PlaybookTemplate.objects.filter(code='KILL_ROOT_BLOCKER').delete()
        before = PlaybookTemplate.objects.count()
        result = PlaybookExecutor.evaluate_dryrun(
            'KILL_ROOT_BLOCKER', cfg, {}, incident=None)
        self.assertEqual(result['status'], 'REJECTED')
        self.assertEqual(PlaybookTemplate.objects.count(), before)
