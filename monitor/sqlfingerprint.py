# -*- coding: utf-8 -*-
"""SQL 归一化与统一指纹 (phase7/10 §5, 代码级契约)。

digest 优先级: ① Oracle sql_id ② MySQL 原生 DIGEST(截32) ③ PG query_id(非0)
              ④ md5(normalize(sql_text))[:32] ⑤ None
哨兵 ASH 与 sql_stat 采集共用, 保证同一 SQL 两处指纹一致。
"""
import hashlib
import re

_RE_BLOCK_COMMENT = re.compile(r'/\*.*?\*/', re.S)
_RE_LINE_COMMENT = re.compile(r'--[^\n]*')
_RE_WS = re.compile(r'\s+')
_RE_QUOTED_S = re.compile(r"'([^']|'')*'")
_RE_QUOTED_D = re.compile(r'"([^"]|"")*"')
_RE_NUMBER = re.compile(r'\b\d+(\.\d+)?\b')
_RE_IN_LIST = re.compile(r'in\s*\(\s*\?(\s*,\s*\?)*\s*\)')


def normalize(sql_text: str) -> str:
    """归一化 SQL 文本: 去注释/小写/压空白/字面量→? /IN 列表折叠。"""
    if not sql_text:
        return ''
    t = _RE_BLOCK_COMMENT.sub(' ', sql_text)
    t = _RE_LINE_COMMENT.sub(' ', t)
    t = t.lower()
    t = _RE_QUOTED_S.sub('?', t)
    t = _RE_QUOTED_D.sub('?', t)
    t = _RE_NUMBER.sub('?', t)
    t = _RE_WS.sub(' ', t).strip()
    t = _RE_IN_LIST.sub('in(?)', t)
    return t


def unified_digest(db_type: str, native_digest=None, sql_text=None):
    """统一 SQL 指纹。返回 str(<=32) 或 None。"""
    if native_digest:
        s = str(native_digest).strip()
        if s and s not in ('0', 'None'):
            return s[:32]
    norm = normalize(sql_text or '')
    if not norm:
        return None
    return hashlib.md5(norm.encode('utf-8', 'replace')).hexdigest()[:32]
