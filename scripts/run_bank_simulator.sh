#!/bin/bash
# ============================================================
# 银行业务模拟器 - 守护脚本
# - PID 文件: logs/bank_simulator.pid
# - 日志:     logs/bank_simulator.log
# - 崩溃自动重启 (间隔 5s)
# - 用法:
#     scripts/run_bank_simulator.sh start   # 启动守护
#     scripts/run_bank_simulator.sh stop    # 优雅停止
#     scripts/run_bank_simulator.sh restart # 重启
#     scripts/run_bank_simulator.sh status  # 查看状态
# ============================================================
set -u

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR" || exit 1

PID_FILE="logs/bank_simulator.pid"
LOG_FILE="logs/bank_simulator.log"
DB_IDS="${BANK_SIM_DB_IDS:-1,2,3,4,6,7}"
RESTART_DELAY=5

mkdir -p logs

log() { echo "[$(date '+%F %T')] $*"; }

is_running() {
    [ -f "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null
}

do_stop() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            log "停止模拟器 (PID=$pid)..."
            kill "$pid"
            for i in $(seq 1 15); do
                kill -0 "$pid" 2>/dev/null || break
                sleep 1
            done
            if kill -0 "$pid" 2>/dev/null; then
                log "强制终止 PID=$pid"
                kill -9 "$pid" 2>/dev/null
            fi
        fi
        rm -f "$PID_FILE"
    fi
    # 兜底: 杀掉所有残留 worker
    pkill -f "run_bank_simulator" 2>/dev/null || true
}

do_start() {
    if is_running; then
        log "模拟器已在运行 (PID=$(cat "$PID_FILE"))"
        return 0
    fi

    # 守护循环: 崩溃后自动重启
    (
        trap 'exit 0' TERM INT
        while true; do
            log "启动模拟器 (db_ids=$DB_IDS)"
            ./venv/bin/python manage.py run_bank_simulator --db-ids "$DB_IDS" >> "$LOG_FILE" 2>&1
            rc=$?
            log "模拟器退出 (rc=$rc), ${RESTART_DELAY}s 后重启"
            sleep "$RESTART_DELAY"
        done
    ) &
    local daemon_pid=$!
    echo "$daemon_pid" > "$PID_FILE"
    disown "$daemon_pid" 2>/dev/null
    log "守护进程已启动 (PID=$daemon_pid), 日志: $LOG_FILE"
}

do_status() {
    if is_running; then
        local pid
        pid=$(cat "$PID_FILE")
        log "✅ 模拟器运行中 (守护PID=$pid)"
        echo "--- 日志末尾 ---"
        tail -5 "$LOG_FILE" 2>/dev/null
        echo "--- 各库最新进度 ---"
        grep -E "\] ok=" "$LOG_FILE" 2>/dev/null | tail -8
    else
        log "❌ 模拟器未运行"
        [ -f "$PID_FILE" ] && rm -f "$PID_FILE"
    fi
}

case "${1:-help}" in
    start)   do_start ;;
    stop)    do_stop ;;
    restart) do_stop; sleep 2; do_start ;;
    status)  do_status ;;
    *)
        echo "用法: $0 {start|stop|restart|status}"
        exit 1
        ;;
esac
