"""Read official CLI account limits without creating a thread or model turn.

The coordinator supplies its restored file profile through its existing CLI
wrapper. Only typed, allowlisted values leave this module; no account IDs,
credit IDs, balances, tokens, backend prose or raw errors are retained.
"""
import json
import math
import os
import selectors
import signal
import subprocess
import tempfile
import time

try:
    from . import o_autorun as auto
except ImportError:
    import o_autorun as auto


REACHED = {"rate_limit_reached", "workspace_owner_credits_depleted",
           "workspace_member_credits_depleted", "workspace_owner_usage_limit_reached",
           "workspace_member_usage_limit_reached"}


def safe_limits(raw):
    if not isinstance(raw, dict) or not isinstance(raw.get("rateLimits"), dict):
        raise auto.Failure("INVALID_LIMIT_RESPONSE")
    allowed = raw.get("ordinaryUsageAllowed")
    result = {"state": "read", "observed_at": int(time.time()),
              "source": "official_cli_account_rate_limits",
              "ordinary_usage_allowed": allowed if type(allowed) is bool else None,
              "buckets": []}
    snapshots = [raw["rateLimits"]]
    by_id = raw.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        snapshots += list(by_id.values())[:16]
    for item in snapshots:
        if not isinstance(item, dict):
            continue
        bucket = {"scope": "codex" if item.get("limitId") == "codex" else "other"}
        reached = item.get("rateLimitReachedType")
        if isinstance(reached, str) and reached in REACHED:
            bucket["reached_type"] = reached
        if type(item.get("spendControlReached")) is bool:
            bucket["spend_control_reached"] = item["spendControlReached"]
        for name in ("primary", "secondary"):
            window = item.get(name)
            if not isinstance(window, dict):
                continue
            safe = {}
            for source, target, maximum in (("usedPercent", "used_percent", 10000),
                                             ("windowDurationMins", "window_minutes", 525600),
                                             ("resetsAt", "resets_at", 4102444800)):
                value = window.get(source)
                if type(value) in (int, float) and 0 <= value <= maximum and math.isfinite(value):
                    safe[target] = value
            if "used_percent" in safe:
                bucket[name] = safe
        if bucket not in result["buckets"]:
            result["buckets"].append(bucket)
    return result


def read_limits(binary, *, env, timeout=30):
    """Use only initialize/initialized/account/rateLimits/read, then terminate."""
    process = None
    try:
        with tempfile.TemporaryFile() as errors, selectors.DefaultSelector() as poll:
            process = subprocess.Popen([binary, "app-server", "--listen", "stdio://"],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=errors,
                                       env=env, start_new_session=True)
            poll.register(process.stdout, selectors.EVENT_READ)

            def send(obj):
                process.stdin.write((json.dumps(obj) + "\n").encode())
                process.stdin.flush()

            send({"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "o_autorun_limits_readonly", "version": "1"},
                "capabilities": {"explicitGatewayOauth": True}}})
            deadline, buffer, total = time.monotonic() + timeout, b"", 0
            while time.monotonic() < deadline:
                ready = poll.select(min(1, max(0, deadline - time.monotonic())))
                if not ready:
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise auto.Failure("LIMIT_QUERY_CLI_EXITED")
                total += len(chunk)
                if total > 2 * 1024 * 1024:
                    raise auto.Failure("LIMIT_RESPONSE_LIMIT")
                buffer += chunk
                while b"\n" in buffer:
                    line, buffer = buffer.split(b"\n", 1)
                    try:
                        message = json.loads(line)
                    except (ValueError, UnicodeError):
                        continue
                    if not isinstance(message, dict) or message.get("id") not in (1, 2):
                        continue
                    if "error" in message:
                        error = message["error"]
                        if isinstance(error, dict) and error.get("code") == -32601:
                            raise auto.Failure("LIMIT_METHOD_UNSUPPORTED")
                        code = auto.classify_error(json.dumps(error).encode())
                        raise auto.Failure(code if code in ("AUTH", "QUOTA", "NETWORK")
                                           else "LIMIT_QUERY_REJECTED")
                    if message["id"] == 1:
                        send({"method": "initialized"})
                        send({"id": 2, "method": "account/rateLimits/read"})
                    else:
                        return safe_limits(message.get("result"))
            raise auto.Failure("LIMIT_QUERY_TIMEOUT")
    except auto.Failure as exc:
        return {"state": "unavailable", "observed_at": int(time.time()), "error_code": exc.code}
    except (OSError, ValueError):
        return {"state": "unavailable", "observed_at": int(time.time()),
                "error_code": "LIMIT_QUERY_TRANSPORT"}
    finally:
        if process:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            except ProcessLookupError:
                process.wait()
            for pipe in (process.stdin, process.stdout):
                if pipe:
                    pipe.close()
