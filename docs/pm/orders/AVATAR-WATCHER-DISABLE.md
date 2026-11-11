# 任务单：停用 watcher（头像同步派单）

**代号**：AVATAR-WATCHER-DISABLE（watcher 类临时单号，不占 DB 编号）

执行：MMMan

## 一、背景与授权

用户 2026-11-11 20:08 拍板：停掉 watcher。依据为 AVATAR-WATCHER-IDENTIFY 报告（2026-11-11 20:05，有条件通过）：watcher 为 M-team 自建（`C:\dispatcher\mman_avatar_watcher.py` + 计划任务 `\MMMan-DBAIOps-AvatarWatcher`），派单通道为 `mman_dbaiops_quiet_scan.py` 读 `avatar_watch_state.json` → 本地 HTTP `/fire` → `mcode exec` 会话直投；宿主内存态会写回磁盘态，进程外头像写入无法持久，watcher 当日已重复下发 7 次、只烧回合、无收益。用户已授权停用。

## 二、任务（授权范围内的写操作）

1. **停用计划任务**：禁用 `\MMMan-DBAIOps-AvatarWatcher`（是禁用、不是删除任务，保留任务定义以备恢复），核实任务状态为 Disabled。
2. **停掉在跑进程**：停止当前 watcher 进程（2026-11-11 09:12:48 起的 `pythonw.exe` PID 12728，以实测为准）与 watchdog 进程；核实进程表中无 `mman_avatar_watcher.py` / `mman_avatar_watchdog.py` 在跑。
3. **停用扫描器的头像派单分支**：在 `mman_dbaiops_quiet_scan.py` 中仅停用头像（avatar）派单分支（改配置开关或加分支守卫均可），**不得动其它派单逻辑**；若判断改动有风险，跳过此步、在报告里写明原因（只做第 1、2 步亦可结单）。
4. **清掉残留待办（仅当第 3 步跳过时需要）**：若第 3 步跳过，把 `avatar_watch_state.json` 里的 todo 清空，防止扫描器凭过期待办再发一单；若第 3 步已做，不必动该文件。

## 三、核验

- 计划任务状态为 Disabled（贴 `schtasks` 查询输出）。
- 进程表中无 watcher/watchdog 进程。
- 改动后至少观察 10 分钟：`mman_quiet_scan.log` 无新增 `fire avatar` 行；`avatar_watch_state.json` 的 mtime 不再推进。
- 只动上述范围：不得改其它计划任务、不得动其它派单分支、不得删文件、不得重启宿主桌面端、不得裸改 `state.json`。

## 四、交付

报告落 `docs/pm/reports/AVATAR-WATCHER-DISABLE-report-m-team.md`，结论取三档之一：

- **通过**：第 1、2、3 步均完成且核验通过。
- **有条件通过**：第 1、2 步完成，第 3 步因风险跳过（原因已写明）且第 4 步已做、核验通过。
- **不通过**：第 1 或第 2 步未完成。

只报事实与证据（任务状态、进程表、日志尾行），不做最终裁决；STATUS.md 与 DECISIONS.md 只由 PM 改。
