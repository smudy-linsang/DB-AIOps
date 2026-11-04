#!/usr/bin/env python3
"""Account-owner one-time setup. Run on your own computer, not in a cloud worker.

Uses official Codex/gh logins. Credentials go from the owner's local file to
GitHub's encrypted Secrets via stdin; they are never printed or committed.
"""
import argparse
import base64
import contextlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import time


class SetupError(Exception):
    def __init__(self, message, code="COMMAND_FAILED"):
        super().__init__(message)
        self.code = code


def launch_args(argv, platform=None):
    if (platform or os.name) != "nt":
        return argv
    binary = shutil.which(argv[0])
    if not binary:
        raise SetupError("Required CLI is unavailable: " + argv[0])
    if argv[0] == "codex" and Path(binary).suffix.lower() in (".cmd", ".bat"):
        # npm's Windows batch shim is unsafe for forwarding TOML quotes. Invoke
        # its official JavaScript entry using node.exe, without cmd.exe parsing.
        entry = Path(binary).parent / "node_modules/@openai/codex/bin/codex.js"
        node = shutil.which("node")
        if not node or not entry.is_file():
            raise SetupError("Install the official Codex npm package and Node.js in this Windows account")
        return [node, str(entry), *argv[1:]]
    return [binary, *argv[1:]]


def cmd(argv, data=None, env=None, cwd=None):
    try:
        r = subprocess.run(launch_args(argv), input=data, env=env, cwd=cwd,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
    except subprocess.TimeoutExpired:
        raise SetupError("CLI request timed out; authorization was not changed", "NETWORK") from None
    except OSError:
        raise SetupError("Required CLI is unavailable", "CLI_UNAVAILABLE") from None
    if r.returncode:
        error = r.stderr.decode("utf-8", "replace").lower()
        if "no cloud environments are available" in error:
            raise SetupError("CLI environment catalog has no visible environments", "ENV_NOT_VISIBLE")
        if "environment" in error and ("not found" in error or "ambiguous" in error):
            raise SetupError("CLI could not resolve the selected environment", "ENV_NOT_VISIBLE")
        if any(s in error for s in ("401", "unauthorized", "not signed in", "not logged in", "authentication token")):
            raise SetupError("Cloud authentication was rejected", "AUTH")
        if any(s in error for s in ("certificate", "tls")):
            raise SetupError("CLI certificate verification failed; keep TLS verification enabled", "TLS")
        if any(s in error for s in ("connection", "network", "resolve host", "timed out")):
            raise SetupError("CLI network request failed", "NETWORK")
        # CLI stderr may contain credential material; only known classifications
        # above and a safe command prefix leave this function.
        raise SetupError("Command failed: " + " ".join(argv[:3]) + " (exit=%d)" % r.returncode)
    return r.stdout


@contextlib.contextmanager
def cloud_repo_context(repo):
    """Supply the real repository identity for CLI's official by-repo lookup.

    No clone or checkout is required and no remote writes take place. The CLI
    queries /environments/by-repo using Git origins before the global catalog.
    """
    if not shutil.which("git"):
        yield None
        return
    with tempfile.TemporaryDirectory(prefix="o-cloud-repo-") as directory:
        cmd(["git", "init", "--quiet", directory])
        cmd(["git", "-C", directory, "remote", "add", "origin", "https://github.com/" + repo + ".git"])
        yield directory


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", default="smudy-linsang/Xinchuang_HW_QuickSizer")
    p.add_argument("--environment", default="Xinchuang_HW_QuickSizer")
    p.add_argument("--auth-file", type=Path)
    p.add_argument("--login", action="store_true", help="use official interactive logins when needed")
    p.add_argument("--no-canary", action="store_true", help="configure only, do not launch the real canary")
    p.add_argument("--check-only", action="store_true", help="check executor prerequisites without changing Secrets or creating tasks")
    p.add_argument("--executor", choices=("runner", "cloud"), default="runner",
                   help="runner uses official codex exec on GitHub; cloud uses the native cloud environment catalog")
    args = p.parse_args()
    if os.environ.get("CODEX_ENVIRONMENT_ID") or os.environ.get("CODEX_CI"):
        raise SetupError("Account-owner setup must run on your own computer, not an injected cloud identity")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        raise SetupError("Invalid GitHub repository")
    args.auth_file = args.auth_file or Path.home() / (".codex-o-autorun" if args.login else ".codex") / "auth.json"
    try:
        cmd(["gh", "auth", "status"])
    except SetupError:
        if not args.login or subprocess.call(launch_args(["gh", "auth", "login"])):
            raise SetupError("Official GitHub login is required") from None
    if args.auth_file.is_symlink() or args.auth_file.name != "auth.json":
        raise SetupError("Expected an owner-controlled auth.json file authentication profile")
    cli_env = dict(os.environ, CODEX_HOME=str(args.auth_file.resolve().parent))
    cloud_command = ["codex", "-c", 'cli_auth_credentials_store="file"']
    cloud_command += (["cloud", "list", "--limit", "1", "--json"] if args.executor == "cloud"
                      else ["login", "status"])
    try:
        if not args.auth_file.is_file():
            raise SetupError("No file authentication profile", "NO_PROFILE")
        cmd(cloud_command, env=cli_env)
    except SetupError as exc:
        if exc.code not in ("AUTH", "NO_PROFILE"):
            raise
        if not args.login:
            raise SetupError("Codex login is not available. Rerun with --login for official account-owner authorization.") from None
        args.auth_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        rc = subprocess.call(launch_args(["codex", "-c", 'cli_auth_credentials_store="file"', "login"]), env=cli_env)
        if rc:
            raise SetupError("Official Codex login did not complete")
        cmd(cloud_command, env=cli_env)
    if args.executor == "cloud":
        print("Cloud authentication verified.")
        with cloud_repo_context(args.repo) as directory:
            try:
                cmd(cloud_command + ["--env", args.environment], env=cli_env, cwd=directory)
            except SetupError as exc:
                if exc.code != "ENV_NOT_VISIBLE":
                    raise
                context = "verified repository origin" if directory else "unavailable (Git is not installed)"
                raise SetupError("ENV_NOT_VISIBLE: login works, but CLI cannot see the selected environment; "
                                 "repository context=" + context + ". No Secrets changed. "
                                 "A working web environment may still exist; do not recreate it or repeat login.",
                                 "ENV_NOT_VISIBLE") from None
        print("Repository environment access verified.")
    else:
        print("Local Codex login is available. Executor: GitHub runner / official codex exec.")
        print("Model access and report delivery will be verified by the real hosted canary.")
    if args.check_only:
        return
    data = args.auth_file.read_bytes()
    try:
        profile = json.loads(data)
        tokens = profile["tokens"]
        if (not isinstance(tokens, dict) or not tokens.get("access_token") or not tokens.get("refresh_token")
                or profile.get("OPENAI_API_KEY")
                or str(profile.get("auth_mode", "")).lower().replace("_", "") == "apikey"):
            raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise SetupError("A ChatGPT OAuth file profile is required for this subscription-based executor") from None
    names = {row["name"] for row in json.loads(cmd(["gh", "secret", "list", "--repo", args.repo, "--json", "name"]))}
    if "O_AUTORUN_STATE_KEY" not in names:
        key = base64.b64encode(secrets.token_bytes(32))
        cmd(["gh", "secret", "set", "O_AUTORUN_STATE_KEY", "--repo", args.repo], data=key)
    cmd(["gh", "secret", "set", "O_CODEX_AUTH_JSON", "--repo", args.repo], data=data)
    cmd(["gh", "variable", "set", "O_CLOUD_ENV_ID", "--repo", args.repo, "--body", args.environment])
    cmd(["gh", "variable", "set", "O_AUTORUN_EXECUTOR", "--repo", args.repo, "--body", args.executor])
    print("Owner authorization and encrypted-state key configured. No credential values were printed.")
    if args.no_canary:
        return
    before = time.time()
    cmd(["gh", "workflow", "run", "o-autorun.yml", "--repo", args.repo, "--ref", "main",
         "-f", "canary=true", "-f", "executor=" + args.executor])
    run_id = None
    from datetime import datetime
    for _ in range(20):
        runs = json.loads(cmd(["gh", "run", "list", "--repo", args.repo, "--workflow", "o-autorun.yml",
                              "--event", "workflow_dispatch", "--limit", "5", "--json", "databaseId,createdAt"]))
        candidates = [r for r in runs if datetime.fromisoformat(r["createdAt"].replace("Z", "+00:00")).timestamp() >= before - 2]
        if candidates:
            run_id = max(candidates, key=lambda r: r["databaseId"])["databaseId"]
            break
        time.sleep(3)
    if run_id is None:
        raise SetupError("Canary dispatch accepted; open GitHub Actions to track the new run")
    print("Real canary run: https://github.com/%s/actions/runs/%s" % (args.repo, run_id))
    # The owner terminal may wait; the automation itself runs on hosted runners.
    rc = subprocess.call(launch_args(["gh", "run", "watch", str(run_id), "--repo", args.repo, "--exit-status"]))
    if rc:
        raise SetupError("Canary did not pass; its GitHub Actions run records the blocker")
    execution = "official codex exec on GitHub runner" if args.executor == "runner" else "native Codex Cloud"
    print("Real hosted canary passed: order -> " + execution + " -> validated main-branch report.")


if __name__ == "__main__":
    try:
        main()
    except SetupError as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
    except OSError:
        print("Required local CLI or owner profile is unavailable; install Python, gh and the official Codex CLI.", file=sys.stderr)
        sys.exit(1)
