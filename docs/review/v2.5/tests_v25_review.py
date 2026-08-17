# -*- coding: utf-8 -*-
"""v2.5 独立复审的复现用例 —— 见 V2.5_INDEPENDENT_REVIEW.md。

与 tests_v2_review.py 同样的约定：每条都能在当前 master 上稳定复现一个真实问题。
本文件**不使用** expectedFailure —— v2.5 已明确"新增缺陷不得以 expected failure
换取绿灯"，因此这些用例默认不接入 CI，由复审报告引用、随修复一并转正。
"""
import json

from django.test import Client, TestCase, tag

from monitor.auth import Perm, RoleCode
from monitor.crypto import encrypt_password
from monitor.models import DatabaseConfig
from monitor.tests_bugfix import login, make_db, make_user


@tag('unit')
class CopilotInventoryDataScopeTests(TestCase):
    """R25-01：Copilot 的资产清单工具无视数据范围，泄露全部纳管实例。

    `CopilotChatView` 对显式传入的 config_id 做了范围校验，但
    `run_copilot_chat()` 拿不到 user，其内部 `get_global_managed_inventory()`
    直接 `DatabaseConfig.objects.filter(is_active=True)` —— 返回所有实例的
    名称/host/port。这是 BUG-103 / REV-01 的第三次复发，这次经由助手泄露。
    """

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
        r = self.client.post(
            '/api/v1/copilot/chat/',
            data=json.dumps({'query': '我们一共纳管了哪些库？给我资产清单'}),
            content_type='application/json')
        body = r.content.decode('utf-8')
        self.assertNotIn(
            '核心交易库-生产', body,
            '越权：数据范围外的实例名称出现在 Copilot 回答中')
        self.assertNotIn(
            '10.88.88.88', body,
            '越权：数据范围外的实例 host 出现在 Copilot 回答中')


@tag('unit')
class CopilotToolsFabricationTests(TestCase):
    """R25-02：Copilot 工具层对零数据实例编造具体事实。

    这些编造值会作为"工具观测结果"喂给 LLM 当作既定事实，再由 LLM 组织成
    权威口径的回答，并生成可点击的动作卡片。
    """

    def setUp(self):
        self.cfg = DatabaseConfig.objects.create(
            name='全新纳管-零数据', db_type='oracle', host='10.9.9.9', port=1521,
            username='u', password=encrypt_password('p'), is_active=True)

    def test_ash_tool_must_not_invent_sessions(self):
        from monitor.copilot import get_realtime_ash
        ash = get_realtime_ash(self.cfg)
        self.assertNotEqual(
            ash.get('sql_text'),
            'UPDATE trade_order SET status = 2 WHERE batch_id = 90218',
            '从未采集过数据的实例，ASH 工具返回了硬编码的样例 SQL 与会话')
        self.assertNotEqual(
            ash.get('blocked_session_count'), 18,
            '零数据实例被报告有 18 个被阻塞会话')

    def test_tablespace_tool_must_not_invent_high_watermark(self):
        from monitor.copilot import get_tablespace_and_capacity_status
        rep = get_tablespace_and_capacity_status(self.cfg)['databases_tablespace_report'][0]
        names = [t['name'] for t in rep['tablespaces']]
        self.assertEqual(
            rep['high_watermark_count'], 0,
            f'零数据实例被报告有 {rep["high_watermark_count"]} 个高水位表空间'
            f'（编造的表空间：{names}）—— 会直接触发不存在的容量升级')

    def test_explain_tool_must_not_invent_execution_plan(self):
        from monitor.copilot import explain_sql
        plan = explain_sql(self.cfg, 'UPDATE trade_order SET status=2 WHERE batch_id=1')
        self.assertFalse(
            any(n.get('cost') == 18450.2 for n in plan['execution_plan_tree']),
            '未连接目标库却返回了带具体 cost/rows 的执行计划')


@tag('unit')
class CopilotSelectedConfigIgnoredTests(TestCase):
    """R25-03：run_copilot_chat 中 config 在赋值前就被两个工具引用。

    `config` 直到 `if config_id:` 分支才赋值，而告警基线工具与表空间工具在此之前
    就以 `config=config`(=None) 调用 —— 用户明明选了目标库，这两个工具却按全局跑。
    """

    def test_tablespace_tool_respects_selected_database(self):
        import inspect
        from monitor import copilot
        src = inspect.getsource(copilot.run_copilot_chat)
        head = src.split('if config_id:')[0]
        self.assertNotIn(
            'get_tablespace_and_capacity_status(config=config)', head,
            'config 尚未赋值就被表空间工具引用，选中的目标库被忽略、退化为全局扫描')


@tag('unit')
class LlmCredentialPlaintextTests(TestCase):
    """R25-04：LLM API Key 明文落库，而同库的数据库密码是加密的。

    模型字段的 verbose_name 写着"加密存储的 API Key"，实际没有任何加解密。
    """

    def test_api_key_must_not_be_stored_in_plaintext(self):
        from django.db import connection
        from monitor.models import LLMProviderCredential
        raw = 'sk-live-SUPERSECRET-abcdef'
        cred = LLMProviderCredential.objects.create(
            name='t', provider_type='openai', base_url='https://api.openai.com',
            api_key=raw, model_name='gpt-4')
        with connection.cursor() as cur:
            cur.execute('SELECT api_key FROM llm_provider_credential WHERE id=%s',
                        [cred.id])
            stored = cur.fetchone()[0]
        self.assertNotEqual(
            stored, raw,
            '大模型 API Key 明文落库；同一个库里 DatabaseConfig.password 是 enc: 加密的')
