#!/usr/bin/env bash
# DB-AIOps 改动验证统一入口
#
# 用法: scripts/validate.sh [unit|backend|frontend|all] [--json]
#   unit     : 零外部依赖，SQLite 内存库，~10s —— 编辑循环里用这个
#   backend  : 完整后端（需 PostgreSQL），含迁移漂移检查
#   frontend : npm build (+ vitest)
#   all      : backend + frontend
# 退出码: 0=全部通过; 1=任一检查失败; 2=用法/环境错误
#
# 与 CI 的关系：本脚本与 .github/workflows/ci.yml 检查同一批内容，
# 本地先跑一遍可以避免把必然失败的 PR 推上去。CI 才是强制门禁。
#
# 注：用 bash 而非 zsh —— CI runner 与多数 Linux 开发机默认无 zsh。
set -uo pipefail

MODE="all"
JSON=0
for arg in "$@"; do
  case "$arg" in
    unit|backend|frontend|all) MODE="$arg" ;;
    --json) JSON=1 ;;
    -h|--help) echo "用法: $0 [unit|backend|frontend|all] [--json]"; exit 0 ;;
    *) echo "未知参数: $arg" >&2; echo "用法: $0 [unit|backend|frontend|all] [--json]" >&2; exit 2 ;;
  esac
done

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$PROJECT_DIR" || exit 2

# 虚拟环境：兼容 venv/ 与 .venv/ 两种惯例
for v in .venv venv; do
  [ -f "$v/bin/activate" ] && { . "$v/bin/activate"; break; }
done

STAGES=()   # 形如 "阶段名|状态|耗时ms"
FAIL=0

_now_ms() {  # 毫秒时间戳；BSD date（macOS）不支持 %N，回退到秒级
  local t
  t=$(date +%s%3N 2>/dev/null)
  if printf '%s' "$t" | grep -qE '^[0-9]+$'; then
    printf '%s' "$t"
  else
    printf '%s' $(( $(date +%s) * 1000 ))
  fi
}

_run() {   # _run <阶段名> <命令...>
  local name="$1"; shift
  local start end rc
  start=$(_now_ms)
  [ "$JSON" -eq 0 ] && echo "=== [$name] ==="
  if [ "$JSON" -eq 1 ]; then "$@" >/dev/null 2>&1; rc=$?; else "$@"; rc=$?; fi
  end=$(_now_ms)
  if [ $rc -eq 0 ]; then
    STAGES+=("$name|pass|$((end-start))")
  else
    STAGES+=("$name|fail|$((end-start))")
    FAIL=1
    [ "$JSON" -eq 0 ] && echo "FAIL: $name"
  fi
  return $rc
}

run_unit() {
  # 关键改动（对比 H 方案）：unit 层用独立 settings，走 SQLite 内存库，
  # 不需要 Docker、不需要 PostgreSQL。降低验证门槛才能真正提高验证率。
  DJANGO_SETTINGS_MODULE=dbmonitor.settings_test_unit \
    _run "unit 测试" python manage.py test monitor --tag unit -v 1
}

run_backend() {
  _run "语法编译"    python -m compileall -q monitor dbmonitor || return 1
  _run "Django 系统检查" python manage.py check                || return 1
  # 新增（H 方案缺失）：模型改了没生成 migration 是最高频的"本地能跑、部署即炸"
  _run "迁移漂移检查" python manage.py makemigrations --check --dry-run || return 1
  _run "依赖完整性"  python scripts/check_deps.py               || return 1
  # 改动（H 方案硬编码了测试模块清单，已漏掉 tests_phase7 等）：
  # 改为自动发现，新增测试文件无需改脚本
  _run "全量测试"    python manage.py test monitor -v 1         || return 1
  return 0
}

run_frontend() {
  ( cd frontend && { [ -d node_modules ] || npm ci; } && npm run build ) \
    && _run "前端构建" true || _run "前端构建" false
  if [ -f frontend/package.json ] && grep -q '"test"' frontend/package.json; then
    ( cd frontend && npm test -- --run ) && _run "前端测试" true || _run "前端测试" false
  fi
}

case "$MODE" in
  unit)     run_unit ;;
  backend)  run_backend ;;
  frontend) run_frontend ;;
  all)      run_backend; run_frontend ;;
esac

if [ "$JSON" -eq 1 ]; then
  printf '{"mode":"%s","exit_code":%d,"stages":[' "$MODE" "$FAIL"
  for i in "${!STAGES[@]}"; do
    IFS='|' read -r n s d <<< "${STAGES[$i]}"
    [ "$i" -gt 0 ] && printf ','
    printf '{"name":"%s","status":"%s","duration_ms":%s}' "$n" "$s" "$d"
  done
  printf '],"git_rev":"%s"}\n' "$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
else
  echo
  for st in "${STAGES[@]}"; do
    IFS='|' read -r n s d <<< "$st"
    printf '  %-16s %-4s %sms\n' "$n" "$s" "$d"
  done
  [ $FAIL -eq 0 ] && echo "===> 验证全部通过" || echo "===> 验证失败，禁止提交"
fi
exit $FAIL
