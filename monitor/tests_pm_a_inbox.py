# -*- coding: utf-8 -*-
"""DB-003：tools/a_inbox.py（A-AUTORUN 的 DB-AIOps 收件脚本）自测接入 unit 层。

判定规则、占用 / 领单竞争（临时裸仓库模拟 origin）、推送自检都在脚本自带的 self_test 里，
这里只负责让它跟着 validate.sh unit 与 CI 一起跑，改坏了能被拦住。
"""
import contextlib
import importlib.util
import io
from pathlib import Path

from django.test import SimpleTestCase, tag

_PATH = Path(__file__).resolve().parent.parent / 'tools' / 'a_inbox.py'


def _load():
    spec = importlib.util.spec_from_file_location('a_inbox', _PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@tag('unit')
class AInboxSelfTest(SimpleTestCase):
    def test_self_test_passes(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = _load().self_test()
        self.assertEqual(rc, 0, buf.getvalue())

    def test_current_board_does_not_wake_manual_orders(self):
        """DB-001～DB-003 由人工承接，自动链从 DB-004 起。"""
        mod = _load()
        root = _PATH.parent.parent
        if not (root / mod.ORDERS).is_dir():
            self.skipTest('当前检出没有黑板目录')
        for it in mod.evaluate(root):
            self.assertGreaterEqual(int(it['order'].split('-')[1]) if 'TEST' not in it['order'] else 99,
                                    mod.MIN_ORDER, it)
