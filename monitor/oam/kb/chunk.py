# -*- coding: utf-8 -*-
"""知识库三步构建 · 第二步：**语义精炼 / 自适应切分**（论文 §1.3① 第 2 项）。

论文原文的要求有三条，逐条对应本文件的实现：

1. 叙述性内容（故障处理方案、最佳实践指南）→ **递归切分**；
2. 结构化内容（配置参数手册）→ **结构感知切分**，硬要求是
   *"一个参数的描述、默认值、取值范围留在同一逻辑单元"*；
3. 每个片段 **< 1000 token**（论文 §6.1.1 的实现规模口径）。

## 结构感知怎么才算真的做到（这是最容易做假的一条）

朴素实现是"按长度硬切 + 尽量对齐换行"，看起来也能保证片段 <1000 token，
但会把 `SGA_TARGET` 的"默认值 2G"切到下一个片段去 —— 参数说明一旦被劈开，
检索命中一个片段就只拿到半个参数，策略据此给修复建议必然出错，而且**错得很
安静**：检索照常命中，只是命中了残缺知识。所以本实现的做法是：

- 先按**参数条目的逻辑边界**归组：识别"参数名"作为单元起点，识别
  `描述/默认值/取值范围/最小值/最大值/单位/类型/作用域/示例` 等字段行并
  **归入当前参数单元**；同时支持 Markdown 标题、Markdown 参数表格、
  `参数名：值` 定义列表三种手册常见版式；
- 每个单元整体成为一个 Chunk，**字段永不跨片段**；
- 单元自带 `fields` 字段（它实际覆盖了哪些字段），落库后可直接核对
  "描述/默认值/取值范围是否真的同单元"，把这条硬要求变成可验收的断言
  而不是一句承诺。

## 硬性上限与"不许劈字段"冲突时怎么办

单个参数条目本身就 >1000 token（罕见但存在，例如一个巨型参数的取值枚举表）。
此时两条要求物理冲突。本实现的取舍是：**字段完整性优先于长度上限** —— 宁可留一个
超限片段，也不产出残缺参数；同时打 `oversized=True` 标记并 `degrade.note` 留痕，
让入库流程把它挑出来交给人处理。静默截断才是真正的错误来源。

## token 计数口径（确定性近似，不引入 tiktoken 等新依赖）

    estimate_tokens(text) = Σ(CJK 字符按 1 计)
                          + Σ(非 CJK 的非空白词干按 ceil(len/4) 计)

依据：CJK 在主流 BPE 里接近 1 字 1 token；英文/数字普遍约 4 字符 1 token。
该近似对中英混排的运维手册误差约在 ±20%，**足够守住"<1000 token"这个硬上限的
方向**，但不能当精确账单用。真实计费口径请在上层用模型分词器单独算。
算法纯函数、无依赖、同输入同输出，因此可单测、可回归。

## 已知限制

1. 只能识别本文件 `_FIELD_WORDS` / `_SECTION_STARTS` 列出的字段与标题形态。
   手册若用完全自定义的字段名（例如"允许范围"），字段块会退化成普通正文，
   此时切分退化为"段落级结构切分"，仍不会劈开段落内的字段行，但单元粒度变粗。
2. 表格切分只按**行**为边界；单元格里若再嵌套表格，超宽行仍可能超限（走 oversized）。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# 论文 §6.1.1：每片段 <1K token
MAX_CHUNK_TOKENS = 1000

STRATEGY_RECURSIVE = 'recursive'
STRATEGY_STRUCTURE = 'structure_aware'

# 字段识别：键名 → 归一化字段名。命中即视为"参数单元内的字段行"。
FIELD_WORDS: dict[str, str] = {
    '描述': 'desc', '说明': 'desc', '含义': 'desc', '作用': 'desc', '用途': 'desc',
    '默认值': 'default', '缺省值': 'default', '初始值': 'default',
    '取值范围': 'range', '取值': 'range', '范围': 'range', '合法值': 'range',
    '最小值': 'min', '最大值': 'max', '最小': 'min', '最大': 'max',
    '单位': 'unit', '类型': 'type', '作用域': 'scope', '级别': 'scope',
    '示例': 'example', '样例': 'example', '备注': 'note', '限制': 'note',
    # 英文手册同义写法
    'description': 'desc', 'purpose': 'desc', 'default': 'default',
    'range': 'range', 'values': 'range', 'unit': 'unit', 'type': 'type',
    'scope': 'scope', 'example': 'example', 'note': 'note',
}

# 论文硬要求点名的三个字段，单独暴露给测试与入库校验用
REQUIRED_COHESION_FIELDS: tuple[str, ...] = ('desc', 'default', 'range')

_CJK_RE = re.compile(r'[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]')
_ASCII_WORD_RE = re.compile(r'[^\s\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]+')
# `键：值` / `键: 值`，键限定在字段词表或标识符形态内
_KV_RE = re.compile(r'^\s*([A-Za-z_一-鿿][A-Za-z0-9_.$一-鿿 ]{0,30}?)\s*[:：]\s*(.*)$')
_BOLD_RE = re.compile(r'^\s*\*\*\s*(.+?)\s*\*\*\s*[:：]?\s*(.*)$')
_HEADING_RE = re.compile(r'^\s*(#{1,6})\s+(.*\S)\s*$')
_IDENT_RE = re.compile(r'^[A-Za-z_][A-Za-z0-9_.$]{1,63}$')


@dataclass
class Chunk:
    """一个语义片段。

    `split_strategy` 取 `recursive` / `structure_aware`；`fields` / `section`
    是结构感知切分的产物，用于事后核对"参数字段同单元"这条论文硬要求。
    """

    chunk_id: str
    text: str
    token_count: int
    split_strategy: str
    ordinal: int = 0
    doc_id: str = ''
    section: str = ''                              # 所属参数名/小节标题
    fields: tuple[str, ...] = ()                   # 单元覆盖的字段（desc/default/range/...）
    oversized: bool = False                        # 超过上限但为保字段完整性未切

    @property
    def has_cohesive_fields(self) -> bool:
        """该单元是否同时覆盖了论文点名的三个字段（desc/default/range）。

        注意这不是硬断言：`range` 与 `min`+`max` 是两种等价写法，所以
        `range_fields = {'range'} | (有 min 且有 max)`。为真说明字段确实同单元。
        """
        got = set(self.fields)
        has_range = 'range' in got or ({'min', 'max'} <= got)
        return {'desc', 'default'} <= got and has_range

    def to_dict(self) -> dict:
        return {
            'chunk_id': self.chunk_id, 'text': self.text,
            'token_count': self.token_count, 'split_strategy': self.split_strategy,
            'ordinal': self.ordinal, 'doc_id': self.doc_id, 'section': self.section,
            'fields': list(self.fields), 'oversized': self.oversized,
        }


@dataclass
class _Unit:
    """结构感知切分的中间产物：一个参数条目的逻辑单元。"""

    name: str
    lines: list = field(default_factory=list)
    fields: list = field(default_factory=list)

    @property
    def text(self) -> str:
        return '\n'.join(self.lines).strip()


# ==========================================================================
# token 估计
# ==========================================================================
def estimate_tokens(text: str) -> int:
    """确定性 token 近似（口径见模块 docstring）。纯函数、无依赖、可单测。"""
    if not text:
        return 0
    total = len(_CJK_RE.findall(text))
    for word in _ASCII_WORD_RE.findall(text):
        total += -(-len(word) // 4)   # ceil(len/4)
    return total


# ==========================================================================
# 入参与标识
# ==========================================================================
def _doc_field(doc: Any, name: str, default: Any = '') -> Any:
    if isinstance(doc, Mapping):
        return doc.get(name, default)
    return getattr(doc, name, default)


def _doc_id(doc: Any) -> str:
    return str(_doc_field(doc, 'doc_id', '') or _doc_field(doc, 'id', '') or 'doc')


def _make_chunk_id(doc_id: str, ordinal: int, text: str) -> str:
    """片段 ID：文档内序号 + 文本内容摘要。

    加内容摘要是为了让 ID 在"文档局部改动后重切"时尽量保持稳定 ——
    §1.6⑤ 要求咨询类判定按文档集合精确相等，ID 抖动会让这种判定失去意义。
    """
    digest = hashlib.sha1(text.encode('utf-8')).hexdigest()[:8]
    return f'{doc_id}#{ordinal:03d}-{digest}'


# ==========================================================================
# 递归切分（叙述性内容）
# ==========================================================================
_SEPARATORS: tuple[str, ...] = (
    '\n\n', '\n', '。', '！', '？', '；', '. ', '; ', '! ', '? ', '，', ', ', ' ',
)


def recursive_split(doc: Any, max_tokens: int = MAX_CHUNK_TOKENS) -> list[Chunk]:
    """递归切分：按分隔符优先级由粗到细切，再按上限装箱。

    与 LangChain 递归切分同构：先用粗分隔符切，切不动才换更细的，
    最后兜底到字符级硬切（只用于超长无分隔符的连续文本，例如大段表格）。
    """
    doc_id = _doc_id(doc)
    title = str(_doc_field(doc, 'title', '') or '')
    body = str(_doc_field(doc, 'body', '') or '')
    # 标题带进每个片段：检索片段时标题是强信号，论文的 1 万 chunk 口径也含标题
    full = f'{title}\n\n{body}' if title else body

    segments = _recursive_segments(full, list(_SEPARATORS), max_tokens)
    return _pack_segments(
        segments, doc_id=doc_id, max_tokens=max_tokens,
        strategy=STRATEGY_RECURSIVE, title=title,
    )


def _recursive_segments(text: str, seps: list[str], max_tokens: int) -> list[str]:
    """把文本切成不超过上限的片段；用尽分隔符后按字符硬切兜底。"""
    if estimate_tokens(text) <= max_tokens:
        return [text]
    if not seps:
        # 分隔符全部用尽：退化为按字符硬切（无分隔符的连续文本，如宽表格/代码块）
        return _hard_split(text, max_tokens)

    sep, rest = seps[0], seps[1:]
    parts = text.split(sep) if sep else list(text)
    out: list[str] = []
    for p in parts:
        if not p.strip():
            continue
        if estimate_tokens(p) > max_tokens:
            out.extend(_recursive_segments(p, rest, max_tokens))
        else:
            out.append(p)
    return out


def _hard_split(text: str, max_tokens: int) -> list[str]:
    """按字符数硬切（无分隔符的连续文本兜底，如宽表格/代码块）。

    只在分隔符全部用尽后触发；此时"保字段同单元"已无手段可用，
    宁可超限也不劈，所以本函数按"最小安全字符步长"切并由调用方标 oversized。
    """
    if estimate_tokens(text) <= max_tokens:
        return [text]
    # 非 CJK 部分 4 字符 1 token ⇒ 每 token 至多 4 个字符；取一半步长留余量
    step = max(16, max_tokens // 2)
    pieces = [text[i:i + step] for i in range(0, len(text), step)]
    return [p for p in pieces if p.strip()]


def _pack_segments(segments: Iterable[str], *, doc_id: str, max_tokens: int,
                   strategy: str, title: str = '',
                   unit_names: list[str] | None = None) -> list[Chunk]:
    """把片段装箱成 <=max_tokens 的 Chunk。装箱时保留顺序，不做重叠切分。"""
    names = unit_names or []
    chunks: list[Chunk] = []
    buf: list[str] = []
    buf_tokens = 0
    buf_name = ''

    def _flush() -> None:
        nonlocal buf, buf_tokens, buf_name
        if not buf:
            return
        text = '\n'.join(buf).strip()
        if text:
            chunks.append(_new_chunk(
                doc_id, len(chunks), text, strategy, buf_name,
                fields=_fields_in(text), oversized=estimate_tokens(text) > max_tokens,
            ))
        buf, buf_tokens, buf_name = [], 0, ''

    for i, seg in enumerate(segments):
        seg = seg.strip()
        if not seg:
            continue
        n = estimate_tokens(seg)
        if n > max_tokens:
            _flush()
            # 单段就超限：交给硬切，逐片单独成 chunk（不与邻居合并）
            for piece in _hard_split(seg, max_tokens):
                chunks.append(_new_chunk(
                    doc_id, len(chunks), piece, strategy, buf_name,
                    fields=_fields_in(piece), oversized=estimate_tokens(piece) > max_tokens,
                ))
            continue
        if buf and buf_tokens + n > max_tokens:
            _flush()
        if not buf and i < len(names):
            buf_name = names[i]
        buf.append(seg)
        buf_tokens += n
    _flush()
    return chunks


def _new_chunk(doc_id: str, ordinal: int, text: str, strategy: str,
               section: str = '', fields: tuple[str, ...] = (),
               oversized: bool = False) -> Chunk:
    return Chunk(
        chunk_id=_make_chunk_id(doc_id, ordinal, text),
        text=text, token_count=estimate_tokens(text),
        split_strategy=strategy, ordinal=ordinal, doc_id=doc_id,
        section=section, fields=fields, oversized=oversized,
    )


def _fields_in(text: str) -> tuple[str, ...]:
    """片段文本里出现的字段（归一化名），有序去重。"""
    found: list[str] = []
    for ln in text.splitlines():
        m = _KV_RE.match(ln)
        if not m:
            continue
        key = m.group(1).strip().lower()
        norm = FIELD_WORDS.get(key)
        if norm and norm not in found:
            found.append(norm)
    return tuple(found)


# ==========================================================================
# 结构感知切分（配置参数手册）
# ==========================================================================
def _norm_field_key(key: str) -> str | None:
    k = key.strip().lower().rstrip('：:').strip()
    return FIELD_WORDS.get(k)


def _is_param_name(key: str) -> bool:
    """判断 `键：值` 里的键是不是"参数名"（是则开新单元），而不是字段名。"""
    k = key.strip()
    if _norm_field_key(k) is not None:
        return False      # 字段行 → 归入当前单元
    return bool(_IDENT_RE.match(k)) or (1 <= len(k) <= 30 and ' ' not in k.strip())


def _units_from_table(lines: list[str], start: int) -> tuple[list[_Unit], int]:
    """解析 Markdown 参数表格：每个数据行作为一个参数单元。

    行是手册里参数的最小完整单位 —— 一行里通常就含"参数名|默认值|取值范围|说明"，
    按行切天然满足论文的字段同单元要求。

    **表头识别**：只有当第 2 行是 `|---|---|` 分隔行时，才把第 1 行当表头。
    否则第 1 行是**数据**（手册里没有表头分隔行的管道表很常见）。
    把数据行误当表头会静默丢掉第一个参数 —— 入库少一条、检索永远查不到、
    而且不留任何痕迹，是最难发现的一类数据损坏，故这里显式区分并留痕。
    """
    from monitor import degrade

    def cells_of(line: str) -> list[str]:
        return [c.strip() for c in line.strip().strip('|').split('|')]

    def is_sep(cells: list[str]) -> bool:
        return bool(cells) and all(c and set(c) <= set('-: ') for c in cells)

    units: list[_Unit] = []
    header: list[str] = []
    # 表头 = 第 1 行，且第 2 行确为分隔行
    has_header = (start + 1 < len(lines)
                  and lines[start].strip().startswith('|')
                  and is_sep(cells_of(lines[start + 1])))

    i = start
    if not has_header and lines[start].strip().startswith('|'):
        degrade.note('oam.kb.chunk',
                     f'第 {start} 行的管道表没有表头分隔行，'
                     f'已按数据行处理（否则会静默丢掉第一个参数）',
                     None)
    while i < len(lines) and lines[i].strip().startswith('|'):
        cells = cells_of(lines[i])
        low = [c.lower() for c in cells]
        if i == start and has_header:
            header = low          # 表头本身不作为参数单元
        elif not is_sep(cells):
            name = next((c for c in cells if c), '')
            u = _Unit(name=name or f'表格第{i}行', lines=[lines[i]])
            for hc, c in zip(header, cells):
                norm = FIELD_WORDS.get(hc.split('（')[0].split('(')[0].strip())
                if norm and c and norm not in u.fields:
                    u.fields.append(norm)
            units.append(u)
        i += 1
    return units, i


def _units_from_lines(lines: list[str]) -> list[_Unit]:
    """按参数条目的逻辑边界归组（Markdown 标题 / 定义列表 / 加粗参数名三种版式）。"""
    units: list[_Unit] = []
    cur: _Unit | None = None
    blanks = 0

    def close() -> None:
        nonlocal cur
        if cur is not None and cur.lines:
            units.append(cur)
        cur = None

    i = 0
    while i < len(lines):
        raw = lines[i]
        line = raw.strip()

        if not line:
            blanks += 1
            if cur is not None and blanks >= 2:
                close()      # 连续空行 = 单元边界
            i += 1
            continue
        blanks = 0

        # ① 表格：交给表格解析器（会一次吃掉整个表）
        if line.startswith('|'):
            close()
            t_units, i = _units_from_table(lines, i)
            units.extend(t_units)
            continue

        # ② Markdown 小节标题：本身是一个单元的起点（并记住所属小节）
        m = _HEADING_RE.match(line)
        if m:
            close()
            cur = _Unit(name=m.group(2).strip(), lines=[line])
            i += 1
            continue

        # ③ 加粗参数名：`**SGA_TARGET**：...`
        m = _BOLD_RE.match(line)
        if m and m.group(1).strip():
            close()
            cur = _Unit(name=m.group(1).strip(), lines=[line])
            i += 1
            continue

        # ④ `键：值` 行 —— 键是字段名则并入当前单元，键是参数名则开新单元
        m = _KV_RE.match(line)
        if m:
            key, value = m.group(1).strip(), m.group(2).strip()
            norm = _norm_field_key(key)
            if norm is not None:
                if cur is None:
                    cur = _Unit(name=f'未命名单元@{i + 1}')
                if norm not in cur.fields:
                    cur.fields.append(norm)
                cur.lines.append(line)
                i += 1
                continue
            if _is_param_name(key):
                close()
                cur = _Unit(name=key, lines=[line])
                if value:
                    cur.lines.append(value)   # 首行取值并入描述，避免孤立
                i += 1
                continue

        # ⑤ 其余按正文续行处理
        if cur is None:
            cur = _Unit(name=f'前言@{i + 1}')
        cur.lines.append(line)
        i += 1

    close()
    return units


def structure_aware_split(doc: Any, max_tokens: int = MAX_CHUNK_TOKENS) -> list[Chunk]:
    """结构感知切分：**一个参数的描述/默认值/取值范围留在同一逻辑单元**。

    单元本身 >max_tokens 时不切，标 `oversized=True` 并留痕 —— 见模块 docstring。
    """
    doc_id = _doc_id(doc)
    title = str(_doc_field(doc, 'title', '') or '')
    body = str(_doc_field(doc, 'body', '') or '')
    lines = body.splitlines()
    units = _units_from_lines(lines)
    if not units:
        # 空文档/纯空白：退化成单片段，保证下游拿到的片段数与文本一致
        return [_new_chunk(doc_id, 0, f'{title}\n\n{body}'.strip(),
                           STRATEGY_STRUCTURE, section=title)]

    chunks: list[Chunk] = []
    for u in units:
        text = u.text
        if not text:
            continue
        tokens = estimate_tokens(text)
        if tokens > max_tokens:
            # 字段完整性优先：整单元保留，标记超限并留痕（不静默截断）
            from monitor import degrade  # 局部导入：与项目惯例一致，避免包级循环依赖
            degrade.note(
                'oam.kb.chunk',
                f'参数单元 {u.name!r} 完整保留但超上限：{tokens} > {max_tokens} token；'
                f'为保"描述/默认值/取值范围同单元"未切分，需人工拆分该条目',
            )
        chunks.append(_new_chunk(
            doc_id, len(chunks), text, STRATEGY_STRUCTURE,
            section=u.name, fields=tuple(u.fields), oversized=tokens > max_tokens,
        ))
    return chunks


# ==========================================================================
# 对外入口
# ==========================================================================
# 论文的口径：叙述性走递归切分，结构化走结构感知切分。
# `basic_fact` 是说明性正文，两者皆可；默认按段落递归切（粒度更均匀）。
def chunk(doc: Any, max_tokens: int = MAX_CHUNK_TOKENS) -> list[Chunk]:
    """自适应切分入口。按文档类目选择切分策略。"""
    category = str(_doc_field(doc, 'category', '') or '').strip().lower()
    if category == 'config_manual':
        return structure_aware_split(doc, max_tokens=max_tokens)
    if category in ('fault_solution', 'best_practice'):
        return recursive_split(doc, max_tokens=max_tokens)
    if category == 'basic_fact':
        return recursive_split(doc, max_tokens=max_tokens)
    # 未标注类目：无法判断是结构化还是叙述性内容时用递归切分，
    # 递归切分最保守（至少不会破坏段落），且不会伪造结构语义
    return recursive_split(doc, max_tokens=max_tokens)


__all__ = [
    'MAX_CHUNK_TOKENS', 'STRATEGY_RECURSIVE', 'STRATEGY_STRUCTURE',
    'FIELD_WORDS', 'REQUIRED_COHESION_FIELDS',
    'Chunk', 'chunk', 'recursive_split', 'structure_aware_split', 'estimate_tokens',
]
