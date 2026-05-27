#!/bin/bash
# DB-AIOps 开发环境启动脚本 (macOS)

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$PROJECT_DIR"

echo "=== 启动基础设施服务 ==="
docker-compose -f docker-compose.dev.yml up -d

echo "=== 等待服务就绪 ==="
sleep 10

echo "=== 激活 Python 虚拟环境 ==="
source venv/bin/activate

echo "=== 启动 Django 后端 (端口 8000) ==="
python manage.py runserver 0.0.0.0:8000 &
DJANGO_PID=$!

echo "=== 启动前端开发服务器 (端口 3000) ==="
cd frontend && npm run dev &
FRONTEND_PID=$!

echo ""
echo "=== 开发环境已启动 ==="
echo "  后端 API:   http://localhost:8000"
echo "  前端页面:   http://localhost:3000"
echo "  Admin:      http://localhost:8000/admin"
echo ""
echo "按 Ctrl+C 停止所有服务"

trap "echo ''; echo '=== 停止服务 ==='; kill $DJANGO_PID $FRONTEND_PID 2>/dev/null; docker-compose -f docker-compose.dev.yml stop; echo '已停止'" EXIT
wait
