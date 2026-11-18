#!/usr/bin/env bash
# Isolated cloud development only. No production credentials or target databases.
set -euo pipefail
cd "$(dirname "$0")/.."
RUNTIME_DIR=/workspace/dbaiops-runtime
mkdir -p "$RUNTIME_DIR"
case "${1:-check}" in
  install)
    python -m venv .venv
    UV_CACHE_DIR=/workspace/.cache/uv uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
    (cd frontend && npm ci --cache /workspace/.cache/npm && npm run build)
    python - <<'PY'
from pathlib import Path
import secrets
root = Path('/workspace/dbaiops-runtime')
p = root / 'postgres.env'
if not p.exists():
    p.write_text('POSTGRES_USER=dbaiops\nPOSTGRES_DB=db_monitor\nPOSTGRES_PASSWORD=' + secrets.token_urlsafe(32) + '\n')
    p.chmod(0o600)
p = root / 'runtime.env'
if not p.exists():
    p.write_text((root / 'postgres.env').read_text() +
                 'POSTGRES_HOST=127.0.0.1\nPOSTGRES_PORT=55432\nES_ENABLED=False\nTIMESCALEDB_ENABLED=False\n'
                 'LLM_ENABLED=False\nAGENT_ENABLED=False\nEMBED_ENABLED=False\nREADINESS_REQUIRE_WORKERS=False\n'
                 'DJANGO_SECRET_KEY=' + secrets.token_urlsafe(64) + '\nDB_MONITOR_SECRET_KEY=' + secrets.token_urlsafe(40) + '\n')
    p.chmod(0o600)
PY
    bash scripts/cloud-environment.sh database
    ;;
  database)
    if docker container inspect dbaiops-dev-postgres >/dev/null 2>&1; then
      docker start dbaiops-dev-postgres >/dev/null
    else
      docker run -d --name dbaiops-dev-postgres --env-file "$RUNTIME_DIR/postgres.env" \
        -p 127.0.0.1:55432:5432 postgres:17@sha256:0d938e975177bef9f1f61f3f9dbcf2e0aa0a139568f66d3415ed8892eb97f6ac >/dev/null
    fi
    ready=0
    for attempt in {1..30}; do
      if docker exec dbaiops-dev-postgres pg_isready -U dbaiops -d db_monitor >/dev/null 2>&1; then ready=1; break; fi
      sleep 1
    done
    [ "$ready" = 1 ] || { echo 'Development PostgreSQL did not become ready' >&2; exit 1; }
    set -a
    source "$RUNTIME_DIR/runtime.env"
    set +a
    .venv/bin/python manage.py migrate --noinput
    .venv/bin/python manage.py check
    ;;
  serve)
    bash scripts/cloud-environment.sh database
    set -a
    source "$RUNTIME_DIR/runtime.env"
    set +a
    exec .venv/bin/python manage.py runserver 0.0.0.0:8010 --noreload
    ;;
  check)
    set -a
    source "$RUNTIME_DIR/runtime.env"
    set +a
    .venv/bin/python manage.py check
    curl --fail --silent http://127.0.0.1:8010/api/v1/health/ >/dev/null
    echo 'Django checks and HTTP readiness passed'
    ;;
  *) echo 'Usage: bash scripts/cloud-environment.sh install|database|serve|check' >&2; exit 2 ;;
esac
