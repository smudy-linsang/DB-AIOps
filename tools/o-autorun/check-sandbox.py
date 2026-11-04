#!/usr/bin/env python3
"""No model/auth call: prove the real CLI can write only inside its workspace."""
import os
import json
from pathlib import Path
import subprocess
import sys
import tempfile


PROBE = '''from pathlib import Path
import sys
import errno
Path("inside.txt").write_text("sandbox-probe")
assert Path("inside.txt").read_text() == "sandbox-probe"
try:
    Path(sys.argv[1]).write_text("must-be-denied")
except OSError as error:
    if error.errno not in (errno.EACCES, errno.EPERM, errno.EROFS):
        raise
    print("SANDBOX-WRITE-BOUNDARY-OK")
else:
    raise SystemExit("SANDBOX-OUTSIDE-WRITE-ALLOWED")
'''


def main():
    with tempfile.TemporaryDirectory(prefix="o-sandbox-check-", dir=os.environ.get("RUNNER_TEMP")) as tmp:
        root = Path(tmp)
        work = root / "workspace"
        work.mkdir()
        outside = root / "outside.txt"
        data = {}
        try:
            result = subprocess.run(
                ["codex", "-c", 'sandbox_mode="workspace-write"', "-c",
                 "sandbox_workspace_write.network_access=true", "sandbox", "--",
                 sys.executable, "-c", PROBE, str(outside)], cwd=work, timeout=45,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except subprocess.TimeoutExpired:
            data["reason"] = "TIMEOUT"
        except OSError:
            data["reason"] = "EXEC_UNAVAILABLE"
        else:
            data.update(inside_written=(work / "inside.txt").is_file(), outside_written=outside.exists(),
                        stdout_marker=result.stdout.strip() == b"SANDBOX-WRITE-BOUNDARY-OK")
            error = result.stderr.lower()
            if data["outside_written"]:
                data["reason"] = "OUTSIDE_WRITE_ALLOWED"
            elif result.returncode:
                data["reason"] = "USER_NAMESPACE_DENIED" if any(s in error for s in (
                    b"rtm_newaddr", b"unshare", b"creating new namespace", b"user namespace")) else "SANDBOX_START_FAILED"
            elif not data["inside_written"]:
                data["reason"] = "WORKSPACE_WRITE_FAILED"
            elif not data["stdout_marker"]:
                data["reason"] = "MARKER_MISSING"
            else:
                data["reason"] = "OK"
        if os.environ.get("RUNNER_TEMP"):
            (Path(os.environ["RUNNER_TEMP"]) / "o-sandbox-probe.json").write_text(json.dumps(data) + "\n")
        if data["reason"] != "OK":
            print("SANDBOX-PREFLIGHT-FAILED: " + data["reason"] + "; no model invoked", file=sys.stderr)
            raise SystemExit(1)
        print("SANDBOX-WRITE-BOUNDARY-OK: inside writable, outside denied; no model invoked")


if __name__ == "__main__":
    main()
