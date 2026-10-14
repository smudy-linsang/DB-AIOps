# -*- coding: utf-8 -*-
"""知识库三步构建 · 第三步：**因果与事实抽取**（论文 §1.3① 第 3 项）。

论文的产物是"循环可直接使用的记录"：

    归一化症状（错误码 / KPI 异常 / 退化模式）
        → 映射到**与根因标签空间一致**的候选成因
        → 绑定到**动作空间**中的可执行动作（诊断探查 + 修复两类）

论文 §1.3① 的示例记录（照搬到本项目的形状）：

    症状 = 查询排队时间过高
    候选成因 = WLM 配置不当导致槽位限制 / 锁等待 / 长事务
    动作 = 调工具检查 WLM 配置、锁等待、Top SQL

## 两条不能省的闸门

1. **成因必须是封闭标签**（`monitor/oam/labels.py`）。因为论文 §3.3 的根因匹配是
   "轻度归一化后精确字符串相等"，**不是语义相似度** —— 成因写成自由文本，
   验证器就永远判不中，评测会退化成自证。这里逐条 `assert_label` 校验，
   非法直接 `ValueError`，不静默丢标签。
2. **动作必须是真实存在的工具名**（`monitor/oam/tool_catalog.py`）。绑定前
   `get_tool(name, db_type)` 校验；工具不存在或该方言没有该工具，直接报错并
   列出该方言可用的工具名 —— 论文 §1.5 要求工具访问"只读、限速、白名单"，
   动作空间本身就是白名单，往里塞不存在的名字等于绕过白名单。

## 方言无法判定时怎么办（REV-10 的直接教训）

`db_types` 必填，且检索时按方言过滤 —— 否则会把 Oracle 的方案推给 MySQL，
这正是本项目已经发生过的 REV-10 审计缺陷。判定不出方言时本实现**不猜**：

- 记录的 `db_types` 留空、`needs_review=True`、`review_reason` 写明原因；
- `degrade.note('oam.kb.extract', ...)` 留痕，等人工复核后补方言再入库；
- 空 `db_types` 的记录在 `index.py` 里**永远进不了任何方言的检索上下文**
  （`KbRecord.covers()` 返回 False），是"失败关闭"而不是"失败开放"。

## 已知限制

1. 论文的抽取是**离线用 Qwen3-32B 做 LLM 抽取**（§6.1.1）。P0 不引入在线 LLM
   依赖，这里用"结构化字段 + 章节正则"实现确定性版本：文档若自带
   `symptom/causes/probe_actions/fix_actions` 字段（迁移 `RULES_V2` 时会带），
   直接采信；否则从正文小节抽取。接口刻意保持"入参 doc、返回记录列表"，
   后续接 LLM 只需替换内部实现，不动调用方。
2. 动作抽取依赖工具目录。工具目录缺失时**明确报错**而不是跳过校验 ——
   静默跳过等于把不存在的动作名写进 KB。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from monitor.oam import labels

# 方言判定不了时的留痕 scope（与 degrade.note 的用法一致）
_DEGRADE_SCOPE = 'oam.kb.extract'

# --------------------------------------------------------------------------
# 方言指纹：只收辨识度高的标志物。像"数据库""性能""SQL"这类词 6 个方言通用，
# 收进指纹表只会制造假阳性，所以宁可少收（宁可 needs_review，也不误判方言）。
# --------------------------------------------------------------------------
_DIALECT_MARKERS: dict[str, tuple[str, ...]] = {
    'oracle': ('ora-', 'oracle', 'rman', 'spfile', 'pfile', 'sqlplus',
               'awr', 'ash', 'tablespace', 'data guard', 'dataguard',
               'rac', 'pga', 'undo_tablespace', 'db_link', 'tnsnames'),
    'mysql': ('mysql', 'innodb', 'myisam', 'mariadb', 'slow_query_log',
              'show engine innodb', 'pt-online-schema-change'),
    'pgsql': ('postgresql', 'postgres', ' pg_', 'pg_catalog', 'vacuum',
              'autovacuum', 'shared_buffers', 'explain (analyze', 'wal',
              'pg_stat_', 'work_mem'),
    'dm': ('达梦', 'dm8', 'dm数据库', 'dm.ini', 'dmserver'),
    'gbase': ('gbase', 'gbase8a', 'gbase8s', 'xa 节点', 'gbase_t'),
    'tdsql': ('tdsql', 'shard', '分片', 'proxy', 'sqlnode', 'sqlnode'),
}

# 正文小节标题 → 记录字段。抽取靠小节而非靠"猜"。
_SECTION_SYMPTOM = ('症状', '现象', '问题', '故障现象', 'symptom', 'trigger')
_SECTION_CAUSES = ('原因', '成因', '根因', '可能原因', 'cause', 'root cause')
_SECTION_PROBE = ('排查', '诊断', '检查', '定位', '探查', 'troubleshoot', 'diagnos')
_SECTION_FIX = ('处理', '解决', '修复', '整改', '措施', 'fix', 'remediation', 'action')

# 列表项/顿号串里的动作候选（探查与修复共用）
_ITEM_SPLIT_RE = re.compile(r'[；;、,，]|(?:\d+[.、)])|(?:^|\s)[-•*]\s+')
# 小节标题只有两种写法：`## 排查步骤`（Markdown）与 `排查步骤：`（整行只有标签）。
# 刻意不写"宽松到整行都能当标题"的形式 —— 那会把 `1. 调用 db.top_sql` 也当成标题，
# 结果正文被当成标题消费掉、小节内容整段丢失，动作一个都抽不出来。
_HEADING_RE = re.compile(r'^\s*(?:#{1,6}\s*|\*\*)\s*([^#*\n]{1,40}?)\s*(?:\*\*)?\s*$')
_LABEL_LINE_RE = re.compile(r'^\s*([^:：\n]{1,24})\s*[:：]\s*$')
# 行内小节：`根因：长事务占用 undo`。中文手册里这比 Markdown 标题更常见，
# 不认它就等于整段根因被丢掉（候选成因空了，记录照样入库 —— 静默失真）。
_INLINE_SECTION_RE = re.compile(r'^\s*([^:：\n]{1,12})\s*[:：]\s*(.+)$')

# 动作名不从正文用正则猜，而是拿 `tool_catalog` 的工具名反查（见 `_candidate_actions`）：
# 工具名带 `db.` / `fix.` 前缀，正则只能切出后半截，查目录必然落空。
# 修复动词：命中即判为 fix 动作，否则为 probe 动作（诊断类工具多为只读探查）
_FIX_VERBS = ('alter', 'set', 'update', 'restart', 'rebuild', 'grant', 'revoke',
              'kill', 'drop', 'create', 'vacuum', 'analyze', 'reindex', 'commit',
              'rollback', 'extend', 'add', 'modify', 'apply', 'fix', 'remediat')


@dataclass
class KbRecord:
    """论文的核心数据结构：把领域知识组织成循环可直接使用的形式。

    字段与设计文档 §4.3 的代码块一一对应；`db_versions` / `needs_review` /
    `record_id` / `text` 是本项目为 §7 P3「KB 新鲜度」与「来源可溯」补的，
    都有默认值，不破坏论文形态。
    """

    symptom: str                       # 归一化症状：错误码 / KPI 异常 / 退化模式
    db_types: list[str] = field(default_factory=list)   # 适用方言（必填语义，见 covers）
    candidate_causes: list[str] = field(default_factory=list)  # 须与根因标签空间一致
    probe_actions: list[str] = field(default_factory=list)     # 诊断探查动作（工具名）
    fix_actions: list[str] = field(default_factory=list)       # 修复动作（工具名）
    source_doc_id: str = ''            # 可溯源（论文教训：证据引用至关重要）
    version: str = ''
    # ---- 本项目增量 ----
    record_id: str = ''                # 稳定主键；空则由 record_key() 派生
    db_versions: dict = field(default_factory=dict)  # {db_type: [min, max]}
    category: str = ''                 # 类目，未命中周报按它聚合
    text: str = ''                     # 检索用文本（症状+成因+源片段）
    source_chunk_ids: list[str] = field(default_factory=list)
    needs_review: bool = False         # 方言/成因判定不可靠，待人工复核
    review_reason: str = ''

    @property
    def key(self) -> str:
        """稳定记录 ID：源文档 + 症状 + 方言集合，同输入必同输出。

        `usedforsecurity=False` 是显式声明这是**内容指纹**而非安全哈希：
        用途是让记录 ID 可复现、可回溯到同一段输入，不承担抗碰撞职责。
        论文 §1.6⑤ 要求咨询类判定按记录 ID 集合精确相等，ID 抖动会让这种
        判定失去意义 —— 但那要求的是"稳定"，不是"安全"。
        """
        payload = '|'.join([self.source_doc_id, self.symptom,
                            ','.join(sorted(self.db_types))])
        digest = hashlib.sha1(payload.encode('utf-8'),
                               usedforsecurity=False).hexdigest()[:12]
        return f'kb-{digest}'

    @property
    def record_id_or_key(self) -> str:
        return self.record_id or self.key

    def covers(self, db_type: str, db_version: str = '') -> bool:
        """该记录能否进入目标方言/版本的检索上下文。

        返回 False 的四种情形（任一命中即失败关闭，绝不"看起来能用"）：
        1. 待人工复核（方言没判定出来）；
        2. 方言集合为空或不含目标方言 —— REV-10 的闸门；
        3. 声明了版本区间但实例真实版本不落在区间内 —— 论文点名的
           "version mismatch" 错误来源。
        """
        if self.needs_review:
            return False
        if not self.db_types or db_type not in self.db_types:
            return False
        rng = (self.db_versions or {}).get(db_type)
        if rng and not version_in_range(db_version, rng):
            return False
        return True

    def to_dict(self) -> dict:
        return {
            'record_id': self.record_id_or_key, 'symptom': self.symptom,
            'db_types': list(self.db_types),
            'candidate_causes': list(self.candidate_causes),
            'probe_actions': list(self.probe_actions),
            'fix_actions': list(self.fix_actions),
            'source_doc_id': self.source_doc_id, 'version': self.version,
            'db_versions': dict(self.db_versions), 'category': self.category,
            'needs_review': self.needs_review, 'review_reason': self.review_reason,
        }


# ==========================================================================
# 版本区间（§7 P3「版本绑定」）
# ==========================================================================
_NUM_RE = re.compile(r'\d+')


def parse_version(version: str) -> tuple[int, ...]:
    """版本号 → 可比较的数字元组。`19c` → (19,)，`11.2.0.4` → (11,2,0,4)。

    只取数字段比较：`8.0.32` 与 `8.0.32-log` 应视为同版本。
    无数字时返回空元组，调用方按"不可判定"处理。
    """
    return tuple(int(g) for g in _NUM_RE.findall(str(version or '')))


def _cmp_version(a: str, b: str) -> int:
    """按元组逐段比较，长度不等时短的一方补 0（`19` 与 `19.1.0` 同序）。"""
    va, vb = parse_version(a), parse_version(b)
    n = max(len(va), len(vb))
    va += (0,) * (n - len(va))
    vb += (0,) * (n - len(vb))
    return (va > vb) - (va < vb)


def version_in_range(version: str, rng: Any) -> bool:
    """真实版本是否落在 `[min, max]` 内。

    - `version` 为空 → 判定不了版本，**不拦**（拦了会把全部条目清空，
      变成"永远检索未命中"，比不拦更糟）；调用方应在报告里标注版本未知。
    - `rng` 端点为空串/None → 该侧无界。
    - `rng` 长度 != 2 → 视为数据错误，直接 False（失败关闭）。
    """
    if not rng:
        return True
    if not isinstance(rng, (list, tuple)) or len(rng) != 2:
        return False
    lo, hi = str(rng[0] or ''), str(rng[1] or '')
    if not version:
        return True
    if lo and _cmp_version(version, lo) < 0:
        return False
    if hi and _cmp_version(version, hi) > 0:
        return False
    return True


def validate_db_versions(db_versions: Any) -> dict:
    """校验并归一化 `db_versions`，端点顺序颠倒时交换并留痕。"""
    from monitor import degrade

    if not db_versions:
        return {}
    if not isinstance(db_versions, Mapping):
        raise ValueError(f'db_versions 必须是 dict，实际是 {type(db_versions).__name__}')
    out: dict[str, list[str]] = {}
    for db_type, rng in db_versions.items():
        if db_type not in labels.ALL_DIALECTS:
            raise ValueError(
                f'db_versions 的键 {db_type!r} 不是已知方言；'
                f'合法方言={labels.ALL_DIALECTS}'
            )
        if not rng:
            continue
        if not isinstance(rng, (list, tuple)) or len(rng) != 2:
            raise ValueError(f'db_versions[{db_type!r}] 必须是 [min, max]，实际是 {rng!r}')
        lo, hi = str(rng[0] or ''), str(rng[1] or '')
        if lo and hi and _cmp_version(lo, hi) > 0:
            degrade.note(_DEGRADE_SCOPE, f'db_versions[{db_type}] 端点颠倒 {lo}>{hi}，已交换')
            lo, hi = hi, lo
        out[db_type] = [lo, hi]
    return out


# ==========================================================================
# 工具目录（依赖契约：monitor/oam/tool_catalog.py）
# ==========================================================================
def _catalog():
    """惰性取工具目录。

    局部导入：工具目录与本模块由不同人并行维护，模块级导入会把"目录尚未
    落盘"变成 `import monitor.oam.kb` 直接崩，掩盖其他模块的问题。
    目录确实不可用时给出明确错误，不静默跳过工具校验。
    """
    from monitor.oam import tool_catalog
    return tool_catalog


def resolve_action(name: str, db_type: str) -> str:
    """校验一个动作名在该方言下真实存在，返回归一化工具名。

    工具不存在/该方言不可用 → `ValueError`，并列出该方言可用的工具名，
    让修数据的人一眼看出该填什么（论文 §1.5：动作空间即白名单）。
    """
    catalog = _catalog()
    spec = catalog.get_tool(str(name).strip(), db_type)
    if spec is None:
        raise ValueError(
            f'动作 {name!r} 不是方言 {db_type} 下真实存在的工具；'
            f'该方言可用工具（部分）={_available_tools(db_type)[:20]}'
        )
    return spec.name


def _validate_actions(actions: Iterable[str], db_type: str, kind: str) -> list[str]:
    """逐条绑定工具名。绑定不上直接报错 —— 动作空间即白名单（论文 §1.5）。"""
    out: list[str] = []
    for a in actions:
        name = str(a or '').strip()
        if not name:
            continue
        catalog = _catalog()
        spec = catalog.get_tool(name, db_type)
        if spec is None:
            available = _available_tools(db_type)[:20]
            raise ValueError(
                f'{kind} 动作 {name!r} 不是方言 {db_type} 下真实存在的工具；'
                f'该方言可用工具（部分）={available}'
            )
        if spec.name not in out:
            out.append(spec.name)
    return out


# ==========================================================================
# 方言 / 成因 / 动作 的抽取与校验
# ==========================================================================
def detect_db_types(*texts: str) -> tuple[list[str], bool]:
    """从文本判定适用方言。返回 `(方言列表, 是否可信)`。

    可信=False 表示一个都没命中（needs_review），**不返回"全部方言"** ——
    全部方言等于没有方言过滤，正是 REV-10 的成因。
    """
    blob = ' '.join(str(t or '') for t in texts).lower()
    hits = [d for d in labels.ALL_DIALECTS
            if any(marker in blob for marker in _DIALECT_MARKERS[d])]
    hits.sort(key=labels.ALL_DIALECTS.index)
    return hits, bool(hits)


def normalize_symptom(text: str) -> str:
    """症状归一化：压空白、去编号前缀，保留原始措辞（含错误码）。

    不做同义词映射 —— 症状文本要与 `OamCase.symptom` 的口径对齐，
    由上层保证同源；这里只做与论文"轻度归一化"同量级的处理。
    """
    s = re.sub(r'\s+', ' ', str(text or '')).strip()
    s = re.sub(r'^[\-•*\d]+[.、)]?\s*', '', s)
    return s[:255]


def _norm_cause_key(text: str) -> str:
    """成因文本的归一化键：去空白与标点、转小写。

    与论文 §3.3 验证器"轻度归一化后精确字符串相等"同一量级 ——
    **不做模糊匹配**。根因映射错一次比缺一次更糟：错的标签会让验证器
    拿错误答案去判对错，且毫无痕迹。
    """
    return re.sub(r'[\s\-_/()（）\[\]:：、，。,.]+', '', str(text or '')).strip().lower()


def _label_name_index(db_types: list[str]) -> dict[str, str]:
    """归一化的标签中文名 → label_id（只收录适用方言的标签）。

    支持手写文档直接写"锁等待激增"这类**标签名**入库 —— 是精确查表，
    不是猜。查不到就报错，绝不近似匹配。
    """
    out: dict[str, str] = {}
    pool = (labels.labels_for(db_types[0]) if db_types
            else tuple(labels.LABELS.values()))
    for lb in pool:
        out.setdefault(_norm_cause_key(lb.name), lb.label_id)
    return out


def _validate_causes(causes: Iterable[str], db_types: list[str]) -> list[str]:
    """逐条用 `labels.assert_label` 校验成因；非法直接报错。

    接受两种写法（都落到 label_id 这个封闭枚举上）：
    - 直接给 label_id（如 `UNDO_TS_TIGHT`）—— `RULES_V2` 迁移走这条；
    - 给标签中文名（如 `Undo 段紧张`）—— 归一化后**精确查表**命中。

    方言未定时（db_types 为空）只校验"标签存在"，是否适用于某方言等人工
    复核补上方言后再校验 —— 但记录已带 needs_review，进不了检索上下文。
    """
    name_index = _label_name_index(db_types)
    # 全局名索引：用于区分"标签根本不存在"与"标签存在但不属于该方言" ——
    # 后者必须报"不适用于方言 mysql"这种能直接指向 REV-10 的错误。
    global_index = _label_name_index([])
    out: list[str] = []
    for c in causes:
        raw = str(c or '').strip()
        if not raw:
            continue
        cid = raw.upper()
        if cid not in labels.LABELS:
            mapped = name_index.get(_norm_cause_key(raw))
            if mapped is None:
                elsewhere = global_index.get(_norm_cause_key(raw))
                if elsewhere:
                    # 命中全局但不在本方言 → 交给 assert_label 报"不适用于方言"
                    cid = elsewhere
                else:
                    raise ValueError(
                        f'候选成因 {c!r} 不是封闭根因标签空间里的 label_id，也不是标签中文名；'
                        f'请改写成 label_id 或 labels.py 里的标签名（当前方言可用 '
                        f'{len(name_index)} 个标签，见 monitor/oam/labels.py）'
                    )
            else:
                cid = mapped
        if db_types:
            for db_type in db_types:
                cid = labels.assert_label(cid, db_type)
        elif cid not in labels.LABELS:
            raise ValueError(
                f'候选成因 {c!r} 不在封闭根因标签空间内；'
                f'共 {len(labels.LABELS)} 个标签，见 monitor/oam/labels.py'
            )
        if cid not in out:
            out.append(cid)
    return out


def _section_kind(head: str, kind_of: Mapping[str, str]) -> str:
    head = head.strip().strip('*# ').lower()
    for key, kind in kind_of.items():
        if key in head:
            return kind
    return ''


def _extract_sections(body: str) -> dict[str, list[str]]:
    """按小节标题切段，归到 症状/成因/探查/修复 四类。

    识别两类版式：`## 排查步骤`（Markdown 标题）与整行只有标签的
    `排查步骤：`（定义列表）。**只有这两种形态算标题**，其余行一律当正文 ——
    判定放宽的后果是正文被当成标题消费、小节内容整段丢失（抽不出动作）。
    没有小节时返回空 dict，由调用方决定兜底策略。
    """
    out: dict[str, list[str]] = {k: [] for k in ('symptom', 'causes', 'probe', 'fix')}
    kind_of = {
        **{k: 'symptom' for k in _SECTION_SYMPTOM},
        **{k: 'causes' for k in _SECTION_CAUSES},
        **{k: 'probe' for k in _SECTION_PROBE},
        **{k: 'fix' for k in _SECTION_FIX},
    }
    current = ''
    seen_section = False
    for line in str(body or '').splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        m = _HEADING_RE.match(stripped) or _LABEL_LINE_RE.match(stripped)
        if m:
            current = _section_kind(m.group(1), kind_of)
            seen_section = seen_section or bool(current)
            continue          # 非目标小节的内容丢弃，避免串味
        inline = _INLINE_SECTION_RE.match(stripped)
        if inline:
            kind = _section_kind(inline.group(1), kind_of)
            if kind:
                current = kind
                seen_section = True
                # 冒号后的内容属于本小节（`根因：长事务` 里的"长事务"就是成因）
                out[kind].append(inline.group(2).strip())
                continue
            # 不是小节标题的 `键：值` 行（如参数说明）：归入当前小节
            if current:
                out[current].append(stripped)
            continue
        if current:
            out[current].append(stripped)
        elif not seen_section:
            # 第一个小节标题之前的内容是症状描述（手册惯例：先说现象再说原因）
            out['symptom'].append(stripped)
    return out


def _available_tools(db_type: str) -> list[str]:
    """该方言下可绑定的工具名 = 只读探查工具 + 变更类工具。

    注意 `tools_for()` **刻意排除了 mutating 的工具**（变更类不进策略的动作空间，
    见 `tool_catalog`）。但**修复动作本来就该指向变更类工具** ——
    论文 §1.5 的要求是"变更类先打风险标签再走人工审批"，不是"变更类不存在"。
    所以绑定与报错提示都要把两类合起来。
    """
    catalog = _catalog()
    names = [s.name for s in catalog.tools_for(db_type)]
    names += [s.name for s in catalog.mutating_tools(db_type)]
    return sorted(set(names))


def _mentions(low_text: str, name: str) -> bool:
    """工具名是否作为**整体**出现在文本里。

    要求两侧不是标识符字符，避免 `sql` 命中 `db.sql_plan` 的后半截。
    """
    idx = low_text.find(name)
    while idx >= 0:
        before = low_text[idx - 1] if idx else ' '
        tail = idx + len(name)
        after = low_text[tail] if tail < len(low_text) else ' '
        if not (before.isalnum() or before in '_.') and not (after.isalnum() or after in '_.'):
            return True
        idx = low_text.find(name, idx + 1)
    return False


def _candidate_actions(text: str, db_type: str) -> list[str]:
    """从正文里认出**工具目录中真实存在**的工具名。

    刻意用目录反查，而不是用正则猜标识符形态：工具名带前缀
    （`db.lock_chain` / `fix.alter_parameter`），正则只能切出后半截
    `lock_chain`，拿去查目录必然落空 —— 那会让所有提到工具名的文档
    都抽不出动作。目录反查对命名风格免疫，且天然只认存在的名字。
    """
    low = str(text or '').lower()
    # 长名优先：`meta.guc_config` 与潜在的 `guc_config` 同文本时先认长的
    return [n for n in sorted(_available_tools(db_type), key=len, reverse=True)
            if _mentions(low, n)]


def _split_items(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        for part in _ITEM_SPLIT_RE.split(line):
            if part and part.strip():
                out.append(part.strip())
    return out


def _extract_actions(sections: dict[str, list[str]], db_type: str,
                     doc_id: str) -> tuple[list[str], list[str]]:
    """抽取并绑定（探查动作, 修复动作）。

    绑定规则：只从**工具目录里真实存在的名字**中认领动作，不认领的一律丢弃
    并留痕 —— 但**修复动作例外**：文档写了修复手段却绑不上工具时报错，
    因为悄悄丢掉修复动作会产出一条"只诊断不给修法"的记录，
    那是比报错更坏的污染（且没有痕迹）。
    """
    from monitor import degrade

    catalog = _catalog()
    probe: list[str] = []
    fix: list[str] = []
    dropped = 0

    for kind, bucket in (('probe', probe), ('fix', fix)):
        for line in sections.get(kind, []):
            bound = False
            for cand in _candidate_actions(line, db_type):
                spec = catalog.get_tool(cand, db_type)
                if spec is not None and spec.name not in bucket:
                    bucket.append(spec.name)
                    bound = True
            if not bound:
                if kind == 'fix':
                    raise ValueError(
                        f'文档 {doc_id} 的修复动作 {line!r} 绑不上方言 {db_type} 的任何工具；'
                        f'该方言可用工具（部分）={_available_tools(db_type)[:20]}。'
                        f'修复动作必须写成工具名，不能丢失'
                    )
                dropped += 1
    if dropped:
        degrade.note(_DEGRADE_SCOPE,
                     f'文档 {doc_id} 有 {dropped} 条探查动作未绑上工具名，已丢弃')
    return probe, fix


def _doc_field(doc: Any, name: str, default: Any = '') -> Any:
    if isinstance(doc, Mapping):
        return doc.get(name, default)
    return getattr(doc, name, default)


def _record_text(symptom: str, causes: list[str], chunks: list, budget_tokens: int) -> tuple[str, list[str]]:
    """检索用文本 = 症状 + 成因名 + 源片段摘要（按 token 预算截断，确定性）。

    成因带上中文名（`labels.LABELS[cid].name`）是为了让查询侧的**自然语言症状**
    也能命中 —— 用户报的是"排队高"，不是标签 ID。
    """
    parts = [symptom]
    for cid in causes:
        lb = labels.LABELS.get(cid)
        parts.append(f'{cid} {lb.name}' if lb else cid)
    head = ' '.join(p for p in parts if p)

    from monitor.oam.kb.chunk import estimate_tokens
    body_parts: list[str] = []
    chunk_ids: list[str] = []
    used = estimate_tokens(head)
    for ch in chunks or ():
        if used >= budget_tokens:
            break
        chunk_ids.append(ch.chunk_id)
        t = getattr(ch, 'text', '') or ''
        body_parts.append(t)
        used += estimate_tokens(t)
    return (head + ' ' + ' '.join(body_parts)).strip(), chunk_ids


def extract(doc: Any, chunks: list | None = None) -> list[KbRecord]:
    """从文档（及其片段）抽取可被循环直接使用的 `KbRecord`。

    抽取优先级（确定性）：
      1. 文档自带 `records`（迁移 `RULES_V2` 时的结构化输入）→ 逐条采信；
      2. 文档自带 `symptom` / `causes` / `probe_actions` / `fix_actions` → 单条记录；
      3. 正文小节抽取（`_extract_sections`）→ 单条记录；
      4. 连症状都抽不到 → 返回空列表（并在留痕里说明）。

    论文的 LLM 离线抽取是 P1 的替换点，本函数的入参与返回形状不变。
    """
    from monitor import degrade

    doc_id = str(_doc_field(doc, 'doc_id', '') or _doc_field(doc, 'id', '') or '')
    version = str(_doc_field(doc, 'version', '') or '')
    title = str(_doc_field(doc, 'title', '') or '')
    body = str(_doc_field(doc, 'body', '') or '')
    category = str(_doc_field(doc, 'category', '') or '')

    if not doc_id:
        raise ValueError('抽取必须带 doc_id：来源可溯是论文硬要求，检索结果要回传记录 ID')

    # ① 结构化 records（迁移 RULES_V2 的入口）
    structured = _doc_field(doc, 'records', None)
    if structured:
        return [_build_record(
            entry, doc_id=doc_id, version=version, category=category,
            chunks=chunks, title=title,
        ) for entry in structured]

    # ②/③ 单条记录
    sections = _extract_sections(body)
    symptom_raw = (_doc_field(doc, 'symptom', '')
                   or ' '.join(sections.get('symptom', []))
                   or title)
    symptom = normalize_symptom(symptom_raw)
    if not symptom:
        degrade.note(_DEGRADE_SCOPE, f'文档 {doc_id} 未抽出症状，返回空记录列表')
        return []

    entry = {
        'symptom': symptom,
        'causes': _doc_field(doc, 'causes', None) or _split_items(sections.get('causes', [])),
        'probe_actions': _doc_field(doc, 'probe_actions', None),
        'fix_actions': _doc_field(doc, 'fix_actions', None),
        'db_types': _doc_field(doc, 'db_types', None),
        'db_versions': _doc_field(doc, 'db_versions', None),
        'sections': sections,
    }
    return [_build_record(
        entry, doc_id=doc_id, version=version, category=category,
        chunks=chunks, title=title,
    )]


def _build_record(entry: Mapping, *, doc_id: str, version: str, category: str,
                  chunks: list | None, title: str) -> KbRecord:
    """组装并**全量校验**一条记录（标签、工具、方言、版本区间都过一遍闸门）。"""
    from monitor import degrade

    symptom = normalize_symptom(entry.get('symptom') or '')
    if not symptom:
        raise ValueError(f'文档 {doc_id} 的记录缺少 symptom')

    # ---- 方言 ----
    needs_review, review_reason = False, ''
    db_types_in = entry.get('db_types')
    if db_types_in:
        db_types = [str(d).strip().lower() for d in db_types_in if str(d).strip()]
        unknown = [d for d in db_types if d not in labels.ALL_DIALECTS]
        if unknown:
            raise ValueError(
                f'文档 {doc_id} 的 db_types 含未知方言 {unknown}；'
                f'合法方言={labels.ALL_DIALECTS}'
            )
        db_types = [d for d in labels.ALL_DIALECTS if d in db_types]   # 固定顺序，便于稳定哈希
    else:
        sections = entry.get('sections') or {}
        probe_text = ' '.join(sections.get('probe', []) + sections.get('fix', []))
        db_types, trusted = detect_db_types(title, symptom, probe_text)
        if not trusted:
            needs_review, review_reason = True, (
                '无法从文档判定适用方言（无高辨识度方言标志物）；'
                'db_types 留空且本记录不进入任何方言的检索上下文，待人工复核'
            )
            degrade.note(_DEGRADE_SCOPE,
                         f'文档 {doc_id} 方言判定失败：{review_reason}；'
                         f'按失败关闭处理，不入检索上下文')
        else:
            needs_review, review_reason = False, ''

    # ---- 成因（封闭标签空间，论文的精确相等匹配前提）----
    causes = _validate_causes(entry.get('causes') or (), db_types)

    # ---- 动作（绑定真实工具名）----
    sections = entry.get('sections') or {}
    probe_in = entry.get('probe_actions')
    fix_in = entry.get('fix_actions')
    probe: list[str] = []
    fix: list[str] = []
    if db_types:
        primary = db_types[0]
        if probe_in or fix_in:
            # 文档自带结构化动作字段时以字段为准，不再从正文小节猜
            probe = _validate_actions(probe_in or (), primary, 'probe')
            fix = _validate_actions(fix_in or (), primary, 'fix')
        else:
            probe, fix = _extract_actions(sections, primary, doc_id)
    else:
        # 方言未定 → 无法校验动作归属，先留原文候选并标记待复核
        probe = [str(a).strip() for a in (probe_in or ()) if str(a).strip()]
        fix = [str(a).strip() for a in (fix_in or ()) if str(a).strip()]
        if not needs_review:
            needs_review = True
            review_reason = '方言未定时动作无法绑定工具'
        degrade.note(_DEGRADE_SCOPE,
                     f'文档 {doc_id} 方言未定，动作 {probe + fix} 未做工具绑定校验')

    text, chunk_ids = _record_text(symptom, causes, chunks or [], budget_tokens=2000)
    return KbRecord(
        symptom=symptom, db_types=db_types, candidate_causes=causes,
        probe_actions=probe, fix_actions=fix, source_doc_id=doc_id,
        version=version, db_versions=validate_db_versions(entry.get('db_versions')),
        category=category, text=text, source_chunk_ids=chunk_ids,
        needs_review=needs_review, review_reason=review_reason,
    )


__all__ = [
    'KbRecord', 'extract', 'detect_db_types', 'normalize_symptom',
    'resolve_action', 'validate_db_versions', 'parse_version', 'version_in_range',
]
