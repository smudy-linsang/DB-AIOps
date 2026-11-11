# 任务单：撤销停用 watcher，继续留用

**代号**：AVATAR-WATCHER-KEEP（watcher 类临时单号，不占 DB 编号）

执行：MMMan

## 一、背景与授权

用户先于 2026-11-11 20:08 拍板停掉 watcher（单 AVATAR-WATCHER-DISABLE，已落黑板，提交 28924568），后于 20:19 改主意：先别停，留着。依据：SYNC8/SYNC9 已证明经宿主进程内 MCP 端点调用 update_bot 可持久切换，通道已通。本单撤销 AVATAR-WATCHER-DISABLE，用户授权继续留用 watcher。

## 二、任务

1. **立即停止执行 AVATAR-WATCHER-DISABLE**：如尚未开始执行，直接停止，不再执行其中任何一步。
2. **如已部分执行，立即恢复**：
   - 如已禁用计划任务 `\MMMan-DBAIOps-AvatarWatcher`，重新启用它（恢复为 Ready/Enabled）。
   - 如已停掉 watcher/watchdog 进程，由计划任务的 watchdog 在下一轮（5 分钟内）拉起 watcher 即可；如 5 分钟后仍未拉起，手动拉起一次。
   - 如已改动 `mman_dbaiops_quiet_scan.py` 的头像派单分支，恢复原状。
   - 如已清空 `avatar_watch_state.json` 的 todo，无需恢复（watcher 下一轮会重写）。
3. **核验（恢复后）**：计划任务状态为 Ready/Enabled；进程表中有 watcher 进程在跑；`avatar_watch_state.json` 的 mtime 在推进（watcher 正常轮询）。

## 三、交付

报告落 `docs/pm/reports/AVATAR-WATCHER-KEEP-report-m-team.md`，结论取三档之一：

- **通过**：DISABLE 未执行或已完全恢复，核验通过。
- **有条件通过**：DISABLE 已部分执行、恢复后核验通过，但有残留（如扫描器分支改动未完全还原，原因已写明）。
- **不通过**：DISABLE 已执行且未能恢复。

只报事实与证据（任务状态、进程表、状态文件 mtime），不做最终裁决；STATUS.md 与 DECISIONS.md 只由 PM 改。
