#!/usr/bin/env python3
"""Official Codex exec worker on a disposable GitHub runner (no cloud catalog).

The adapter keeps the dispatcher contract. Only mechanically validated review
diffs enter its resumable encrypted cache; raw model logs stay temporary.
"""
import base64
import io
import json
import os
from pathlib import Path
import stat
import signal
import shutil
import subprocess
import tempfile
import zlib

try:
    from . import o_autorun as auto
except ImportError:
    import o_autorun as auto


def worker_environment():
    env = dict(os.environ)
    for key in list(env):
        if key in {"GITHUB_TOKEN", "GH_TOKEN", "O_CODEX_AUTH_JSON", "O_AUTORUN_STATE_KEY",
                   "GIT_ASKPASS", "SSH_ASKPASS", "GIT_CONFIG_PARAMETERS", "GIT_CONFIG_COUNT",
                   "GITHUB_ENV", "GITHUB_OUTPUT", "GITHUB_STATE", "GITHUB_PATH", "GITHUB_STEP_SUMMARY",
                   "ACTIONS_RUNTIME_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"} \
                or key.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            env.pop(key, None)
    # CLI auth uses the dedicated file profile. Do not silently choose API-key
    # billing because an unrelated runner setting happened to provide one.
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY"):
        env.pop(key, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def artifact_diagnostics(work, job):
    """Fixed labels/enums only; never expose file contents or arbitrary paths."""
    result = {}
    for label in ("report", "receipt"):
        p = work / job[label + "_path"]
        if p.is_symlink():
            kind = "symlink"
        elif not p.exists():
            kind = "missing"
        elif not stat.S_ISREG(p.lstat().st_mode):
            kind = "not_regular"
        elif any(parent.is_symlink() for parent in p.parents):
            kind = "symlink_ancestor"
        elif p.stat().st_mode & 0o111:
            kind = "executable"
        else:
            kind = "regular"
        result[label] = kind
    return result


def terminal_failure_code(error):
    """Classify a CLI error payload, never arbitrary command/agent output."""
    raw = error if isinstance(error, bytes) else json.dumps(error).encode()
    text = raw.decode("utf-8", "replace").lower()
    if any(p in text for p in ("contextwindowexceeded", "context_window_exceeded", "context_length_exceeded")):
        return "CONTEXT_LIMIT"
    if any(p in text for p in ("you've hit your usage limit", "you have hit your usage limit",
                               "usage limit exceeded", "usage limit reached", "usagelimitexceeded",
                               "ratelimitexceeded", "rate_limit_exceeded", "rate_limit_reached")):
        return "QUOTA"
    if "stream disconnected" in text:
        return "NETWORK"
    code = auto.classify_error(raw)
    if code in ("AUTH", "QUOTA", "NETWORK"):
        return code
    if "sandbox" in text or "bwrap" in text:
        return "EXEC_SANDBOX_FAILED"
    return "EXEC_CLI_FAILED"


def event_diagnostics(log):
    """Extract only known event counters and error categories from CLI JSONL."""
    log.seek(0, 2)
    log.seek(max(0, log.tell() - 1024 * 1024))
    result = {"turn_completed": 0, "turn_failed": 0, "command_items": 0,
              "failed_commands": 0, "file_change_items": 0, "agent_messages": 0,
              "parsed_events": 0, "terminal_error": None,
              "signals_scope": "all_events_hint_only"}
    signals = set()
    for line in log:
        try:
            event = json.loads(line)
        except (ValueError, UnicodeError):
            continue
        if not isinstance(event, dict):
            continue
        result["parsed_events"] += 1
        kind = event.get("type")
        if kind in ("turn.completed", "turn.failed"):
            result[kind.replace(".", "_")] += 1
        if kind == "turn.failed":
            result["terminal_error"] = {"code": terminal_failure_code(event.get("error", {})),
                                        "source": "turn.failed"}
        elif kind == "error":
            result["terminal_error"] = {"code": terminal_failure_code(event.get("error", event.get("message", {}))),
                                        "source": "error"}
        elif kind == "turn.completed":
            result["terminal_error"] = None
        item = event.get("item", {})
        if kind == "item.completed" and isinstance(item, dict):
            item_type = item.get("type")
            if item_type == "command_execution":
                result["command_items"] += 1
                if item.get("exit_code") not in (None, 0):
                    result["failed_commands"] += 1
            elif item_type == "file_change":
                result["file_change_items"] += 1
            elif item_type == "agent_message":
                result["agent_messages"] += 1
        # Legacy contextual hints can match quoted source text. Only the
        # separately classified terminal_error is execution-failure evidence.
        text = json.dumps(event).lower()
        for signal, phrases in {
            "SANDBOX": ("sandbox failed", "sandbox error", "bwrap:", "landlock"),
            "WRITE_DENIED": ("read-only file system", "permission denied", "write access denied"),
            "MODEL_UNAVAILABLE": ("model is not supported", "model does not exist", "model_not_found"),
            "AUTH": ("401 unauthorized", "authentication failed", "not logged in"),
            "QUOTA": ("usage_limit", "quota exceeded", "rate limit exceeded"),
        }.items():
            if any(phrase in text for phrase in phrases):
                signals.add(signal)
    result["signals"] = sorted(signals)
    return result


def output_instruction(job):
    receipt = {"schema_version": 1, "role": "O", "task_id": job["task_id"],
               "round": job["round"], "source_sha": job["source_sha"],
               "job_key": job["job_key"], "report_path": job["report_path"],
               "conclusion": "按实测填写三档结论", "report_sha256": "报告原始字节SHA256",
               "checks": [{"name": "实际检查项", "result": "PASS/FAIL/BLOCKED"}], "evidence": []}
    extra = ""
    if "deliverables" in job:
        receipt["deliverable_sha256"] = {p: "该文件原始字节SHA256" for p in job["deliverables"]}
        extra = "\n显式交付路径：" + auto.encode(job["deliverables"]) + "；不得附带未声明文件。\n"
    if job.get("kind") == "notice_ack":
        receipt.update(ack_path=job["ack_path"], ack_sha256="回执原始字节SHA256",
                       ack_status="完成/未完成", ack_completed_at="完成时为带时区ISO8601；未完成填null")
        receipt["checks"] = [{"name": "notice-read", "result": "PASS/FAIL/BLOCKED"},
                             {"name": "ack-validated", "result": "PASS/FAIL/BLOCKED"}]
        extra += ("\n通知回执：" + job["ack_path"] +
                 ("；原文件只读，必须复用其真实完成时间和字节哈希，禁止覆盖。\n" if job["ack_existing_sha256"]
                  else "；原文件不存在，须实际创建；完成确认写明本人已完成学习，完成时间写 YYYY-MM-DD HH:MM:SS +时区。\n"))
    return ("\n必须使用工具在当前工作目录实际创建两个普通文件，不能仅在最终聊天中展示报告：\n"
            "报告文件：" + job["report_path"] + "\n凭证文件：" + job["receipt_path"] + extra +
            "\n先创建父目录，再写报告，最后计算报告原始字节 SHA256 并写 JSON 凭证。"
            "凭证结构（替换中文占位符，checks 必须对应实际检查）：\n" + auto.encode(receipt) +
            "\n结束前用工具确认两个文件均存在、非软链接，并复算凭证中的报告哈希。"
            "若工具无法写文件，明确报告执行故障，不声称已完成。最终答复只简述已写路径。\n")


def stop_owned_process(process):
    """Kill only the fresh process group we created, including test servers."""
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.communicate(timeout=5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            process.poll()
        process.communicate()
    except ProcessLookupError:
        process.communicate()


def prepare_runtime():
    """Prepare browser/PDF tools only when a business review actually starts."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise auto.Failure("GITHUB_RUNNER_RUNTIME_REQUIRED")
    env = worker_environment()
    auto.command(["sudo", "-n", "apt-get", "update", "-qq"], env=env, timeout=600)
    auto.command(["sudo", "-n", "apt-get", "install", "-y", "-qq", "poppler-utils", "fonts-noto-cjk"],
                 env=env, timeout=600)
    auto.command(["npm", "install", "--global", "playwright@1.64.1"], env=env, timeout=600)
    auto.command([shutil.which("playwright") or "playwright", "install", "--with-deps", "chromium"],
                 env=env, timeout=900)
    root = auto.command(["npm", "root", "--global"], env=env).decode().strip()
    return {"NODE_PATH": root}


def prepare_notice_runtime():
    """Administrative reading needs PDF text extraction, not a browser stack."""
    if shutil.which("pdftotext"):
        return {}
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise auto.Failure("GITHUB_RUNNER_RUNTIME_REQUIRED")
    env = worker_environment()
    auto.command(["sudo", "-n", "apt-get", "update", "-qq"], env=env, timeout=600)
    auto.command(["sudo", "-n", "apt-get", "install", "-y", "-qq", "poppler-utils"], env=env, timeout=600)
    return {}


class ExecWorker:
    synchronous = True

    def __init__(self, cfg, cache, *, runner_id=None, prepare=None):
        self.cfg, self.cache = cfg, cache
        self.runner_id = runner_id or os.environ.get("GITHUB_RUN_ID", "")
        if not self.runner_id:
            raise auto.Failure("GITHUB_RUNNER_REQUIRED")
        self.prepare = prepare or prepare_runtime

    def preflight(self):
        # No model call for idle polling. Authentication/model entitlement is
        # proved by the real canary, never by this local status command alone.
        auto.command([self.cfg.codex_binary, "login", "status"], env=worker_environment())

    @staticmethod
    def failure_code(raw, events=None):
        events = event_diagnostics(io.BytesIO(raw)) if events is None else events
        if events.get("terminal_error"):
            return events["terminal_error"]["code"]
        if events.get("parsed_events"):
            return "EXEC_CLI_FAILED"
        return terminal_failure_code(raw)

    def submit(self, job):
        identity = self.runner_id + ":" + job["job_key"]
        if job.get("recovery_request_id"):
            identity += ":" + job["recovery_request_id"]
        task_id = "task_gha_" + auto.digest(identity.encode())[:32]
        if task_id in self.cache:
            return task_id
        self.diagnostics = {}
        try:
            result = self.execute(job)
            self.cache[task_id] = {"status": "completed", "patch": base64.b64encode(zlib.compress(result)).decode()}
        except auto.Failure as exc:
            self.cache[task_id] = {"status": "failed", "error": exc.code,
                                   "diagnostics": self.diagnostics}
            print(auto.encode({"worker_error": exc.code, "job_key": job["job_key"],
                               "diagnostics": self.diagnostics}), flush=True)
        return task_id

    def execute(self, job):
        env = worker_environment()
        if job.get("kind") in ("notice_ack", "delivery"):
            env.update(prepare_notice_runtime())
        elif not job["task_id"].startswith("WAKE-O-CANARY-"):
            env.update(self.prepare())
        with tempfile.TemporaryDirectory(prefix="exec-review-", dir=self.cfg.state_dir) as tmp:
            root, work = Path(tmp), Path(tmp) / "repo"
            # This preparation uses coordinator Git credentials. The model's
            # separate clone has no checkout@v4 credential extraheader.
            auto.command(["git", "clone", "--quiet", "--no-checkout", self.cfg.repo_url, str(work)])
            # Ambient core.autocrlf=true (common on Windows/WSL) smudges LF→CRLF after
            # add and poisons the publish patch vs in-memory receipt hashes.
            auto.command(["git", "config", "core.autocrlf", "false"], cwd=work)
            auto.command(["git", "checkout", "--quiet", "--detach", job["dispatch_sha"]], cwd=work)
            baseline = auto.command(["git", "rev-parse", "HEAD"], cwd=work).decode().strip()
            instruction = (auto.prompt(job) + "\n执行器：GitHub Actions 临时云主机中的官方 codex exec。"
                           "按已授权任务直接执行，不请求人工继续；只写规定报告/receipt/证据。"
                           "不要读取认证文件或环境密钥；不要 git commit/push；不更改 Git 配置。"
                           "当前克隆已包含 dispatch_sha 和 source_sha 对象，可在本地归档读取。"
                           "运行环境版本以本机实测为准，任务要求版本不满足时在报告标注限制。\n")
            instruction += output_instruction(job)
            if job.get("recovery_request_id"):
                instruction += ("\n本次为已确认结束且未交付的原任务受控恢复；保留原 source_sha/job_key。"
                                "报告记录 recovery_request_id，不将前次执行过的动作冒充本次已完成检查。\n")
            argv = [self.cfg.codex_binary, "-a", "never", "-c", "sandbox_workspace_write.network_access=true",
                    "exec", "--sandbox", "workspace-write",
                    "--ephemeral", "--ignore-user-config", "--color", "never", "--json", "-"]
            with (root / "worker.log").open("w+b") as log:
                try:
                    process = subprocess.Popen(argv, cwd=work, env=env, stdin=subprocess.PIPE,
                                               stdout=log, stderr=log, start_new_session=True)
                except OSError:
                    raise auto.Failure("EXEC_UNAVAILABLE") from None
                try:
                    process.communicate(instruction.encode(), timeout=self.cfg.task_timeout)
                except subprocess.TimeoutExpired:
                    stop_owned_process(process)
                    self.diagnostics = {"artifacts": artifact_diagnostics(work, job),
                                        "events": event_diagnostics(log)}
                    raise auto.Failure("EXECUTION_TIMEOUT") from None
                self.diagnostics = {"artifacts": artifact_diagnostics(work, job),
                                    "events": event_diagnostics(log)}
                if process.returncode:
                    log.seek(0, 2)
                    log.seek(max(0, log.tell() - 65536))
                    raise auto.Failure(self.failure_code(log.read(), self.diagnostics["events"]))
            paths = set(auto.command(["git", "diff", "--name-only", "-z", baseline], cwd=work).split(b"\0"))
            paths.update(auto.command(["git", "ls-files", "--others", "--exclude-standard", "-z"], cwd=work).split(b"\0"))
            paths.discard(b"")
            try:
                names = {p.decode("utf-8") for p in paths}
                # Include explicitly declared .log evidence even when ignored.
                receipt = work / job["receipt_path"]
                if receipt.exists() and not receipt.is_symlink():
                    meta = json.loads(receipt.read_text(encoding="utf-8"))
                    for item in meta.get("evidence", []):
                        names.add(auto.path_ok(item.get("path"), job["evidence_prefix"]))
                names.update(auto.mandatory_paths(job))
            except (ValueError, UnicodeError, TypeError, AttributeError):
                raise auto.Failure("INVALID_RESULT_RECEIPT") from None
            if len(names) > 64:
                raise auto.Failure("INVALID_PATCH_PATHS")
            auto.Publisher(self.cfg, None).validate(work, job, sorted(names))
            auto.command(["git", "add", "--intent-to-add", "--force", "--", *sorted(names)], cwd=work)
            patch = auto.command(["git", "diff", "--no-ext-diff", "--no-textconv", "--binary", baseline], cwd=work)
            if not patch.startswith(b"diff --git ") or len(patch) > auto.MAX_PATCH:
                raise auto.Failure("INVALID_CLOUD_DIFF")
            return patch

    def status(self, task_id):
        if task_id not in self.cache:
            raise auto.Failure("EXEC_RESULT_NOT_AVAILABLE")
        return self.cache[task_id]["status"]

    def diff(self, task_id):
        try:
            row = self.cache[task_id]
            if row["status"] != "completed":
                raise ValueError()
            data = base64.b64decode(row["patch"], validate=True)
            decoder = zlib.decompressobj()
            patch = decoder.decompress(data, auto.MAX_PATCH + 1)
            if len(patch) > auto.MAX_PATCH or not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
                raise ValueError()
            return patch
        except (KeyError, ValueError, zlib.error):
            raise auto.Failure("INVALID_EXEC_RESULT_CACHE") from None
