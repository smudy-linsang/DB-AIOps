#!/usr/bin/env python3
"""Resumable GitHub coordinator; no daemon or plaintext credential Git commits.

Secrets enter only through the Actions environment. The state branch holds an
AES-GCM authenticated bundle (queue + refreshed OAuth profile) and safe status.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import sys
import tempfile
import time

try:
    from . import o_autorun as auto
except ImportError:
    import o_autorun as auto

STATE_BRANCH = "o-autorun-state"
MAGIC = b"O-AUTORUN-STATE-1\n"


class CheckpointAbort(BaseException):
    """Must escape dispatcher retries: never submit after durable intent failed."""


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_bytes(data)
    temp.chmod(0o600)
    temp.replace(path)


def auth_profile(raw):
    try:
        obj = json.loads(raw)
        tokens = obj["tokens"]
        if (not isinstance(tokens, dict) or not tokens.get("access_token")
                or not tokens.get("refresh_token") or obj.get("OPENAI_API_KEY")
                or str(obj.get("auth_mode", "")).lower().replace("_", "") == "apikey"):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise auto.Failure("CHATGPT_OAUTH_PROFILE_REQUIRED") from None
    return obj


def aead(key):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise auto.Failure("INSTALL_CI_REQUIREMENTS") from None
    if len(key) != 32:
        raise auto.Failure("STATE_KEY_MUST_BE_32_BYTES")
    return AESGCM(key)


class StateBranch:
    def __init__(self, repo, directory, key, initial_auth, auth_file):
        self.repo = Path(repo).resolve()
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.key, self.auth_file = key, Path(auth_file)
        aead(key)
        self.initial = auth_profile(initial_auth)
        self.bootstrap_hash = auto.digest(auto.encode(self.initial).encode())
        self.parent = None
        self.bootstrap_changed = False
        self.previous_fingerprint = None
        self.executor = "cloud"
        self.worker_cache = {}
        self.host_preflight = {}

    def git(self, *args, **kwargs):
        return auto.command(["git", "-C", str(self.repo), *args], **kwargs)

    def aad(self):
        # Repository remote is authenticated by Git; do not store its credentials.
        return MAGIC + STATE_BRANCH.encode()

    @staticmethod
    def fingerprint(db, profile, bootstrap_hash, executor="cloud", worker_cache=None, host_preflight=None):
        # Scanning an idle inbox only changes its observation timestamp. Avoid
        # committing a fresh randomly encrypted copy every five minutes forever.
        contents = {"auth": profile, "bootstrap_hash": bootstrap_hash,
                    "executor": executor, "worker_cache": worker_cache or {},
                    "host_preflight": host_preflight or {}}
        for table, order in (("jobs", "key"), ("inbox", "path"), ("events", "seq")):
            rows = [dict(row) for row in db.execute("SELECT * FROM " + table + " ORDER BY " + order)]
            if table == "inbox":
                for row in rows:
                    row.pop("updated", None)
            contents[table] = rows
        return auto.digest(auto.encode(contents).encode())

    def restore(self):
        refs = self.git("ls-remote", "--heads", "origin", "refs/heads/" + STATE_BRANCH).decode().strip()
        profile = self.initial
        if refs:
            self.parent = refs.split()[0]
            self.git("fetch", "--quiet", "origin", self.parent)
            raw = self.git("show", self.parent + ":state.bundle")
            if not raw.startswith(MAGIC) or len(raw) < len(MAGIC) + 28:
                raise auto.Failure("INVALID_ENCRYPTED_STATE")
            nonce = raw[len(MAGIC):len(MAGIC) + 12]
            try:
                payload = aead(self.key).decrypt(nonce, raw[len(MAGIC) + 12:], self.aad())
                state = json.loads(payload)
                if state["format"] != 1:
                    raise ValueError()
                self.executor = state.get("executor", "cloud")
                self.worker_cache = state.get("worker_cache", {})
                self.host_preflight = state.get("host_preflight", {})
                if self.executor not in ("cloud", "runner") or not isinstance(self.worker_cache, dict):
                    raise ValueError()
                database = base64.b64decode(state["sqlite"], validate=True)
                if not database.startswith(b"SQLite format 3\0"):
                    raise ValueError()
                if state["bootstrap_hash"] == self.bootstrap_hash:
                    profile = auth_profile(auto.encode(state["auth"]))
                else:
                    self.bootstrap_changed = True
            except Exception:
                raise auto.Failure("STATE_DECRYPTION_FAILED") from None
            atomic(self.directory / "queue.sqlite3", database)
            db = sqlite3.connect(self.directory / "queue.sqlite3")
            db.row_factory = sqlite3.Row
            try:
                self.previous_fingerprint = self.fingerprint(db, state["auth"], state["bootstrap_hash"],
                                                             self.executor, self.worker_cache, self.host_preflight)
            finally:
                db.close()
        atomic(self.auth_file, auto.encode(profile).encode())

    def checkpoint(self, store):
        current_auth = auth_profile(self.auth_file.read_bytes())
        fingerprint = self.fingerprint(store.db, current_auth, self.bootstrap_hash, self.executor,
                                       self.worker_cache, self.host_preflight)
        if self.parent and fingerprint == self.previous_fingerprint:
            return
        with tempfile.TemporaryDirectory(dir=self.directory) as tmp:
            snapshot = Path(tmp) / "queue.sqlite3"
            dest = sqlite3.connect(snapshot)
            try:
                store.db.backup(dest)
            finally:
                dest.close()
            state = {"format": 1, "bootstrap_hash": self.bootstrap_hash,
                     "executor": self.executor, "worker_cache": self.worker_cache,
                     "host_preflight": self.host_preflight,
                     "auth": current_auth, "sqlite": base64.b64encode(snapshot.read_bytes()).decode()}
        nonce = secrets.token_bytes(12)
        encrypted = MAGIC + nonce + aead(self.key).encrypt(nonce, auto.encode(state).encode(), self.aad())
        # No prompts, credentials, auth hashes or raw CLI errors in public status.
        status = {"schema_version": 1, "updated_at": time.time(), "executor": self.executor,
                  "host_preflight": self.host_preflight,
                  "recovery_requests": [v for k, v in sorted(self.worker_cache.items())
                                        if k.startswith("recovery:") and v.get("kind") == "review_recovery"],
                  "inbox": [{"manifest_path": r["path"], "state": r["state"],
                             "reason_code": (r["reason"] or "").split(":", 1)[0]}
                            for r in store.db.execute("SELECT path,state,reason FROM inbox ORDER BY path")],
                  "jobs": [
            {"job_key": row["key"], "state": row["state"], "cloud_id": row["cloud_id"],
             "error_code": (row["error"] or "").split(":", 1)[0],
             "published_sha": row["published_sha"],
             "worker_error": self.worker_cache.get(row["cloud_id"], {}).get("error"),
             "worker_diagnostics": self.worker_cache.get(row["cloud_id"], {}).get("diagnostics", {})}
            for row in store.rows()]}
        entries = []
        for path, value in (("state.bundle", encrypted), ("status.json", auto.encode(status).encode())):
            blob = self.git("hash-object", "-w", "--stdin", data=value).decode().strip()
            entries.append("100644 blob %s\t%s\n" % (blob, path))
        tree = self.git("mktree", data="".join(entries).encode()).decode().strip()
        args = ["-c", "user.name=O automation", "-c", "user.email=o-automation@users.noreply.github.com",
                "commit-tree", tree]
        if self.parent:
            args += ["-p", self.parent]
        commit = self.git(*args, data=b"O automation encrypted checkpoint\n").decode().strip()
        self.git("push", "--quiet", "origin", commit + ":refs/heads/" + STATE_BRANCH)
        self.parent = commit
        self.previous_fingerprint = fingerprint


def git_auth(directory):
    """Use the Actions short-lived token via askpass; never copy it to the bundle."""
    if not os.environ.get("GITHUB_TOKEN"):
        return
    askpass = Path(directory) / "git-askpass.py"
    source = ("#!" + sys.executable + "\nimport os,sys\n"
              "print('x-access-token' if 'username' in sys.argv[1].lower() else os.environ['GITHUB_TOKEN'])\n")
    atomic(askpass, source.encode())
    askpass.chmod(0o700)
    os.environ["GIT_ASKPASS"] = str(askpass)
    os.environ["GIT_TERMINAL_PROMPT"] = "0"


def canary(repo):
    """A distinct infrastructure namespace, not a business T-number allocation."""
    task = "WAKE-O-CANARY-" + str(time.time_ns())
    repo = Path(repo)
    source = auto.command(["git", "rev-parse", "HEAD"], cwd=repo).decode().strip()
    order = "docs/pm/orders/" + task + "-read-and-report.md"
    manifest = auto.DISPATCH + task + ".json"
    text = ("# O 自动接入金丝雀\n\n执行：O\n\n"
            "仅验证自动领单/执行/回写。阅读本单和协作 README，按 O-AUTO 提示词写报告及 receipt，"
            "记录完整被审 SHA 和 job_key。checks 至少含 read-order=PASS。"
            "不用跑业务全量测试，不修改产品或测试，不直接推送，不访问 9100。\n")
    for name, content in ((order, text), (manifest, auto.encode({
            "schema_version": 1, "task_id": task, "round": 1, "member": "O",
            "order_path": order, "source_sha": source, "requires": []}))):
        p = repo / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    auto.command(["git", "add", "--", order, manifest], cwd=repo)
    auto.command(["git", "-c", "user.name=O automation", "-c", "user.email=o-automation@users.noreply.github.com",
                  "commit", "--quiet", "-m", "docs(WAKE): O real cloud canary"], cwd=repo)
    auto.command(["git", "push", "--quiet", "origin", "HEAD:refs/heads/main"], cwd=repo)
    return task


def diagnose_blocked_limits(dispatcher, state):
    """One read-only account check per relevant failed execution; never retry it."""
    try:
        from .o_usage_diagnostics import read_limits
        from .o_exec_worker import worker_environment
    except ImportError:
        from o_usage_diagnostics import read_limits
        from o_exec_worker import worker_environment
    if state.executor != "runner":
        return
    for row in dispatcher.store.rows():
        if row["state"] != "blocked" or not row["cloud_id"]:
            continue
        cached = state.worker_cache.get(row["cloud_id"], {})
        diagnostics = cached.get("diagnostics", {})
        events = diagnostics.get("events", {})
        if cached.get("error") != "QUOTA" and not (
                cached.get("error") == "EXEC_CLI_FAILED" and events.get("turn_failed")
                and "QUOTA" in events.get("signals", [])):
            continue
        if "account_limits" not in diagnostics:
            diagnostics["account_limits"] = read_limits(dispatcher.cfg.codex_binary,
                                                        env=worker_environment())
            cached["diagnostics"] = diagnostics


def apply_recovery_request(repo, dispatcher, state):
    """Consume one committed request only for an ended, failed, unpublished review."""
    path = "tools/o-autorun/recovery-request.json"
    entry = auto.command(["git", "ls-tree", "-z", "HEAD", "--", path], cwd=repo)
    if not entry:
        return
    raw = auto.command(["git", "show", "HEAD:" + path], cwd=repo)
    try:
        request = json.loads(raw)
        if (not entry.startswith(b"100644 ") or len(raw) > 4096 or not isinstance(request, dict)
                or set(request) != {"schema_version", "request_id", "job_key", "expected_cloud_id", "action"}
                or request["schema_version"] != 1 or request["action"] != "retry_confirmed_failed"
                or not isinstance(request["request_id"], str)
                or not re.fullmatch(r"T-[0-9]{3,}-[0-9]{8}-R[0-9]+-restore-[0-9]+", request["request_id"])
                or not isinstance(request["job_key"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", request["job_key"])
                or not isinstance(request["expected_cloud_id"], str)
                or not re.fullmatch(r"task_gha_[0-9a-f]{32}", request["expected_cloud_id"])):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise auto.Failure("INVALID_RECOVERY_REQUEST") from None
    marker = "recovery:" + request["request_id"]
    with dispatcher.store.lock():
        if marker in state.worker_cache:
            return
        rows = dispatcher.store.rows()
        record = {"kind": "review_recovery", "request_id": request["request_id"],
                  "job_key": request["job_key"], "previous_cloud_id": request["expected_cloud_id"],
                  "observed_at": int(time.time()), "state": "rejected"}
        try:
            row = next((r for r in rows if r["key"] == request["job_key"]), None)
            if (state.executor != "runner" or row is None or row["state"] != "blocked"
                    or row["cloud_id"] != request["expected_cloud_id"] or not row["cloud_done"]
                    or row["published_sha"]):
                raise auto.Failure("RECOVERY_NOT_CONFIRMED_FAILED")
            if any(r["state"] in ("running", "publishing", "submitting", "submission_unknown")
                   or (r["state"] == "blocked" and r["cloud_id"] and not r["cloud_done"]) for r in rows):
                raise auto.Failure("RECOVERY_ACTIVE_EXECUTION_EXISTS")
            previous = state.worker_cache.get(row["cloud_id"], {})
            if previous.get("status") != "failed":
                raise auto.Failure("RECOVERY_CACHE_NOT_FAILED")
            record.update(previous_worker_error=previous.get("error"),
                          previous_diagnostics=previous.get("diagnostics", {}))
            job = json.loads(row["payload"])
            if job.get("kind", "review") != "review" or "deliverables" in job:
                raise auto.Failure("RECOVERY_KIND_UNSUPPORTED")
            dispatcher.board.refresh()
            current = auto.resolve(dispatcher.board, job["manifest_path"])
            if current["job_key"] != row["key"]:
                raise auto.Failure("RECOVERY_SOURCE_CHANGED")
            if dispatcher.publisher.existing(job):
                raise auto.Failure("RECOVERY_ALREADY_DELIVERED")
            for output in (job["report_path"], job["receipt_path"]):
                try:
                    dispatcher.board.read(output)
                except auto.Waiting as exc:
                    if exc.code != "MISSING_FILE":
                        raise
                else:
                    raise auto.Failure("RECOVERY_ARTIFACT_COLLISION")
            try:
                from .o_usage_diagnostics import read_limits
                from .o_exec_worker import worker_environment
            except ImportError:
                from o_usage_diagnostics import read_limits
                from o_exec_worker import worker_environment
            limits = read_limits(dispatcher.cfg.codex_binary, env=worker_environment())
            record["account_limits"] = limits
            if limits.get("state") != "read" or limits.get("ordinary_usage_allowed") is not True:
                raise auto.Failure("RECOVERY_USAGE_NOT_ALLOWED")
            job["recovery_request_id"] = request["request_id"]
            with dispatcher.store.db:
                dispatcher.store.db.execute("UPDATE jobs SET payload=? WHERE key=?",
                                            (auto.encode(job), row["key"]))
            dispatcher.store.set(row["key"], "retry", "RECOVERY_REQUEST:" + request["request_id"],
                                 cloud_id=None, cloud_done=0, attempts=0, next_at=0, submitted_at=None)
            record["state"] = "applied"
        except auto.Failure as exc:
            record["reason_code"] = exc.code
        state.worker_cache[marker] = record
        # No execution can start until the new queue state AND consumed request
        # have been durably checkpointed. Ambiguous checkpoint failures abort.
        state.checkpoint(dispatcher.store)


def run(repo, directory, key, initial_auth, environment, *, auth_file=None, cloud=None,
        canary_task=None, wait_seconds=0, executor="cloud"):
    if executor not in ("cloud", "runner"):
        raise auto.Failure("INVALID_EXECUTOR")
    auth_file = Path(auth_file) if auth_file else Path.home() / ".codex/auth.json"
    state = StateBranch(repo, directory, key, initial_auth, auth_file)
    state.restore()
    remote = auto.command(["git", "remote", "get-url", "origin"], cwd=repo).decode().strip()
    cli = state.directory / "codex-file-auth.sh"
    binary = shutil.which("codex") or "codex"
    import shlex
    atomic(cli, ("#!/bin/sh\nexec " + shlex.quote(binary) + " -c 'cli_auth_credentials_store=\"file\"' \"$@\"\n").encode())
    cli.chmod(0o700)
    cfg = auto.Config(remote, str(Path(directory).resolve()),
                      environment_id=environment if executor == "cloud" else "github-actions",
                      codex_binary=str(cli))
    previous_executor = state.executor
    if executor == "runner" and cloud is None:
        try:
            from .o_exec_worker import ExecWorker
        except ImportError:
            from o_exec_worker import ExecWorker
        cloud = ExecWorker(cfg, state.worker_cache)
    dispatcher = auto.Dispatcher(cfg, cloud=cloud)
    if previous_executor != executor and any(
            r["state"] in ("running", "publishing", "submitting", "submission_unknown")
            or (r["state"] == "blocked" and r["cloud_id"] and not r["cloud_done"])
            for r in dispatcher.store.rows()):
        dispatcher.store.db.close()
        raise auto.Failure("ACTIVE_TASK_EXECUTOR_CHANGE_FORBIDDEN")
    state.executor = executor
    if state.bootstrap_changed:
        for row in dispatcher.store.rows():
            if row["state"] == "blocked" and (row["error"] or "").split(":", 1)[0] in ("AUTH", "QUOTA"):
                dispatcher.store.set(row["key"], "running" if row["cloud_id"] else "queued",
                                     attempts=0, next_at=0,
                                     submitted_at=time.time() if row["cloud_id"] else None)
    # Prove access before creating a canary or turning inbox jobs into AUTH blockers.
    try:
        dispatcher.cloud.preflight()
    except auto.Failure:
        state.checkpoint(dispatcher.store)  # preserve any legitimate OAuth refresh
        dispatcher.store.db.close()
        raise
    diagnose_blocked_limits(dispatcher, state)
    state.checkpoint(dispatcher.store)
    if canary_task is True:
        canary_task = canary(repo)

    def durable_intent(key, transition):
        if transition in ("submitting", "running"):
            try:
                state.checkpoint(dispatcher.store)
            except Exception as exc:
                raise CheckpointAbort("DURABLE_CHECKPOINT_FAILED") from exc

    dispatcher.store.on_transition = durable_intent
    deadline = time.time() + wait_seconds
    try:
        apply_recovery_request(repo, dispatcher, state)
        while True:
            before = {r["key"]: r["state"] for r in dispatcher.store.rows()}
            dispatcher.tick()
            state.checkpoint(dispatcher.store)
            if executor == "runner":
                # exec is synchronous; deliver its persisted result in the same
                # runner. Do not start another queued review in this second tick.
                dispatcher.tick(monitor_only=True)
                for row in dispatcher.store.rows():
                    if row["state"] == "done":
                        state.worker_cache.pop(row["cloud_id"], None)
                diagnose_blocked_limits(dispatcher, state)
                state.checkpoint(dispatcher.store)
                if not canary_task and any(r["state"] == "blocked" and before.get(r["key"]) != "blocked"
                                           for r in dispatcher.store.rows()):
                    raise auto.Failure("REVIEW_EXECUTION_BLOCKED")
            if dispatcher.store.db.execute("SELECT 1 FROM inbox WHERE state='invalid' LIMIT 1").fetchone():
                raise auto.Failure("DISPATCH_INBOX_INVALID")
            if not canary_task:
                break
            matches = [r for r in dispatcher.store.rows()
                       if json.loads(r["payload"])["task_id"] == canary_task]
            if matches and matches[0]["state"] == "done":
                print(auto.encode({"canary": canary_task, "state": "done",
                                   "published_sha": matches[0]["published_sha"]}))
                break
            if matches and matches[0]["state"] in ("blocked", "submission_unknown"):
                raise auto.Failure("CANARY_BLOCKED")
            if time.time() >= deadline:
                raise auto.Failure("CANARY_PENDING", "scheduled dispatcher will continue tracking")
            time.sleep(20)
    finally:
        try:
            state.checkpoint(dispatcher.store)
        finally:
            dispatcher.store.db.close()


def main():
    os.umask(0o077)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", default=".")
    p.add_argument("--directory", required=True)
    p.add_argument("--environment", default="")
    p.add_argument("--canary", action="store_true")
    p.add_argument("--executor", choices=("runner", "cloud"), default="runner")
    p.add_argument("--record-host-preflight", choices=("success", "failure", "cancelled"))
    p.add_argument("--probe-file", type=Path)
    args = p.parse_args()
    initial = os.environ.get("O_CODEX_AUTH_JSON", "")
    key_string = os.environ.get("O_AUTORUN_STATE_KEY", "")
    if not initial or not key_string:
        raise auto.Failure("OWNER_AUTH_SETUP_REQUIRED")
    try:
        key = base64.b64decode(key_string, validate=True)
    except ValueError:
        raise auto.Failure("INVALID_STATE_KEY") from None
    args.directory = str(Path(args.directory).resolve())
    git_auth(args.directory)
    if args.record_host_preflight:
        state = StateBranch(args.repo, args.directory, key, initial, Path.home() / ".codex/auth.json")
        state.restore()
        state.host_preflight = {"state": args.record_host_preflight,
                                "source_sha": auto.command(["git", "rev-parse", "HEAD"], cwd=args.repo).decode().strip()}
        if args.probe_file and args.probe_file.is_file():
            probe = json.loads(args.probe_file.read_bytes())
            # Public telemetry only accepts these booleans and known reasons.
            reasons = {"OK", "USER_NAMESPACE_DENIED", "WORKSPACE_WRITE_FAILED", "OUTSIDE_WRITE_ALLOWED",
                       "SANDBOX_START_FAILED", "MARKER_MISSING", "TIMEOUT", "EXEC_UNAVAILABLE"}
            if isinstance(probe, dict) and probe.get("reason") in reasons:
                state.host_preflight["reason"] = probe["reason"]
                for name in ("inside_written", "outside_written", "stdout_marker"):
                    if type(probe.get(name)) is bool:
                        state.host_preflight[name] = probe[name]
        store = auto.Store(state.directory)
        try:
            state.checkpoint(store)
        finally:
            store.db.close()
        return
    run(args.repo, args.directory, key, initial, args.environment,
        canary_task=args.canary, wait_seconds=1200 if args.canary else 0, executor=args.executor)


if __name__ == "__main__":
    try:
        main()
    except CheckpointAbort:
        print(auto.encode({"error": "DURABLE_CHECKPOINT_FAILED"}), file=sys.stderr)
        sys.exit(1)
    except (auto.Failure, ValueError, KeyError, OSError) as exc:
        print(auto.encode({"error": exc.code if isinstance(exc, auto.Failure) else type(exc).__name__}), file=sys.stderr)
        sys.exit(1)
