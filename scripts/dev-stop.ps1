# ===========================================
# DB-AIOps 开发环境 - 一键停止脚本
# 用途: 重启电脑前运行，优雅关闭所有服务
# 用法: 右键"使用 PowerShell 运行" 或终端 ./scripts/dev-stop.ps1
# ===========================================

Write-Host "============================================" -ForegroundColor Cyan
Write-Host "  DB-AIOps 开发环境 - 停止所有服务" -ForegroundColor Cyan
Write-Host "============================================" -ForegroundColor Cyan

$ErrorActionPreference = "SilentlyContinue"

# --- 1. 停止 Django 开发服务器 (runserver) ---
Write-Host "`n[1/4] 停止 Django runserver (端口 8888)..." -ForegroundColor Yellow
$runserver = Get-NetTCPConnection -LocalPort 8888 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($runserver) {
    $pid = $runserver.OwningProcess
    Stop-Process -Id $pid -Force
    Write-Host "  已停止 PID=$pid" -ForegroundColor Green
} else {
    Write-Host "  未运行 (跳过)" -ForegroundColor Gray
}

# --- 2. 停止 Django 采集守护进程 (start_monitor) ---
Write-Host "`n[2/4] 停止 start_monitor 采集进程..." -ForegroundColor Yellow
$monitors = Get-Process -Name "python" -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -match "start_monitor"
}
if ($monitors) {
    $monitors | Stop-Process -Force
    Write-Host "  已停止 $($monitors.Count) 个采集进程" -ForegroundColor Green
} else {
    # 备选: 按命令行参数查找
    $wmic = Get-WmiObject Win32_Process -Filter "CommandLine LIKE '%start_monitor%'" -ErrorAction SilentlyContinue
    if ($wmic) {
        $wmic | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
        Write-Host "  已停止 $($wmic.Count) 个采集进程 (WMI)" -ForegroundColor Green
    } else {
        Write-Host "  未运行 (跳过)" -ForegroundColor Gray
    }
}

# --- 3. 停止 Docker 容器 ---
Write-Host "`n[3/4] 停止 Docker 容器..." -ForegroundColor Yellow

# 先停独立容器 (GBase 8A)
$gbase = docker ps -q -f "name=gbase8a" 2>$null
if ($gbase) {
    docker stop gbase8a | Out-Null
    Write-Host "  gbase8a -> 已停止" -ForegroundColor Green
} else {
    Write-Host "  gbase8a -> 未运行" -ForegroundColor Gray
}

# 再停 docker-compose 编排容器
if (Test-Path "C:\DB_Monitor\docker-compose.yml") {
    Push-Location C:\DB_Monitor
    $composeRunning = docker compose ps -q 2>$null
    if ($composeRunning) {
        docker compose stop
        Write-Host "  docker-compose 服务已停止" -ForegroundColor Green
    } else {
        Write-Host "  docker-compose 服务未运行" -ForegroundColor Gray
    }
    Pop-Location
}

# --- 4. DM8 达梦 (如果单独部署) ---
Write-Host "`n[4/4] 检查达梦 DM8..." -ForegroundColor Yellow
$dm = Get-Process -Name "dmserver" -ErrorAction SilentlyContinue
if ($dm) {
    $dm | Stop-Process -Force
    Write-Host "  dmserver -> 已停止" -ForegroundColor Green
} else {
    Write-Host "  dmserver -> 未运行 (跳过)" -ForegroundColor Gray
}

# --- 汇总 ---
Write-Host "`n============================================" -ForegroundColor Cyan
Write-Host "  所有开发服务已停止，可以安全重启电脑" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Cyan
Write-Host "`n  重启后运行: scripts\dev-start.ps1" -ForegroundColor White
Read-Host "`n按回车键退出"
