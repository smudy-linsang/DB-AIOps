# ===========================================
# DB-AIOps 开发环境 - 状态查看
# 用法: ./scripts/dev-status.ps1
# ===========================================

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  DB-AIOps 开发环境 - 服务状态" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# --- Docker 容器 ---
Write-Host "`n[Docker 容器]" -ForegroundColor Yellow
$containers = @(
    @{Name="dbmonitor-mysql";         Port="3306";  Desc="MySQL 8.0"},
    @{Name="dbmonitor-oracle";        Port="1521";  Desc="Oracle XE 21c"},
    @{Name="dbmonitor-timescaledb";   Port="5432";  Desc="TimescaleDB (PG16)"},
    @{Name="dbmonitor-postgres";      Port="5433";  Desc="PostgreSQL (元数据)"},
    @{Name="dbmonitor-redis";         Port="6379";  Desc="Redis 7"},
    @{Name="dbmonitor-elasticsearch"; Port="9200";  Desc="Elasticsearch 8.12"},
    @{Name="dbmonitor-web";           Port="8000";  Desc="Django Web (Docker)"},
    @{Name="dbmonitor-frontend";      Port="3000";  Desc="Frontend (Docker)"},
    @{Name="dbmonitor-collector";     Port="";      Desc="采集守护进程 (Docker)"},
    @{Name="gbase8a";                 Port="5258";  Desc="GBase 8A"}
)

foreach ($c in $containers) {
    $running = docker inspect -f "{{.State.Status}}" $c.Name 2>$null
    if ($running -eq "running") {
        $health = docker inspect -f "{{.State.Health.Status}}" $c.Name 2>$null
        $portStr = if ($c.Port) { ":$($c.Port)" } else { "" }
        if ($health -eq "healthy") {
            Write-Host "  OK   $($c.Desc)$portStr" -ForegroundColor Green
        } elseif ($health) {
            Write-Host "  WARN $($c.Desc)$portStr ($health)" -ForegroundColor Yellow
        } else {
            Write-Host "  OK   $($c.Desc)$portStr (no healthcheck)" -ForegroundColor Green
        }
    } elseif ($running) {
        Write-Host "  STOP $($c.Desc) ($running)" -ForegroundColor Red
    } else {
        Write-Host "  --   $($c.Desc) (不存在)" -ForegroundColor DarkGray
    }
}

# --- Django 本地进程 ---
Write-Host "`n[Django 本地进程]" -ForegroundColor Yellow

# runserver
$rs = Get-NetTCPConnection -LocalPort 8888 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($rs) {
    Write-Host "  OK   runserver (端口 8888) PID=$($rs.OwningProcess)" -ForegroundColor Green
} else {
    Write-Host "  STOP runserver (端口 8888)" -ForegroundColor Red
}

# start_monitor
$mon = Get-WmiObject Win32_Process -Filter "CommandLine LIKE '%start_monitor%'" -ErrorAction SilentlyContinue
if ($mon) {
    Write-Host "  OK   start_monitor PID=$($mon.ProcessId)" -ForegroundColor Green
} else {
    Write-Host "  STOP start_monitor" -ForegroundColor Red
}

# DM8
$dm = Get-Process -Name "dmserver" -ErrorAction SilentlyContinue
if ($dm) {
    Write-Host "  OK   DM8 dmserver PID=$($dm.Id)" -ForegroundColor Green
} else {
    Write-Host "  --   DM8 dmserver (未安装/未运行)" -ForegroundColor DarkGray
}

# --- 数据库连接快速测试 ---
Write-Host "`n[数据库连接测试]" -ForegroundColor Yellow
$testScript = @"
import os,django; os.environ['DJANGO_SETTINGS_MODULE']='dbmonitor.settings'; django.setup()
from monitor.models import DatabaseConfig
from monitor.checkers import CHECKER_MAP
for db in DatabaseConfig.objects.filter(is_active=True):
    checker_cls = CHECKER_MAP.get(db.db_type)
    if not checker_cls: continue
    try:
        checker = checker_cls(command_instance=None)
        conn = checker.get_connection(db)
        cur = conn.cursor() if hasattr(conn, 'cursor') else None
        if cur:
            cur.execute('SELECT 1')
            cur.close()
        conn.close()
        print(f'  OK   {db.name} ({db.db_type}:{db.port})')
    except Exception as e:
        print(f'  FAIL {db.name} ({db.db_type}:{db.port}) - {str(e)[:50]}')
"@
$testScript | python 2>$null

Write-Host "`n============================================" -ForegroundColor Cyan
