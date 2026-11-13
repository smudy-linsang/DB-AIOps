# =============================================================================
# DB-026 b1 治本实施驱动脚本（默认只读校验；写库动作需显式 -Confirm + 环境变量注入口令）
# =============================================================================
# 用途：DB-026 订单任务 2 的「备脚本、备回退」件，与 db026_b1_apply.sql / db026_b1_rollback.sql 配套。
# 授权依据：STATUS v1.47（commit 53a2900）—— G-5 获准（限 b1 的 S3）、S-2 采纳（平台侧同批）。
# 执行时点：**一律在 11-15 09:50 D2 复判之后**；D2 之前只评估、不动库。
#
# 用法：
#   只读校验现状（默认，不改库、不需要口令）：
#     powershell -File scripts/db026_b1.ps1 -Mode Verify
#   实施（建角色 + 授权）：
#     $env:DB026_ROLE_PASSWORD='…'      # DB_Monitor 开发副本角色口令
#     $env:DB026_APP_ROLE_PASSWORD='…'  # 平台侧角色口令
#     powershell -File scripts/db026_b1.ps1 -Mode Apply -Confirm
#   回退（撤销两角色及其权限）：
#     powershell -File scripts/db026_b1.ps1 -Mode Rollback -Confirm
#
# 安全边界：
#   * 口令只从环境变量读取，**不写文件、不打印、不进仓库**（psql -v 传值会在宿主
#     进程列表中短暂可见；若环境要求更严，改由操作者在容器内交互式执行 SQL 文件）。
#   * SQL 以 docker cp 进容器后以 psql -f 执行（字节精确，避免 PowerShell 管道
#     给原生命令时插入 BOM 破坏脚本首行），执行完即从容器内删除临时副本。
#   * 本脚本只做 DB 侧动作；**不重启任何进程/容器、不改采集器与 Web 的连接指向**
#     （那属 b1-3 / b1-5 的独立动作，按 D2 后窗口执行）。
#   * Apply / Rollback 缺 -Confirm 一律拒绝执行。
# =============================================================================

[CmdletBinding()]
param(
    [ValidateSet('Verify', 'Apply', 'Rollback')]
    [string]$Mode = 'Verify',
    [string]$Container = 'db-aiops-timescaledb',
    [switch]$Confirm
)

$ErrorActionPreference = 'Stop'
$applySql = Join-Path $PSScriptRoot 'db026_b1_apply.sql'
$rollbackSql = Join-Path $PSScriptRoot 'db026_b1_rollback.sql'

function Invoke-SqlText {
    param([string]$Sql)
    $out = & docker exec $Container psql -U postgres -d postgres -v ON_ERROR_STOP=1 -c $Sql
    if ($LASTEXITCODE -ne 0) { throw "psql 退出码 $LASTEXITCODE" }
    $out
}

function Invoke-SqlFile {
    param([string]$Path, [string[]]$Vars = @())
    $remote = '/tmp/' + [System.IO.Path]::GetFileName($Path)
    & docker cp $Path ("$($Container):" + $remote) | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "docker cp 失败" }
    try {
        $dockerArgs = @('exec', $Container, 'psql', '-U', 'postgres', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1')
        foreach ($v in $Vars) { $dockerArgs += @('-v', $v) }
        $dockerArgs += @('-f', $remote)
        & docker @dockerArgs
        if ($LASTEXITCODE -ne 0) { throw "psql 退出码 $LASTEXITCODE" }
    }
    finally {
        & docker exec $Container rm -f $remote | Out-Null
    }
}

function Get-EnvOrThrow([string]$name) {
    $val = [Environment]::GetEnvironmentVariable($name)
    if ([string]::IsNullOrWhiteSpace($val)) { throw "环境变量 $name 未设置或为空（口令只经环境变量注入）" }
    if ($val.Length -lt 8) { throw "环境变量 $name 长度不足 8 位" }
    return $val
}

Write-Output "=== DB-026 b1 驱动（Mode=$Mode；容器=$Container）==="

switch ($Mode) {
    'Verify' {
        Write-Output '--- 现状校验（只读）---'
        Invoke-SqlText "SELECT rolname, rolsuper, rolcanlogin, rolconnlimit FROM pg_roles WHERE rolname IN ('dbmonitor_app','dbaiops_app') ORDER BY rolname;"
        Invoke-SqlText "SELECT current_setting('max_connections') AS max_connections, current_setting('superuser_reserved_connections') AS reserved, current_setting('max_connections')::int - current_setting('superuser_reserved_connections')::int AS client_usable_slots;"
        Invoke-SqlText "SELECT count(*) AS client_backends FROM pg_stat_activity WHERE backend_type='client backend';"
    }
    'Apply' {
        if (-not $Confirm) { throw 'Apply 需要显式 -Confirm（本次属写库动作）' }
        $pw = Get-EnvOrThrow 'DB026_ROLE_PASSWORD'
        $pw2 = Get-EnvOrThrow 'DB026_APP_ROLE_PASSWORD'
        Write-Output '--- 将执行：建 dbmonitor_app(LIMIT 30) 与 dbaiops_app(LIMIT 60) + 两库授权（口令不打印）---'
        Invoke-SqlFile -Path $applySql -Vars @("pw=$pw", "pw2=$pw2")
        Write-Output '--- 实施后校验 ---'
        Invoke-SqlText "SELECT rolname, rolsuper, rolcanlogin, rolconnlimit FROM pg_roles WHERE rolname IN ('dbmonitor_app','dbaiops_app') ORDER BY rolname;"
        Write-Output '--- 提醒：限额须与「切角色」同批才生效；b1-5 平台侧切换需重启 Web/采集器（排 D2 后、避开 15:00-15:10）---'
    }
    'Rollback' {
        if (-not $Confirm) { throw 'Rollback 需要显式 -Confirm（本次属写库动作）' }
        Write-Output '--- 将执行：REASSIGN/DROP OWNED + DROP ROLE（不动既有表与数据）---'
        Invoke-SqlFile -Path $rollbackSql
        Write-Output '--- 回退后：平台侧与 DB_Monitor 侧连接串需切回 postgres 并重启对应进程（独立动作，D2 后窗口）---'
    }
}
