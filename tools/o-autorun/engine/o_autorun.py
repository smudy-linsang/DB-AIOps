#!/usr/bin/env python3
"""Durable O review dispatcher. Python >=3.9; standard library only.

Only explicit main-branch dispatch manifests are consumed. Cloud workers have
no publishing responsibility: a deterministic publisher validates their diff.
"""
import argparse
import contextlib
import dataclasses
from datetime import datetime
import fcntl
import hashlib
import json
import os
import re
import sqlite3
import socket
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

CONCLUSIONS = {"通过", "有条件通过", "不通过"}
SHA = re.compile(r"[0-9a-f]{40}\Z")
TASK = re.compile(r"(?:T-[0-9]{3,}|WAKE-O-CANARY-(?:[0-9]+|PM-[0-9]{8}-[0-9]{2,4}))\Z")
TASK_ID = re.compile(r"task_[A-Za-z0-9_-]+\Z")
NOTICE_ID = re.compile(r"NOTICE-(?:[0-9]{3,}|AUTO-CANARY-[0-9]+)\Z")
DISPATCH = "docs/pm/dispatch/"
MAX_PATCH = 32 * 1024 * 1024


class Failure(Exception):
    def __init__(self, code, detail=""):
        self.code, self.detail = code, detail
        super().__init__(code + (": " + detail if detail else ""))


class Waiting(Failure):
    pass


class Ignored(Failure):
    """A well-formed dispatch envelope explicitly assigned to another member."""


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def runtime_fingerprint():
    # A snapshot restored into a worker must not become another coordinator.
    pid_start = Path("/proc/1/stat").read_text().rsplit(")", 1)[1].split()[19]
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    return digest(encode([socket.gethostname(), boot, pid_start]).encode())


def path_ok(value, prefix):
    if not isinstance(value, str) or not value.startswith(prefix):
        raise Failure("INVALID_PATH")
    p = PurePosixPath(value)
    if (str(p) != value or p.is_absolute() or ".." in p.parts
            or any(ord(c) < 32 for c in value) or "\\" in value):
        raise Failure("INVALID_PATH")
    return value


def conclusion(text):
    first = text.splitlines()[0].strip().lstrip("# ").strip("* ") if text else ""
    if first not in CONCLUSIONS:
        raise Failure("INVALID_CONCLUSION")
    return first


def classify_error(raw):
    error = raw.decode("utf-8", "replace").lower()
    if (any(s in error for s in ("unauthorized", "not logged in", "not signed in", "authentication failed", "authentication token"))
            or re.search(r"(?:\bhttp(?:/\d(?:\.\d)?)?\s+|\bstatus(?:\s+code)?[\s=:]+|\breturned error:\s*)401\b", error)):
        return "AUTH"
    if (any(s in error for s in ("quota", "usage_limit", "rate limit", "too many requests"))
            or re.search(r"(?:\bhttp(?:/\d(?:\.\d)?)?\s+|\bstatus(?:\s+code)?[\s=:]+|\breturned error:\s*)429\b", error)):
        return "QUOTA"
    if any(s in error for s in ("error sending request", "resolve host", "connection", "network")):
        return "NETWORK"
    return "COMMAND_FAILED"


def command(argv, *, cwd=None, data=None, timeout=45, env=None):
    """Never log raw command output/credentials on failures."""
    try:
        p = subprocess.run(argv, cwd=cwd, env=env, input=data, stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise Failure("TIMEOUT") from None
    except OSError:
        raise Failure("EXEC_UNAVAILABLE") from None
    if p.returncode:
        raise Failure(classify_error(p.stderr), "exit=%d" % p.returncode)
    return p.stdout


@dataclasses.dataclass
class Config:
    repo_url: str
    state_dir: str
    environment_id: str = ""
    branch: str = "main"
    codex_binary: str = "codex"
    interval: int = 60
    max_attempts: int = 3
    retry_seconds: int = 60
    task_timeout: int = 10800
    author_name: str = "O automation"
    author_email: str = "o-automation@users.noreply.github.com"
    coordinator_binding: str = ""

    @classmethod
    def read(cls, path):
        cfg = cls(**json.loads(Path(path).read_text(encoding="utf-8")))
        if not cfg.repo_url or cfg.branch != "main":
            raise Failure("INVALID_CONFIG", "repo_url required; branch must be main")
        if "://" in cfg.repo_url and (urlsplit(cfg.repo_url).username or urlsplit(cfg.repo_url).password):
            raise Failure("INVALID_CONFIG", "use credential helper, not URL credentials")
        if cfg.interval < 1 or cfg.max_attempts < 1 or cfg.retry_seconds < 1 or cfg.task_timeout < 1:
            raise Failure("INVALID_CONFIG")
        cfg.state_dir = str(Path(cfg.state_dir).expanduser().resolve())
        if cfg.coordinator_binding and cfg.coordinator_binding != runtime_fingerprint():
            raise Failure("COORDINATOR_HOST_CHANGED", "snapshot restored; do not launch a second coordinator")
        return cfg


class Store:
    def __init__(self, directory):
        self.on_transition = None
        self.root = Path(directory)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db = sqlite3.connect(str(self.root / "queue.sqlite3"))
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS inbox (
              path TEXT PRIMARY KEY, state TEXT, reason TEXT, updated REAL);
            CREATE TABLE IF NOT EXISTS jobs (
              key TEXT PRIMARY KEY, payload TEXT NOT NULL, state TEXT NOT NULL,
              cloud_id TEXT, attempts INTEGER NOT NULL DEFAULT 0,
              cloud_done INTEGER NOT NULL DEFAULT 0,
              next_at REAL NOT NULL DEFAULT 0, submitted_at REAL,
              error TEXT, published_sha TEXT, updated REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS events (
              seq INTEGER PRIMARY KEY AUTOINCREMENT, job_key TEXT,
              state TEXT NOT NULL, detail TEXT, ts REAL NOT NULL);
        """)
        if "cloud_done" not in {r[1] for r in self.db.execute("PRAGMA table_info(jobs)")}:
            self.db.execute("ALTER TABLE jobs ADD COLUMN cloud_done INTEGER NOT NULL DEFAULT 0")
        self.db.commit()

    def inbox(self, path, state, reason=""):
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO inbox VALUES (?,?,?,?)",
                            (path, state, reason, time.time()))

    def add(self, job):
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO jobs (key,payload,state,updated) VALUES (?,?,?,?)",
                            (job["job_key"], encode(job), "queued", time.time()))

    def set(self, key, state, detail="", **fields):
        allowed = {"cloud_id", "cloud_done", "attempts", "next_at", "submitted_at", "published_sha"}
        if not set(fields) <= allowed:
            raise ValueError("unknown database field")
        fields.update(state=state, error=detail, updated=time.time())
        with self.db:
            self.db.execute("UPDATE jobs SET " + ",".join(k + "=?" for k in fields) + " WHERE key=?",
                            list(fields.values()) + [key])
            self.db.execute("INSERT INTO events(job_key,state,detail,ts) VALUES (?,?,?,?)",
                            (key, state, detail, time.time()))
        if self.on_transition is not None:
            self.on_transition(key, state)

    def rows(self):
        return list(self.db.execute("SELECT * FROM jobs ORDER BY updated,key"))

    @contextlib.contextmanager
    def lock(self):
        with (self.root / "dispatcher.lock").open("a") as f:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Failure("ALREADY_RUNNING") from None
            try:
                yield
            finally:
                fcntl.flock(f, fcntl.LOCK_UN)


class Board:
    def __init__(self, cfg):
        self.cfg = cfg
        self.repo = Path(cfg.state_dir) / "board.git"
        self.ref = "refs/remotes/origin/main"

    def git(self, *args, **kwargs):
        return command(["git", "--git-dir", str(self.repo), *args], **kwargs)

    def refresh(self):
        if not self.repo.exists():
            command(["git", "init", "--bare", "--quiet", str(self.repo)])
            self.git("remote", "add", "origin", self.cfg.repo_url)
        self.git("fetch", "--quiet", "origin", "+refs/heads/main:" + self.ref)
        self.head = self.git("rev-parse", self.ref).decode().strip()

    def read(self, path):
        try:
            return self.git("show", self.head + ":" + path)
        except Failure as exc:
            if exc.code == "COMMAND_FAILED":
                raise Waiting("MISSING_FILE", path) from None
            raise

    def mode(self, path):
        entry = self.git("ls-tree", "-z", self.head, "--", path).split(b"\0")[0]
        return entry.split(b" ", 1)[0].decode() if entry else None

    def manifests(self):
        raw = self.git("ls-tree", "-r", "--name-only", "-z", self.head, "--", DISPATCH)
        return [p.decode("utf-8") for p in raw.split(b"\0") if p.endswith(b".json")]

    def ancestor(self, sha):
        self.git("merge-base", "--is-ancestor", sha, self.head)


def receipt(board, path, task, round_no, role, source=None):
    path_ok(path, "docs/pm/reports/")
    try:
        obj = json.loads(board.read(path))
    except (ValueError, UnicodeError):
        raise Failure("INVALID_RECEIPT") from None
    if not isinstance(obj, dict):
        raise Failure("INVALID_RECEIPT")
    if (obj.get("schema_version") != 1 or obj.get("task_id") != task
            or obj.get("round") != round_no or obj.get("role") != role):
        raise Waiting("RECEIPT_IDENTITY_MISMATCH", path)
    sha = obj.get("source_sha", "")
    if not isinstance(sha, str) or not SHA.fullmatch(sha):
        raise Failure("INVALID_SOURCE_SHA")
    if source and sha != source:
        raise Waiting("STALE_RECEIPT", path)
    if obj.get("conclusion") != "通过":
        raise Waiting("PREREQUISITE_NOT_PASSED", path)
    report_path = path_ok(obj.get("report_path"), "docs/pm/reports/" + task + "-")
    report = board.read(report_path)
    if obj.get("report_sha256") != digest(report):
        raise Waiting("REPORT_HASH_MISMATCH", path)
    if conclusion(report.decode("utf-8")) != obj["conclusion"]:
        raise Waiting("REPORT_CONCLUSION_MISMATCH", path)
    return obj


def resolve(board, manifest_path):
    path_ok(manifest_path, DISPATCH)
    try:
        obj = json.loads(board.read(manifest_path))
    except (ValueError, UnicodeError):
        raise Failure("INVALID_MANIFEST") from None
    if not isinstance(obj, dict):
        raise Failure("INVALID_MANIFEST")
    task, round_no = obj.get("task_id", ""), obj.get("round")
    if (obj.get("schema_version") != 1
            or not isinstance(task, str) or not TASK.fullmatch(task)
            or type(round_no) is not int or round_no < 1):
        raise Failure("INVALID_MANIFEST")
    member = obj.get("member")
    if member != "O":
        if (isinstance(member, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", member)
                and member.casefold() != "o"):
            raise Ignored("OTHER_MEMBER")
        raise Failure("INVALID_MANIFEST")
    if "order_path" not in obj:
        raise Failure("MISSING_ORDER_PATH")
    order = path_ok(obj.get("order_path"), "docs/pm/orders/" + task + "-")
    board.read(order)
    kind = obj.get("kind", "delivery" if "deliverables" in obj else "review")
    if kind not in ("review", "notice_ack", "delivery"):
        raise Failure("INVALID_TASK_KIND")
    if any(k in obj for k in ("report_path", "receipt_path", "ack_path", "output_path")):
        raise Failure("UNSUPPORTED_OUTPUT_OVERRIDE")
    notice = {}
    if kind == "notice_ack":
        notice_id = obj.get("notice_id", "")
        if not isinstance(notice_id, str) or not NOTICE_ID.fullmatch(notice_id):
            raise Failure("INVALID_NOTICE_ID")
        if "notice_path" not in obj:
            raise Failure("MISSING_NOTICE_PATH")
        notice_path = path_ok(obj["notice_path"], "docs/pm/" + notice_id + "-")
        board.read(notice_path)
        if board.mode(notice_path) != "100644":
            raise Failure("INVALID_NOTICE_DOCUMENT")
        ack_path = "docs/pm/reports/" + notice_id + "-回执-O.md"
        try:
            ack = board.read(ack_path)
        except Waiting as exc:
            if exc.code != "MISSING_FILE":
                raise
            ack_hash = None
        else:
            if board.mode(ack_path) != "100644" or len(ack) > 5 * 1024 * 1024:
                raise Failure("INVALID_EXISTING_ACK")
            ack_hash = digest(ack)
        notice = {"kind": kind, "notice_id": notice_id, "notice_path": notice_path,
                  "ack_path": ack_path, "ack_existing_sha256": ack_hash}
    if bool(obj.get("source_sha")) == bool(obj.get("source_from")):
        raise Failure("INVALID_MANIFEST", "exactly one source selector required")
    requirements = obj.get("requires", [])
    if not isinstance(requirements, list):
        raise Failure("INVALID_MANIFEST")
    source = obj.get("source_sha")
    if obj.get("source_from"):
        source = receipt(board, obj["source_from"], task, round_no, "GG")["source_sha"]
    if not isinstance(source, str) or not SHA.fullmatch(source):
        raise Failure("INVALID_SOURCE_SHA")
    for requirement in requirements:
        if not isinstance(requirement, dict) or requirement.get("role") not in ("GG", "A"):
            raise Failure("INVALID_MANIFEST")
        receipt(board, requirement.get("receipt"), task, round_no, requirement["role"], source)
    try:
        board.ancestor(source)
    except Failure:
        raise Waiting("SOURCE_NOT_ON_MAIN") from None
    identity = [task, "O", round_no, source]
    if notice:
        identity += [kind, notice["notice_id"], notice["notice_path"]]
    key = digest(encode(identity).encode())
    stem = "%s-R%d-review-O" % (task, round_no)
    report_path = "docs/pm/reports/" + stem + ".md"
    outputs = {}
    if "deliverables" in obj:
        declared = obj["deliverables"]
        if (not isinstance(declared, list) or not declared or len(declared) > 16
                or any(not isinstance(p, str) for p in declared) or len(set(declared)) != len(declared)):
            raise Failure("INVALID_DELIVERABLES")
        for path in declared:
            path_ok(path, "docs/pm/reports/")
            name = path[len("docs/pm/reports/"):]
            if ("/" in name or not name.endswith(".md") or len(name) <= 3 or name.startswith(".")
                    or name in ("STATUS.md", "DECISIONS.md") or any(c in name for c in "*?[]{}")):
                raise Failure("INVALID_DELIVERABLE_PATH")
        if notice:
            if set(declared) != {report_path, notice["ack_path"]}:
                raise Failure("INVALID_NOTICE_DELIVERABLES")
        elif report_path not in declared:
            report_path = declared[0]
        identity += ["deliverables", declared]
        key = digest(encode(identity).encode())
        outputs = {"kind": kind, "deliverables": declared}
    elif kind == "delivery":
        raise Failure("DELIVERABLES_REQUIRED")
    return {"job_key": key, "task_id": task, "round": round_no, "source_sha": source,
            "dispatch_sha": board.head, "manifest_path": manifest_path, "order_path": order,
            "requires": requirements,
            "report_path": report_path,
            "receipt_path": "docs/pm/reports/" + stem + ".receipt.json",
            "evidence_prefix": "docs/pm/reports/evidence/%s-R%d-O/" % (task, round_no), **notice, **outputs}


def prompt(job):
    notice_instruction = ""
    if job.get("kind") == "notice_ack":
        notice_instruction = """行政通知回执任务：以 dispatch_sha 中的任务单和 notice_path 为本轮工作上下文。
这是本人学习/确认任务，无需产品回归、浏览器或服务器；source_sha 仍须完整写入本轮核验报告。
先读通知及其明确要求的必读/精读材料，再如实写本人回执；不得虚构已学习、倒填完成时间或代其他人确认。
ack_path 由系统固定为本通知的 O 回执。若 ack_existing_sha256 非空，原回执只读，核对其内容/完成时间及哈希；
不得改写或删除。只有原回执明确确认本人完成且材料覆盖符合通知，才在本轮报告判通过；否则如实列 FAIL/BLOCKED。
若原回执不存在，完成通知要求后在 ack_path 实际创建普通 UTF-8 Markdown 文件，首行三档结论，写明 notice_id、
O 本人、完成确认（完成/未完成及原因）、实际完成时间（带时区）、覆盖范围。未完成也须如实交回执，不伪装通过。
核验报告和机器 receipt 仍写 report_path/receipt_path。receipt 另含 ack_path、ack_sha256、ack_status（完成/未完成）、
ack_completed_at（完成时为带时区 ISO8601 时间；未完成为 null）。该时间必须与原回执记载一致，不能把核验时间当学习时间。
checks 至少含 notice-read、ack-validated；报告说明本轮是复用既有回执还是新建，不把手动旧回执说成自动新建。
"""
    if "deliverables" in job:
        notice_instruction += ("显式交付白名单：只写 deliverables 中列明的 Markdown 文件和固定 receipt_path；"
                               "不提交未列出的 evidence。已有通知回执按只读规则复用。"
                               "receipt 还必须含 deliverable_sha256 对象，键为每个 deliverables 路径，值为该文件原始字节SHA256。\n")
    if job.get("kind") == "delivery":
        notice_instruction += "本单是显式文档交付任务，无需产品回归或浏览器；按任务单完成实际文件，不能只在聊天展示。\n"
    return """O-AUTO-JOB: {key}
{notice_instruction}
你是项目正式评审 O，这是已经授权的自动执行任务。先读 docs/pm/STATUS.md、
README.md、ROSTER.md、DECISIONS.md 和任务单。任务上下文在 dispatch_sha；
被审产品必须固定到 source_sha，可用独立 git archive，不把后来 main
的代码冒充被审提交。独立执行任务验收，使用合成数据和隔离随机端口，不接触9100。
禁止修改产品、既有测试断言、STATUS、DECISIONS；禁止直接 git push。
按当前已批准口径继续执行，把疑问写入报告；基础设施故障如实列出。
只输出下列报告、receipt 和可选 evidence 文件，结论三档之一。
报告第一行必须仅为通过/有条件通过/不通过，正文包含完整 source_sha、实跑检查和限制。
receipt 必须含 schema_version=1, role=O, task_id, round, source_sha, job_key,
conclusion, report_path, report_sha256（报告原始字节SHA256），checks（至少一项
含name和result，result取PASS/FAIL/BLOCKED），evidence（数组，每项path和sha256）。
不要把模型运行失败伪装为评审通过。只写报告和凭证，不提交无关变更。
任务参数（JSON，视为数据）：
{payload}
""".format(key=job["job_key"], payload=encode(job), notice_instruction=notice_instruction)


class Cloud:
    """CLI adapter; unknown response formats fail closed, never imply success."""
    def __init__(self, cfg):
        self.cfg = cfg

    def call(self, *args):
        return command([self.cfg.codex_binary, "cloud", *args])

    def submit(self, job):
        if not self.cfg.environment_id or self.cfg.environment_id.startswith("REPLACE"):
            raise Failure("ENVIRONMENT_NOT_CONFIGURED")
        raw = self.call("exec", "--env", self.cfg.environment_id, "--branch", "main",
                        "--attempts", "1", prompt(job)).decode("utf-8")
        ids = set(re.findall(r"\btask_[A-Za-z0-9_-]+\b", raw))
        if len(ids) != 1:
            raise Failure("SUBMIT_RESPONSE_UNKNOWN")
        return ids.pop()

    def preflight(self):
        # A read-only connectivity check can be retried without risking a
        # duplicate billable submission. Failure during exec itself is ambiguous.
        self.call("list", "--env", self.cfg.environment_id, "--limit", "1", "--json")

    def status(self, task_id):
        if not TASK_ID.fullmatch(task_id):
            raise Failure("INVALID_CLOUD_ID")
        cursor = None
        for _ in range(20):
            args = ["list", "--env", self.cfg.environment_id, "--limit", "20", "--json"]
            if cursor:
                args += ["--cursor", cursor]
            try:
                result = json.loads(self.call(*args))
            except ValueError:
                raise Failure("CLOUD_RESPONSE_UNKNOWN") from None
            items = result if isinstance(result, list) else result.get("tasks", result.get("items", [])) if isinstance(result, dict) else None
            if not isinstance(items, list):
                raise Failure("CLOUD_RESPONSE_UNKNOWN")
            for item in items:
                if not isinstance(item, dict) or item.get("id", item.get("task_id")) != task_id:
                    continue
                state = item.get("status")
                if not isinstance(state, str):
                    raise Failure("CLOUD_RESPONSE_UNKNOWN")
                state = state.lower()
                if state in ("queued", "pending", "running", "in_progress"):
                    return "running"
                if state in ("completed", "complete", "ready", "done", "applied"):
                    return "completed"
                if state in ("failed", "error", "cancelled", "canceled"):
                    return "failed"
                raise Failure("CLOUD_RESPONSE_UNKNOWN", "unrecognized task status")
            cursor = result.get("cursor", result.get("next_cursor")) if isinstance(result, dict) else None
            if not cursor:
                break
        raise Failure("CLOUD_TASK_NOT_VISIBLE")

    def diff(self, task_id):
        raw = self.call("diff", task_id)
        if not raw.startswith(b"diff --git ") or len(raw) > MAX_PATCH:
            raise Failure("INVALID_CLOUD_DIFF")
        return raw


def mandatory_paths(job):
    paths = {job["report_path"], job["receipt_path"]}
    paths.update(job.get("deliverables", []))
    if job.get("kind") == "notice_ack" and job["ack_existing_sha256"] is None:
        paths.add(job["ack_path"])
    elif job.get("kind") == "notice_ack":
        paths.discard(job["ack_path"])
    return paths


def validate_ack(job, meta, raw):
    """Check the notice receipt binding, not a claim that learning can be proved by code."""
    if meta.get("ack_path") != job["ack_path"] or meta.get("ack_sha256") != digest(raw):
        raise Failure("ACK_IDENTITY_MISMATCH")
    if job["ack_existing_sha256"] is not None and digest(raw) != job["ack_existing_sha256"]:
        raise Failure("ACK_EXISTING_CHANGED")
    try:
        text = raw.decode("utf-8")
    except UnicodeError:
        raise Failure("INVALID_ACK_TEXT") from None
    ack_conclusion = conclusion(text)
    if job["notice_id"] not in text or not re.search(r"\bO\b", text) or "本人" not in text:
        raise Failure("INVALID_ACK_TEXT")
    status = meta.get("ack_status")
    if status not in ("完成", "未完成"):
        raise Failure("INVALID_ACK_STATUS")
    if status == "完成":
        try:
            completed = datetime.fromisoformat(meta["ack_completed_at"])
            if completed.tzinfo is None or completed.timestamp() > time.time() + 300:
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            raise Failure("INVALID_ACK_COMPLETION_TIME") from None
        visible_times = []
        for line in text.splitlines():
            label = re.sub(r"[*`#]", "", line).strip().lstrip("-").strip()
            if not re.match(r"(?:学习)?完成时间\s*[:：]", label):
                continue
            for match in re.finditer(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})\s*(Z|[+-]\d{2}:\d{2})", label):
                try:
                    visible_times.append(datetime.fromisoformat(match[1] + "T" + match[2] + match[3].replace("Z", "+00:00")).timestamp())
                except ValueError:
                    continue
        if completed.timestamp() not in visible_times:
            raise Failure("ACK_COMPLETION_TIME_MISMATCH")
        if not re.search(r"本人.{0,20}(?:已完成|完成学习)", text):
            raise Failure("INVALID_ACK_TEXT")
    elif meta.get("ack_completed_at") is not None:
        raise Failure("INVALID_ACK_COMPLETION_TIME")
    elif "未完成" not in text:
        raise Failure("INVALID_ACK_TEXT")
    if meta.get("conclusion") == "通过" and (status != "完成" or ack_conclusion != "通过"):
        raise Failure("ACK_CONCLUSION_MISMATCH")
    checks = {c.get("name"): c.get("result") for c in meta.get("checks", []) if isinstance(c, dict)}
    if not {"notice-read", "ack-validated"} <= checks.keys():
        raise Failure("ACK_CHECKS_MISSING")


class Publisher:
    def __init__(self, cfg, board):
        self.cfg, self.board = cfg, board

    def existing(self, job):
        try:
            raw = self.board.read(job["receipt_path"])
        except Waiting:
            return None
        try:
            meta = json.loads(raw)
        except ValueError:
            raise Failure("PUBLISH_COLLISION") from None
        if not isinstance(meta, dict):
            raise Failure("PUBLISH_COLLISION")
        if meta.get("job_key") != job["job_key"]:
            raise Failure("PUBLISH_COLLISION")
        report = self.board.read(job["report_path"])
        if digest(report) != meta.get("report_sha256"):
            raise Failure("PUBLISHED_REPORT_HASH_MISMATCH")
        if job.get("kind") == "notice_ack":
            if self.board.mode(job["ack_path"]) != "100644":
                raise Failure("INVALID_EXISTING_ACK")
            validate_ack(job, meta, self.board.read(job["ack_path"]))
        if "deliverables" in job:
            expected = {p: digest(self.board.read(p)) for p in job["deliverables"]}
            if meta.get("deliverable_sha256") != expected:
                raise Failure("DELIVERABLE_HASH_MISMATCH")
        return self.board.head

    def validate(self, work, job, paths):
        mandatory = mandatory_paths(job)
        if not mandatory <= set(paths):
            raise Failure("REPORT_MISSING")
        if (job.get("kind") == "notice_ack" and job["ack_existing_sha256"] is not None
                and job["ack_path"] in paths):
            raise Failure("ACK_ALREADY_EXISTS")
        total = 0
        inspect = set(paths)
        if job.get("kind") == "notice_ack":
            inspect.add(job["ack_path"])
        for path in inspect:
            if path not in mandatory:
                if job.get("kind") != "notice_ack" or path != job["ack_path"]:
                    path_ok(path, job["evidence_prefix"])
            p = work / path
            if not p.exists() or p.is_symlink() or not stat.S_ISREG(p.lstat().st_mode):
                raise Failure("INVALID_ARTIFACT_TYPE")
            # Check ancestors too; do not follow a symlink into another tree.
            if any(parent.is_symlink() for parent in p.parents if parent != work.parent):
                raise Failure("INVALID_ARTIFACT_TYPE")
            if p.stat().st_mode & 0o111 or p.stat().st_size > 5 * 1024 * 1024:
                raise Failure("ARTIFACT_LIMIT")
            total += p.stat().st_size
        if total > 20 * 1024 * 1024:
            raise Failure("ARTIFACT_LIMIT")
        report = (work / job["report_path"]).read_bytes()
        try:
            text = report.decode("utf-8")
            meta = json.loads((work / job["receipt_path"]).read_text(encoding="utf-8"))
        except (ValueError, UnicodeError):
            raise Failure("INVALID_RESULT_RECEIPT") from None
        if not isinstance(meta, dict):
            raise Failure("INVALID_RESULT_RECEIPT")
        expected = {"schema_version": 1, "role": "O", "task_id": job["task_id"],
                    "round": job["round"], "source_sha": job["source_sha"],
                    "job_key": job["job_key"], "report_path": job["report_path"],
                    "report_sha256": digest(report), "conclusion": conclusion(text)}
        if any(meta.get(k) != v for k, v in expected.items()) or job["source_sha"] not in text:
            raise Failure("RESULT_IDENTITY_MISMATCH")
        checks = meta.get("checks")
        if not isinstance(checks, list) or not checks or any(
                not isinstance(c, dict) or not isinstance(c.get("name"), str)
                or not c["name"] or c.get("result") not in ("PASS", "FAIL", "BLOCKED") for c in checks):
            raise Failure("CHECKS_MISSING")
        if meta["conclusion"] == "通过" and any(c["result"] != "PASS" for c in checks):
            raise Failure("CONCLUSION_CHECKS_MISMATCH")
        if job.get("kind") == "notice_ack":
            validate_ack(job, meta, (work / job["ack_path"]).read_bytes())
        if "deliverables" in job:
            expected = {p: digest((work / p).read_bytes()) for p in job["deliverables"]}
            if meta.get("deliverable_sha256") != expected:
                raise Failure("DELIVERABLE_HASH_MISMATCH")
        evidence = meta.get("evidence", [])
        if not isinstance(evidence, list):
            raise Failure("INVALID_EVIDENCE")
        if "deliverables" in job and evidence:
            raise Failure("UNDECLARED_ARTIFACT")
        evidence_paths = set()
        for item in evidence:
            if not isinstance(item, dict):
                raise Failure("INVALID_EVIDENCE")
            p = path_ok(item.get("path"), job["evidence_prefix"])
            if p in evidence_paths or p not in paths or digest((work / p).read_bytes()) != item.get("sha256"):
                raise Failure("INVALID_EVIDENCE")
            evidence_paths.add(p)
        if set(paths) != mandatory | evidence_paths:
            raise Failure("UNDECLARED_ARTIFACT")
        return meta

    def publish(self, job, patch):
        if not patch or len(patch) > MAX_PATCH:
            raise Failure("INVALID_CLOUD_DIFF")
        self.board.refresh()
        if self.existing(job):
            return self.board.head
        with tempfile.TemporaryDirectory(prefix="publish-", dir=self.cfg.state_dir) as tmp:
            work = Path(tmp)
            command(["git", "clone", "--quiet", "--shared", "--no-checkout", str(self.board.repo), tmp])
            # Keep patch bytes identical to worker-side validation (see o_exec_worker).
            command(["git", "config", "core.autocrlf", "false"], cwd=work)
            command(["git", "checkout", "--quiet", "--detach", self.board.head], cwd=work)
            stats = command(["git", "apply", "--numstat", "-z", "-"], cwd=work, data=patch)
            try:
                paths = [p.split(b"\t", 2)[2].decode("utf-8") for p in stats.split(b"\0") if p]
            except (IndexError, UnicodeError):
                raise Failure("INVALID_PATCH_PATHS") from None
            if not paths or len(paths) > 64 or len(paths) != len(set(paths)):
                raise Failure("INVALID_PATCH_PATHS")
            for p in paths:
                if p not in mandatory_paths(job):
                    path_ok(p, job["evidence_prefix"])
                else:
                    path_ok(p, "docs/pm/reports/")
                if (work / p).exists() or (work / p).is_symlink():
                    raise Failure("PUBLISH_COLLISION")
            command(["git", "apply", "--binary", "-"], cwd=work, data=patch)
            self.validate(work, job, paths)
            # Only validated, explicitly declared paths: .log evidence may be
            # ignored by the product's global *.log rule, but is intentional.
            command(["git", "add", "--force", "--", *paths], cwd=work)
            command(["git", "-c", "user.name=" + self.cfg.author_name,
                     "-c", "user.email=" + self.cfg.author_email, "commit", "--quiet", "-m",
                     "docs(%s): O R%d automatic review" % (job["task_id"], job["round"])], cwd=work)
            command(["git", "push", "--quiet", self.cfg.repo_url, "HEAD:refs/heads/main"], cwd=work)
        self.board.refresh()
        confirmed = self.existing(job)
        if not confirmed:
            raise Failure("PUBLISH_NOT_CONFIRMED")
        return confirmed


class Dispatcher:
    def __init__(self, cfg, cloud=None):
        self.cfg = cfg
        self.store = Store(cfg.state_dir)
        self.board = Board(cfg)
        self.cloud = cloud or Cloud(cfg)
        self.publisher = Publisher(cfg, self.board)

    def scan(self, order=None):
        self.board.refresh()
        manifests = self.board.manifests()
        # A removed bad manifest must not remain a permanent invalid inbox row.
        current = set(manifests)
        with self.store.db:
            self.store.db.executemany("DELETE FROM inbox WHERE path=?", [
                (r["path"],) for r in self.store.db.execute("SELECT path FROM inbox")
                if r["path"] not in current])
        for path in manifests:
            try:
                job = resolve(self.board, path)
                if order and job["order_path"] != order:
                    continue
                self.store.add(job)
                self.store.inbox(path, "ready")
            except Ignored as e:
                self.store.inbox(path, "ignored", str(e))
            except Waiting as e:
                self.store.inbox(path, "waiting", str(e))
            except (Failure, ValueError, TypeError, KeyError) as e:
                self.store.inbox(path, "invalid", str(e) if isinstance(e, Failure) else type(e).__name__)

    def retry(self, row, failure, target="retry"):
        attempts = row["attempts"] + 1
        state = "blocked" if attempts >= self.cfg.max_attempts or failure.code in ("AUTH", "QUOTA") else target
        self.store.set(row["key"], state, str(failure), attempts=attempts,
                       next_at=time.time() + min(3600, self.cfg.retry_seconds * 2 ** (attempts - 1)))

    def tick(self, scan_only=False, order=None, monitor_only=False):
        with self.store.lock():
            self.scan(order)
            if scan_only:
                return
            rows = self.store.rows()
            # A persisted submit intent without an ID may already have created a
            # paid task. Never blindly resubmit after a crash/ambiguous response.
            for row in rows:
                if row["state"] == "submitting":
                    self.store.set(row["key"], "submission_unknown", "SUBMISSION_UNKNOWN: reconcile cloud task before retry")
            for row in self.store.rows():
                if row["state"] not in ("running", "publishing") or row["next_at"] > time.time():
                    continue
                job = json.loads(row["payload"])
                try:
                    self.board.refresh()
                    existing = self.publisher.existing(job)
                    if existing:
                        self.store.set(row["key"], "done", published_sha=existing, cloud_done=1)
                        continue
                    if row["state"] == "running":
                        if (not getattr(self.cloud, "synchronous", False)
                                and time.time() - row["submitted_at"] > self.cfg.task_timeout):
                            raise Failure("TASK_TIMEOUT")
                        status = self.cloud.status(row["cloud_id"])
                        if status == "running":
                            self.store.set(row["key"], "running", attempts=0, next_at=0)
                            continue
                        if status == "failed":
                            self.store.set(row["key"], "blocked", cloud_done=1)
                            raise Failure("CLOUD_EXECUTION_FAILED")
                        self.store.set(row["key"], "publishing", attempts=0, next_at=0, cloud_done=1)
                    patch = self.cloud.diff(row["cloud_id"])
                    published = self.publisher.publish(job, patch)
                    self.store.set(row["key"], "done", published_sha=published)
                except Failure as e:
                    if e.code in ("TASK_TIMEOUT", "CLOUD_EXECUTION_FAILED"):
                        self.store.set(row["key"], "blocked", str(e))
                    else:
                        # Publishing may already have replaced running. Retry
                        # that committed phase; do not reset its attempt count
                        # or monitor a completed model request again.
                        fresh = next(r for r in self.store.rows() if r["key"] == row["key"])
                        self.retry(fresh, e, fresh["state"])
            if monitor_only:
                return
            if any(r["state"] in ("running", "publishing", "submitting", "submission_unknown")
                   or (r["state"] == "blocked" and r["cloud_id"] and not r["cloud_done"])
                   for r in self.store.rows()):
                return
            for row in self.store.rows():
                if row["state"] not in ("queued", "retry") or row["next_at"] > time.time():
                    continue
                job = json.loads(row["payload"])
                try:
                    # Recheck dependencies immediately before the external side effect.
                    self.board.refresh()
                    current = resolve(self.board, job["manifest_path"])
                    if current["job_key"] != job["job_key"]:
                        self.store.set(row["key"], "superseded", "source changed before launch")
                        continue
                    if job.get("kind") == "notice_ack":
                        # A prior administrative task may have published this
                        # same notice while this task waited in the queue.
                        job = current
                        with self.store.db:
                            self.store.db.execute("UPDATE jobs SET payload=? WHERE key=?",
                                                  (encode(job), job["job_key"]))
                    if not self.cfg.environment_id or self.cfg.environment_id.startswith("REPLACE"):
                        raise Failure("ENVIRONMENT_NOT_CONFIGURED")
                    self.cloud.preflight()
                    self.store.set(row["key"], "submitting", submitted_at=time.time())
                    cloud_id = self.cloud.submit(job)
                    self.store.set(row["key"], "running", cloud_id=cloud_id, attempts=0, next_at=0)
                except Waiting as e:
                    self.store.set(row["key"], "queued", str(e), next_at=time.time() + self.cfg.interval)
                except Failure as e:
                    fresh = next(r for r in self.store.rows() if r["key"] == row["key"])
                    if fresh["state"] == "submitting":
                        self.store.set(row["key"], "submission_unknown", "SUBMISSION_UNKNOWN: " + str(e))
                    else:
                        self.retry(row, e)
                break


def doctor(cfg):
    checks = {"python": sys.version_info >= (3, 9), "environment_id": bool(cfg.environment_id) and not cfg.environment_id.startswith("REPLACE")}
    store = Store(cfg.state_dir)
    with store.lock():
        for name, argv in (("git", ["git", "--version"]),
                           ("codex", [cfg.codex_binary, "--version"]),
                           ("login", [cfg.codex_binary, "login", "status"])):
            try:
                command(argv)
                checks[name] = True
            except Failure as e:
                checks[name] = e.code
        try:
            Board(cfg).refresh()
            checks["github_read"] = True
        except Failure as e:
            checks["github_read"] = e.code
        try:
            args = ["list", "--limit", "1", "--json"]
            if checks["environment_id"]:
                args += ["--env", cfg.environment_id]
            Cloud(cfg).call(*args)
            checks["cloud_read"] = True
        except Failure as e:
            checks["cloud_read"] = e.code
    print(encode(checks))
    return 0 if all(v is True for v in checks.values()) else 1


def main(argv=None):
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--config", required=True)
    sub = p.add_subparsers(dest="action", required=True)
    for action in ("once", "run", "status", "doctor", "scan"):
        s = sub.add_parser(action)
        if action == "scan":
            s.add_argument("--order")
    for action in ("retry", "attach"):
        s = sub.add_parser(action)
        s.add_argument("--job", required=True)
        if action == "attach":
            s.add_argument("--cloud-id", required=True)
    args = p.parse_args(argv)
    cfg = Config.read(args.config)
    if args.action == "doctor":
        return doctor(cfg)
    d = Dispatcher(cfg)
    if args.action in ("retry", "attach"):
        with d.store.lock():
            row = next((r for r in d.store.rows() if r["key"] == args.job), None)
            if row is None:
                raise Failure("JOB_NOT_FOUND")
            if args.action == "attach":
                if row["state"] != "submission_unknown":
                    raise Failure("RECONCILE_NOT_REQUIRED")
                d.cloud.status(args.cloud_id)
                d.store.set(args.job, "running", cloud_id=args.cloud_id, attempts=0,
                            next_at=0, submitted_at=time.time())
            else:
                if row["state"] not in ("blocked", "retry"):
                    raise Failure("RETRY_NOT_ALLOWED")
                d.store.set(args.job, "running" if row["cloud_id"] else "queued", attempts=0,
                            next_at=0, submitted_at=time.time() if row["cloud_id"] else None)
        return 0
    if args.action == "status":
        print(encode({"jobs": [dict(r) for r in d.store.rows()],
                      "inbox": [dict(r) for r in d.store.db.execute("SELECT * FROM inbox ORDER BY path")]}))
        return 0
    while True:
        try:
            d.tick(scan_only=args.action == "scan", order=getattr(args, "order", None))
            print(encode({"event": "tick", "time": time.time(),
                          "states": {s: sum(r["state"] == s for r in d.store.rows())
                                     for s in sorted({r["state"] for r in d.store.rows()})}}), flush=True)
        except Failure as e:
            print(encode({"event": "error", "code": e.code}), file=sys.stderr, flush=True)
            if args.action != "run":
                return 1
        if args.action != "run":
            return 0
        time.sleep(cfg.interval)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Failure, ValueError, TypeError, KeyError) as e:
        print(encode({"event": "fatal", "code": e.code if isinstance(e, Failure) else type(e).__name__}), file=sys.stderr)
        sys.exit(1)
