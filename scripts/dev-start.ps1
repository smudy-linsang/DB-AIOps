# ===========================================
# DB-AIOps 开发环境 - 一键启动脚本
# 用途: 电脑重启后运行，启动所有测试库+监控服务
# 用法: 右键"使用 PowerShell 运行" 或终端 ./scripts/dev-start.ps1
# ===========================================

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  DB-AIOps 开发环境 - 启动所有服务" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

# --- 前置检查: Docker ---
Write-Host "`n[前置] 检查 Docker..." -ForegroundColor Yellow
$dockerOk = $false
try {
    $null = docker info 2>&1
    if ($LASTEXITCODE -eq 0) { $dockerOk = $true }
} catch {}

if (-not $dockerOk) {
    Write-Host "  Docker 未运行! 正在启动 Docker Desktop..." -ForegroundColor Red
    Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe" -WindowStyle Normal
    Write-Host "  等待 Docker 就绪 (最多120秒)..." -ForegroundColor Yellow
    for ($i = 1; $i -le 24; $i++) {
        Start-Sleep -Seconds 5
        try {
            $null = docker info 2>&1
            if ($LASTEXITCODE -eq 0) {
                $dockerOk = $true
                Write-Host "  Docker 已就绪! (等待$($i*5)秒)" -ForegroundColor Green
                break
            }
        } catch {}
        Write-Host "  ... 等待中 ($($i*5)s)" -ForegroundColor DarkGray
    }
    if (-not $dockerOk) {
        Write-Host "  Docker 启动超时，请手动启动后重试" -ForegroundColor Red
        Read-Host "按回车退出"
        exit 1
    }
} else {
    Write-Host "  Docker 已运行" -ForegroundColor Green
}

# --- 1. 启动 Docker Compose 基础设施 ---
Write-Host "`n[1/5] 启动基础设施 (PostgreSQL/TimescaleDB/Redis/ES/Oracle/MySQL)..." -ForegroundColor Yellow
Push-Location C:\DB_Monitor
docker compose up -d timescaledb redis elasticsearch dbmonitor-postgres dbmonitor-oracle dbmonitor-mysql 2>$null
# 也尝试完整 compose (兼容有 mysql/oracle 在 compose 中的情况)
docker compose up -d 2>$null
Pop-Location

# 等待健康检查
Write-Host "  等待基础设施健康检查..." -ForegroundColor Yellow
$services = @("dbmonitor-timescaledb", "dbmonitor-redis", "dbmonitor-elasticsearch", "dbmonitor-mysql", "dbmonitor-oracle")
$maxWait = 90
$elapsed = 0
while ($elapsed -lt $maxWait) {
    $allHealthy = $true
    foreach ($svc in $services) {
        $status = docker inspect -f "{{.State.Health.Status}}" $svc 2>$null
        if ($status -ne "healthy") {
            $allHealthy = $false
            break
        }
    }
    if ($allHealthy) { break }
    Start-Sleep -Seconds 5
    $elapsed += 5
    Write-Host "  ... 等待中 ($elapsed/$maxWait s)" -ForegroundColor DarkGray
}

# 打印状态
Write-Host "`n  容器状态:" -ForegroundColor White
foreach ($svc in $services) {
    $status = docker inspect -f "{{.State.Health.Status}}" $svc 2>$null
    if ($status -eq "healthy") {
        Write-Host "    $svc -> healthy" -ForegroundColor Green
    } else {
        $running = docker inspect -f "{{.State.Status}}" $svc 2>$null
        Write-Host "    $svc -> $running (health: $status)" -ForegroundColor Yellow
    }
}

# --- 2. 启动 GBase 8A (独立容器) ---
Write-Host "`n[2/5] 启动 GBase 8A..." -ForegroundColor Yellow
$gbaseRunning = docker ps -q -f "name=gbase8a" 2>$null
if ($gbaseRunning) {
    Write-Host "  gbase8a -> 已在运行" -ForegroundColor Green
} else {
    $gbaseExists = docker ps -aq -f "name=gbase8a" 2>$null
    if ($gbaseExists) {
        docker start gbase8a | Out-Null
        Write-Host "  gbase8a -> 已启动" -ForegroundColor Green
    } else {
        Write-Host "  gbase8a 容器不存在，需要创建" -ForegroundColor Red
        Write-Host "  运行: docker run -d --name gbase8a --privileged --hostname=gbase8a -p 5258:5258 shihd/gbase8a:1.0" -ForegroundColor Gray
    }
}
# GBase 需要一点启动时间
Start-Sleep -Seconds 5

# --- 3. 启动 DM8 达梦 (如果本地安装) ---
Write-Host "`n[3/5] 检查达梦 DM8..." -ForegroundColor Yellow
$dm = Get-Process -Name "dmserver" -ErrorAction SilentlyContinue
if ($dm) {
    Write-Host "  dmserver -> 已在运行" -ForegroundColor Green
} else {
    # 尝试常见路径启动
    $dmPaths = @(
        "C:\dmdbms\bin\dmserver.exe",
        "D:\dmdbms\bin\dmserver.exe"
    )
    $dmFound = $false
    foreach ($p in $dmPaths) {
        if (Test-Path $p) {
            # 达梦需要以服务方式启动
            $dmService = Get-Service -Name "DmServiceDMSERVER" -ErrorAction SilentlyContinue
            if ($dmService) {
                Start-Service "DmServiceDMSERVER"
                Write-Host "  dmserver -> 通过 Windows 服务启动" -ForegroundColor Green
            } else {
                Start-Process $p -WindowStyle Minimized
                Write-Host "  dmserver -> 直接启动" -ForegroundColor Green
            }
            $dmFound = $true
            break
        }
    }
    if (-not $dmFound) {
        Write-Host "  dmserver -> 未安装 (跳过)" -ForegroundColor Gray
    }
}

# --- 4. Django 数据库迁移 ---
Write-Host "`n[4/5] Django 数据库迁移..." -ForegroundColor Yellow
Push-Location C:\DB_Monitor
python manage.py migrate --noinput 2>&1 | Select-Object -Last 3
Pop-Location

# --- 5. 启动 Django 服务 ---
Write-Host "`n[5/5] 启动 Django 服务..." -ForegroundColor Yellow

# 启动采集守护进程 (后台)
Push-Location C:\DB_Monitor
$monitorProc = Start-Process python -ArgumentList "manage.py", "start_monitor" -WindowStyle Minimized -PassThru
Write-Host "  start_monitor -> PID=$($monitorProc.Id) (后台运行)" -ForegroundColor Green

# 启动 runserver (后台)
$serverProc = Start-Process python -ArgumentList "manage.py", "runserver", "0.0.0.0:8888" -WindowStyle Minimized -PassThru
Write-Host "  runserver -> PID=$($serverProc.Id) (端口 8888)" -ForegroundColor Green
Pop-Location

# 等待 runserver 启动
Start-Sleep -Seconds 3

# --- 6. 连接验证 ---
Write-Host "`n[验证] 检查所有数据库连接..." -ForegroundColor Yellow
$testScript = @"
import os,django; os.environ['DJANGO_SETTINGS_MODULE']='dbmonitor.settings'; django.setup()
from monitor.models import DatabaseConfig
from monitor.checkers import CHECKER_MAP
results = []
for db in DatabaseConfig.objects.filter(is_active=True):
    checker_cls = CHECKER_MAP.get(db.db_type)
    if not checker_cls:
        results.append(f'  {db.name} ({db.db_type}) -> 无采集器')
        continue
    try:
        checker = checker_cls(command_instance=None)
        conn = checker.get_connection(db)
        conn.close()
        results.append(f'  {db.name} ({db.db_type}) -> OK')
    except Exception as e:
        results.append(f'  {db.name} ({db.db_type}) -> FAIL: {str(e)[:60]}')
print('\n'.join(results))
"@

$testScript | python 2>$null

# --- 汇总 ---
Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  DB-AIOps 开发环境已启动!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  Web 界面:  http://localhost:8888" -ForegroundColor White
Write-Host "  前端开发:  http://localhost:3000" -ForegroundColor White
Write-Host "  API:       http://localhost:8888/api/" -ForegroundColor White
Write-Host ""
Write-Host "  停止服务:  scripts\dev-stop.ps1" -ForegroundColor Gray
Write-Host ""

# 保存 PID 信息
$pidInfo = @{
    runserver = $serverProc.Id
    start_monitor = $monitorProc.Id
    timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
} | ConvertTo-Json
$pidInfo | Out-File -FilePath "C:\DB_Monitor\scripts\.dev-pids.json" -Encoding utf8

Read-Host "按回车键退出"
