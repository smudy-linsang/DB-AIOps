#!/usr/bin/env python3
"""A 黑板收件脚本（DB-AIOps 版）—— DB-003 任务 3：A-AUTORUN 增加 DB-AIOps 黑板监听。

移植自 Xinchuang_HW_QuickSizer 的 tools/a_inbox.py（判定规则、占用 / 领单 / 自检逻辑一致），
只改 DB-AIOps 必需的部分，Xinchuang 侧文件与定时任务一律不动：

  - 单号：DB-001 起（DB-xxx、返工 DB-xxx-Rn）；测试单 DB-TEST-xxx 不受起始单号限制
    （DB-003：全链路验收测试单事后归档，不占正式编号）。
  - 起始单号 MIN_ORDER = 4：DB-001～DB-003 由人工承接，不自动叫醒。
  - 回执（文件名含「回执」或 receipt）不算报告：接单规矩要求先回 ETA 回执，
    若把回执当成交付报告，单会在干活前就被判为 done。
  - 别名表与成员解析内置（来自 Xinchuang member_poller.py 的 WAKE-W-4 版）：
    DB-AIOps 的 member_poller 适配归 GorkMan（DB-003 任务 1），本脚本不依赖它。
  - 推送自检的放行范围按 DB-AIOps 目录（monitor/tests_*、frontend/src/test/ 只许新增）。

调用方：
  - GitHub Actions（.github/workflows/a-autorun.yml）：黑板有推送时 `--dispatch`，
    有活就调用 A 的 DB-AIOps 定时任务 API 拉起新会话；不调用模型，无活几秒结束。
  - A 的自动会话（提示词见 docs/pm/A-AUTORUN-PROMPT.md）：`--next` 取单、`--claim` 领单、
    `--guard` 推送前自检、`--finish` 交单时删占用。

判定规则：
  1. 路由到 A：任务单首部「执行：/执行人：」行出现 A；「无需 A/O 评审」这类否定写法不算。
  2. 未完成：reports/ 下没有 A 对同一单号（含返工轮次）的报告（回执不算）。
  3. 已就绪：单内有「A就绪：<路径>、<路径>」时以这些文件都存在为准
     （「A就绪：立即」立即就绪，「A就绪：取消」不做）；没有时按「顺序：X→A」/「须待 X 交付后」；
     A 与他人以「、」并列且 A 前面的成员没有实施 / 修复类分工时各方独立就绪；
     否则执行行里排在 A 前面的成员（O 评审除外）都已交本单报告即就绪。
  4. 未被占用：docs/pm/claims/<单号>-A.json 不存在或已超时（STALE_HOURS）；
     达到 MAX_ATTEMPTS 后不再执行，改出「执行失败」报告。

只用 Python 3 标准库与 git 命令。

用法：
  python3 tools/a_inbox.py --list | --next | --check | --dispatch
  python3 tools/a_inbox.py --claim DB-004 | --finish DB-004 | --guard | --self-test
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

MIN_ORDER = 4           # DB-001～DB-003 由人工承接（DB-003 立单时自动链尚未接入）
STALE_HOURS = 3         # 占用超过该时长且无报告, 视为上次会话已死
MAX_ATTEMPTS = 2        # 同一单最多自动执行次数, 超出改出「执行失败」报告
DISPATCH_STALE_MIN = 15  # Actions 拉起后该时长内无会话接单, 允许再次拉起
MAX_DISPATCHES = 4      # 连续拉起无人接单的上限, 超出标 stuck 等人工
ME = "A"

ORDERS = "docs/pm/orders"
REPORTS = "docs/pm/reports"
CLAIMS = "docs/pm/claims"

# DB-004 / DB-004-R1 / 测试单 DB-TEST-001（测试单不受 MIN_ORDER 限制）
ORDER_ID_RE = re.compile(r"^(DB-(TEST-)?(\d+)(?:-R\d+)?)(?=[-_.])")
RECEIPT_RE = re.compile(r"回执|receipt", re.I)

# 推送自检: 允许改动的路径前缀; 测试文件只许新增
GUARD_ALLOW = ("docs/pm/reports/", "docs/pm/claims/")
GUARD_ADD_ONLY = ("monitor/tests_", "frontend/src/test/", "frontend/e2e/")

# 各成员交报告时文件名里的标记 (小写比较)
REPORT_TOKENS = {
    "GG": ["gg"],
    "GorkMan": ["gork-team", "gork", "gorkman"],
    "DSMan": ["d-team", "dsman"],
    "MMMan": ["m-team", "mmman"],
    "C": ["c"],
    "O": ["o"],
    "Lingma": ["lingma"],
    "A": ["a"],
}
NOT_PREREQ = {"O"}  # O 是评审, 与 A 并行, 不作为 A 的前置

# 执行行里标明「先干活」的分工词: 带这些词的成员即使与 A 以「、」并列也是 A 的前置
WORK_ROLE_RE = re.compile(r"实施|修复|返工|开发|构建|热修|改造|修改")
SEQ_LINE_RE = re.compile(r"^[\s>*-]*\**顺序\**[:：][ \t]*(.+)$", re.M)
WAIT_FOR_RE = re.compile(r"(?:须|需|要)?待[ \t]*([^，。,；;\n]+?)[ \t]*(?:交付|交报告|提交|完成|交单)(?:后|之后)")
CLAUSE_SPLIT_RE = re.compile(r"[，,；;]")

NEGATION_RE = re.compile(r"(?:无需|不需|不需要|不含|不涉及|免)[^，,；;。]*")


# 别名表与解析：来自 Xinchuang tools/member_poller.py（WAKE-W-4 E 版）
ALIASES = {
    "A": "A", "C": "C", "D team": "DSMan", "D-team": "DSMan", "DSMan": "DSMan",
    "GG": "GG", "Gork": "GorkMan", "GorkMan": "GorkMan", "Lingma": "Lingma",
    "M": "MMMan", "M-team": "MMMan", "MMMan": "MMMan", "D": "DSMan", "O": "O",
    "antigravity": "GG", "chatgpt codex": "O", "claude code": "A", "cline": "C",
    "deepseek": "DSMan", "gork-team": "GorkMan", "智能体M": "MMMan", "智能体D": "DSMan",
    "lingma": "Lingma", "minimax": "MMMan", "AideMan": "AideMan", "muse": "muse",
}
_SINGLE_LETTER = {"A", "C", "M", "D", "O"}


def resolve_members(raw):
    """负责人原始字符串 -> 成员 id 列表（去重）。长别名优先占位；单字母别名须独立成词
    （防误命中 TDSQL 里的 D、DEC-007 里的 C）。"""
    if not raw:
        return []
    claimed, result = [], []
    for alias in sorted(ALIASES, key=len, reverse=True):
        member = ALIASES[alias]
        hit = False
        if alias in _SINGLE_LETTER:
            for m in re.finditer(r"(?<![A-Za-z0-9_\-])" + re.escape(alias) + r"(?![A-Za-z0-9_\-])", raw):
                span = (m.start(), m.end())
                if not any(span[0] < e and s < span[1] for s, e in claimed):
                    claimed.append(span)
                    hit = True
                    break
        else:
            start = 0
            while True:
                i = raw.find(alias, start)
                if i < 0:
                    break
                span = (i, i + len(alias))
                if not any(span[0] < e and s < span[1] for s, e in claimed):
                    claimed.append(span)
                    hit = True
                    break
                start = i + 1
        if hit and member not in result:
            result.append(member)
    return result


def now_utc():
    return datetime.datetime.now(datetime.timezone.utc)


def order_id_of(filename):
    """-> (单号, 序号)。测试单序号为 0，表示不受 MIN_ORDER 限制。"""
    m = ORDER_ID_RE.match(filename)
    if not m:
        return None, None
    return m.group(1), (0 if m.group(2) else int(m.group(3)))


def members_in_order(raw):
    """负责人字符串 -> [(首次出现位置, 成员id)], 按出现顺序。否定片段先剔除。"""
    if not raw:
        return []
    raw = NEGATION_RE.sub(lambda m: " " * len(m.group(0)), raw)
    members = resolve_members(raw)
    out = []
    for mem in members:
        pos = None
        for alias, target in ALIASES.items():
            if target != mem:
                continue
            if len(alias) == 1:
                m = re.search(r"(?<![A-Za-z0-9_\-])" + re.escape(alias) + r"(?![A-Za-z0-9_\-])", raw)
                i = m.start() if m else -1
            else:
                i = raw.find(alias)
            if i >= 0 and (pos is None or i < pos):
                pos = i
        out.append((pos if pos is not None else 10 ** 6, mem))
    out.sort()
    return out


def report_members(report_name, oid):
    """报告文件名 -> 该报告属于哪些成员 (按文件名标记)。单号必须完全一致。"""
    rid, _ = order_id_of(report_name)
    if rid != oid or RECEIPT_RE.search(report_name):
        return set()
    stem = report_name[len(oid):]
    stem = re.sub(r"\.md$", "", stem, flags=re.I)
    segs = [s.lower() for s in re.split(r"[-_.]", stem) if s]
    joined = "-" + "-".join(segs) + "-"
    found = set()
    for mem, toks in REPORT_TOKENS.items():
        for t in toks:
            if "-" + t + "-" in joined:
                found.add(mem)
    return found


def parse_assignee(text):
    """取首部第一条含「执行：/执行人：/负责人：」的行。
    member_poller.parse_assignee 先找「执行：」再找「执行人：」, 正文里引用的「执行：」
    会抢在首部「执行人：」之前被命中 (T-033 实例), 这里按行序取第一条。"""
    head = "\n".join(text.splitlines()[:30])
    head = re.sub(r"```[\s\S]*?```", "", head)
    for line in head.splitlines():
        m = re.search(r"(?:执行人|执行|负责人)[:：][ \t]*(.+)", line)
        if m:
            return m.group(1).strip().rstrip("｜|").strip()
    return None


def parse_ready_line(text):
    """返回 None (无此行) / 'now' / 'cancel' / [路径...]"""
    head = "\n".join(text.splitlines()[:40])
    # 只认独占一行的就绪行 (可带列表符/加粗), 正文里引用的 `A就绪：立即` 不算
    m = re.search(r"^[ \t>*-]*\**A就绪\**[:：][ \t]*(.+)$", head, re.M)
    if not m:
        return None
    v = m.group(1).strip().strip("`").strip()
    if v.startswith("立即"):
        return "now"
    if v.startswith("取消") or v.startswith("无需"):
        return "cancel"
    paths = [p.strip().strip("`").strip() for p in re.split(r"[、,，;；\s]+", v)]
    return [p for p in paths if p]


def explicit_prereq(text):
    """任务单里写明的顺序依赖 -> A 的前置成员列表; 没写返回 None。"""
    head = "\n".join(text.splitlines()[:40])
    head = re.sub(r"```[\s\S]*?```", "", head)
    m = SEQ_LINE_RE.search(head)
    if m:
        seq = members_in_order(m.group(1))
        names = [x for _, x in seq]
        if ME in names:
            a_pos = dict((x, p) for p, x in seq)[ME]
            return [x for p, x in seq if p < a_pos and x != ME]
    found = []
    for m in WAIT_FOR_RE.finditer(head):
        for _, x in members_in_order(m.group(1)):
            if x != ME and x not in found:
                found.append(x)
    return found if found else None


def is_parallel(raw):
    """执行行里 A 与他人以「、」并列, 且 A 前面的并列成员没有实施/修复类分工 -> True。"""
    if not raw:
        return False
    raw = NEGATION_RE.sub(lambda m: " " * len(m.group(0)), raw)
    for clause in CLAUSE_SPLIT_RE.split(raw):
        names = [x for _, x in members_in_order(clause)]
        if ME not in names:
            continue
        if "、" not in clause or len(names) < 2:
            return False
        before = clause[:min(p for p, x in members_in_order(clause) if x == ME)]
        return not WORK_ROLE_RE.search(before)
    return False


def _exists(root, p):
    p = p.lstrip("/")
    return (root / p).exists() or (root / "docs/pm" / p).exists()


def evaluate(root, now=None):
    """扫描黑板, 返回所有路由到 A 的单的判定列表。"""
    root = Path(root)
    now = now or now_utc()
    reports = sorted(os.listdir(root / REPORTS)) if (root / REPORTS).is_dir() else []
    orders_dir = root / ORDERS
    items = []
    for fn in sorted(os.listdir(orders_dir)) if orders_dir.is_dir() else []:
        if not fn.endswith(".md"):
            continue
        oid, num = order_id_of(fn)
        if not oid or (num and num < MIN_ORDER):
            continue
        text = (orders_dir / fn).read_text(encoding="utf-8", errors="replace")
        raw = parse_assignee(text)
        seq = members_in_order(raw)
        names = [m for _, m in seq]
        if ME not in names:
            continue
        item = {"order": oid, "path": f"{ORDERS}/{fn}", "assignees": names}
        done_by = {}
        for r in reports:
            for mem in report_members(r, oid):
                done_by.setdefault(mem, []).append(r)
        if ME in done_by:
            item.update(state="done", reports=done_by[ME])
            items.append(item)
            continue
        ready = parse_ready_line(text)
        if ready == "cancel":
            item.update(state="cancelled")
            items.append(item)
            continue
        if ready is None:
            explicit = explicit_prereq(text)
            if explicit is not None:
                prereq, kind = explicit, "order"
            elif is_parallel(raw):
                prereq, kind = [], "parallel"
            else:
                a_pos = dict((m, p) for p, m in seq)[ME]
                prereq = [m for p, m in seq if p < a_pos and m != ME and m not in NOT_PREREQ]
                kind = "default"
            missing = [m for m in prereq if m not in done_by]
            item["ready_rule"] = kind + ":" + (",".join(prereq) if prereq else "none")
        elif ready == "now":
            missing = []
            item["ready_rule"] = "A就绪：立即"
        else:
            missing = [p for p in ready if not _exists(root, p)]
            item["ready_rule"] = "A就绪：" + "、".join(ready)
        if missing:
            item.update(state="waiting", missing=missing)
            items.append(item)
            continue
        claim = read_claim(root, oid)
        attempts = int(claim.get("attempts", 0)) if claim else 0
        dispatches = int(claim.get("dispatches", 0)) if claim else 0
        item.update(attempts=attempts, dispatches=dispatches)
        if claim and claim.get("status") == "dispatched":
            t = _parse_time(claim.get("dispatched_at"))
            age_m = (now - t).total_seconds() / 60 if t else 10 ** 6
            if age_m < DISPATCH_STALE_MIN:
                item.update(state="dispatched", mode=_mode(attempts), claim_age_min=round(age_m, 1))
                items.append(item)
                continue
            if dispatches >= MAX_DISPATCHES:
                item.update(state="stuck", note=f"已拉起 {dispatches} 次均无会话接单, 需人工检查定时任务/令牌")
                items.append(item)
                continue
        elif claim:
            t = _parse_time(claim.get("started_at"))
            age_h = (now - t).total_seconds() / 3600 if t else 99
            if age_h < STALE_HOURS:
                item.update(state="claimed", claim_age_h=round(age_h, 2))
                items.append(item)
                continue
        item.update(state="ready", mode=_mode(attempts))
        items.append(item)
    return items


def _mode(attempts):
    return "FAIL_REPORT" if attempts >= MAX_ATTEMPTS else "RUN"


def next_item(root, now=None, states=("ready",)):
    for it in evaluate(root, now):
        if it["state"] in states:
            return it
    return None


# ---------------- 占用文件 ----------------

def claim_path(root, oid):
    return Path(root) / CLAIMS / f"{oid}-A.json"


def read_claim(root, oid):
    p = claim_path(root, oid)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {"attempts": MAX_ATTEMPTS, "started_at": None}


def _parse_time(s):
    try:
        return datetime.datetime.fromisoformat(s)
    except Exception:  # noqa: BLE001
        return None


def _git(root, *args, check=True):
    r = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=str(root),
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {r.stderr.strip()}")
    return r


def _push_claim(root, oid, allowed_states, build, message, remote, branch, tries):
    """写占用文件 -> 提交 -> 推送 (git 推送天然原子); 被拒则 rebase 后重新判定。"""
    root = Path(root)
    for _ in range(tries):
        _git(root, "pull", "--rebase", "-q", remote, branch)
        it = next((x for x in evaluate(root) if x["order"] == oid), None)
        if not it or it["state"] not in allowed_states:
            return False, f"{oid} 当前不可领: {it['state'] if it else '不存在或未路由到 A'}"
        data = build(it, read_claim(root, oid) or {})
        p = claim_path(root, oid)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        rel = p.relative_to(root).as_posix()
        _git(root, "add", "--", rel)
        _git(root, "commit", "-q", "-m", message(data), "--", rel)
        r = _git(root, "push", "-q", remote, f"HEAD:{branch}", check=False)
        if r.returncode == 0:
            return True, json.dumps(data, ensure_ascii=False)
        _git(root, "reset", "-q", "--hard", "HEAD~1")
    return False, "推送多次被拒, 放弃本轮"


def claim(root, oid, session="", remote="origin", branch="main", tries=3):
    """A 会话领单: 接手 Actions 的 dispatched 占用, 或直接领 ready 单。返回 (ok, 说明)。"""
    def build(it, old):
        return {
            "order": oid, "member": ME, "status": "running",
            "started_at": now_utc().isoformat(timespec="seconds"),
            "attempts": int(old.get("attempts", 0)) + 1,
            "dispatches": int(old.get("dispatches", 0)),
            "dispatched_at": old.get("dispatched_at", ""),
            "mode": it["mode"],
            "session": session or os.environ.get("CLAUDE_CODE_REMOTE_SESSION_ID", ""),
        }
    return _push_claim(root, oid, ("ready", "dispatched"), build,
                       lambda d: f"chore(pm): A 领单 {oid}（第 {d['attempts']} 次，{d['mode']}）",
                       remote, branch, tries)


def dispatch(root, remote="origin", branch="main", tries=3):
    """Actions 用: 取下一张 ready 单, 先推 dispatched 占用再拉起会话, 防重复拉起。"""
    it = next_item(root)
    if not it:
        return False, ""
    oid = it["order"]

    def build(it2, old):
        return {
            "order": oid, "member": ME, "status": "dispatched",
            "dispatched_at": now_utc().isoformat(timespec="seconds"),
            "attempts": int(old.get("attempts", 0)),
            "dispatches": int(old.get("dispatches", 0)) + 1,
        }
    return _push_claim(root, oid, ("ready",), build,
                       lambda d: f"chore(pm): 拉起 A 处理 {oid}（第 {d['dispatches']} 次拉起）",
                       remote, branch, tries)


def finish(root, oid):
    p = claim_path(root, oid)
    if p.exists():
        _git(root, "rm", "-q", "--", p.relative_to(Path(root)).as_posix())
        return True
    return False


# ---------------- 推送自检 ----------------

def guard_check(changes):
    """changes: [(状态字母, 路径)] -> 违规说明列表 (空表示通过)。"""
    bad = []
    for st, path in changes:
        st = st[0]
        if path.startswith(GUARD_ALLOW):
            continue
        if path.startswith(GUARD_ADD_ONLY):
            if st != "A":
                bad.append(f"{path}: 既有测试只许新增, 不许修改/删除/改名 ({st})")
            continue
        bad.append(f"{path}: 不在 A 允许改动的范围 ({st})")
    return bad


def guard(root, base="origin/main"):
    _git(root, "fetch", "-q", "origin", "main", check=False)
    r = _git(root, "diff", "--name-status", "--no-renames", f"{base}...HEAD")
    changes = []
    for line in r.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            changes.append((parts[0], parts[-1]))
    dirty = _git(root, "status", "--porcelain").stdout.strip()
    bad = guard_check(changes)
    return changes, bad, dirty


# ---------------- 自测 ----------------

def self_test():
    results = []

    def ok(name, cond):
        results.append((name, bool(cond)))

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        (root / ORDERS).mkdir(parents=True)
        (root / REPORTS).mkdir(parents=True)

        def order(fn, head):
            (root / ORDERS / fn).write_text(f"# {fn}\n\n{head}\n\n正文\n", encoding="utf-8")

        def report(fn):
            (root / REPORTS / fn).write_text("结论：通过\n", encoding="utf-8")

        order("DB-003-旧单.md", "- 执行：GG 实施，A 独立回归")
        order("DB-042-中文文件名回归.md", "- 执行：GG 实施，A 独立回归，O 正式复验")
        order("DB-043-只有A.md", "> 下达人：muse｜执行：A")
        order("DB-044-无需A.md", "- 执行：GG 实施（构建），**无需 A/O 评审**")
        order("DB-045-就绪行.md", "- 执行：GG 实施，A 独立回归\n- A就绪：reports/DB-045-report-GG.md、reports/DB-045-review-O.md")
        order("DB-046-团队前置.md", "- 执行：gork-team（GorkMan）+ GG 实施，A-team（A）独立回归")
        order("DB-046-R1-返工.md", "- 执行：GG 返工，A 复验")
        order("DB-047-O在前.md", "- 执行：gork-team（GorkMan 修复）、O（评审）、A（回归/E2E）")
        order("DB-048-取消.md", "- 执行：A\n- A就绪：取消")
        order("DB-049-立即.md", "- 执行：GG 实施，A 独立回归\nA就绪：立即")
        order("DB-051-引用.md", "- 下达人：muse｜执行人：muse（亲修）\n- 背景：行尾\"执行：\"把下一行吞成负责人（GG 与 A 双双漏叫醒）")
        order("DB-055-并行督办.md", "- 执行：DSMan、GorkMan、A、O、C\n- 类型：合规学习督办")
        order("DB-056-并行显式顺序.md", "- 执行：DSMan、GorkMan、A、O、C\n- 顺序：GorkMan→A")
        order("DB-057-并行须待.md", "- 执行：DSMan、GorkMan、A\n- 说明：A 须待 DSMan 交付后再回归")
        order("DB-058-正文引用就绪行.md", "- 执行：GG 实施，A 独立回归\n- 要求：任务单中出现 `A就绪：立即` 时强制立即")

        order("DB-TEST-001-全链路验收.md", "- 执行：GorkMan、A、O")
        st = {i["order"]: i for i in evaluate(root)}
        ok("测试单 DB-TEST-001 不受起始单号限制且并行就绪", st["DB-TEST-001"]["state"] == "ready")
        ok("ORDER_ID_RE 识别 DB 单号与返工轮次", order_id_of("DB-012-R2-x.md") == ("DB-012-R2", 12)
           and order_id_of("T-042-x.md") == (None, None))
        ok("DB-003 低于起始单号不处理", "DB-003" not in st)
        ok("中文文件名 + GG 未交 -> waiting", st["DB-042"]["state"] == "waiting" and st["DB-042"]["missing"] == ["GG"])
        ok("只有 A -> ready RUN", st["DB-043"]["state"] == "ready" and st["DB-043"]["mode"] == "RUN")
        ok("「无需 A/O 评审」不路由到 A", "DB-044" not in st)
        ok("A就绪行: 缺文件 -> waiting", st["DB-045"]["state"] == "waiting" and len(st["DB-045"]["missing"]) == 2)
        ok("团队前置: gork-team+GG 都要交", st["DB-046"]["missing"] == ["GorkMan", "GG"])
        ok("返工轮次单独成单", st["DB-046-R1"]["state"] == "waiting")
        ok("O 在 A 前不作前置", st["DB-047"]["missing"] == ["GorkMan"])
        ok("A就绪：取消", st["DB-048"]["state"] == "cancelled")
        ok("A就绪：立即", st["DB-049"]["state"] == "ready")
        ok("正文引用的「执行：」不抢首部执行人行", "DB-051" not in st)
        ok("next 按单号取最小", next_item(root)["order"] == "DB-043")
        ok("DB-053 并行多人督办 -> 各方独立 ready", st["DB-055"]["state"] == "ready"
           and st["DB-055"]["ready_rule"] == "parallel:none")
        ok("DB-053 并行 + 顺序行 -> 按顺序 waiting", st["DB-056"]["state"] == "waiting"
           and st["DB-056"]["missing"] == ["GorkMan"])
        ok("DB-053 并行 + 须待 X 交付后 -> waiting", st["DB-057"]["state"] == "waiting"
           and st["DB-057"]["missing"] == ["DSMan"])
        ok("DB-053 正文引用的 A就绪 不算就绪行", st["DB-058"]["state"] == "waiting"
           and st["DB-058"]["missing"] == ["GG"])

        report("DB-042-report-GG.md")
        report("DB-045-report-GG.md")
        report("DB-045-review-O.md")
        report("DB-046-report-gork-team.md")
        report("DB-046-report-GG.md")
        report("DB-047-report-gork-team.md")
        report("DB-046-R1-regression-A.md")
        st = {i["order"]: i for i in evaluate(root)}
        ok("实施方交报告后 -> ready", st["DB-042"]["state"] == "ready")
        ok("A就绪行文件齐 -> ready", st["DB-045"]["state"] == "ready")
        ok("R1 的 A 报告不算基础单完成", st["DB-046"]["state"] == "ready" and st["DB-046-R1"]["state"] == "done")
        ok("DB-047 gork-team 交后 ready", st["DB-047"]["state"] == "ready")

        report("DB-043-回执-A.md")
        report("DB-044-receipt-A.md")
        ok("回执不算 A 的报告", {i["order"]: i for i in evaluate(root)}["DB-043"]["state"] == "ready")
        report("DB-043-regression-A.md")
        ok("A 报告存在 -> done", {i["order"]: i for i in evaluate(root)}["DB-043"]["state"] == "done")
        ok("report_members 不误判 GG 报告为 A", report_members("DB-042-report-GG.md", "DB-042") == {"GG"})
        ok("report_members 识别中文段 A 报告", report_members("DB-035-M09验证-A.md", "DB-035") == {"A"})

        (root / CLAIMS).mkdir(parents=True)
        cp = claim_path(root, "DB-042")
        t0 = now_utc()
        cp.write_text(json.dumps({"status": "running", "started_at": t0.isoformat(), "attempts": 1}), encoding="utf-8")
        ok("新鲜占用 -> claimed", {i["order"]: i for i in evaluate(root, t0)}["DB-042"]["state"] == "claimed")
        later = t0 + datetime.timedelta(hours=STALE_HOURS + 0.1)
        it = {i["order"]: i for i in evaluate(root, later)}["DB-042"]
        ok("超时占用 -> 可重领 RUN", it["state"] == "ready" and it["mode"] == "RUN")
        cp.write_text(json.dumps({"status": "running", "started_at": t0.isoformat(), "attempts": MAX_ATTEMPTS}), encoding="utf-8")
        it = {i["order"]: i for i in evaluate(root, later)}["DB-042"]
        ok("达到尝试上限 -> FAIL_REPORT", it["mode"] == "FAIL_REPORT")
        cp.write_text(json.dumps({"status": "dispatched", "dispatched_at": t0.isoformat(), "dispatches": 1}), encoding="utf-8")
        st = {i["order"]: i for i in evaluate(root, t0)}
        ok("刚拉起 -> dispatched, Actions 不再重复拉起", st["DB-042"]["state"] == "dispatched"
           and next_item(root, t0)["order"] != "DB-042")
        ok("会话 --next 能取到 dispatched 单", next_item(root, t0, ("ready", "dispatched"))["order"] in ("DB-042", "DB-045"))
        late = t0 + datetime.timedelta(minutes=DISPATCH_STALE_MIN + 1)
        ok("拉起超时无人接 -> 可再拉起", {i["order"]: i for i in evaluate(root, late)}["DB-042"]["state"] == "ready")
        cp.write_text(json.dumps({"status": "dispatched", "dispatched_at": t0.isoformat(),
                                  "dispatches": MAX_DISPATCHES}), encoding="utf-8")
        ok("拉起次数用尽 -> stuck", {i["order"]: i for i in evaluate(root, late)}["DB-042"]["state"] == "stuck")

    ok("guard: 报告/占用/新增测试放行", guard_check([("A", "docs/pm/reports/DB-001-regression-A.md"),
                                                 ("D", "docs/pm/claims/DB-001-A.json"),
                                                 ("A", "monitor/tests_new_case.py"), ("A", "frontend/src/test/x.test.js")]) == [])
    ok("guard: 改既有测试拦截", len(guard_check([("M", "monitor/tests_bugfix.py")])) == 1)
    ok("guard: 删既有测试拦截", len(guard_check([("D", "frontend/src/test/sqlPerf.test.js")])) == 1)
    ok("guard: STATUS/DECISIONS/产品代码拦截", len(guard_check([("M", "docs/pm/STATUS.md"),
                                                          ("M", "docs/pm/DECISIONS.md"),
                                                          ("M", "monitor/api_views.py")])) == 3)

    # 原子领单: 用临时裸仓库模拟 origin, 两个克隆抢同一单
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        env_ok = True
        try:
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(td / "origin.git")], check=True)
            for name in ("s1", "s2"):
                subprocess.run(["git", "clone", "-q", str(td / "origin.git"), str(td / name)],
                               check=True, capture_output=True)
                for k, v in (("user.name", "selftest"), ("user.email", "selftest@example.invalid")):
                    subprocess.run(["git", "-C", str(td / name), "config", k, v], check=True)
            s1 = td / "s1"
            (s1 / ORDERS).mkdir(parents=True)
            (s1 / REPORTS).mkdir(parents=True)
            (s1 / ORDERS / "DB-050-抢单.md").write_text("- 执行：A\n", encoding="utf-8")
            (s1 / REPORTS / ".keep").write_text("", encoding="utf-8")
            _git(s1, "add", "-A")
            _git(s1, "commit", "-q", "-m", "init")
            _git(s1, "push", "-q", "origin", "HEAD:main")
            _git(td / "s2", "pull", "-q", "origin", "main")
        except Exception as e:  # noqa: BLE001
            env_ok = False
            print(f"[SKIP] 领单竞争自测环境搭建失败: {e}")
        if env_ok:
            okd, _ = dispatch(td / "s2")
            okd2, _ = dispatch(td / "s1")
            ok("拉起: 第一次推 dispatched 成功", okd)
            ok("拉起: 第二次无可拉起单", not okd2)
            ok1, _ = claim(td / "s1", "DB-050")
            ok2, msg2 = claim(td / "s2", "DB-050")
            ok("领单: 先到者接手 dispatched", ok1 and read_claim(td / "s1", "DB-050")["dispatches"] == 1)
            ok("领单: 后到者看到占用后放弃", (not ok2) and "claimed" in msg2)
            finish(td / "s1", "DB-050")
            ok("finish 暂存删除占用文件", not claim_path(td / "s1", "DB-050").exists())

    passed = sum(1 for _, c in results if c)
    for name, c in results:
        print(f"[{'PASS' if c else 'FAIL'}] {name}")
    print(f"self-test: {passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--list", action="store_true")
    g.add_argument("--next", action="store_true")
    g.add_argument("--check", action="store_true")
    g.add_argument("--dispatch", action="store_true")
    g.add_argument("--claim", metavar="ORDER")
    g.add_argument("--finish", metavar="ORDER")
    g.add_argument("--guard", action="store_true")
    g.add_argument("--self-test", action="store_true")
    a = ap.parse_args()
    root = Path(a.root)

    if a.self_test:
        return self_test()
    if a.list:
        for it in evaluate(root):
            print(json.dumps(it, ensure_ascii=False))
        return 0
    if a.dispatch:
        good, msg = dispatch(root)
        if good:
            print(msg)
        elif msg:
            print("拉起前占用失败: " + msg, file=sys.stderr)
        return 0 if good else 1
    if a.next or a.check:
        it = next_item(root, states=("ready", "dispatched") if a.next else ("ready",))
        if it:
            print(json.dumps(it, ensure_ascii=False))
        return 0 if (it or a.next) else 1
    if a.claim:
        good, msg = claim(root, a.claim)
        print(("领单成功: " if good else "领单失败: ") + msg)
        return 0 if good else 3
    if a.finish:
        print("已暂存删除占用文件" if finish(root, a.finish) else "无占用文件")
        return 0
    if a.guard:
        changes, bad, dirty = guard(root)
        for st, p in changes:
            print(f"  {st}\t{p}")
        if dirty:
            print("工作区还有未提交改动 (不会被推送, 但请确认无遗漏):\n" + dirty)
        if bad:
            print("GUARD 不通过:\n  " + "\n  ".join(bad))
            return 2
        print("GUARD 通过")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
